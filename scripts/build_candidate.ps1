param(
    [Parameter(Mandatory = $true)][string]$Python,
    [string]$Output = (Join-Path $PSScriptRoot "..\.artifacts\candidate"),
    [string]$Iscc = ""
)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonPath = (Resolve-Path -LiteralPath $Python).Path
$outputPath = [IO.Path]::GetFullPath($Output)
function Copy-Contents([string]$Source, [string]$Destination) {
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    Get-ChildItem -LiteralPath $Source -Force | Copy-Item -Destination $Destination -Recurse -Force
}
function Copy-UltraVNC([string]$Destination) {
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    # Viewer 会在自身目录保存会话配置；安装包只收集固定上游文件，避免混入实验设置。
    foreach ($name in @(
        "winvnc.exe", "vncviewer.exe", "ddengine64.dll", "vnchooks.dll",
        "authSSP.dll", "logging.dll", "logmessages.dll",
        "LICENSE-UltraVNC.txt", "UPSTREAM.txt"
    )) {
        Copy-Item -LiteralPath (Join-Path $root "third_party\ultravnc\$name") -Destination $Destination -Force
    }
}
& $pythonPath -c "import sys; assert sys.version_info[:3] == (3,8,10), 'Release build requires official CPython 3.8.10'"
if ($LASTEXITCODE -ne 0) { throw "Wrong release interpreter" }
Push-Location $root
try {
    & $pythonPath scripts/prepare_ultravnc.py
    if ($LASTEXITCODE -ne 0) { throw "UltraVNC preparation failed" }
    $builds = @(
        @{Name="LanClassroom-Teacher"; Entry="teacher/main.py"; Windowed=$true},
        @{Name="LanClassroom-Student-Service"; Entry="student/service.py"; Windowed=$false},
        @{Name="LanClassroom-Student-Agent"; Entry="student/agent.py"; Windowed=$true}
    )
    foreach ($build in $builds) {
        $env:CLASSROOM_FREEZE_ROLE = $build.Name
        $arguments = @(
            "-m", "PyInstaller", "--noconfirm", "--distpath", $outputPath,
            "--workpath", (Join-Path $outputPath ("work\" + $build.Name)),
            (Join-Path $PSScriptRoot "classroom.spec")
        )
        & $pythonPath @arguments
        if ($LASTEXITCODE -ne 0) { throw "$($build.Name) build failed" }
    }
    Copy-UltraVNC (Join-Path $outputPath "LanClassroom-Teacher\UltraVNC")
    $student = Join-Path $outputPath "Student"
    New-Item -ItemType Directory -Force -Path $student | Out-Null
    Copy-Contents (Join-Path $outputPath "LanClassroom-Student-Service") (Join-Path $student "Service")
    Copy-Contents (Join-Path $outputPath "LanClassroom-Student-Agent") (Join-Path $student "Agent")
    Copy-UltraVNC (Join-Path $student "UltraVNC")
    & $pythonPath scripts/check_pe_compat.py --maximum-version 6.1 `
        --runtime-directory "$outputPath\LanClassroom-Teacher" `
        --runtime-directory "$student\Service" `
        --runtime-directory "$student\Agent" `
        "$outputPath\LanClassroom-Teacher\LanClassroom-Teacher.exe" `
        "$outputPath\LanClassroom-Teacher\python38.dll" `
        "$outputPath\LanClassroom-Teacher\PyQt5\Qt\bin\Qt5Core.dll" `
        "$outputPath\Student\Service\LanClassroom-Student-Service.exe" `
        "$outputPath\Student\Service\python38.dll" `
        "$outputPath\Student\Agent\LanClassroom-Student-Agent.exe" `
        "$outputPath\Student\Agent\python38.dll" `
        "$outputPath\Student\Agent\PyQt5\Qt\bin\Qt5Core.dll" `
        "$student\UltraVNC\winvnc.exe" "$student\UltraVNC\vncviewer.exe"
    if ($LASTEXITCODE -ne 0) { throw "PE compatibility check failed" }
    if ($Iscc) {
        $version = (Get-Content (Join-Path $root "version.txt") -Raw).Trim()
        foreach ($role in @("teacher", "student")) {
            & $Iscc "/DAppVersion=$version" "/DBuildRoot=$outputPath" `
                "/O$outputPath\installer" "installer/$role.iss"
            if ($LASTEXITCODE -ne 0) { throw "$role installer build failed" }
        }
        & $pythonPath scripts/check_pe_compat.py --maximum-version 6.1 `
            "$outputPath\installer\LanClassroom-Teacher-Setup.exe" `
            "$outputPath\installer\LanClassroom-Student-Setup.exe"
        if ($LASTEXITCODE -ne 0) { throw "Installer PE compatibility check failed" }
        & (Join-Path $PSScriptRoot "validate_candidate.ps1") -Root $outputPath -Python $pythonPath
    }
    Write-Host "CANDIDATE_BUILD_OK $outputPath"
} finally {
    Pop-Location
}
