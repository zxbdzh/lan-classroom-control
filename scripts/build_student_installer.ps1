$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$studentExe = Join-Path $root "dist\LanClassroom-Student\LanClassroom-Student.exe"
$scriptPath = Join-Path $root "installer\student.iss"

if (-not (Test-Path $studentExe)) {
    throw "Student build not found: $studentExe. Build the PyInstaller onedir first."
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
& $isccPath $scriptPath
