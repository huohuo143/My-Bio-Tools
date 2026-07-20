import Foundation

struct UserProfile: Codable, Equatable, Sendable {
    let id: String
    let email: String
    let realName: String
    let labRole: String
    let status: String
    let reviewReason: String?
    let authorizationExpiresAt: Int64?
    let authorizationPermanent: Bool?
}

struct DeviceProfile: Codable, Identifiable, Equatable {
    let id: String
    let platform: String
    let deviceName: String
    let appVersion: String
    let firstSeenAt: Int64
    let lastSeenAt: Int64
    let revokedAt: Int64?
    let current: Bool
}

struct AuthTokens: Codable, Equatable, Sendable {
    let accessToken: String
    let accessExpiresAt: Int64
    let refreshToken: String
    let refreshExpiresAt: Int64
    let offlineLicense: String
    let offlineLicenseExpiresAt: Int64
    let serverTime: Int64
}

struct StoredSession: Codable, Equatable, Sendable {
    let user: UserProfile
    let tokens: AuthTokens
}

struct SavedLoginCredentials: Codable, Equatable, Sendable {
    let email: String
    let password: String
}

struct BackendAuthorization: Equatable {
    let offlineLicense: String
    let installationHash: String
    let publicJWK: String
    let omicsKeyB64: String
}

enum AuthPhase: Equatable {
    case checking
    case signedOut
    case unverified(String)
    case pending(String)
    case rejected(String)
    case suspended(String)
    case authorized(UserProfile, expiresAt: Date, offline: Bool)
    case configurationMissing(String)
}

struct OfflineLicenseClaims: Codable {
    let typ: String
    let sub: String
    let device: String
    let iat: Int64
    let exp: Int64
    let version: Int
    let omicsKeyB64: String

    enum CodingKeys: String, CodingKey {
        case typ, sub, device, iat, exp, version
        case omicsKeyB64 = "omics_key_b64"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        typ = try container.decode(String.self, forKey: .typ)
        sub = try container.decode(String.self, forKey: .sub)
        device = try container.decode(String.self, forKey: .device)
        iat = try container.decode(Int64.self, forKey: .iat)
        exp = try container.decode(Int64.self, forKey: .exp)
        version = try container.decode(Int.self, forKey: .version)
        omicsKeyB64 = try container.decodeIfPresent(String.self, forKey: .omicsKeyB64) ?? ""
    }
}

enum AuthClientError: LocalizedError {
    case server(code: String, message: String, status: Int)
    case invalidResponse
    case network(Error)
    case configuration(String)

    var errorDescription: String? {
        switch self {
        case let .server(_, message, _): message
        case .invalidResponse: "授权服务返回了无效数据。"
        case let .network(error): "无法连接授权服务：\(error.localizedDescription)"
        case let .configuration(message): message
        }
    }

    var isExplicitRevocation: Bool {
        guard case let .server(code, _, status) = self else { return false }
        return status == 401 || status == 403 || [
            "AUTHORIZATION_REVOKED", "AUTHORIZATION_EXPIRED", "ACCOUNT_SUSPENDED", "ACCOUNT_DELETED", "SESSION_EXPIRED"
        ].contains(code)
    }
}
