import SwiftUI

struct AuthGateView: View {
    @EnvironmentObject private var auth: AuthStore
    @State private var mode: Mode = .login
    @State private var email = ""
    @State private var password = ""
    @State private var confirmation = ""
    @State private var realName = ""
    @State private var labRole = "硕士研究生"
    @State private var applicationNote = ""
    @State private var rememberCredentials = false
    @State private var didRestoreSavedCredentials = false

    private enum Mode: String, CaseIterable, Identifiable {
        case login = "登录"
        case register = "注册"
        var id: Self { self }
    }

    var body: some View {
        ZStack {
            LinearGradient(
                colors: [Color(nsColor: .windowBackgroundColor), Color.accentColor.opacity(0.08)],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            switch auth.phase {
            case .checking:
                ProgressView("正在验证账号与设备授权…")
                    .controlSize(.large)
            case let .configurationMissing(message):
                statusCard(icon: "wrench.and.screwdriver.fill", title: "授权配置未完成", message: message, action: nil)
            case let .unverified(account):
                statusCard(
                    icon: "envelope.badge.fill", title: "请验证邮箱",
                    message: "验证邮件已发送到 \(account)。验证后将进入管理员审核。",
                    action: ("重发验证邮件", { Task { await auth.resendVerification(email: account) } })
                )
            case let .pending(account):
                statusCard(
                    icon: "person.badge.clock.fill", title: "等待管理员审核",
                    message: "\(account) 已完成邮箱验证。审核通过后可直接登录，无需重新安装。",
                    action: nil
                )
            case let .rejected(message):
                statusCard(icon: "person.crop.circle.badge.xmark", title: "申请未通过", message: message, action: nil)
            case let .suspended(message):
                statusCard(icon: "lock.circle.fill", title: "账号已停用", message: message, action: nil)
            case .signedOut:
                form
            case .authorized:
                EmptyView()
            }
        }
        .frame(minWidth: 760, minHeight: 580)
    }

    private var form: some View {
        HStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 16) {
                Image(nsImage: NSApplication.shared.applicationIconImage)
                    .resizable().scaledToFit().frame(width: 92, height: 92)
                Text("My Bio Tools").font(.system(size: 34, weight: .semibold, design: .rounded))
                Text("课题组内部科研分析工具").font(.title3.weight(.medium))
                Label("账号须经管理员审核", systemImage: "person.badge.shield.checkmark")
                Label("分析文件与结果默认只在本机处理", systemImage: "lock.shield")
                Label("每个账号最多 2 台设备，支持 7 天离线使用", systemImage: "laptopcomputer.and.iphone")
                Text("版本 \(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "—")")
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
            }
            .frame(maxWidth: 420, maxHeight: .infinity, alignment: .topLeading)
            .padding(48)

            VStack(spacing: 18) {
                Picker("账号操作", selection: $mode) {
                    ForEach(Mode.allCases) { Text($0.rawValue).tag($0) }
                }
                .pickerStyle(.segmented)

                TextField("邮箱", text: $email)
                    .textFieldStyle(.roundedBorder)
                    .textContentType(.username)
                if mode == .register {
                    TextField("真实姓名", text: $realName).textFieldStyle(.roundedBorder)
                    Picker("课题组身份", selection: $labRole) {
                        ForEach(["导师", "博士后", "博士研究生", "硕士研究生", "本科生", "其他"], id: \.self) { Text($0) }
                    }
                    TextEditor(text: $applicationNote)
                        .frame(height: 72)
                        .overlay(RoundedRectangle(cornerRadius: 6).stroke(.quaternary))
                        .help("请简要说明身份和使用需求")
                }
                SecureField("密码（至少 8 个字符）", text: $password)
                    .textFieldStyle(.roundedBorder)
                    .textContentType(.password)
                if mode == .register {
                    SecureField("确认密码", text: $confirmation).textFieldStyle(.roundedBorder)
                }
                if mode == .login {
                    Toggle("记住账号和密码", isOn: $rememberCredentials)
                        .toggleStyle(.checkbox)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .help("密码仅保存在这台 Mac 的系统 Keychain 中")
                        .onChange(of: rememberCredentials) { enabled in
                            if !enabled { auth.forgetSavedLoginCredentials() }
                        }
                }
                if let notice = auth.notice {
                    Text(notice).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                Button(mode == .login ? "登录" : "提交注册申请") {
                    Task {
                        if mode == .login {
                            await auth.login(
                                email: email,
                                password: password,
                                rememberCredentials: rememberCredentials
                            )
                        } else if password == confirmation {
                            await auth.register(
                                email: email, realName: realName, labRole: labRole,
                                applicationNote: applicationNote, password: password
                            )
                        }
                        if mode == .register {
                            password = ""
                            confirmation = ""
                        }
                    }
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(auth.isBusy || email.isEmpty || password.isEmpty || (mode == .register && (realName.isEmpty || password != confirmation)))

                if mode == .login {
                    Button("忘记密码") { Task { await auth.forgotPassword(email: email) } }
                        .buttonStyle(.link)
                        .disabled(email.isEmpty || auth.isBusy)
                }
                if auth.isBusy { ProgressView().controlSize(.small) }
            }
            .padding(36)
            .frame(width: 400)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
            .padding(48)
        }
        .task { await restoreSavedCredentialsIfNeeded() }
    }

    private func restoreSavedCredentialsIfNeeded() async {
        guard !didRestoreSavedCredentials else { return }
        didRestoreSavedCredentials = true
        guard let credentials = await auth.savedLoginCredentials() else { return }
        email = credentials.email
        password = credentials.password
        rememberCredentials = true
    }

    private func statusCard(
        icon: String,
        title: String,
        message: String,
        action: (String, () -> Void)?
    ) -> some View {
        VStack(spacing: 18) {
            Image(systemName: icon).font(.system(size: 48)).foregroundStyle(.tint)
            Text(title).font(.title2.bold())
            Text(message).multilineTextAlignment(.center).foregroundStyle(.secondary).frame(maxWidth: 520)
            if let notice = auth.notice { Text(notice).font(.callout).foregroundStyle(.secondary) }
            if let action { Button(action.0, action: action.1).buttonStyle(.borderedProminent) }
            Button("返回登录") { auth.returnToLogin() }.buttonStyle(.link)
        }
        .padding(44)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 20))
    }
}

