import AppKit
import Combine
import Darwin
import Foundation

enum BackendState: Equatable {
    case idle
    case starting
    case ready(URL)
    case failed(String)
}

@MainActor
final class BackendController: ObservableObject {
    @Published private(set) var state: BackendState = .idle
    @Published private(set) var recentOutput = ""
    @Published private(set) var pageReloadID = 0
    @Published private(set) var startupDuration: TimeInterval?

    private var process: Process?
    private var outputPipe: Pipe?
    private var healthTask: Task<Void, Never>?
    private var launchID = UUID()
    private var startupBeganAt: Date?
    private var authorization: BackendAuthorization?
    private var omicsUnlockDirectory: URL?

    private static let bundleID = "top.aizs.my-bio-tools"
    private static let backendRelativePath = "backend/BioToolsBackend"
    private static let appSourceRelativePath = "app_source"

    func configureAuthorization(_ authorization: BackendAuthorization?) {
        guard self.authorization != authorization else { return }
        self.authorization = authorization
        guard authorization != nil else {
            stop()
            return
        }
        startIfNeeded()
    }

    func startIfNeeded() {
        switch state {
        case .idle, .failed:
            start()
        case .starting, .ready:
            break
        }
    }

    func start() {
        guard let authorization else {
            stop()
            return
        }
        stop()
        recentOutput = ""
        startupDuration = nil
        startupBeganAt = Date()

        let currentLaunchID = UUID()
        launchID = currentLaunchID
        state = .starting

        do {
            let resources = try resolveResources()
            let unlockDirectory = try createOmicsUnlockDirectory()
            omicsUnlockDirectory = unlockDirectory
            let port = try availableLoopbackPort()
            let baseURL = URL(string: "http://127.0.0.1:\(port)")!

            let task = Process()
            task.executableURL = resources.backend
            task.arguments = [
                "--port", String(port),
                "--app-dir", resources.appSource.path,
                "--parent-pid", String(ProcessInfo.processInfo.processIdentifier)
            ]
            task.currentDirectoryURL = resources.appSource

            var environment = ProcessInfo.processInfo.environment
            environment["PYTHONNOUSERSITE"] = "1"
            environment["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"
            environment["STREAMLIT_SERVER_FILE_WATCHER_TYPE"] = "none"
            environment["MY_BIO_TOOLS_OFFLINE_LICENSE"] = authorization.offlineLicense
            environment["MY_BIO_TOOLS_INSTALLATION_HASH"] = authorization.installationHash
            environment["MY_BIO_TOOLS_LICENSE_PUBLIC_JWK"] = authorization.publicJWK
            environment["MY_BIO_TOOLS_OMICS_KEY_B64"] = authorization.omicsKeyB64
            environment["MY_BIO_TOOLS_OMICS_UNLOCK_DIR"] = unlockDirectory.path
            task.environment = environment

            let pipe = Pipe()
            outputPipe = pipe
            task.standardOutput = pipe
            task.standardError = pipe

            pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
                let data = handle.availableData
                guard !data.isEmpty else {
                    handle.readabilityHandler = nil
                    return
                }
                guard let text = String(data: data, encoding: .utf8) else { return }
                Task { @MainActor [weak self] in
                    self?.record(text)
                }
            }

            task.terminationHandler = { [weak self] finishedTask in
                Task { @MainActor [weak self] in
                    guard let self, self.launchID == currentLaunchID else { return }
                    self.healthTask?.cancel()
                    self.healthTask = nil
                    self.process = nil
                    self.cleanupOmicsUnlockDirectory()
                    self.state = .failed(
                        "内置服务意外退出（代码 \(finishedTask.terminationStatus)）。请打开运行日志查看详情。"
                    )
                }
            }

            try task.run()
            process = task

            healthTask = Task { [weak self] in
                await self?.waitForHealth(
                    baseURL: baseURL,
                    currentLaunchID: currentLaunchID
                )
            }
        } catch {
            cleanupOmicsUnlockDirectory()
            state = .failed(error.localizedDescription)
            record("启动失败：\(error.localizedDescription)\n")
        }
    }

    func restart() {
        guard authorization != nil else {
            stop()
            return
        }
        stop()
        Task { [weak self] in
            try? await Task.sleep(nanoseconds: 300_000_000)
            guard !Task.isCancelled else { return }
            self?.start()
        }
    }

    func reloadPage() {
        guard case .ready = state else { return }
        pageReloadID += 1
    }

    func stop() {
        launchID = UUID()
        healthTask?.cancel()
        healthTask = nil

        outputPipe?.fileHandleForReading.readabilityHandler = nil
        outputPipe = nil

        if let process, process.isRunning {
            process.terminate()
        }
        process = nil
        cleanupOmicsUnlockDirectory()
        state = .idle
    }

    func openLog() {
        let url = logFileURL
        ensureLogFileExists(at: url)
        NSWorkspace.shared.open(url)
    }

