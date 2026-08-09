#define AppName "LanClassroom Teacher"
#define AppNameZh "局域网机房控制系统教师端"
#ifndef AppVersion
  #define AppVersion "2.0.0"
#endif
#define AppPublisher "LanClassroom"
#define AppExeName "LanClassroom-Teacher.exe"

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
Source: "..\dist\LanClassroom-Teacher\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppNameZh}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{commondesktop}\{#AppNameZh}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"

[Run]
Filename: "{app}\{#AppExeName}"; Description: "启动教师端"; Flags: nowait postinstall skipifsilent

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
