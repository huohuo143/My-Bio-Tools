[CmdletBinding()]
param(
    [string]$PythonExecutable = "",
    [string]$WebViewInstallerPath = "",
    [switch]$SkipTests,
    [switch]$SkipWebViewDownload,
    [switch]$SkipInstaller,
    [switch]$RunLiveTests,
    [switch]$RunRuntimeSmokeTest,
    [string]$AuthBaseUrl = "https://mybiotools.aizs.top",
    [string]$LicensePublicJwk = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$Root = Split-Path -Parent $PSScriptRoot
$Version = "1.9.7"
$Build = 26
$RuntimeInstallerName = "MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
$RuntimeInstallerUrl = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
$Venv = Join-Path $Root ".build-venv-win"
$BuildRoot = Join-Path $Root "build\windows"
$BackendDist = Join-Path $BuildRoot "backend"
$PyInstallerWork = Join-Path $BuildRoot "pyinstaller"
$AppStage = Join-Path $BuildRoot "app"
$DistRoot = Join-Path $Root "dist\windows"
$PortableZip = Join-Path $DistRoot "My-Bio-Tools-$Version-build$Build-win-x64-portable.zip"
$SetupExe = Join-Path $DistRoot "My-Bio-Tools-$Version-build$Build-win-x64-setup.exe"
$CachedRuntimeInstaller = Join-Path $Root "windows\prerequisites\$RuntimeInstallerName"
$Project = Join-Path $Root "windows\MyBioTools.Windows\MyBioTools.Windows.csproj"
$SmokeProject = Join-Path $Root "windows\MyBioTools.Windows.Smoke\MyBioTools.Windows.Smoke.csproj"
$BackendSpec = Join-Path $Root "packaging\BioToolsBackend.windows.spec"
$NlstradamusDir = Join-Path $Root "app_source\vendor\nlstradamus"
$NlstradamusSource = Join-Path $NlstradamusDir "NLStradamus.cpp"
$NlstradamusBinDir = Join-Path $NlstradamusDir "bin"
$NlstradamusExe = Join-Path $NlstradamusBinDir "NLStradamus.exe"

if (-not $LicensePublicJwk) {
    $LicensePublicJwk = $env:MY_BIO_TOOLS_LICENSE_PUBLIC_JWK
}
if (-not $LicensePublicJwk) {
    throw "缺少 LicensePublicJwk 或 MY_BIO_TOOLS_LICENSE_PUBLIC_JWK；拒绝生成无法登录的 Windows 安装包。"
}
try {
    $parsedPublicJwk = $LicensePublicJwk | ConvertFrom-Json
} catch {
    throw "LicensePublicJwk 不是有效 JSON。"
}
$publicJwkProperties = @($parsedPublicJwk.PSObject.Properties.Name)
if (
    $publicJwkProperties -notcontains "kty" -or
    $publicJwkProperties -notcontains "crv" -or
    $publicJwkProperties -notcontains "x" -or
    $parsedPublicJwk.kty -ne "OKP" -or
    $parsedPublicJwk.crv -ne "Ed25519" -or
    -not $parsedPublicJwk.x
) {
    throw "LicensePublicJwk 必须是包含 kty=OKP、crv=Ed25519 和 x 的公钥。"
}
if ($publicJwkProperties -contains "d") {
    throw "LicensePublicJwk 包含私钥字段 d；拒绝把私钥写入 Windows 分发包。"
}

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList
    )

    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "命令执行失败（代码 $LASTEXITCODE）：$FilePath $($ArgumentList -join ' ')"
    }
}

function Resolve-BuildPython {
    if ($PythonExecutable) {
        if (-not (Test-Path $PythonExecutable)) {
            throw "找不到指定的 Python：$PythonExecutable"
        }
        return @{ Command = $PythonExecutable; Prefix = @() }
    }

    $launcher = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($launcher) {
        return @{ Command = $launcher.Source; Prefix = @("-3.12") }
    }

    $python = Get-Command "python.exe" -ErrorAction SilentlyContinue
    if ($python) {
        return @{ Command = $python.Source; Prefix = @() }
    }

    throw "未找到 Python 3.12 x64。请先安装 Python 3.12，再重新运行构建脚本。"
}

