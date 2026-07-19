import XCTest
@testable import BioToolsApp

final class AuthConfigurationTests: XCTestCase {
    func testValidEd25519PublicJWKIsAccepted() throws {
        let jwk = #"{"key_ops":["verify"],"kty":"OKP","crv":"Ed25519","x":"public-key-material"}"#
        XCTAssertEqual(try AuthConfiguration.validatePublicJWK("  \(jwk)\n"), jwk)
    }

    func testMissingOrMalformedPublicJWKIsRejected() {
        XCTAssertThrowsError(try AuthConfiguration.validatePublicJWK(""))
        XCTAssertThrowsError(try AuthConfiguration.validatePublicJWK(#"{"kty":"RSA"}"#))
        XCTAssertThrowsError(try AuthConfiguration.validatePublicJWK("not-json"))
    }
}
