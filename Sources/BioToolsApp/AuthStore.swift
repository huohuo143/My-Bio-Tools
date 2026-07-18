import AppKit
import Combine
import Foundation

@MainActor
final class AuthStore: ObservableObject {
    @Published private(set) var phase: AuthPhase = .checking
    @Published private(set) var authorization: BackendAuthorization?
    @Published private(set) var devices: [DeviceProfile] = []
    @Published private(set) var isBusy = false
    @Published private(set) var notice: String?

    private let keychain = KeychainStore()
    private let verifier = LicenseVerifier()
    private var configuration: AuthConfiguration?
    private var api: AuthAPIClient?
    private var session: StoredSession?
    private var installationID = ""
    private var periodicTask: Task<Void, Never>?
    private var wakeObserver: NSObjectProtocol?

    private static let installationAccount = "installation-id"
    private static let sessionAccount = "session"
    private static let trustedTimeAccount = "last-trusted-server-time"
    private static let savedLoginAccount = "saved-login-credentials"

    var isAuthorized: Bool { authorization != nil }

    func appUpdateContext() async -> AppUpdateContext? {
        await refreshAuthorization()
        guard let configuration, let session, authorization != nil else { return nil }
        return AppUpdateContext(
            baseURL: configuration.baseURL,
            accessToken: session.tokens.accessToken,
            publicJWK: configuration.publicJWK
        )
    }

