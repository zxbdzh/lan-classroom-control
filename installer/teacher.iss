#define AppName "LanClassroom Teacher"
#define AppNameZh "局域网机房控制系统教师端"
#ifndef AppVersion
  #define AppVersion "2.1.1"
#endif
#define AppPublisher "LanClassroom"
#define AppExeName "LanClassroom-Teacher.exe"
#ifndef BuildRoot
  #define BuildRoot "..\dist"
#endif

[Setup]
AppId={{3F274F69-43BC-4A34-99CC-401E679B4C31}
AppName={#AppNameZh}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\LanClassroom\Teacher
DefaultGroupName=LanClassroom
OutputDir=..\dist\installer
OutputBaseFilename=LanClassroom-Teacher-Setup
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"

[Files]
Source: "{#BuildRoot}\LanClassroom-Teacher\*"; DestDir: "{app}"; Excludes: "UltraVNC\*"; Flags: ignoreversion recursesubdirs createallsubdirs
; 保留原版 Viewer 的同版本文件，避免重复安装时替换仍在使用的相同二进制。
Source: "..\third_party\ultravnc\*"; DestDir: "{app}\UltraVNC"; Excludes: "*.vnc"; Flags: replacesameversion restartreplace

[Icons]
Name: "{group}\{#AppNameZh}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{commondesktop}\{#AppNameZh}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"

[Run]
Filename: "{app}\{#AppExeName}"; Description: "启动教师端"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""LanClassroomTeacherInbound"" program=""{app}\{#AppExeName}"""; Flags: runhidden waituntilterminated; RunOnceId: "RemoveTeacherFirewall"

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
  if IsWindows7() then begin
    if not FileExists(ExpandConstant('{sys}\api-ms-win-crt-runtime-l1-1-0.dll')) then begin
      MsgBox('Windows 7 缺少 Universal C Runtime。请先安装 KB2999226，并确认已安装 SHA-2 更新 KB4474419 与服务堆栈更新 KB4490628。', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  Rule: String;
begin
  if CurStep <> ssPostInstall then
    Exit;
  Rule := 'name="LanClassroomTeacherInbound"';
  Exec(ExpandConstant('{sys}\netsh.exe'),
    'advfirewall firewall delete rule ' + Rule, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  { 按程序放行覆盖可配置端口和公共网络，避免发现与主控被默认入站策略拦截。 }
  if not Exec(ExpandConstant('{sys}\netsh.exe'),
    'advfirewall firewall add rule ' + Rule +
    ' dir=in action=allow program="' + ExpandConstant('{app}\{#AppExeName}') +
    '" enable=yes profile=any', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    RaiseException('无法运行防火墙配置，请检查系统权限后重新安装。');
  if ResultCode <> 0 then
    RaiseException('添加教师端防火墙规则失败，请检查系统策略后重新安装。');
end;
