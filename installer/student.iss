#define AppName "LanClassroom Student"
#define AppNameZh "局域网机房控制系统学生端"
#ifndef AppVersion
  #define AppVersion "2.0.0"
#endif
#define AppPublisher "LanClassroom"
#define ServiceExe "LanClassroom-Student-Service.exe"
#define AgentExe "LanClassroom-Student-Agent.exe"

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
Source: "..\dist\LanClassroom-Student-Service\*"; DestDir: "{app}\Service"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\dist\LanClassroom-Student-Agent\*"; DestDir: "{app}\Agent"; Flags: ignoreversion recursesubdirs createallsubdirs

[Registry]
Root: HKLM; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "LanClassroomStudentAgent"; ValueData: """{app}\Agent\{#AgentExe}"""; Flags: uninsdeletevalue

[Run]
Filename: "{app}\Service\{#ServiceExe}"; Parameters: "--startup auto update"; Flags: runhidden waituntilterminated; Check: ShouldUpdateService
Filename: "{app}\Service\{#ServiceExe}"; Parameters: "--startup auto install"; Flags: runhidden waituntilterminated; Check: ShouldInstallService
Filename: "{app}\Service\{#ServiceExe}"; Parameters: "start"; Flags: runhidden waituntilterminated
Filename: "{app}\Agent\{#AgentExe}"; Description: "启动学生端会话代理"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\Service\{#ServiceExe}"; Parameters: "stop"; Flags: runhidden waituntilterminated; RunOnceId: "StopStudentService"
Filename: "{app}\Service\{#ServiceExe}"; Parameters: "remove"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveStudentService"
Filename: "{sys}\taskkill.exe"; Parameters: "/IM {#AgentExe} /F"; Flags: runhidden; RunOnceId: "StopStudentAgent"

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
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM "LanClassroom-Student.exe" /F', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := '';
end;
