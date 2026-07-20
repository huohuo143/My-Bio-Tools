import AppKit
import SwiftUI

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }
}

@main
struct BioToolsApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var backend = BackendController()
    @StateObject private var auth = AuthStore()
    @StateObject private var updates = AppUpdateStore()

    var body: some Scene {
        WindowGroup("My Bio Tools") {
            ContentView()
                .environmentObject(backend)
                .environmentObject(auth)
                .environmentObject(updates)
                .onAppear {
                    Task { await auth.bootstrap() }
                }
                .onReceive(auth.$authorization) { backend.configureAuthorization($0) }
        }
        .defaultSize(width: 1280, height: 820)
        .commands {
            CommandGroup(replacing: .newItem) {}

            CommandMenu("工具") {
                Button("刷新当前工具") {
                    backend.reloadPage()
                }
                .keyboardShortcut("r", modifiers: [.command])

                Button("重新启动内置服务") {
                    backend.restart()
                }
                .keyboardShortcut("r", modifiers: [.command, .shift])

                Button("打开运行日志") {
                    backend.openLog()
                }
                .keyboardShortcut("l", modifiers: [.command, .shift])

                Divider()

                Button("检查软件更新") {
                    Task { await updates.check(auth: auth) }
                }
                .disabled(!auth.isAuthorized)
            }
        }
    }
}