    private func waitForHealth(baseURL: URL, currentLaunchID: UUID) async {
        let healthURL = baseURL.appendingPathComponent("_stcore/health")

        for _ in 0..<240 {
            guard !Task.isCancelled, launchID == currentLaunchID else { return }

            var request = URLRequest(url: healthURL)
            request.timeoutInterval = 1
            request.cachePolicy = .reloadIgnoringLocalCacheData

            do {
                let (_, response) = try await URLSession.shared.data(for: request)
                if let http = response as? HTTPURLResponse, http.statusCode == 200 {
                    guard launchID == currentLaunchID else { return }
                    if let startupBeganAt {
                        startupDuration = Date().timeIntervalSince(startupBeganAt)
                    }
                    state = .ready(baseURL)
                    return
                }
            } catch {
                // The local service normally needs a few seconds to become ready.
            }

            try? await Task.sleep(nanoseconds: 250_000_000)
        }

        guard launchID == currentLaunchID else { return }
        if let process, process.isRunning {
            process.terminate()
        }
        process = nil
        cleanupOmicsUnlockDirectory()
        state = .failed("内置服务在 60 秒内未能启动。请重试或打开运行日志。")
    }

    private func createOmicsUnlockDirectory() throws -> URL {
        let caches = FileManager.default.urls(
            for: .cachesDirectory,
            in: .userDomainMask
        )[0]
        let root = caches
            .appendingPathComponent(Self.bundleID, isDirectory: true)
            .appendingPathComponent("authenticated-omics", isDirectory: true)
        let directory = root.appendingPathComponent(UUID().uuidString, isDirectory: true)
        do {
            try FileManager.default.createDirectory(
                at: directory,
                withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700]
            )
            try FileManager.default.setAttributes(
                [.posixPermissions: 0o700],
                ofItemAtPath: directory.path
            )
            return directory
        } catch {
            throw BackendError.omicsUnlockDirectory(error.localizedDescription)
        }
    }

    private func cleanupOmicsUnlockDirectory() {
        guard let directory = omicsUnlockDirectory else { return }
        omicsUnlockDirectory = nil
        try? FileManager.default.removeItem(at: directory)
    }

    private func resolveResources() throws -> (backend: URL, appSource: URL) {
        guard let resourceURL = Bundle.main.resourceURL else {
            throw BackendError.missingResources
        }

        let backend = resourceURL.appendingPathComponent(Self.backendRelativePath)
        let appSource = resourceURL.appendingPathComponent(
            Self.appSourceRelativePath,
            isDirectory: true
        )
        let mainScript = appSource.appendingPathComponent("main.py")

        guard FileManager.default.isExecutableFile(atPath: backend.path) else {
            throw BackendError.missingBackend(backend.path)
        }
        guard FileManager.default.fileExists(atPath: mainScript.path) else {
            throw BackendError.missingAppSource(mainScript.path)
        }

        return (backend, appSource)
    }

    private func availableLoopbackPort() throws -> UInt16 {
        let descriptor = Darwin.socket(AF_INET, SOCK_STREAM, 0)
        guard descriptor >= 0 else {
            throw BackendError.noAvailablePort
        }
        defer { Darwin.close(descriptor) }

        var address = sockaddr_in()
        address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        address.sin_family = sa_family_t(AF_INET)
        address.sin_port = 0
        address.sin_addr = in_addr(s_addr: inet_addr("127.0.0.1"))

        let bindResult = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.bind(
                    descriptor,
                    $0,
                    socklen_t(MemoryLayout<sockaddr_in>.size)
                )
            }
        }
        guard bindResult == 0 else {
            throw BackendError.noAvailablePort
        }

        var length = socklen_t(MemoryLayout<sockaddr_in>.size)
        let nameResult = withUnsafeMutablePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.getsockname(descriptor, $0, &length)
            }
        }
        guard nameResult == 0 else {
            throw BackendError.noAvailablePort
        }

        return UInt16(bigEndian: address.sin_port)
    }

    private func record(_ text: String) {
        recentOutput.append(text)
        if recentOutput.count > 20_000 {
            recentOutput = String(recentOutput.suffix(20_000))
        }

        let url = logFileURL
        ensureLogFileExists(at: url)

        guard let data = text.data(using: .utf8),
              let handle = try? FileHandle(forWritingTo: url) else {
            return
        }
        defer { try? handle.close() }
        do {
            try handle.seekToEnd()
            try handle.write(contentsOf: data)
        } catch {
            // Logging must never interrupt the app.
        }
    }

    private var logFileURL: URL {
        let library = FileManager.default.urls(
            for: .libraryDirectory,
            in: .userDomainMask
        )[0]
        return library
            .appendingPathComponent("Logs", isDirectory: true)
            .appendingPathComponent("My Bio Tools", isDirectory: true)
            .appendingPathComponent("backend.log")
    }

    private func ensureLogFileExists(at url: URL) {
        let directory = url.deletingLastPathComponent()
        try? FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true
        )
        if !FileManager.default.fileExists(atPath: url.path) {
            FileManager.default.createFile(atPath: url.path, contents: nil)
        }
    }
}

private enum BackendError: LocalizedError {
    case missingResources
    case missingBackend(String)
    case missingAppSource(String)
    case omicsUnlockDirectory(String)
    case noAvailablePort

    var errorDescription: String? {
        switch self {
        case .missingResources:
            return "App 资源目录不存在，请重新安装应用。"
        case .missingBackend(let path):
            return "内置运行环境缺失或不可执行：\(path)"
        case .missingAppSource(let path):
            return "工具入口文件缺失：\(path)"
        case .omicsUnlockDirectory(let reason):
            return "无法创建多组学安全运行目录：\(reason)"
        case .noAvailablePort:
            return "无法为内置服务分配本地端口。"
        }
    }
}
