import Foundation

struct AuthAPIClient {
    private struct EmptyBody: Encodable {}
    private struct MessageResponse: Decodable { let message: String }
    private struct ErrorEnvelope: Decodable {
        struct Detail: Decodable { let code: String; let message: String }
        let error: Detail
    }
    private struct SessionResponse: Decodable {
        let user: UserProfile
        let accessToken: String
        let accessExpiresAt: Int64
        let refreshToken: String
        let refreshExpiresAt: Int64
        let offlineLicense: String
        let offlineLicenseExpiresAt: Int64
        let serverTime: Int64

        var session: StoredSession {
            StoredSession(
                user: user,
                tokens: AuthTokens(
                    accessToken: accessToken,
                    accessExpiresAt: accessExpiresAt,
                    refreshToken: refreshToken,
                    refreshExpiresAt: refreshExpiresAt,
                    offlineLicense: offlineLicense,
                    offlineLicenseExpiresAt: offlineLicenseExpiresAt,
                    serverTime: serverTime
                )
            )
        }
    }
    private struct DevicesResponse: Decodable { let devices: [DeviceProfile] }

    let configuration: AuthConfiguration
    var session: URLSession = .shared

    func register(
        email: String,
        realName: String,
        labRole: String,
        applicationNote: String,
        password: String
    ) async throws -> String {
        let response: MessageResponse = try await request(
            "/api/v1/register",
            method: "POST",
            body: [
                "email": email, "realName": realName, "labRole": labRole,
                "applicationNote": applicationNote, "password": password,
            ]
        )
        return response.message
    }

    func resendVerification(email: String) async throws -> String {
        let response: MessageResponse = try await request(
            "/api/v1/email/resend", method: "POST", body: ["email": email]
        )
        return response.message
    }

    func forgotPassword(email: String) async throws -> String {
        let response: MessageResponse = try await request(
            "/api/v1/password/forgot", method: "POST", body: ["email": email]
        )
        return response.message
    }

    func login(email: String, password: String, installationID: String) async throws -> StoredSession {
        struct LoginBody: Encodable {
            let email: String; let password: String; let installationId: String
            let platform: String; let deviceName: String; let appVersion: String
        }
        let deviceName = Host.current().localizedName ?? "Mac"
        let appVersion = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "1.8.0"
        let response: SessionResponse = try await request(
            "/api/v1/login",
            method: "POST",
            body: LoginBody(
                email: email,
                password: password,
                installationId: installationID,
                platform: "macos",
                deviceName: deviceName,
                appVersion: appVersion
            )
        )
        return response.session
    }

    func refresh(refreshToken: String, installationID: String) async throws -> StoredSession {
        struct RefreshBody: Encodable { let refreshToken: String; let installationId: String }
        let response: SessionResponse = try await request(
            "/api/v1/token/refresh",
            method: "POST",
            body: RefreshBody(refreshToken: refreshToken, installationId: installationID)
        )
        return response.session
    }

    func logout(accessToken: String) async throws {
        let _: MessageResponse = try await request(
            "/api/v1/logout", method: "POST", body: EmptyBody(), bearer: accessToken
        )
    }

    func devices(accessToken: String) async throws -> [DeviceProfile] {
        let response: DevicesResponse = try await request(
            "/api/v1/me/devices", method: "GET", body: Optional<EmptyBody>.none, bearer: accessToken
        )
        return response.devices
    }

    func revokeDevice(_ id: String, accessToken: String) async throws {
        let _: MessageResponse = try await request(
            "/api/v1/me/devices/\(id)", method: "DELETE", body: Optional<EmptyBody>.none, bearer: accessToken
        )
    }

    private func request<ResponseType: Decodable, Body: Encodable>(
        _ path: String,
        method: String,
        body: Body?,
        bearer: String? = nil
    ) async throws -> ResponseType {
        let url = configuration.baseURL.appending(path: path)
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = 20
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            request.httpBody = try JSONEncoder().encode(body)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        if let bearer { request.setValue("Bearer \(bearer)", forHTTPHeaderField: "Authorization") }

        let data: Data
        let response: URLResponse
        do { (data, response) = try await session.data(for: request) }
        catch { throw AuthClientError.network(error) }
        guard let http = response as? HTTPURLResponse else { throw AuthClientError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else {
            if let envelope = try? JSONDecoder().decode(ErrorEnvelope.self, from: data) {
                throw AuthClientError.server(
                    code: envelope.error.code,
                    message: envelope.error.message,
                    status: http.statusCode
                )
            }
            throw AuthClientError.server(code: "HTTP_\(http.statusCode)", message: "授权请求失败。", status: http.statusCode)
        }
        do { return try JSONDecoder().decode(ResponseType.self, from: data) }
        catch { throw AuthClientError.invalidResponse }
    }
}
