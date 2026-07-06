#define AppName "LanClassroom Student"
#define AppNameZh "局域网机房控制系统学生端"
#define AppVersion "1.0.8"
#define AppPublisher "LanClassroom"
#define AppExeName "LanClassroom-Student.exe"
#define InstallPassword GetEnv("LAN_CLASSROOM_INSTALL_PASSWORD")
#if InstallPassword == ""
  #define InstallPassword "yhfbcs"
#endif
#define UninstallPasswordValue GetEnv("LAN_CLASSROOM_UNINSTALL_PASSWORD")
#if UninstallPasswordValue == ""
  #define UninstallPasswordValue "yhfbcs"
#endif

[Setup]
AppId={{9A4B9ED0-FAD0-4D41-A7C6-7E4566C3124D}
AppName={#AppNameZh}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\LanClassroom\Student
DefaultGroupName=LanClassroom
DisableDirPage=no
DisableProgramGroupPage=no
AllowNoIcons=yes
AlwaysShowDirOnReadyPage=yes
AlwaysShowGroupOnReadyPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=LanClassroom-Student-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExeName}
CloseApplications=yes
RestartApplications=no
Password={#InstallPassword}
Encryption=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
SetupWindowTitle=安装 - %1
UninstallAppTitle=卸载
UninstallAppFullTitle=%1 卸载
ExitSetupTitle=退出安装
ExitSetupMessage=安装尚未完成。如果现在退出，程序将不会被安装。%n%n是否退出安装？
ButtonBack=< 上一步(&B)
ButtonNext=下一步(&N) >
ButtonInstall=安装(&I)
ButtonOK=确定
ButtonCancel=取消
ButtonYes=是(&Y)
ButtonNo=否(&N)
ButtonFinish=完成(&F)
ClickNext=单击“下一步”继续，或单击“取消”退出安装。
WelcomeLabel1=欢迎使用 [name] 安装向导
WelcomeLabel2=本向导将在你的计算机上安装 [name/ver]。%n%n建议继续前关闭其他应用程序。
WizardSelectDir=选择安装位置
SelectDirDesc=[name] 应该安装到哪里？
SelectDirLabel3=安装程序将把 [name] 安装到以下文件夹。
SelectDirBrowseLabel=如需安装到其他文件夹，请单击“浏览”。
DiskSpaceGBLabel=至少需要 [gb] GB 可用磁盘空间。
DiskSpaceMBLabel=至少需要 [mb] MB 可用磁盘空间。
CannotInstallToNetworkDrive=安装程序不能安装到网络驱动器。
CannotInstallToUNCPath=安装程序不能安装到 UNC 路径。
InvalidPath=必须输入包含盘符的完整路径，例如：C:\APP，或 UNC 路径，例如：\\server\share。
InvalidDrive=你选择的驱动器或 UNC 共享不存在或不可访问。请重新选择。
DiskSpaceWarningTitle=磁盘空间不足
DirNameTooLong=文件夹名称或路径过长。
InvalidDirName=文件夹名称无效。
BadDirName32=文件夹名称不能包含以下任何字符：%n%n%1
DirExistsTitle=文件夹已存在
DirExists=文件夹：%n%n%1%n%n已经存在。是否仍安装到该文件夹？
DirDoesntExistTitle=文件夹不存在
DirDoesntExist=文件夹：%n%n%1%n%n不存在。是否创建该文件夹？
WizardSelectProgramGroup=选择开始菜单文件夹
SelectStartMenuFolderDesc=安装程序应将程序快捷方式放到哪个开始菜单文件夹？
SelectStartMenuFolderLabel3=安装程序将在以下开始菜单文件夹中创建程序快捷方式。
SelectStartMenuFolderBrowseLabel=如需选择其他文件夹，请单击“浏览”。
MustEnterGroupName=必须输入文件夹名称。
GroupNameTooLong=文件夹名称或路径过长。
InvalidGroupName=文件夹名称无效。
BadGroupName=文件夹名称不能包含以下任何字符：%n%n%1
NoProgramGroupCheck2=不创建开始菜单文件夹(&D)
SelectTasksDesc=请选择要执行的附加任务。
SelectTasksLabel2=请选择安装 [name] 时要执行的附加任务，然后单击“下一步”。
WizardReady=准备安装
ReadyLabel1=安装程序已准备好开始安装 [name]。
ReadyLabel2a=单击“安装”继续；如需检查或更改设置，请单击“上一步”。
ReadyMemoDir=安装位置：
ReadyMemoGroup=开始菜单文件夹：
ReadyMemoTasks=附加任务：
WizardPreparing=正在准备安装
PreparingDesc=安装程序正在准备安装 [name]。
WizardInstalling=正在安装
InstallingLabel=请稍候，安装程序正在安装 [name]。
FinishedHeadingLabel=正在完成 [name] 安装向导
FinishedLabelNoIcons=安装程序已在你的计算机上安装 [name]。
FinishedLabel=安装程序已在你的计算机上安装 [name]。你可以通过安装的快捷方式启动应用程序。
FinishedRestartLabel=要完成 [name] 的安装，必须重新启动计算机。是否现在重新启动？
FinishedRestartMessage=要完成 [name] 的安装，必须重新启动计算机。%n%n是否现在重新启动？
ShowReadmeCheck=是，查看 README 文件
YesRadio=是，现在重新启动计算机(&Y)
NoRadio=否，稍后我自己重新启动(&N)
RunEntryExec=运行 %1
RunEntryShellExec=查看 %1
ChangeDiskTitle=安装程序需要下一张磁盘
SelectDiskLabel2=请插入磁盘 %1 并单击“确定”。%n%n如果此磁盘上的文件位于其他文件夹，请输入正确路径或单击“浏览”。
PathLabel=路径(&P)：
FileNotInDir2=文件“%1”不在“%2”中。请插入正确磁盘或选择其他文件夹。
SelectDirectoryLabel=请指定下一张磁盘的位置。
SetupAborted=安装未完成。%n%n请修正问题后重新运行安装程序。
StatusClosingApplications=正在关闭应用程序...
StatusCreateDirs=正在创建目录...
StatusExtractFiles=正在解压文件...
StatusCreateIcons=正在创建快捷方式...
StatusCreateIniEntries=正在创建 INI 项...
StatusCreateRegistryEntries=正在创建注册表项...
StatusRegisterFiles=正在注册文件...
StatusSavingUninstall=正在保存卸载信息...
StatusRunProgram=正在完成安装...
StatusRollback=正在回滚更改...
ErrorInternal2=内部错误：%1
ErrorFunctionFailedNoCode=%1 失败
ErrorFunctionFailed=%1 失败；错误代码 %2
ErrorFunctionFailedWithMessage=%1 失败；错误代码 %2。%n%3
ErrorExecutingProgram=无法执行文件：%n%1
ErrorRegOpenKey=打开注册表键时出错：%n%1\%2
ErrorRegCreateKey=创建注册表键时出错：%n%1\%2
ErrorRegWriteKey=写入注册表键时出错：%n%1\%2
ErrorIniEntry=在文件“%1”中创建 INI 项时出错。
SourceIsCorrupted=源文件已损坏
SourceDoesntExist=源文件“%1”不存在
ExistingFileReadOnly2=无法替换现有文件，因为它是只读的。
ExistingFileReadOnlyRetry=请移除只读属性后单击“重试”，或单击“忽略”跳过。
ExistingFileReadOnlyKeepExisting=保留现有文件
ErrorReadingExistingDest=读取现有文件时出错：
ErrorChangingAttr=更改现有文件属性时出错：
ErrorCreatingTemp=在目标目录创建文件时出错：
ErrorReadingSource=读取源文件时出错：
ErrorCopying=复制文件时出错：
ErrorReplacingExistingFile=替换现有文件时出错：
ErrorRestartReplace=重启替换失败：
ErrorRenamingTemp=重命名目标目录中的文件时出错：
ErrorRegisterServer=注册 DLL/OCX 时出错：%1
ErrorRegSvr32Failed=RegSvr32 失败，退出代码 %1
ErrorRegisterTypeLib=注册类型库时出错：%1
ErrorOpeningReadme=打开 README 文件时出错。
ErrorRestartingComputer=安装程序无法重新启动计算机。请手动重新启动。
UninstallNotFound=文件“%1”不存在，无法卸载。
UninstallOpenError=无法打开文件“%1”，无法卸载。
UninstallUnsupportedVer=卸载日志文件“%1”的格式不被此版本卸载程序识别，无法卸载。
UninstallUnknownEntry=卸载日志中遇到未知项（%1）。
ConfirmUninstall=确定要完全移除 %1 及其所有组件吗？
OnlyAdminCanUninstall=只有管理员才能卸载此安装。
UninstallStatusLabel=请稍候，正在从计算机中移除 %1。
UninstalledAll=%1 已成功从计算机中移除。
UninstalledMost=%1 卸载完成。%n%n部分项目无法移除，可手动删除。
UninstalledAndNeedsRestart=要完成 %1 的卸载，必须重新启动计算机。%n%n是否现在重新启动？
UninstallDataCorrupted=文件“%1”已损坏，无法卸载。
WizardUninstalling=卸载状态
StatusUninstalling=正在卸载 %1...
ShutdownBlockReasonInstallingApp=正在安装 %1。
ShutdownBlockReasonUninstallingApp=正在卸载 %1。

[Files]
Source: "..\dist\LanClassroom-Student\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式(&D)"; GroupDescription: "附加快捷方式："

[Icons]
Name: "{group}\{#AppNameZh}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{group}\卸载 {#AppNameZh}"; Filename: "{uninstallexe}"
Name: "{commondesktop}\{#AppNameZh}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "启动学生端"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/IM {#AppExeName} /F"; Flags: runhidden; RunOnceId: "StopStudentClient"

[UninstallDelete]
Type: files; Name: "{commonappdata}\LanClassroom\student_guard.ini"
Type: dirifempty; Name: "{commonappdata}\LanClassroom"

[Code]
const
  TaskName = 'LanClassroomStudent';
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';
  RunValueName = 'LanClassroomStudent';
  ExpectedUninstallPassword = '{#UninstallPasswordValue}';

function GuardConfigPath(): String;
begin
  Result := ExpandConstant('{commonappdata}\LanClassroom\student_guard.ini');
end;

function StudentExePath(): String;
begin
  Result := ExpandConstant('{app}\{#AppExeName}');
end;

procedure StopRunningStudent();
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'),
    '/IM "{#AppExeName}" /F', '', SW_HIDE,
    ewWaitUntilTerminated, ResultCode);
end;

function CreateStartupTask(): Boolean;
var
  ResultCode: Integer;
  Params: String;
begin
  Params := '/Create /TN "' + TaskName + '" /TR "\"' + StudentExePath() +
    '\"" /SC ONLOGON /RL HIGHEST /F';
  Result := Exec(ExpandConstant('{sys}\schtasks.exe'), Params, '',
    SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

procedure WriteRegistryFallback();
begin
  RegWriteStringValue(HKLM, RunKey, RunValueName, '"' + StudentExePath() + '"');
end;

procedure RemoveRegistryFallback();
begin
  RegDeleteValue(HKLM, RunKey, RunValueName);
  RegDeleteValue(HKCU, RunKey, RunValueName);
end;

procedure WriteGuardConfig();
begin
  ForceDirectories(ExtractFileDir(GuardConfigPath()));
  SetIniString('Guard', 'exit_password', '{#InstallPassword}', GuardConfigPath());
end;

procedure RemoveStartupEntries();
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\schtasks.exe'),
    '/Delete /TN "' + TaskName + '" /F', '', SW_HIDE,
    ewWaitUntilTerminated, ResultCode);
  RemoveRegistryFallback();
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopRunningStudent();
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    if not CreateStartupTask() then begin
      WriteRegistryFallback();
    end else begin
      RemoveRegistryFallback();
    end;
    WriteGuardConfig();
  end;
end;

function InitializeUninstall(): Boolean;
var
  Form: TSetupForm;
  PromptLabel: TNewStaticText;
  PasswordEdit: TPasswordEdit;
  OKButton: TNewButton;
  CancelButton: TNewButton;
begin
  Result := False;
  Form := CreateCustomForm(ScaleX(360), ScaleY(120), False, True);
  try
    Form.Caption := '卸载验证';

    PromptLabel := TNewStaticText.Create(Form);
    PromptLabel.Parent := Form;
    PromptLabel.Left := ScaleX(12);
    PromptLabel.Top := ScaleY(12);
    PromptLabel.Width := ScaleX(336);
    PromptLabel.Caption := '请输入卸载密码：';

    PasswordEdit := TPasswordEdit.Create(Form);
    PasswordEdit.Parent := Form;
    PasswordEdit.Left := ScaleX(12);
    PasswordEdit.Top := ScaleY(36);
    PasswordEdit.Width := ScaleX(336);

    OKButton := TNewButton.Create(Form);
    OKButton.Parent := Form;
    OKButton.Left := ScaleX(176);
    OKButton.Top := ScaleY(78);
    OKButton.Width := ScaleX(80);
    OKButton.Caption := '确定';
    OKButton.ModalResult := mrOk;

    CancelButton := TNewButton.Create(Form);
    CancelButton.Parent := Form;
    CancelButton.Left := ScaleX(268);
    CancelButton.Top := ScaleY(78);
    CancelButton.Width := ScaleX(80);
    CancelButton.Caption := '取消';
    CancelButton.ModalResult := mrCancel;

    Form.ActiveControl := PasswordEdit;

    if Form.ShowModal() = mrOk then begin
      if PasswordEdit.Text = ExpectedUninstallPassword then begin
        Result := True;
      end else begin
        MsgBox('卸载密码错误，已取消卸载。', mbError, MB_OK);
      end;
    end;
  finally
    Form.Free();
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then begin
    RemoveStartupEntries();
  end;
end;
