# My Bio Tools for Windows

Windows 版以已验证的 macOS `1.9.7 Build 26` 发布源码为唯一功能基线，共用 `app_source` 中的 Python/Streamlit 工具、内置水稻注释数据和加密多组学数据包。Windows 原生外壳使用 .NET 10 WPF 与 Microsoft Edge WebView2，最终用户无需安装 Python、Conda、OpenSSL 或 .NET。

## 支持范围

- Windows 10/11 x64。
- 5 个本地工具可离线运行。
- RiceData、Rice eFP 与“水稻基因一站式分析”的在线数据/预测部分需要联网；内置 IRGSP 序列、NLStradamus 和已授权的多组学检索可在本机运行。
- 保留 ChatGPT 账号（Codex）、本机 Ollama，以及 DeepSeek、豆包、智谱 GLM、通义千问、ChatAnywhere、OpenAI/自定义兼容 API 六类会话级入口。
- 云端 API Key 仅保存在当前 APP 会话内存，不写入偏好文件、日志、安装包或报告；发送内容仍是去标识化结构化证据。
- 后台项目队列在 APP 保持打开时继续运行；切换工具或最小化不会中断，退出 APP 或重启内置服务会结束未完成任务。
- Windows ARM64、Windows 7/8 和 MSIX 暂不支持。

## Windows 构建环境

在 Windows 10/11 x64 电脑准备以下构建工具：

1. Python 3.12 x64。
2. .NET 10 SDK。
3. Inno Setup 6。
4. MinGW-w64 `g++.exe`，用于从 GPLv3 v1.8 原始源码构建 NLStradamus Windows x64 辅助程序。
5. 首次构建需要联网下载 Python 包和微软 WebView2 x64 离线安装程序；也可通过参数使用已下载的官方安装程序。

构建过程使用项目内 `.build-venv-win` 隔离环境，不修改系统 Python 包。执行：

```powershell
$env:MY_BIO_TOOLS_LICENSE_PUBLIC_JWK = '{"kty":"OKP","crv":"Ed25519","x":"生产公钥"}'
powershell -NoProfile -ExecutionPolicy Bypass -File .\script\build_windows.ps1
```

构建脚本会把授权公钥写入分发目录的 `auth-config.json`；缺少公钥时会拒绝生成无法登录的安装包。签名私钥永不进入 Windows 构建机或安装包。

同时执行 RiceData、RGAP 与 UTR/启动子在线验证：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\script\build_windows.ps1 -RunLiveTests
```

使用已有 WebView2 x64 Evergreen Standalone Installer：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\script\build_windows.ps1 `
  -WebViewInstallerPath "C:\Downloads\MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
```

使用已审核账号登录后，构建并执行授权、多组学解锁、进程与健康检查：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\script\build_windows.ps1 -RunRuntimeSmokeTest
```

## 交付文件

构建结果位于 `dist\windows`：

- `My-Bio-Tools-1.9.7-build26-win-x64-setup.exe`
- `My-Bio-Tools-1.9.7-build26-win-x64-portable.zip`
- `SHA256SUMS.txt`
- `version-manifest.json`
- `windows-build-requirements.lock.txt`
- `windows-build-validation.md`

安装包采用按用户安装，默认目录为 `%LOCALAPPDATA%\Programs\My Bio Tools`，包含开始菜单、可选桌面快捷方式和卸载入口，不要求管理员权限。便携版解压到本地磁盘后直接运行 `My Bio Tools.exe`。

## WebView2 与离线使用

Windows 11 和多数 Windows 10 已安装 WebView2 Runtime。完整分发包仍附带微软官方 x64 Evergreen Standalone Installer：

- 安装包仅在检测不到有效 Runtime 时静默执行离线安装。
- 便携版检测不到 Runtime 时显示安装按钮，并在用户确认后执行离线安装。
- 构建脚本验证安装程序的 Microsoft Authenticode 签名并记录 SHA256。

账号联网验证成功后可在本机离线使用不超过 7 天；授权过期或检测到时间回拨时必须联网。RiceData 与统一水稻分析工具的外部数据部分仍需联网。

多组学数据库始终以加密文件随包分发。解锁密钥来自经过 Ed25519 签名的离线授权，Windows 外壳在当前用户缓存目录创建一次性只读 SQLite，退出、注销、授权失效或后端异常退出时立即清理。密钥和明文数据库不写入安装目录。

## 运行行为

- 后端只监听随机的 `127.0.0.1` 端口。
- 未登录、未审核、已停用或授权失效时不启动 Python 后端；退出或撤销授权后立即停止。
- 安装 ID 为随机值，不读取硬盘序列号、MAC 地址等硬件标识。
- 外部网站在系统默认浏览器打开。
- 结果保存到 Windows“下载”目录，同名文件自动增加序号，完成后在资源管理器定位。
- 运行日志：`%LOCALAPPDATA%\WuLab\My Bio Tools\Logs\backend.log`。
- 日志达到 5 MB 后轮转，保留 3 份历史日志。
- `Ctrl+R` 刷新，`Ctrl+Shift+R` 重启服务，`Ctrl+Shift+L` 打开日志。
- 关闭主窗口时 Windows Job Object 会终止整棵内置后端进程树。
- 关闭后端时同时删除本次会话解锁的临时多组学数据库。

## 验收

构建脚本默认执行源码检查、核心单元测试、预测适配器契约测试、全部页面冒烟测试、本地工作流测试、六类云端模型的无密钥离线契约测试、PyInstaller 解释模块烟雾测试、.NET AES-256-CTR 标准向量测试和 NLStradamus Windows 二进制自检。最终分发前还应在干净 Windows 10 与 Windows 11 x64 环境逐项验证 7 个工具、登录与离线授权、多组学解锁、Word/Excel/ZIP 下载、WebView2 缺失处理、安装升级与卸载。

PyInstaller 和 Inno Setup 不是从 Apple Silicon macOS 交叉生成 Windows 安装包的工具。本工程可在 macOS 完成源码与分发契约检查，但 `setup.exe` 和便携 ZIP 必须在原生 Windows 10/11 x64 环境运行上述 PowerShell 构建命令后交付。

当前分发包默认未做 Authenticode 代码签名，首次运行可能出现 Windows SmartScreen 提示。提供代码签名证书后再加入签名步骤。
