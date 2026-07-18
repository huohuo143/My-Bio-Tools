import AppKit
import CryptoKit
import Foundation

struct AppUpdateContext {
    let baseURL: URL
    let accessToken: String
    let publicJWK: String
}

struct AppUpdateManifest: Codable, Equatable {
    let typ: String
    let issuedAt: Int64
    let expiresAt: Int64
    let schemaVersion: Int
    let platform: String
    let bundleIdentifier: String
    let appVersion: String
    let build: Int
    let minimumSystemVersion: String
    let size: Int64
    let sha256: String
    let r2Key: String
    let releaseNotes: String
    let publishedAt: String
    let mandatory: Bool

    enum CodingKeys: String, CodingKey {
        case typ, platform, build, size, sha256, mandatory
        case issuedAt = "iat"
        case expiresAt = "exp"
        case schemaVersion = "schema_version"
        case bundleIdentifier = "bundle_identifier"
        case appVersion = "app_version"
        case minimumSystemVersion = "minimum_system_version"
        case r2Key = "r2_key"
        case releaseNotes = "release_notes"
        case publishedAt = "published_at"
    }
}

struct UpdateManifestVerifier {
    private struct PublicJWK: Decodable { let kty: String; let crv: String; let x: String }
    private struct Header: Decodable { let alg: String; let typ: String }

    func verify(token: String, publicJWK: String, now: Date = Date()) throws -> AppUpdateManifest {
        let components = token.split(separator: ".", omittingEmptySubsequences: false)
        guard components.count == 3,
              let headerData = Data(updateBase64URL: String(components[0])),
              let payloadData = Data(updateBase64URL: String(components[1])),
              let signature = Data(updateBase64URL: String(components[2])) else {
            throw AppUpdateError.invalidManifest
        }
        let header = try JSONDecoder().decode(Header.self, from: headerData)
        guard header.alg == "EdDSA", header.typ == "JWT" else { throw AppUpdateError.invalidManifest }
        let jwk = try JSONDecoder().decode(PublicJWK.self, from: Data(publicJWK.utf8))
        guard jwk.kty == "OKP", jwk.crv == "Ed25519",
              let rawKey = Data(updateBase64URL: jwk.x) else { throw AppUpdateError.invalidPublicKey }
        let key = try Curve25519.Signing.PublicKey(rawRepresentation: rawKey)
        let signingInput = Data("\(components[0]).\(components[1])".utf8)
        guard key.isValidSignature(signature, for: signingInput) else { throw AppUpdateError.invalidSignature }
        let manifest = try JSONDecoder().decode(AppUpdateManifest.self, from: payloadData)
        let current = Int64(now.timeIntervalSince1970)
        guard manifest.typ == "app-update", manifest.schemaVersion == 1,
              manifest.platform == "macos-arm64",
              manifest.bundleIdentifier == "top.aizs.my-bio-tools",
              manifest.expiresAt > current, manifest.issuedAt <= current + 300,
              manifest.size > 0, manifest.build > 0,
              manifest.sha256.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil else {
            throw AppUpdateError.invalidManifest
        }
        return manifest
    }
}

enum AppUpdatePhase: Equatable {
    case idle
    case checking
    case upToDate
    case available(AppUpdateManifest)
    case downloading(AppUpdateManifest)
    case preparing(AppUpdateManifest)
    case failed(String)
}

@MainActor
final class AppUpdateStore: ObservableObject {
    @Published private(set) var phase: AppUpdatePhase = .idle
    @Published private(set) var notice: String?

