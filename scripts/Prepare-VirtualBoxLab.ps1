[CmdletBinding()]
param(
    [string]$DownloadDirectory = 'F:\tmp\classroom-virtualbox\20261114',
    [string]$McpDirectory = 'F:\DevTools\virtualbox-mcp\v0.4.1',
    [string]$ScreenshotDirectory = 'F:\tmp\classroom-virtualbox\screenshots'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Receive-VerifiedFile {
    param([string]$Uri, [string]$Destination, [string]$Sha256)

    if ($Sha256.Length -ne 64) {
        throw "Invalid SHA-256 for $Destination"
    }
    if (Test-Path -LiteralPath $Destination) {
        if ((Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash -ieq $Sha256) {
            Write-Host "Verified existing file: $Destination"
            return
        }
        throw "Existing file has an unexpected hash; preserved: $Destination"
    }

    # 先写入本次下载的临时文件，校验通过才公开最终安装包。
    $partialPath = "$Destination.partial"
    & curl.exe --fail --location --retry 2 --connect-timeout 20 --max-time 600 `
        --silent --show-error --output $partialPath $Uri
    if ($LASTEXITCODE -ne 0) {
        throw "Download failed: $Uri"
    }
    $actualHash = (Get-FileHash -LiteralPath $partialPath -Algorithm SHA256).Hash
    if ($actualHash -ine $Sha256) {
        throw "SHA-256 mismatch: $partialPath ($actualHash)"
    }
    [System.IO.File]::Move($partialPath, $Destination)
    Write-Host "Downloaded and verified: $Destination"
}

# MCP 的截图工具不会创建父目录，首次调用前必须准备输出位置。
New-Item -ItemType Directory -Path $DownloadDirectory, $McpDirectory, $ScreenshotDirectory -Force | Out-Null
$mcpArchive = Join-Path $DownloadDirectory 'mcp-virtualbox-v0.4.1-windows-amd64.zip'
Receive-VerifiedFile `
    -Uri 'https://github.com/mb85/mcp-virtualbox/releases/download/v0.4.1/mcp-virtualbox_windows_amd64.zip' `
    -Destination $mcpArchive `
    -Sha256 'b161528b3b2ffc422deee420f9e2a5aded6bca8e1c360ed5ce2dc57651756afb'

$mcpExecutable = Join-Path $McpDirectory 'mcp-virtualbox.exe'
if (-not (Test-Path -LiteralPath $mcpExecutable)) {
    Expand-Archive -LiteralPath $mcpArchive -DestinationPath $McpDirectory
}
if (-not (Test-Path -LiteralPath $mcpExecutable)) {
    throw 'MCP archive does not contain the expected executable'
}
Write-Host "MCP executable ready: $mcpExecutable"

$installerPath = Join-Path $DownloadDirectory 'VirtualBox-7.2.22-175245-Win.exe'
Receive-VerifiedFile `
    -Uri 'https://download.virtualbox.org/virtualbox/7.2.22/VirtualBox-7.2.22-175245-Win.exe' `
    -Destination $installerPath `
    -Sha256 'afd0ff1656cbe04ed88ce619594ae021419d97cce89fa9bdc443e9a963f1713f'

$signature = Get-AuthenticodeSignature -LiteralPath $installerPath
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Oracle') {
    throw "Oracle installer signature check failed: $($signature.Status)"
}
Write-Host "Oracle signature verified: $($signature.SignerCertificate.Subject)"
Write-Host 'PREPARATION_COMPLETE'