function Test-WebViewInstallerSignature {
    param([Parameter(Mandatory = $true)][string]$Path)

    $signature = Get-AuthenticodeSignature -FilePath $Path
    if ($signature.Status -ne "Valid") {
        throw "WebView2 离线安装程序签名无效：$($signature.Status)"
    }
    if (-not $signature.SignerCertificate -or $signature.SignerCertificate.Subject -notmatch "Microsoft Corporation") {
        throw "WebView2 离线安装程序不是 Microsoft 签名文件。"
    }
}

Write-Host "[1/9] 检查 Windows x64 构建环境"
if (-not [Environment]::Is64BitOperatingSystem) {
    throw "Windows 安装包必须在 64 位 Windows 10/11 上构建。"
}

$pythonInfo = Resolve-BuildPython
$pythonCommand = [string]$pythonInfo.Command
$pythonPrefix = [string[]]$pythonInfo.Prefix
Invoke-Native $pythonCommand ($pythonPrefix + @("-c", "import platform,sys; assert sys.version_info[:2] == (3,12), sys.version; assert platform.machine().lower() in ('amd64','x86_64'), platform.machine(); print(sys.version)"))

$dotnet = Get-Command "dotnet.exe" -ErrorAction SilentlyContinue
if (-not $dotnet) {
    throw "未找到 .NET 10 SDK。请安装 .NET 10 SDK 后重新运行。"
}
$dotnetVersionText = (& $dotnet.Source --version).Trim()
if ($LASTEXITCODE -ne 0 -or -not $dotnetVersionText.StartsWith("10.")) {
    throw "需要 .NET 10 SDK，当前版本：$dotnetVersionText"
}

Write-Host "[2/9] 创建隔离的 Python 3.12 构建环境"
if (-not (Test-Path (Join-Path $Venv "Scripts\python.exe"))) {
    Invoke-Native $pythonCommand ($pythonPrefix + @("-m", "venv", $Venv))
}
$VenvPython = Join-Path $Venv "Scripts\python.exe"
Invoke-Native $VenvPython @("-m", "pip", "install", "--upgrade", "pip")
Invoke-Native $VenvPython @("-m", "pip", "install", "-r", (Join-Path $Root "packaging\runtime-requirements.txt"), "pyinstaller==6.21.0")
Invoke-Native $VenvPython @("-m", "pip", "check")

Write-Host "[2b/9] 构建并验证 NLStradamus 1.8 Windows x64 辅助程序"
New-Item -ItemType Directory -Path $NlstradamusBinDir -Force | Out-Null
$mingw = Get-Command "g++.exe" -ErrorAction SilentlyContinue
Push-Location $NlstradamusDir
try {
    if ($mingw) {
        Invoke-Native $mingw.Source @(
            "-O3", "-std=c++11", "-Werror=return-type",
            $NlstradamusSource, "-o", $NlstradamusExe
        )
    } else {
        throw "未找到 g++.exe；NLStradamus 1.8 原版源码使用可变长数组扩展，请安装 MinGW-w64。"
    }
} finally {
    Pop-Location
}
$nlHelp = (& $NlstradamusExe -h | Out-String)
if ($LASTEXITCODE -ne 0 -or $nlHelp -notmatch "NLStradamus v1\.8") {
    throw "NLStradamus 1.8 Windows 辅助程序自检失败。"
}

Write-Host "[3/9] 验证源码与全部 Streamlit 页面"
if (-not $SkipTests) {
    Invoke-Native $VenvPython @((Join-Path $Root "script\validate_windows_source.py"))
    Invoke-Native $VenvPython @((Join-Path $Root "script\verify_source.py"))
    Invoke-Native $VenvPython @((Join-Path $Root "script\test_core_functions.py"))
    Invoke-Native $VenvPython @((Join-Path $Root "script\test_prediction_adapters.py"))
    Invoke-Native $VenvPython @((Join-Path $Root "script\test_streamlit_pages.py"))
    Invoke-Native $VenvPython @((Join-Path $Root "script\test_streamlit_workflows.py"))
    Invoke-Native $VenvPython @((Join-Path $Root "script\test_codex_chatgpt.py"))
    Invoke-Native $VenvPython @((Join-Path $Root "script\test_report_interpretation.py"))
    Invoke-Native $VenvPython @((Join-Path $Root "script\test_multi_provider_api.py"))
    Invoke-Native $VenvPython @((Join-Path $Root "script\test_backend_license_gate.py"))
    if ($RunLiveTests) {
        Invoke-Native $VenvPython @((Join-Path $Root "script\validate_ricedata_live.py"))
        Invoke-Native $VenvPython @((Join-Path $Root "script\validate_rgap_live.py"))
        Invoke-Native $VenvPython @((Join-Path $Root "script\validate_utr_promoter_live.py"))
        Invoke-Native $VenvPython @((Join-Path $Root "script\validate_efp_live.py"))
    }
}

