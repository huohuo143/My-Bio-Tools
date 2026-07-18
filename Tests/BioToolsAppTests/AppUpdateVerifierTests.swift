import CryptoKit
import Foundation
import XCTest
@testable import BioToolsApp

final class AppUpdateVerifierTests: XCTestCase {
    func testSignedArm64ManifestVerifies() throws {
        let key = Curve25519.Signing.PrivateKey()
        let token = try signedManifest(key: key, expiresAt: 2_000_000_000)
        let manifest = try UpdateManifestVerifier().verify(
            token: token,
            publicJWK: publicJWK(key),
            now: Date(timeIntervalSince1970: 1_900_000_000)
        )
        XCTAssertEqual(manifest.appVersion, "1.9.1")
        XCTAssertEqual(manifest.build, 20)
        XCTAssertEqual(manifest.bundleIdentifier, "top.aizs.my-bio-tools")
        XCTAssertEqual(manifest.platform, "macos-arm64")
    }

    func testTamperedExpiredAndWrongBundleManifestsAreRejected() throws {
        let key = Curve25519.Signing.PrivateKey()
        let valid = try signedManifest(key: key, expiresAt: 2_000_000_000)
        var components = valid.split(separator: ".").map(String.init)
        let signatureIndex = components[2].index(
            components[2].startIndex,
            offsetBy: components[2].count / 2
        )
        components[2].replaceSubrange(
            signatureIndex...signatureIndex,
            with: components[2][signatureIndex] == "A" ? "B" : "A"
        )
        let tampered = components.joined(separator: ".")
        XCTAssertThrowsError(try UpdateManifestVerifier().verify(
            token: tampered, publicJWK: publicJWK(key),
            now: Date(timeIntervalSince1970: 1_900_000_000)
        ))
        XCTAssertThrowsError(try UpdateManifestVerifier().verify(
            token: try signedManifest(key: key, expiresAt: 1_800_000_000),
            publicJWK: publicJWK(key), now: Date(timeIntervalSince1970: 1_900_000_000)
        ))
        XCTAssertThrowsError(try UpdateManifestVerifier().verify(
            token: try signedManifest(key: key, expiresAt: 2_000_000_000, bundleID: "example.invalid"),
            publicJWK: publicJWK(key), now: Date(timeIntervalSince1970: 1_900_000_000)
        ))
    }

    private func signedManifest(
        key: Curve25519.Signing.PrivateKey,
        expiresAt: Int,
        bundleID: String = "top.aizs.my-bio-tools"
    ) throws -> String {
        let header = try segment(["alg": "EdDSA", "typ": "JWT"])
        let payload = try segment([
            "typ": "app-update",
            "iat": 1_800_000_000,
            "exp": expiresAt,
            "schema_version": 1,
            "platform": "macos-arm64",
            "bundle_identifier": bundleID,
            "app_version": "1.9.1",
            "build": 20,
            "minimum_system_version": "13.0",
            "size": 1_234_567,
            "sha256": String(repeating: "a", count: 64),
            "release_source": "github",
            "github_repository": "huohuo143/My-Bio-Tools",
            "github_asset_id": 190019,
            "release_notes": "一键安全更新与科研结果解读",
            "published_at": "2026-07-18T10:00:00Z",
            "mandatory": false,
        ] as [String: Any])
        let signingInput = "\(header).\(payload)"
        let signature = try key.signature(for: Data(signingInput.utf8))
        return "\(signingInput).\(base64URL(signature))"
    }

    private func publicJWK(_ key: Curve25519.Signing.PrivateKey) -> String {
        """
        {"kty":"OKP","crv":"Ed25519","x":"\(base64URL(key.publicKey.rawRepresentation))"}
        """
    }

    private func segment(_ object: Any) throws -> String {
        base64URL(try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]))
    }

    private func base64URL(_ data: Data) -> String {
        data.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}
