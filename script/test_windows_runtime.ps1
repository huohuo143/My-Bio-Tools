[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$AppDir,
    [switch]$RequireAuthorizedAccount
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$AppDir = (Resolve-Path $AppDir).Path
$AppExe = Join-Path $AppDir "My Bio Tools.exe"
if (-not (Test-Path $AppExe)) {
    throw "找不到待验证 APP：$AppExe"
}

$ProductKey = "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
function Get-RegistryVersion {
    param([string]$Path)
    $item = Get-ItemProperty -Path $Path -Name "pv" -ErrorAction SilentlyContinue
    if ($item) {
        return $item.pv
    }
    return $null
}

$RuntimeVersions = @(
    (Get-RegistryVersion "HKCU:\$ProductKey"),
    (Get-RegistryVersion "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}")
) | Where-Object { $_ -and $_ -ne "0.0.0.0" }
if (-not $RuntimeVersions) {
    throw "当前 Windows 未安装 WebView2 Runtime；请先运行分发包中的离线安装程序。"
}

$app = $null
try {
    $app = Start-Process -FilePath $AppExe -WorkingDirectory $AppDir -PassThru
    $deadline = (Get-Date).AddSeconds(60)
    $healthPassed = $false
    $backend = $null

    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
        $backend = Get-CimInstance Win32_Process -Filter "Name = 'BioToolsBackend.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($AppDir, [StringComparison]::OrdinalIgnoreCase) } |
            Select-Object -First 1
        if (-not $backend -or $backend.CommandLine -notmatch "--port\s+(\d+)") {
            continue
        }

        $port = [int]$Matches[1]
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/_stcore/health" -TimeoutSec 2
            if ($response.StatusCode -eq 200 -and $response.Content -match "ok") {
                $healthPassed = $true
                Write-Host "PASS Windows APP 与内置服务健康检查（端口 $port）"
                break
            }
        } catch {
            # Continue until the global startup deadline.
        }
    }

    if (-not $healthPassed) {
        if ($RequireAuthorizedAccount) {
            throw "Windows APP 未在 60 秒内完成登录授权、多组学解锁和内置服务健康检查。请先用已审核账号登录。"
        }
        if ($app.HasExited) {
            throw "Windows APP 在登录界面验证期间意外退出。"
        }
        Write-Host "PASS Windows APP 登录/注册界面保持运行；未使用账号，不启动受保护后端"
    }
} finally {
    if ($app -and -not $app.HasExited) {
        $null = $app.CloseMainWindow()
        if (-not $app.WaitForExit(5000)) {
            Stop-Process -Id $app.Id -Force -ErrorAction SilentlyContinue
        }
    }
}

Start-Sleep -Seconds 2
$orphan = Get-CimInstance Win32_Process -Filter "Name = 'BioToolsBackend.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($AppDir, [StringComparison]::OrdinalIgnoreCase) }
if ($orphan) {
    throw "关闭 APP 后仍发现残留 BioToolsBackend.exe 进程。"
}
$omicsCache = Join-Path $env:LOCALAPPDATA "WuLab\My Bio Tools\Cache\authenticated-omics"
$plaintextDatabases = @()
if (Test-Path $omicsCache) {
    $plaintextDatabases = @(Get-ChildItem $omicsCache -File -Filter "wulab_omics_v1.sqlite" -Recurse -ErrorAction SilentlyContinue)
}
if ($plaintextDatabases.Count -gt 0) {
    throw "关闭 APP 后仍发现临时明文多组学数据库。"
}

Write-Host "PASS 关闭 APP 后无残留后端进程和临时多组学数据库"