Write-Host "[4/9] 构建 Windows PyInstaller onedir 后端"
if (Test-Path $BuildRoot) {
    Remove-Item $BuildRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $BackendDist -Force | Out-Null
New-Item -ItemType Directory -Path $PyInstallerWork -Force | Out-Null
$env:PYINSTALLER_CONFIG_DIR = Join-Path $Root ".pyinstaller-win"
Invoke-Native $VenvPython @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--distpath", $BackendDist,
    "--workpath", $PyInstallerWork,
    $BackendSpec
)
$BackendBundle = Join-Path $BackendDist "BioToolsBackend"
$BackendExe = Join-Path $BackendBundle "BioToolsBackend.exe"
if (-not (Test-Path $BackendExe)) {
    throw "未生成 Windows 后端：$BackendExe"
}
Invoke-Native $BackendExe @("--runtime-smoke-test")
$DocxRuntime = Join-Path $BackendBundle "_internal\docx"
$DocxParts = Join-Path $DocxRuntime "parts"
if (-not (Test-Path $DocxParts -PathType Container)) {
    throw "冻结后端缺少 python-docx 实体目录：$DocxParts"
}
foreach ($template in @("default-header.xml", "default-footer.xml")) {
    $rawTemplate = Join-Path $DocxParts "..\templates\$template"
    if (-not (Test-Path $rawTemplate -PathType Leaf)) {
        throw "冻结后端无法按 python-docx 原始相对路径读取模板：$rawTemplate"
    }
}
$DefaultDocx = Join-Path $DocxRuntime "templates\default.docx"
if (-not (Test-Path $DefaultDocx -PathType Leaf)) {
    throw "冻结后端缺少 python-docx 默认文档模板：$DefaultDocx"
}
Write-Host "python-docx 冻结运行时布局验证通过。"

Write-Host "[5/9] 发布 .NET 10 WPF 原生外壳"
Invoke-Native $dotnet.Source @(
    "run", "--project", $SmokeProject,
    "--configuration", "Release"
)
Invoke-Native $dotnet.Source @(
    "publish", $Project,
    "--configuration", "Release",
    "--runtime", "win-x64",
    "--self-contained", "true",
    "--output", $AppStage
)
$AppExe = Join-Path $AppStage "My Bio Tools.exe"
if (-not (Test-Path $AppExe)) {
    throw "未生成 Windows APP 外壳：$AppExe"
}

New-Item -ItemType Directory -Path (Join-Path $AppStage "backend") -Force | Out-Null
Copy-Item (Join-Path $BackendBundle "*") (Join-Path $AppStage "backend") -Recurse -Force
Copy-Item (Join-Path $Root "app_source") (Join-Path $AppStage "app_source") -Recurse -Force
$StagedMacNlstradamus = Join-Path $AppStage "app_source\vendor\nlstradamus\bin\NLStradamus"
if (Test-Path $StagedMacNlstradamus) {
    Remove-Item $StagedMacNlstradamus -Force
}
Get-ChildItem (Join-Path $AppStage "app_source") -Directory -Filter "__pycache__" -Recurse -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem (Join-Path $AppStage "app_source") -File -Filter "*.pyc" -Recurse -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $Root "packaging\THIRD_PARTY_NOTICES.txt") $AppStage -Force
$authConfig = [ordered]@{
    baseUrl = $AuthBaseUrl
    publicJwk = $LicensePublicJwk
}
$authConfig | ConvertTo-Json -Compress | Set-Content (Join-Path $AppStage "auth-config.json") -Encoding UTF8

