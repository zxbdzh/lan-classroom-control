$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$serviceExe = Join-Path $root "dist\LanClassroom-Student-Service\LanClassroom-Student-Service.exe"
$agentExe = Join-Path $root "dist\LanClassroom-Student-Agent\LanClassroom-Student-Agent.exe"
$scriptPath = Join-Path $root "installer\student.iss"

foreach ($artifact in @($serviceExe, $agentExe)) {
    if (-not (Test-Path $artifact)) {
        throw "Student build not found: $artifact. Build both PyInstaller onedir targets first."
    }
}

$iscc = Get-Command iscc.exe -ErrorAction SilentlyContinue
if (-not $iscc) {
    $defaultPath = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    if (Test-Path $defaultPath) {
        $iscc = Get-Item $defaultPath
    }
}

if (-not $iscc) {
    throw "Inno Setup 6 compiler not found. Install Inno Setup, then rerun this script."
}

$isccPath = if ($iscc.Source) { $iscc.Source } else { $iscc.FullName }
$version = (Get-Content (Join-Path $root "version.txt") -Raw).Trim()
if ($version -notmatch '^\d+\.\d+\.\d+([.-][0-9A-Za-z]+)*$') {
    throw "Invalid version.txt value: $version"
}
& $isccPath "/DAppVersion=$version" $scriptPath
