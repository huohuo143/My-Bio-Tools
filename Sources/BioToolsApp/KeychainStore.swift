import Foundation
import Security

struct KeychainStore {
    enum Accessibility {
        case afterFirstUnlock
        case whenUnlocked

        var securityValue: CFString {
            switch self {
            case .afterFirstUnlock:
                kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
            case .whenUnlocked:
                kSecAttrAccessibleWhenUnlockedThisDeviceOnly
            }
        }
    }

    private let service: String

    init(service: String = "top.aizs.my-bio-tools.auth") {
        self.service = service
    }

    func data(for account: String) throws -> Data? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = result as? Data else {
            throw KeychainError.status(status)
        }
        return data
    }

    func save(
        _ data: Data,
        for account: String,
        accessibility: Accessibility = .afterFirstUnlock
    ) throws {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        let attributes: [String: Any] = [
            kSecValueData as String: data,
            kSecAttrAccessible as String: accessibility.securityValue,
        ]
        let updateStatus = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if updateStatus == errSecSuccess { return }
        guard updateStatus == errSecItemNotFound else { throw KeychainError.status(updateStatus) }
        let addQuery = query.merging(attributes) { _, new in new }
        let addStatus = SecItemAdd(addQuery as CFDictionary, nil)
        guard addStatus == errSecSuccess else { throw KeychainError.status(addStatus) }
    }

    func delete(_ account: String) throws {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw KeychainError.status(status)
        }
    }
}

enum KeychainError: LocalizedError {
    case status(OSStatus)

    var errorDescription: String? {
        switch self {
        case let .status(status):
            return SecCopyErrorMessageString(status, nil) as String? ?? "Keychain 错误 \(status)"
        }
    }
}