Write-Host "[6/9] 准备并验证 WebView2 x64 离线安装程序"
New-Item -ItemType Directory -Path (Split-Path -Parent $CachedRuntimeInstaller) -Force | Out-Null
if ($WebViewInstallerPath) {
    if (-not (Test-Path $WebViewInstallerPath)) {
        throw "找不到指定的 WebView2 安装程序：$WebViewInstallerPath"
    }
    Copy-Item $WebViewInstallerPath $CachedRuntimeInstaller -Force
} elseif (-not (Test-Path $CachedRuntimeInstaller)) {
    if ($SkipWebViewDownload) {
        throw "未找到缓存的 WebView2 离线安装程序，且已指定 SkipWebViewDownload。"
    }
    Invoke-WebRequest -UseBasicParsing -Uri $RuntimeInstallerUrl -OutFile $CachedRuntimeInstaller
}
Test-WebViewInstallerSignature $CachedRuntimeInstaller
New-Item -ItemType Directory -Path (Join-Path $AppStage "prerequisites") -Force | Out-Null
Copy-Item $CachedRuntimeInstaller (Join-Path $AppStage "prerequisites\$RuntimeInstallerName") -Force
$WebViewHash = (Get-FileHash $CachedRuntimeInstaller -Algorithm SHA256).Hash.ToLowerInvariant()

Write-Host "[7/9] 生成版本清单并验证分发目录"
New-Item -ItemType Directory -Path $DistRoot -Force | Out-Null
Invoke-Native $VenvPython @("-m", "pip", "freeze", "--all") | Set-Content (Join-Path $DistRoot "windows-build-requirements.lock.txt") -Encoding UTF8
$manifest = [ordered]@{
    product = "My Bio Tools"
    version = $Version
    build = $Build
    baseline = "macOS 1.9.7 Build 26"
    platform = "win-x64"
    python = "3.12"
    dotnet = $dotnetVersionText
    webview2_sdk = "1.0.4078.44"
    webview2_installer_sha256 = $WebViewHash
    build_time_utc = [DateTime]::UtcNow.ToString("o")
    tools = 7
    online_tools = 2
    cloud_ai_providers = 6
    codex_chatgpt = $true
    ollama = $true
    authenticated_omics = $true
}
$manifest | ConvertTo-Json | Set-Content (Join-Path $AppStage "version-manifest.json") -Encoding UTF8
$manifest | ConvertTo-Json | Set-Content (Join-Path $DistRoot "version-manifest.json") -Encoding UTF8
Invoke-Native $VenvPython @(
    (Join-Path $Root "script\validate_windows_source.py"),
    "--staged-app", $AppStage,
    "--report", (Join-Path $DistRoot "windows-build-validation.md")
)

Write-Host "[8/9] 生成便携版 ZIP 与 Inno Setup 安装包"
if (Test-Path $PortableZip) {
    Remove-Item $PortableZip -Force
}
Compress-Archive -Path (Join-Path $AppStage "*") -DestinationPath $PortableZip -CompressionLevel Optimal

if (-not $SkipInstaller) {
    $isccCandidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
    )
    $isccCommand = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($isccCommand) {
        $Iscc = $isccCommand.Source
    } else {
        $Iscc = $isccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    }
    if (-not $Iscc) {
        throw "未找到 Inno Setup 6。安装后重试，或使用 -SkipInstaller 仅生成便携版。"
    }

    $iss = Join-Path $Root "windows\installer\MyBioTools.iss"
    $icon = Join-Path $Root "windows\MyBioTools.Windows\Assets\AppIcon.ico"
    Invoke-Native $Iscc @(
        "/DSourceDir=$AppStage",
        "/DOutputDir=$DistRoot",
        "/DIconFile=$icon",
        $iss
    )
    if (-not (Test-Path $SetupExe)) {
        throw "Inno Setup 未生成预期安装包：$SetupExe"
    }
}

if ($RunRuntimeSmokeTest) {
    Invoke-Native "powershell.exe" @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", (Join-Path $Root "script\test_windows_runtime.ps1"),
        "-AppDir", $AppStage,
        "-RequireAuthorizedAccount"
    )
}

Write-Host "[9/9] 生成 SHA256 校验文件"
$artifacts = @($PortableZip)
if (-not $SkipInstaller) {
    $artifacts += $SetupExe
}
$checksumLines = foreach ($artifact in $artifacts) {
    $hash = Get-FileHash $artifact -Algorithm SHA256
    "$($hash.Hash.ToLowerInvariant()) *$([IO.Path]::GetFileName($hash.Path))"
}
$checksumLines | Set-Content (Join-Path $DistRoot "SHA256SUMS.txt") -Encoding ASCII

Write-Host "Windows 版构建完成：$DistRoot"
Get-ChildItem $DistRoot -File | Select-Object Name, Length, LastWriteTime
