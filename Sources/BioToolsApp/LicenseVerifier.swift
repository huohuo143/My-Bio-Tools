import CryptoKit
import Foundation

struct LicenseVerifier {
    private struct PublicJWK: Decodable { let kty: String; let crv: String; let x: String }
    private struct Header: Decodable { let alg: String; let typ: String }

    func installationHash(for installationID: String) -> String {
        let digest = SHA256.hash(data: Data("my-bio-tools-installation:\(installationID)".utf8))
        return Data(digest).base64URLEncodedString()
    }

    func verify(
        token: String,
        publicJWK: String,
        installationID: String,
        lastTrustedServerTime: Int64?,
        now: Date = Date()
    ) throws -> OfflineLicenseClaims {
        let components = token.split(separator: ".", omittingEmptySubsequences: false)
        guard components.count == 3,
              let headerData = Data(base64URLEncoded: String(components[0])),
              let payloadData = Data(base64URLEncoded: String(components[1])),
              let signature = Data(base64URLEncoded: String(components[2])) else {
            throw LicenseError.malformed
        }
        let header = try JSONDecoder().decode(Header.self, from: headerData)
        guard header.alg == "EdDSA", header.typ == "JWT" else { throw LicenseError.malformed }
        let jwk = try JSONDecoder().decode(PublicJWK.self, from: Data(publicJWK.utf8))
        guard jwk.kty == "OKP", jwk.crv == "Ed25519", let rawKey = Data(base64URLEncoded: jwk.x) else {
            throw LicenseError.invalidPublicKey
        }
        let key = try Curve25519.Signing.PublicKey(rawRepresentation: rawKey)
        let signingInput = Data("\(components[0]).\(components[1])".utf8)
        guard key.isValidSignature(signature, for: signingInput) else { throw LicenseError.invalidSignature }
        let claims = try JSONDecoder().decode(OfflineLicenseClaims.self, from: payloadData)
        let nowSeconds = Int64(now.timeIntervalSince1970)
        guard claims.typ == "offline-license", claims.version == 1 else { throw LicenseError.wrongType }
        guard let omicsKey = Data(base64Encoded: claims.omicsKeyB64), omicsKey.count == 32 else {
            throw LicenseError.missingOmicsKey
        }
        guard claims.device == installationHash(for: installationID) else { throw LicenseError.wrongDevice }
        guard claims.exp > nowSeconds else { throw LicenseError.expired }
        guard claims.iat <= nowSeconds + 300 else { throw LicenseError.issuedInFuture }
        if let lastTrustedServerTime, nowSeconds + 300 < lastTrustedServerTime {
            throw LicenseError.clockRollback
        }
        return claims
    }
}

enum LicenseError: LocalizedError {
    case malformed, invalidPublicKey, invalidSignature, wrongType, missingOmicsKey, wrongDevice, expired, issuedInFuture, clockRollback

    var errorDescription: String? {
        switch self {
        case .malformed: "离线授权格式无效。"
        case .invalidPublicKey: "授权公钥无效。"
        case .invalidSignature: "离线授权签名无效。"
        case .wrongType: "授权类型无效。"
        case .missingOmicsKey: "离线授权未包含已签名的多组学解锁信息，请联网重新登录。"
        case .wrongDevice: "授权不属于当前设备。"
        case .expired: "离线授权已过期，请联网重新验证。"
        case .issuedInFuture: "离线授权签发时间无效，请联网重新验证。"
        case .clockRollback: "系统时间早于最近信任时间，需要联网验证。"
        }
    }
}

private extension Data {
    init?(base64URLEncoded value: String) {
        var base64 = value.replacingOccurrences(of: "-", with: "+")
            .replacingOccurrences(of: "_", with: "/")
        base64 += String(repeating: "=", count: (4 - base64.count % 4) % 4)
        self.init(base64Encoded: base64)
    }

    func base64URLEncodedString() -> String {
        base64EncodedString().replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}