    var currentVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0.0.0"
    }

    var currentBuild: Int {
        Int(Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "0") ?? 0
    }

    var availableUpdate: AppUpdateManifest? {
        if case let .available(manifest) = phase { return manifest }
        return nil
    }

    func check(auth: AuthStore, silent: Bool = false) async {
        guard !isBusy else { return }
        phase = .checking
        if !silent { notice = nil }
        guard let context = await auth.appUpdateContext() else {
            phase = .failed("需要联网登录后才能检查内部版本更新。")
            return
        }
        do {
            let manifest = try await AppUpdateClient().manifest(context: context)
            if manifest.build > currentBuild {
                phase = .available(manifest)
                notice = "发现 My Bio Tools \(manifest.appVersion)（build \(manifest.build)）。"
            } else {
                phase = .upToDate
                if !silent { notice = "当前已是最新版本。" }
            }
        } catch {
            phase = .failed(error.localizedDescription)
            if !silent { notice = error.localizedDescription }
        }
    }

    func install(_ manifest: AppUpdateManifest, auth: AuthStore) async {
        guard !isBusy else { return }
        guard let context = await auth.appUpdateContext() else {
            phase = .failed("登录状态已过期，请重新登录后再更新。")
            return
        }
        do {
            phase = .downloading(manifest)
            notice = "正在下载并校验更新安装包…"
            let dmgURL = try await AppUpdateClient().download(manifest: manifest, context: context)
            phase = .preparing(manifest)
            notice = "正在验证应用身份并准备安全替换…"
            try await Task.detached(priority: .userInitiated) {
                try AppUpdateInstaller().prepareAndLaunch(
                    dmgURL: dmgURL,
                    manifest: manifest,
                    currentAppURL: Bundle.main.bundleURL,
                    currentPID: ProcessInfo.processInfo.processIdentifier
                )
            }.value
            notice = "更新已通过校验，软件将自动重启。"
            NSApplication.shared.terminate(nil)
        } catch {
            phase = .failed(error.localizedDescription)
            notice = error.localizedDescription
        }
    }

    private var isBusy: Bool {
        switch phase {
        case .checking, .downloading, .preparing: true
        default: false
        }
    }
}

private struct AppUpdateClient {
    private struct Envelope: Decodable { let manifestToken: String }

