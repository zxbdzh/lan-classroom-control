#define AppName "LanClassroom Student"
#define AppNameZh "局域网机房控制系统学生端"
#ifndef AppVersion
  #define AppVersion "2.1.3"
#endif
#define AppPublisher "LanClassroom"
#define ServiceExe "LanClassroom-Student-Service.exe"
#define AgentExe "LanClassroom-Student-Agent.exe"
#ifndef BuildRoot
  #define BuildRoot "..\dist"
#endif

[Setup]
AppId={{9A4B9ED0-FAD0-4D41-A7C6-7E4566C3124D}
AppName={#AppNameZh}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\LanClassroom\Student
DefaultGroupName=LanClassroom
DisableDirPage=yes
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=LanClassroom-Student-Setup
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"

[Files]
Source: "{#BuildRoot}\LanClassroom-Student-Service\*"; DestDir: "{app}\Service"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#BuildRoot}\LanClassroom-Student-Agent\*"; DestDir: "{app}\Agent"; Flags: ignoreversion recursesubdirs createallsubdirs
; 钩子 DLL 可能仍由桌面进程加载；相同内容不替换，真正更新且被占用时按系统重启替换。
Source: "..\third_party\ultravnc\*"; DestDir: "{app}\UltraVNC"; Excludes: "*.vnc"; Flags: replacesameversion restartreplace

[Registry]
Root: HKLM; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "LanClassroomStudentAgent"; ValueData: """{app}\Agent\{#AgentExe}"""; Flags: uninsdeletevalue

[Run]
Filename: "{app}\Service\{#ServiceExe}"; Parameters: "--startup auto update"; Flags: runhidden waituntilterminated; Check: ShouldUpdateService
Filename: "{app}\Service\{#ServiceExe}"; Parameters: "--startup auto install"; Flags: runhidden waituntilterminated; Check: ShouldInstallService
Filename: "{app}\Service\{#ServiceExe}"; Parameters: "start"; Flags: runhidden waituntilterminated
Filename: "{sys}\sc.exe"; Parameters: "failure LanClassroomStudentV2 reset= 86400 actions= restart/2000/restart/5000/restart/10000"; Flags: runhidden waituntilterminated
Filename: "{app}\Agent\{#AgentExe}"; Description: "启动学生端会话代理"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""LanClassroomStudentServiceInbound"" program=""{app}\Service\{#ServiceExe}"""; Flags: runhidden waituntilterminated; RunOnceId: "RemoveStudentFirewall"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""LanClassroomStudentAgentInbound"" program=""{app}\Agent\{#AgentExe}"""; Flags: runhidden waituntilterminated; RunOnceId: "RemoveStudentAgentFirewall"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""LanClassroomUltraVNC"" program=""{app}\UltraVNC\winvnc.exe"""; Flags: runhidden waituntilterminated; RunOnceId: "RemoveVNCFirewall"
Filename: "{sys}\sc.exe"; Parameters: "stop LanClassroomVNC"; Flags: runhidden waituntilterminated; RunOnceId: "StopClassroomVNC"
Filename: "{app}\Service\{#ServiceExe}"; Parameters: "stop"; Flags: runhidden waituntilterminated; RunOnceId: "StopStudentService"
Filename: "{sys}\sc.exe"; Parameters: "delete LanClassroomVNC"; Flags: runhidden waituntilterminated; RunOnceId: "DeleteClassroomVNC"
Filename: "{app}\Service\{#ServiceExe}"; Parameters: "remove"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveStudentService"
Filename: "{sys}\taskkill.exe"; Parameters: "/IM {#AgentExe} /F"; Flags: runhidden; RunOnceId: "StopStudentAgent"

[UninstallDelete]
Type: files; Name: "{commonappdata}\UltraVNC\LanClassroomVNC.ini"

[Code]
function IsWindows7(): Boolean;
var
  Version: TWindowsVersion;
begin
  GetWindowsVersionEx(Version);
  Result := (Version.Major = 6) and (Version.Minor = 1);
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if IsWindows7() and not FileExists(ExpandConstant('{sys}\api-ms-win-crt-runtime-l1-1-0.dll')) then begin
    MsgBox('Windows 7 缺少 Universal C Runtime。请先安装 KB2999226，并确认已安装 SHA-2 更新 KB4474419 与服务堆栈更新 KB4490628。', mbError, MB_OK);
    Result := False;
  end;
end;

function IsV2ServiceInstalled(): Boolean;
begin
  Result := RegKeyExists(
    HKLM,
    'SYSTEM\CurrentControlSet\Services\LanClassroomStudentV2'
  );
end;

function ShouldUpdateService(): Boolean;
begin
  Result := IsV2ServiceInstalled();
end;

function ShouldInstallService(): Boolean;
begin
  Result := not IsV2ServiceInstalled();
end;

procedure RemoveV1Startup();
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\schtasks.exe'), '/Delete /TN "LanClassroomStudent" /F', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  RegDeleteValue(HKLM, 'Software\Microsoft\Windows\CurrentVersion\Run', 'LanClassroomStudent');
  RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'LanClassroomStudent');
end;

procedure StopInstalledV2();
var
  ResultCode: Integer;
  ServicePath: String;
begin
  ServicePath := ExpandConstant('{app}\Service\{#ServiceExe}');
  if FileExists(ServicePath) then
    Exec(ServicePath, '--wait 20 stop', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(
    ExpandConstant('{sys}\taskkill.exe'),
    '/IM "{#AgentExe}" /F',
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  );
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  RemoveV1Startup();
  StopInstalledV2();
  Exec(ExpandConstant('{sys}\sc.exe'), 'stop LanClassroomVNC', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM "LanClassroom-Student.exe" /F', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  Rule: String;
begin
  if CurStep <> ssPostInstall then
    Exit;
  Rule := 'name="LanClassroomStudentServiceInbound"';
  Exec(ExpandConstant('{sys}\netsh.exe'),
    'advfirewall firewall delete rule ' + Rule, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  { 学生主动连接 TCP，但教师发现和广播仍需接收入站 UDP；不依赖交互式放行弹窗。 }
  if not Exec(ExpandConstant('{sys}\netsh.exe'),
    'advfirewall firewall add rule ' + Rule +
    ' dir=in action=allow program="' + ExpandConstant('{app}\Service\{#ServiceExe}') +
    '" enable=yes profile=any', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    RaiseException('无法运行防火墙配置，请检查系统权限后重新安装。');
  if ResultCode <> 0 then
    RaiseException('添加学生端防火墙规则失败，请检查系统策略后重新安装。');
  Rule := 'name="LanClassroomStudentAgentInbound"';
  Exec(ExpandConstant('{sys}\netsh.exe'),
    'advfirewall firewall delete rule ' + Rule, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  { 广播接收运行在用户会话代理中；仅放行服务仍会在首次广播时弹窗阻断普通学生。 }
  if not Exec(ExpandConstant('{sys}\netsh.exe'),
    'advfirewall firewall add rule ' + Rule +
    ' dir=in action=allow program="' + ExpandConstant('{app}\Agent\{#AgentExe}') +
    '" enable=yes profile=any', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    RaiseException('无法运行会话代理防火墙配置，请检查系统权限后重新安装。');
  if ResultCode <> 0 then
    RaiseException('添加学生会话代理防火墙规则失败，请检查系统策略后重新安装。');
end;
