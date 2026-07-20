[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$AppDir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$email = $env:MY_BIO_TOOLS_TEST_EMAIL
$password = $env:MY_BIO_TOOLS_TEST_PASSWORD
if (-not $email -or -not $password) {
    throw "缺少私有测试账号环境变量；不执行真实登录验收。"
}

$AppDir = (Resolve-Path $AppDir).Path
$AppExe = Join-Path $AppDir "My Bio Tools.exe"
if (-not (Test-Path $AppExe)) {
    throw "找不到待验证 APP：$AppExe"
}

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

function Find-AutomationElement {
    param(
        [Parameter(Mandatory = $true)]$Root,
        [Parameter(Mandatory = $true)][string]$AutomationId
    )

    $condition = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::AutomationIdProperty,
        $AutomationId
    )
    return $Root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $condition)
}

function Wait-ForBackendHealth {
    param([Parameter(Mandatory = $true)][string]$ApplicationDirectory)

    $deadline = (Get-Date).AddSeconds(120)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
        $backend = Get-CimInstance Win32_Process -Filter "Name = 'BioToolsBackend.exe'" -ErrorAction SilentlyContinue |
            Where-Object {
                $_.ExecutablePath -and
                $_.ExecutablePath.StartsWith($ApplicationDirectory, [StringComparison]::OrdinalIgnoreCase) -and
                $_.CommandLine -match "--port\s+(\d+)"
            } |
            Select-Object -First 1
        if (-not $backend) { continue }

        $port = [int]$Matches[1]
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/_stcore/health" -TimeoutSec 2
            if ($response.StatusCode -eq 200 -and $response.Content -match "ok") {
                Write-Host "PASS 审核账号登录、授权数据库解锁和内置服务健康检查（端口 $port）"
                return
            }
        } catch {
            # Continue until the global deadline.
        }
    }
    throw "真实登录后未在 120 秒内完成授权解锁和内置服务健康检查。"
}

$authPath = Join-Path $env:LOCALAPPDATA "WuLab\My Bio Tools\auth.dat"
if (Test-Path $authPath) {
    Remove-Item $authPath -Force
}

$app = $null
try {
    $app = Start-Process -FilePath $AppExe -WorkingDirectory $AppDir -PassThru
    $windowDeadline = (Get-Date).AddSeconds(45)
    while ($app.MainWindowHandle -eq 0 -and (Get-Date) -lt $windowDeadline) {
        Start-Sleep -Milliseconds 250
        $app.Refresh()
    }
    if ($app.HasExited -or $app.MainWindowHandle -eq 0) {
        throw "Windows APP 登录窗口未能打开。"
    }

    $root = [System.Windows.Automation.AutomationElement]::FromHandle($app.MainWindowHandle)
    $controlsDeadline = (Get-Date).AddSeconds(30)
    do {
        $emailBox = Find-AutomationElement -Root $root -AutomationId "LoginEmailBox"
        $passwordBox = Find-AutomationElement -Root $root -AutomationId "LoginPasswordBox"
        $loginButton = Find-AutomationElement -Root $root -AutomationId "LoginButton"
        if ($emailBox -and $passwordBox -and $loginButton) { break }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $controlsDeadline)

    if (-not $emailBox -or -not $passwordBox -or -not $loginButton) {
        throw "未找到登录表单控件。"
    }

    $emailPattern = [System.Windows.Automation.ValuePattern]$emailBox.GetCurrentPattern(
        [System.Windows.Automation.ValuePattern]::Pattern
    )
    $passwordPattern = [System.Windows.Automation.ValuePattern]$passwordBox.GetCurrentPattern(
        [System.Windows.Automation.ValuePattern]::Pattern
    )
    $invokePattern = [System.Windows.Automation.InvokePattern]$loginButton.GetCurrentPattern(
        [System.Windows.Automation.InvokePattern]::Pattern
    )
    $emailPattern.SetValue($email)
    $passwordPattern.SetValue($password)
    $invokePattern.Invoke()

    Wait-ForBackendHealth -ApplicationDirectory $AppDir
} finally {
    $password = $null
    $env:MY_BIO_TOOLS_TEST_PASSWORD = $null
    if ($app -and -not $app.HasExited) {
        $null = $app.CloseMainWindow()
        if (-not $app.WaitForExit(8000)) {
            Stop-Process -Id $app.Id -Force -ErrorAction SilentlyContinue
        }
    }
}

Start-Sleep -Seconds 2
$orphan = Get-CimInstance Win32_Process -Filter "Name = 'BioToolsBackend.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($AppDir, [StringComparison]::OrdinalIgnoreCase) }
if ($orphan) {
    throw "真实登录验收结束后仍有残留后端进程。"
}
$omicsCache = Join-Path $env:LOCALAPPDATA "WuLab\My Bio Tools\Cache\authenticated-omics"
$plaintext = @()
if (Test-Path $omicsCache) {
    $plaintext = @(Get-ChildItem $omicsCache -File -Filter "wulab_omics_v1.sqlite" -Recurse -ErrorAction SilentlyContinue)
}
if ($plaintext.Count -gt 0) {
    throw "真实登录验收结束后仍有临时明文多组学数据库。"
}

Write-Host "PASS 真实登录验收结束后无残留后端进程和临时明文数据库"