    init() {
        wakeObserver = NotificationCenter.default.addObserver(
            forName: NSWorkspace.didWakeNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in await self?.refreshAuthorization() }
        }
    }

    deinit {
        if let wakeObserver { NotificationCenter.default.removeObserver(wakeObserver) }
        periodicTask?.cancel()
    }

    func bootstrap() async {
        guard case .checking = phase else { return }
        do {
            let configuration = try AuthConfiguration.load()
            self.configuration = configuration
            api = AuthAPIClient(configuration: configuration)
            installationID = try loadOrCreateInstallationID()
            session = try loadSession()
            if session == nil {
                phase = .signedOut
                return
            }
            await refreshAuthorization()
            startPeriodicRefresh()
        } catch let error as AuthClientError {
            phase = .configurationMissing(error.localizedDescription)
        } catch {
            phase = .signedOut
            notice = "无法读取本机授权信息：\(error.localizedDescription)"
        }
    }

    @discardableResult
    func login(email: String, password: String, rememberCredentials: Bool) async -> Bool {
        guard let api else { return false }
        isBusy = true; notice = nil
        defer { isBusy = false }
        do {
            let next = try await api.login(email: email, password: password, installationID: installationID)
            try accept(next, offline: false)
            do {
                if rememberCredentials {
                    try saveLoginCredentials(email: email, password: password)
                } else {
                    try clearSavedLoginCredentials()
                }
            } catch {
                notice = "登录成功，但未能保存账号和密码：\(error.localizedDescription)"
            }
            startPeriodicRefresh()
            return true
        } catch {
            applyLoginError(error, email: email)
            return false
        }
    }

    func savedLoginCredentials() -> SavedLoginCredentials? {
        do {
            guard let data = try keychain.data(for: Self.savedLoginAccount) else { return nil }
            return try JSONDecoder().decode(SavedLoginCredentials.self, from: data)
        } catch {
            try? keychain.delete(Self.savedLoginAccount)
            return nil
        }
    }

    func forgetSavedLoginCredentials() {
        do {
            try clearSavedLoginCredentials()
        } catch {
            notice = "未能清除已保存的账号和密码：\(error.localizedDescription)"
        }
    }

    func register(email: String, realName: String, labRole: String, applicationNote: String, password: String) async {
        guard let api else { return }
        isBusy = true; notice = nil
        defer { isBusy = false }
        do {
            notice = try await api.register(
                email: email, realName: realName, labRole: labRole,
                applicationNote: applicationNote, password: password
            )
            phase = .unverified(email)
        } catch { notice = error.localizedDescription }
    }

    func resendVerification(email: String) async {
        guard let api else { return }
        isBusy = true
        defer { isBusy = false }
        do { notice = try await api.resendVerification(email: email) }
        catch { notice = error.localizedDescription }
    }

    func forgotPassword(email: String) async {
        guard let api else { return }
        isBusy = true
        defer { isBusy = false }
        do { notice = try await api.forgotPassword(email: email) }
        catch { notice = error.localizedDescription }
    }

    func refreshAuthorization() async {
        guard let api, let current = session, let configuration else {
            if phase == .checking { phase = .signedOut }
            return
        }
        do {
            let next = try await api.refresh(
                refreshToken: current.tokens.refreshToken,
                installationID: installationID
            )
            try accept(next, offline: false)
        } catch let error as AuthClientError where error.isExplicitRevocation {
            clearLocalSession(message: error.localizedDescription)
        } catch let error as AuthClientError {
            guard case .network = error else {
                denyAuthorization(message: error.localizedDescription)
                return
            }
            do {
                let claims = try verifier.verify(
                    token: current.tokens.offlineLicense,
                    publicJWK: configuration.publicJWK,
                    installationID: installationID,
                    lastTrustedServerTime: try loadTrustedTime()
                )
                authorization = BackendAuthorization(
                    offlineLicense: current.tokens.offlineLicense,
                    installationHash: verifier.installationHash(for: installationID),
                    publicJWK: configuration.publicJWK,
                    omicsKeyB64: claims.omicsKeyB64
                )
                phase = .authorized(current.user, expiresAt: Date(timeIntervalSince1970: TimeInterval(claims.exp)), offline: true)
                notice = "授权服务暂时不可达，当前使用 7 天离线授权。"
            } catch { clearLocalSession(message: error.localizedDescription) }
        } catch {
            denyAuthorization(message: error.localizedDescription)
        }
    }

    func loadDevices() async {
        await refreshAuthorization()
        guard let api, let session, authorization != nil else { return }
        do { devices = try await api.devices(accessToken: session.tokens.accessToken) }
        catch { notice = error.localizedDescription }
    }

    func revokeDevice(_ id: String) async {
        await refreshAuthorization()
        guard let api, let session, authorization != nil else { return }
        do {
            let revokedCurrentDevice = devices.first(where: { $0.id == id })?.current == true
            try await api.revokeDevice(id, accessToken: session.tokens.accessToken)
            if revokedCurrentDevice {
                clearLocalSession(message: "当前设备已解绑。")
            } else {
                await loadDevices()
            }
        } catch { notice = error.localizedDescription }
    }

    func logout() async {
        await refreshAuthorization()
        if let api, let session { try? await api.logout(accessToken: session.tokens.accessToken) }
        clearLocalSession(message: nil)
    }

    func returnToLogin() {
        try? keychain.delete(Self.sessionAccount)
        authorization = nil
        session = nil
        phase = .signedOut
        notice = nil
    }

    private func accept(_ next: StoredSession, offline: Bool) throws {
        guard let configuration else { throw AuthClientError.configuration("授权配置缺失。") }
        let claims: OfflineLicenseClaims
        do {
            claims = try verifier.verify(
                token: next.tokens.offlineLicense,
                publicJWK: configuration.publicJWK,
                installationID: installationID,
                lastTrustedServerTime: nil
            )
        } catch LicenseError.missingOmicsKey {
            throw AuthClientError.configuration(
                "授权服务仍在返回旧版授权数据，未包含 v1.8.0 所需的多组学解锁信息。请升级授权服务后重试。"
            )
        }
        session = next
        try keychain.save(JSONEncoder().encode(next), for: Self.sessionAccount)
        try keychain.save(Data(String(next.tokens.serverTime).utf8), for: Self.trustedTimeAccount)
        authorization = BackendAuthorization(
            offlineLicense: next.tokens.offlineLicense,
            installationHash: verifier.installationHash(for: installationID),
            publicJWK: configuration.publicJWK,
            omicsKeyB64: claims.omicsKeyB64
        )
        phase = .authorized(next.user, expiresAt: Date(timeIntervalSince1970: TimeInterval(claims.exp)), offline: offline)
        notice = nil
    }

    private func applyLoginError(_ error: Error, email: String) {
        notice = error.localizedDescription
        guard case let .server(code, message, _) = error as? AuthClientError else { return }
        switch code {
        case "EMAIL_UNVERIFIED": phase = .unverified(email)
        case "PENDING_REVIEW": phase = .pending(email)
        case "ACCOUNT_REJECTED": phase = .rejected(message)
        case "ACCOUNT_SUSPENDED": phase = .suspended(message)
        default: break
        }
    }

    private func clearLocalSession(message: String?) {
        try? keychain.delete(Self.sessionAccount)
        authorization = nil
        session = nil
        devices = []
        periodicTask?.cancel()
        periodicTask = nil
        phase = .signedOut
        notice = message
    }

    private func denyAuthorization(message: String?) {
        authorization = nil
        devices = []
        phase = .signedOut
        notice = message
    }

    private func loadOrCreateInstallationID() throws -> String {
        if let data = try keychain.data(for: Self.installationAccount),
           let value = String(data: data, encoding: .utf8), !value.isEmpty { return value }
        let value = UUID().uuidString.lowercased()
        try keychain.save(Data(value.utf8), for: Self.installationAccount)
        return value
    }

    private func loadSession() throws -> StoredSession? {
        guard let data = try keychain.data(for: Self.sessionAccount) else { return nil }
        return try JSONDecoder().decode(StoredSession.self, from: data)
    }

    private func loadTrustedTime() throws -> Int64? {
        guard let data = try keychain.data(for: Self.trustedTimeAccount),
              let string = String(data: data, encoding: .utf8) else { return nil }
        return Int64(string)
    }

    private func saveLoginCredentials(email: String, password: String) throws {
        let credentials = SavedLoginCredentials(
            email: email.trimmingCharacters(in: .whitespacesAndNewlines).lowercased(),
            password: password
        )
        try keychain.save(
            JSONEncoder().encode(credentials),
            for: Self.savedLoginAccount,
            accessibility: .whenUnlocked
        )
    }

    private func clearSavedLoginCredentials() throws {
        try keychain.delete(Self.savedLoginAccount)
    }

    private func startPeriodicRefresh() {
        periodicTask?.cancel()
        periodicTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(21_600))
                guard !Task.isCancelled else { return }
                await self?.refreshAuthorization()
            }
        }
    }
}
