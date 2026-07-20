import CryptoKit
import Foundation
import XCTest
@testable import BioToolsApp

final class LicenseVerifierTests: XCTestCase {
    func testUserProfileDecodesAuthorizationPeriodAndLegacyPayload() throws {
        let datedJSON = #"{"id":"u1","email":"member@example.test","realName":"测试成员","labRole":"博士研究生","status":"active","reviewReason":null,"authorizationExpiresAt":1800000000,"authorizationPermanent":false}"#
        let dated = try JSONDecoder().decode(UserProfile.self, from: Data(datedJSON.utf8))
        XCTAssertEqual(dated.authorizationExpiresAt, 1_800_000_000)
        XCTAssertEqual(dated.authorizationPermanent, false)

        let legacyJSON = #"{"id":"u1","email":"member@example.test","realName":"测试成员","labRole":"博士研究生","status":"active","reviewReason":null}"#
        let legacy = try JSONDecoder().decode(UserProfile.self, from: Data(legacyJSON.utf8))
        XCTAssertNil(legacy.authorizationExpiresAt)
        XCTAssertNil(legacy.authorizationPermanent)
    }

    private let installationID = "test-installation-1"
    private let publicJWK = #"{"kty":"OKP","crv":"Ed25519","x":"11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo"}"#
    private let token = "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJ0eXAiOiJvZmZsaW5lLWxpY2Vuc2UiLCJzdWIiOiJ1c2VyLXRlc3QiLCJkZXZpY2UiOiJTSHJyVlVIZUtuNEZLdURoR2JZdVpMWlFaZV9JYTR4T1p3THcwcDFkRllBIiwiaWF0IjoxNzAwMDAwMDAwLCJleHAiOjQxMDI0NDQ4MDAsInZlcnNpb24iOjEsIm9taWNzX2tleV9iNjQiOiJBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBPSJ9.8GfIMRJgHTll-dziWVIVisx5UZfQoIyRKt7Maqkjm6-Iff52od2tzxpP9IVQYgpwCtC7fygBbrxqctITN6DHDg"

    func testWorkerSignedLicenseVerifiesInMacClient() throws {
        let claims = try LicenseVerifier().verify(
            token: token,
            publicJWK: publicJWK,
            installationID: installationID,
            lastTrustedServerTime: 1_700_000_000,
            now: Date(timeIntervalSince1970: 1_800_000_000)
        )
        XCTAssertEqual(claims.sub, "user-test")
        XCTAssertEqual(claims.device, "SHrrVUHeKn4FKuDhGbYuZLZQZe_Ia4xOZwLw0p1dFYA")
        XCTAssertEqual(claims.omicsKeyB64, "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    }

    func testWrongDeviceTamperingAndClockRollbackAreRejected() {
        let verifier = LicenseVerifier()
        XCTAssertThrowsError(try verifier.verify(
            token: token, publicJWK: publicJWK, installationID: "different-device",
            lastTrustedServerTime: nil, now: Date(timeIntervalSince1970: 1_800_000_000)
        ))
        var tampered = token
        tampered.replaceSubrange(tampered.index(before: tampered.endIndex)..., with: "A")
        XCTAssertThrowsError(try verifier.verify(
            token: tampered, publicJWK: publicJWK, installationID: installationID,
            lastTrustedServerTime: nil, now: Date(timeIntervalSince1970: 1_800_000_000)
        ))
        XCTAssertThrowsError(try verifier.verify(
            token: token, publicJWK: publicJWK, installationID: installationID,
            lastTrustedServerTime: 1_800_000_301, now: Date(timeIntervalSince1970: 1_800_000_000)
        ))
    }

    func testLegacyLicenseWithoutOmicsKeyHasSpecificError() throws {
        let verifier = LicenseVerifier()
        let privateKey = Curve25519.Signing.PrivateKey()
        let publicKey = privateKey.publicKey.rawRepresentation
        let header = try encodedSegment(["alg": "EdDSA", "typ": "JWT"])
        let payload = try encodedSegment([
            "typ": "offline-license",
            "sub": "legacy-user",
            "device": verifier.installationHash(for: installationID),
            "iat": 1_700_000_000,
            "exp": 4_102_444_800,
            "version": 1,
        ] as [String: Any])
        let signingInput = "\(header).\(payload)"
        let signature = try privateKey.signature(for: Data(signingInput.utf8))
        let token = "\(signingInput).\(base64URL(signature))"
        let publicJWK = """
        {"kty":"OKP","crv":"Ed25519","x":"\(base64URL(publicKey))"}
        """

        XCTAssertThrowsError(try verifier.verify(
            token: token,
            publicJWK: publicJWK,
            installationID: installationID,
            lastTrustedServerTime: nil,
            now: Date(timeIntervalSince1970: 1_800_000_000)
        )) { error in
            guard case LicenseError.missingOmicsKey = error else {
                return XCTFail("Expected missingOmicsKey, got \(error)")
            }
        }
    }

    private func encodedSegment(_ object: Any) throws -> String {
        base64URL(try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]))
    }

    private func base64URL(_ data: Data) -> String {
        data.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}