struct AccountView: View {
    @EnvironmentObject private var auth: AuthStore
    @EnvironmentObject private var updates: AppUpdateStore
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                Text("账号与设备").font(.title2.bold())
                Spacer()
                Button("完成") { dismiss() }.keyboardShortcut(.defaultAction)
            }
            if case let .authorized(user, expiresAt, offline) = auth.phase {
                GroupBox {
                    LabeledContent("姓名", value: user.realName)
                    LabeledContent("邮箱", value: user.email)
                    LabeledContent("身份", value: user.labRole)
                    LabeledContent("授权到期", value: expiresAt.formatted(date: .abbreviated, time: .shortened))
                    LabeledContent("当前模式", value: offline ? "离线授权" : "已联网验证")
                }
            }
            Text("已绑定设备").font(.headline)
            List(auth.devices) { device in
                HStack {
                    Image(systemName: device.platform == "macos" ? "laptopcomputer" : "pc")
                    VStack(alignment: .leading) {
                        Text(device.deviceName + (device.current ? "（当前设备）" : ""))
                        Text("\(device.appVersion) · 最近使用 \(Date(timeIntervalSince1970: TimeInterval(device.lastSeenAt)).formatted())")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    if device.revokedAt == nil {
                        Button("解绑") { Task { await auth.revokeDevice(device.id) } }
                    }
                }
            }
            .frame(minHeight: 160)
            GroupBox("软件更新") {
                VStack(alignment: .leading, spacing: 10) {
                    LabeledContent("当前版本", value: "\(updates.currentVersion)（build \(updates.currentBuild)）")
                    switch updates.phase {
                    case .checking:
                        ProgressView("正在检查更新…").controlSize(.small)
                    case let .available(manifest), let .downloading(manifest), let .preparing(manifest):
                        LabeledContent("可用版本", value: "\(manifest.appVersion)（build \(manifest.build)）")
                        LabeledContent("安装包大小", value: ByteCountFormatter.string(fromByteCount: manifest.size, countStyle: .file))
                        Text(manifest.releaseNotes).font(.callout).foregroundStyle(.secondary)
                        if case .downloading = updates.phase {
                            ProgressView("正在下载并校验…").controlSize(.small)
                        } else if case .preparing = updates.phase {
                            ProgressView("正在准备安全替换…").controlSize(.small)
                        } else {
                            Button("立即更新并重启") {
                                Task { await updates.install(manifest, auth: auth) }
                            }
                            .buttonStyle(.borderedProminent)
                        }
                    case .upToDate:
                        Label("当前已是最新版本", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                    case let .failed(message):
                        Text(message).font(.caption).foregroundStyle(.red)
                    case .idle:
                        EmptyView()
                    }
                    HStack {
                        Button("检查更新") { Task { await updates.check(auth: auth) } }
                        if let notice = updates.notice {
                            Text(notice).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            HStack {
                Button("发送密码重置邮件") {
                    if case let .authorized(user, _, _) = auth.phase { Task { await auth.forgotPassword(email: user.email) } }
                }
                Spacer()
                Button("退出登录", role: .destructive) { Task { await auth.logout(); dismiss() } }
            }
            if let notice = auth.notice { Text(notice).font(.caption).foregroundStyle(.secondary) }
        }
        .padding(24)
        .frame(width: 660, height: 690)
        .task {
            await auth.loadDevices()
            await updates.check(auth: auth, silent: true)
        }
    }
}
