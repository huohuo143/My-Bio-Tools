import Foundation
import XCTest
@testable import BioToolsApp

final class KeychainStoreTests: XCTestCase {
    func testSavedLoginCredentialsRoundTripInKeychain() throws {
        let store = KeychainStore(
            service: "top.aizs.my-bio-tools.tests.\(UUID().uuidString)"
        )
        let account = "saved-login-credentials"
        defer { try? store.delete(account) }

        let expected = SavedLoginCredentials(
            email: "researcher@example.com",
            password: "test-password-only"
        )
        try store.save(
            JSONEncoder().encode(expected),
            for: account,
            accessibility: .whenUnlocked
        )

        let storedData = try XCTUnwrap(store.data(for: account))
        XCTAssertEqual(try JSONDecoder().decode(SavedLoginCredentials.self, from: storedData), expected)

        try store.delete(account)
        XCTAssertNil(try store.data(for: account))
    }
}