    func manifest(context: AppUpdateContext) async throws -> AppUpdateManifest {
        var request = URLRequest(url: context.baseURL.appending(path: "/api/v1/app-update"))
        request.timeoutInterval = 20
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue("Bearer \(context.accessToken)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let (data, response) = try await URLSession.shared.data(for: request)
        try validate(response: response, data: data)
        let envelope = try JSONDecoder().decode(Envelope.self, from: data)
        return try UpdateManifestVerifier().verify(token: envelope.manifestToken, publicJWK: context.publicJWK)
    }

    func download(manifest: AppUpdateManifest, context: AppUpdateContext) async throws -> URL {
        var request = URLRequest(url: context.baseURL.appending(path: "/api/v1/app-update/download"))
        request.timeoutInterval = 900
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue("Bearer \(context.accessToken)", forHTTPHeaderField: "Authorization")
        let (temporaryURL, response) = try await URLSession.shared.download(for: request)
        try validate(response: response, data: Data())
        let root = try AppUpdatePaths.rootDirectory()
        let downloads = root.appending(path: "Downloads", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: downloads, withIntermediateDirectories: true)
        let destination = downloads.appending(path: "My-Bio-Tools-\(manifest.appVersion)-\(UUID().uuidString).dmg")
        try FileManager.default.moveItem(at: temporaryURL, to: destination)
        return destination
    }

    private func validate(response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse else { throw AppUpdateError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else {
            if let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let error = object["error"] as? [String: Any], let message = error["message"] as? String {
                throw AppUpdateError.server(message)
            }
            throw AppUpdateError.server("更新服务返回 HTTP \(http.statusCode)。")
        }
    }
}

private enum AppUpdatePaths {
    static func rootDirectory() throws -> URL {
        let base = try FileManager.default.url(
            for: .applicationSupportDirectory, in: .userDomainMask,
            appropriateFor: nil, create: true
        )
        let root = base.appending(path: "My Bio Tools/Updates", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        return root
    }
}

private struct AppUpdateInstaller {
    func prepareAndLaunch(
        dmgURL: URL,
        manifest: AppUpdateManifest,
        currentAppURL: URL,
        currentPID: Int32
    ) throws {
        guard currentAppURL.pathExtension == "app",
              currentAppURL.lastPathComponent == "My Bio Tools.app" else {
            throw AppUpdateError.unsupportedInstallLocation
        }
        let attributes = try FileManager.default.attributesOfItem(atPath: dmgURL.path)
        guard (attributes[.size] as? NSNumber)?.int64Value == manifest.size else {
            throw AppUpdateError.sizeMismatch
        }
        guard try sha256(of: dmgURL) == manifest.sha256 else { throw AppUpdateError.checksumMismatch }

        let mountData = try run("/usr/bin/hdiutil", ["attach", "-nobrowse", "-readonly", "-plist", dmgURL.path])
        let mountURL = try mountPoint(from: mountData)
        defer { _ = try? run("/usr/bin/hdiutil", ["detach", mountURL.path]) }
        let sourceApp = try appBundle(in: mountURL)
        let root = try AppUpdatePaths.rootDirectory()
        let stagingRoot = root.appending(path: "Staging/\(manifest.appVersion)-\(manifest.build)-\(UUID().uuidString)", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: stagingRoot, withIntermediateDirectories: true)
        let stagedApp = stagingRoot.appending(path: "My Bio Tools.app", directoryHint: .isDirectory)
        _ = try run("/usr/bin/ditto", [sourceApp.path, stagedApp.path])
        try validate(appURL: stagedApp, manifest: manifest)

        let backupRoot = root.appending(path: "Backups", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: backupRoot, withIntermediateDirectories: true)
        let backupApp = backupRoot.appending(
            path: "My Bio Tools-\(currentVersion(at: currentAppURL))-\(Int(Date().timeIntervalSince1970)).app",
            directoryHint: .isDirectory
        )
        let logURL = root.appending(path: "update-helper.log")
        let helperURL = root.appending(path: "install-update-\(UUID().uuidString).zsh")
        try helperScript.write(to: helperURL, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: helperURL.path)

        let process = Process()
        process.executableURL = URL(filePath: "/bin/zsh")
        process.arguments = [
            helperURL.path, currentAppURL.path, stagedApp.path, backupApp.path,
            String(currentPID), logURL.path,
        ]
        try process.run()
    }

    private func validate(appURL: URL, manifest: AppUpdateManifest) throws {
        guard let bundle = Bundle(url: appURL), bundle.bundleIdentifier == manifest.bundleIdentifier,
              bundle.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String == manifest.appVersion,
              Int(bundle.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "") == manifest.build else {
            throw AppUpdateError.bundleIdentityMismatch
        }
        _ = try run("/usr/bin/codesign", ["--verify", "--deep", "--strict", appURL.path])
        let architectures = String(decoding: try run(
            "/usr/bin/lipo", ["-archs", appURL.appending(path: "Contents/MacOS/My Bio Tools").path]
        ), as: UTF8.self)
        guard architectures.split(whereSeparator: \.isWhitespace).contains("arm64") else {
            throw AppUpdateError.wrongArchitecture
        }
    }

    private func currentVersion(at appURL: URL) -> String {
        Bundle(url: appURL)?.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "unknown"
    }

    private func sha256(of fileURL: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: fileURL)
        defer { try? handle.close() }
        var hasher = SHA256()
        while true {
            let data = try handle.read(upToCount: 4 * 1024 * 1024) ?? Data()
            if data.isEmpty { break }
            hasher.update(data: data)
        }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }

    private func mountPoint(from plistData: Data) throws -> URL {
        guard let root = try PropertyListSerialization.propertyList(from: plistData, format: nil) as? [String: Any],
              let entities = root["system-entities"] as? [[String: Any]],
              let path = entities.compactMap({ $0["mount-point"] as? String }).first else {
            throw AppUpdateError.mountFailed
        }
        return URL(filePath: path, directoryHint: .isDirectory)
    }

    private func appBundle(in directory: URL) throws -> URL {
        let candidates = try FileManager.default.contentsOfDirectory(
            at: directory, includingPropertiesForKeys: nil, options: [.skipsHiddenFiles]
        ).filter { $0.pathExtension == "app" && $0.lastPathComponent == "My Bio Tools.app" }
        guard candidates.count == 1 else { throw AppUpdateError.bundleIdentityMismatch }
        return candidates[0]
    }

    @discardableResult
    private func run(_ executable: String, _ arguments: [String]) throws -> Data {
        let process = Process()
        let output = Pipe()
        let errors = Pipe()
        process.executableURL = URL(filePath: executable)
        process.arguments = arguments
        process.standardOutput = output
        process.standardError = errors
        try process.run()
        process.waitUntilExit()
        let data = output.fileHandleForReading.readDataToEndOfFile()
        let errorData = errors.fileHandleForReading.readDataToEndOfFile()
        guard process.terminationStatus == 0 else {
            let detail = String(decoding: errorData, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
            throw AppUpdateError.commandFailed(detail.isEmpty ? executable : detail)
        }
        return data
    }

    private var helperScript: String {
        """
        #!/bin/zsh
        set -eu
        current_app="$1"
        staged_app="$2"
        backup_app="$3"
        parent_pid="$4"
        log_file="$5"
        exec >>"$log_file" 2>&1
        echo "update-start $(date -Iseconds)"
        for _ in {1..240}; do
          if ! kill -0 "$parent_pid" 2>/dev/null; then break; fi
          sleep 0.25
        done
        if kill -0 "$parent_pid" 2>/dev/null; then
          echo "parent-still-running"
          exit 20
        fi
        if ! /bin/mv "$current_app" "$backup_app"; then
          echo "backup-failed"
          /usr/bin/open -n "$current_app" || true
          exit 21
        fi
        if ! /bin/mv "$staged_app" "$current_app"; then
          echo "install-failed"
          /bin/mv "$backup_app" "$current_app" || true
          /usr/bin/open -n "$current_app" || true
          exit 22
        fi
        /usr/bin/xattr -cr "$current_app" || true
        if ! /usr/bin/codesign --verify --deep --strict "$current_app"; then
          echo "post-install-signature-failed"
          failed_app="${current_app%.app}-failed-$(date +%s).app"
          /bin/mv "$current_app" "$failed_app" || true
          /bin/mv "$backup_app" "$current_app" || true
          /usr/bin/open -n "$current_app" || true
          exit 23
        fi
        /usr/bin/open -n "$current_app"
        echo "update-complete $(date -Iseconds)"
        """
    }
}

enum AppUpdateError: LocalizedError {
    case invalidResponse, invalidManifest, invalidPublicKey, invalidSignature
    case server(String), sizeMismatch, checksumMismatch, mountFailed
    case bundleIdentityMismatch, wrongArchitecture, unsupportedInstallLocation
    case commandFailed(String)

    var errorDescription: String? {
        switch self {
        case .invalidResponse: "更新服务返回了无效响应。"
        case .invalidManifest: "更新清单格式、平台或有效期无效，已停止更新。"
        case .invalidPublicKey: "安装包内的更新验证公钥无效。"
        case .invalidSignature: "更新清单签名验证失败，已停止更新。"
        case let .server(message): message
        case .sizeMismatch: "更新安装包大小与签名清单不一致，已停止更新。"
        case .checksumMismatch: "更新安装包 SHA-256 校验失败，已停止更新。"
        case .mountFailed: "无法安全挂载更新安装包。"
        case .bundleIdentityMismatch: "更新包的应用身份或版本与签名清单不一致。"
        case .wrongArchitecture: "更新包不是 Apple Silicon（arm64）版本。"
        case .unsupportedInstallLocation: "当前不是标准 My Bio Tools.app，无法自动替换。"
        case let .commandFailed(detail): "更新准备失败：\(detail)"
        }
    }
}

private extension Data {
    init?(updateBase64URL value: String) {
        var base64 = value.replacingOccurrences(of: "-", with: "+")
            .replacingOccurrences(of: "_", with: "/")
        base64 += String(repeating: "=", count: (4 - base64.count % 4) % 4)
        self.init(base64Encoded: base64)
    }
}
