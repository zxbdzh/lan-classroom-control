# 学生端安装器

学生端安装器使用 Inno Setup 6 生成，提供：

- 安装密码
- 卸载密码
- 安装后开机自启
- 优先创建 Windows 计划任务并以最高权限运行，失败时回退到 Run 注册表自启

默认安装/卸载密码均为：

```text
LanClassroom@2026
```

本地或 CI 构建时可用环境变量覆盖：

```powershell
$env:LAN_CLASSROOM_INSTALL_PASSWORD="your-install-password"
$env:LAN_CLASSROOM_UNINSTALL_PASSWORD="your-uninstall-password"
iscc installer\student.iss
```

构建前需要先生成学生端 onedir：

```powershell
uv run --locked pyinstaller --noconfirm --onedir --windowed --name LanClassroom-Student student/main.py
```

输出文件：

```text
dist\installer\LanClassroom-Student-Setup.exe
```
