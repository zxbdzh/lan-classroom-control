param(
    [Parameter(Mandatory = $true)][string]$Root,
    [Parameter(Mandatory = $true)][string]$Python
)
$ErrorActionPreference = "Stop"
$candidate = (Resolve-Path -LiteralPath $Root).Path
& $Python (Join-Path $PSScriptRoot "validate_candidate.py") --root $candidate
if ($LASTEXITCODE -ne 0) { throw "Frozen startup validation failed" }
Get-FileHash -Algorithm SHA256 -LiteralPath `
    (Join-Path $candidate "installer\LanClassroom-Teacher-Setup.exe"), `
    (Join-Path $candidate "installer\LanClassroom-Student-Setup.exe")
