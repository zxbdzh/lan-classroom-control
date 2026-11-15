param(
    [Parameter(Mandatory = $true)][string]$TeacherVhd,
    [Parameter(Mandatory = $true)][string]$Win7Vhd,
    [Parameter(Mandatory = $true)][string]$Win10Vhd,
    [string]$Root = "F:\VMs\LanClassroomLab",
    [switch]$Start
)
$ErrorActionPreference = "Stop"
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "创建 Hyper-V 实验室需要管理员终端；本脚本不会弹 UAC 或自动重启宿主机。"
}
foreach ($image in @($TeacherVhd, $Win7Vhd, $Win10Vhd)) {
    if (-not (Test-Path -LiteralPath $image -PathType Leaf)) { throw "缺少虚拟磁盘：$image" }
    if ([IO.Path]::GetExtension($image) -notin @(".vhd", ".vhdx")) {
        throw "请提供已安装系统的 VHD/VHDX，不要传 ISO：$image"
    }
}
if (-not (Get-Command New-VM -ErrorAction SilentlyContinue)) {
    throw "Hyper-V 管理模块未安装；本脚本不会启用功能或重启宿主机。"
}
if ((Get-Service vmms).Status -ne "Running") { Start-Service vmms }
$switchName = "LanClassroomLabPrivate"
$network = Get-VMSwitch -Name $switchName -ErrorAction SilentlyContinue
if ($network -and $network.SwitchType -ne "Private") {
    throw "同名交换机不是隔离专用网络，停止以免误改现有网络。"
}
if (-not $network) { New-VMSwitch -Name $switchName -SwitchType Private | Out-Null }
$labRoot = [IO.Path]::GetFullPath($Root)
New-Item -ItemType Directory -Path $labRoot -Force | Out-Null
$definitions = @(
    @{Name="LanClassroomLab-Teacher"; Image=$TeacherVhd; Memory=4GB},
    @{Name="LanClassroomLab-Win7"; Image=$Win7Vhd; Memory=2GB},
    @{Name="LanClassroomLab-Win10"; Image=$Win10Vhd; Memory=4GB}
)
foreach ($definition in $definitions) {
    $name = $definition.Name
    if (Get-VM -Name $name -ErrorAction SilentlyContinue) {
        throw "虚拟机已存在：$name。请单独检查并启动；本脚本不覆盖原机。"
    }
    $extension = [IO.Path]::GetExtension($definition.Image)
    $disk = Join-Path $labRoot "$name$extension"
    if (Test-Path -LiteralPath $disk) { throw "差分磁盘已存在：$disk" }
    $parent = (Resolve-Path -LiteralPath $definition.Image).Path
    New-VHD -Path $disk -ParentPath $parent -Differencing | Out-Null
    # Win7 使用第 1 代；原盘必须是 BIOS/MBR 安装，父盘保持只读不改。
    New-VM -Name $name -Generation 1 -Path $labRoot `
        -MemoryStartupBytes $definition.Memory -VHDPath $disk -SwitchName $switchName | Out-Null
    Set-VMProcessor -VMName $name -Count 2
    Set-VM -Name $name -AutomaticStartAction Nothing -AutomaticStopAction ShutDown
    if ($Start) { Start-VM -Name $name }
}
Get-VM -Name "LanClassroomLab-*" | Select-Object Name,State,Generation,MemoryAssigned
Write-Host "未打开 VMConnect；私有交换机不连接宿主机网卡，也不更改 WiFi。"
