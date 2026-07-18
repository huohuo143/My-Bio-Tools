import AppKit
import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var backend: BackendController
    @EnvironmentObject private var auth: AuthStore
    @EnvironmentObject private var updates: AppUpdateStore
    @State private var showingAccount = false

    var body: some View {
        Group {
            if auth.isAuthorized {
                switch backend.state {
                case .idle, .starting:
                    LaunchView()
                case .ready(let url):
                    VStack(spacing: 0) {
                        ServiceBar(
                            startupDuration: backend.startupDuration,
                            reload: backend.reloadPage,
                            restart: backend.restart,
                            openLog: backend.openLog,
                            openAccount: { showingAccount = true }
                        )
                        Divider()
                        StreamlitWebView(url: url, reloadID: backend.pageReloadID)
                    }
                case .failed(let message):
                    FailureView(
                        message: message,
                        details: backend.recentOutput,
                        retry: backend.restart,
                        openLog: backend.openLog
                    )
                }
            } else {
                AuthGateView()
            }
        }
        .frame(minWidth: 960, minHeight: 640)
        .onReceive(
            NotificationCenter.default.publisher(
                for: NSApplication.willTerminateNotification
            )
        ) { _ in
            backend.stop()
        }
        .sheet(isPresented: $showingAccount) {
            AccountView().environmentObject(auth).environmentObject(updates)
        }
        .task(id: auth.isAuthorized) {
            if auth.isAuthorized { await updates.check(auth: auth, silent: true) }
        }
    }
}

private struct ServiceBar: View {
    let startupDuration: TimeInterval?
    let reload: () -> Void
    let restart: () -> Void
    let openLog: () -> Void
    let openAccount: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            Circle()
                .fill(.green)
                .frame(width: 7, height: 7)
                .accessibilityHidden(true)

            Text("分析环境已就绪")
                .font(.system(size: 12, weight: .medium))

            if let startupDuration {
                Text("· \(startupDuration, format: .number.precision(.fractionLength(1))) 秒")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
            }

            Spacer()

            Button(action: reload) {
                Label("刷新", systemImage: "arrow.clockwise")
            }
            .buttonStyle(.borderless)
            .help("刷新当前工具（⌘R）")

            Button(action: openAccount) {
                Label("账号", systemImage: "person.crop.circle")
            }
            .buttonStyle(.borderless)

            Menu {
                Button("重新启动内置服务", action: restart)
                Button("打开运行日志", action: openLog)
            } label: {
                Image(systemName: "ellipsis.circle")
            }
            .menuStyle(.borderlessButton)
            .fixedSize()
            .help("更多运行选项")
        }
        .padding(.horizontal, 14)
        .frame(height: 36)
        .background(.bar)
    }
}

private struct LaunchView: View {
    @State private var isAnimating = false

    var body: some View {
        ZStack {
            LinearGradient(
                colors: [
                    Color(nsColor: .windowBackgroundColor),
                    Color.accentColor.opacity(0.06)
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )

            VStack(spacing: 20) {
                ZStack {
                    RoundedRectangle(cornerRadius: 24, style: .continuous)
                        .fill(.regularMaterial)
                        .frame(width: 128, height: 128)
                        .shadow(color: .black.opacity(0.08), radius: 24, y: 10)

                    Image(nsImage: NSApplication.shared.applicationIconImage)
                        .resizable()
                        .scaledToFit()
                        .frame(width: 96, height: 96)
                }
                .scaleEffect(isAnimating ? 1 : 0.96)

                VStack(spacing: 7) {
                    Text("My Bio Tools")
                        .font(.system(size: 30, weight: .semibold, design: .rounded))
                    Text("正在准备本地科研分析环境")
                        .font(.headline)
                    Text("加载 Python、Streamlit 与内置水稻注释数据")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }

                ProgressView()
                    .controlSize(.large)
                    .frame(width: 220)

                Label("数据默认仅在本机处理", systemImage: "lock.shield")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .padding(56)
        }
        .onAppear {
            withAnimation(.easeOut(duration: 0.7)) {
                isAnimating = true
            }
        }
    }
}

private struct FailureView: View {
    let message: String
    let details: String
    let retry: () -> Void
    let openLog: () -> Void

    var body: some View {
        ZStack {
            Color(nsColor: .windowBackgroundColor)

            VStack(spacing: 18) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.system(size: 42))
                    .foregroundStyle(.orange)

                Text("分析环境启动失败")
                    .font(.title2.bold())

                Text(message)
                    .multilineTextAlignment(.center)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: 640)

                if !details.isEmpty {
                    DisclosureGroup("查看技术详情") {
                        ScrollView {
                            Text(details)
                                .font(.system(.caption, design: .monospaced))
                                .textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(12)
                        }
                        .frame(maxWidth: 720, maxHeight: 220)
                        .background(.quaternary.opacity(0.5))
                        .clipShape(RoundedRectangle(cornerRadius: 9))
                    }
                    .frame(maxWidth: 720)
                }

                HStack(spacing: 12) {
                    Button("打开运行日志", action: openLog)
                    Button("重新启动", action: retry)
                        .buttonStyle(.borderedProminent)
                        .keyboardShortcut(.defaultAction)
                }
            }
            .padding(48)
        }
    }
}
