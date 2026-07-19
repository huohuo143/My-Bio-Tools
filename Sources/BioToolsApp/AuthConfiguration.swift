import Foundation

struct AuthConfiguration {
    private struct PublicJWK: Decodable {
        let kty: String
        let crv: String
        let x: String
    }

    let baseURL: URL
    let publicJWK: String

    static func load(bundle: Bundle = .main) throws -> AuthConfiguration {
        guard let rawURL = bundle.object(forInfoDictionaryKey: "MyBioToolsAuthBaseURL") as? String,
              let url = URL(string: rawURL), url.scheme == "https" else {
            throw AuthClientError.configuration("安装包缺少有效的授权服务地址。")
        }
        let publicJWK = try validatePublicJWK(
            bundle.object(forInfoDictionaryKey: "MyBioToolsLicensePublicJWK") as? String ?? ""
        )
        return AuthConfiguration(baseURL: url, publicJWK: publicJWK)
    }

    static func validatePublicJWK(_ rawValue: String) throws -> String {
        let value = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else {
            throw AuthClientError.configuration("安装包尚未注入生产授权公钥，请联系开发者重新构建。")
        }
        guard let data = value.data(using: .utf8),
              let jwk = try? JSONDecoder().decode(PublicJWK.self, from: data),
              jwk.kty == "OKP", jwk.crv == "Ed25519", !jwk.x.isEmpty else {
            throw AuthClientError.configuration("安装包内的授权公钥无效，请联系开发者重新构建。")
        }
        return value
    }
}
