[CmdletBinding()]
param(
    [string]$PackageDirectory = 'F:\tmp\classroom-virtualbox\20261114\msi',
    [string]$ReportDirectory = 'F:\tmp\classroom-virtualbox\20261114\installation'
)

$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]$identity
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    # 普通终端直接失败，不自动弹出 UAC 干扰用户正在操作的窗口。
    throw 'Administrator terminal required. This script does not open a UAC prompt.'
}

$expectedFiles = @{
    'VirtualBox-7.2.22-r175245-MultiArch_amd64.msi' = '67875d993fe2460f4515fc4571feeffe0b3542f2ac450920332f8a1d53d566e8'
    'common.cab' = '6eb8849bfad98f3dacd37d89a0520facca51183ed14c6634f3eda91a4a6b5868'
}
foreach ($entry in $expectedFiles.GetEnumerator()) {
    $filePath = Join-Path $PackageDirectory $entry.Key
    if (-not (Test-Path -LiteralPath $filePath -PathType Leaf)) {
        throw "Missing package: $filePath"
    }
    if ((Get-FileHash -LiteralPath $filePath -Algorithm SHA256).Hash -ine $entry.Value) {
        throw "Package hash mismatch: $filePath"
    }
}
$msiPath = Join-Path $PackageDirectory 'VirtualBox-7.2.22-r175245-MultiArch_amd64.msi'
$signature = Get-AuthenticodeSignature -LiteralPath $msiPath
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Oracle') {
    throw 'The MSI does not have a valid Oracle signature.'
}

$vboxManage = Join-Path $env:ProgramFiles 'Oracle\VirtualBox\VBoxManage.exe'
if (Test-Path -LiteralPath $vboxManage) {
    throw 'VirtualBox is already installed. Check the existing installation before running this setup again.'
}
New-Item -ItemType Directory -Path $ReportDirectory -Force | Out-Null
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$logPath = Join-Path $ReportDirectory "virtualbox-msi-$timestamp.log"

# 仅装核心：NAT 与虚拟 USB tablet 不需要向宿主物理网卡绑定桥接过滤驱动。
$arguments = @(
    '/i', "`"$msiPath`"", '/qn', '/norestart',
    'REBOOT=ReallySuppress', 'ADDLOCAL=VBoxApplication',
    'VBOX_START=0', 'VBOX_INSTALLDESKTOPSHORTCUT=0',
    'VBOX_INSTALLQUICKLAUNCHSHORTCUT=0', 'VBOX_REGISTERFILEEXTENSIONS=0',
    '/L*v', "`"$logPath`""
)
$installer = Start-Process -FilePath "$env:SystemRoot\System32\msiexec.exe" `
    -ArgumentList $arguments -WindowStyle Hidden -Wait -PassThru
$exitCode = $installer.ExitCode
$report = [ordered]@{
    recorded_at = (Get-Date).ToUniversalTime().ToString('o')
    msi_exit_code = $exitCode
    installed = ($exitCode -in @(0, 3010))
    restart_required = ($exitCode -eq 3010)
    features = @('VBoxApplication')
    automatic_reboot = $false
    gui_launched = $false
    log = $logPath
    guest_tests = 'not_tested'
}
$reportPath = Join-Path $ReportDirectory "installation-$timestamp.json"
$report | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $reportPath -Encoding UTF8
if (-not $report.installed) {
    throw "VirtualBox MSI failed: $exitCode. Log: $logPath"
}
Write-Host "VirtualBox core installation completed. Report: $reportPath"
if ($report.restart_required) {
    Write-Host 'Windows reports that a restart is required. No restart was performed.'
}
