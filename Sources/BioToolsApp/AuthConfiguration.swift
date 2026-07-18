import Foundation

struct AuthConfiguration {
    let baseURL: URL
    let publicJWK: String

    static func load(bundle: Bundle = .main) throws -> AuthConfiguration {
        guard let rawURL = bundle.object(forInfoDictionaryKey: "MyBioToolsAuthBaseURL") as? String,
              let url = URL(string: rawURL), url.scheme == "https" else {
            throw AuthClientError.configuration("安装包缺少有效的授权服务地址。")
        }
        let publicJWK = (bundle.object(forInfoDictionaryKey: "MyBioToolsLicensePublicJWK") as? String ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !publicJWK.isEmpty else {
            throw AuthClientError.configuration("安装包尚未注入生产授权公钥，请联系开发者重新构建。")
        }
        return AuthConfiguration(baseURL: url, publicJWK: publicJWK)
    }
}
