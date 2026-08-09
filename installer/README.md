# 安装器

v2 使用 Inno Setup 6 生成两个独立安装包：

- `LanClassroom-Teacher-Setup.exe`：教师控制台。
- `LanClassroom-Student-Setup.exe`：学生 Windows 服务和隐藏会话代理。

学生安装器会注册 `LanClassroomStudentV2` 自动服务，并在用户登录时启动 `LanClassroom-Student-Agent.exe`。升级 v1 时会停止旧客户端并清理 `LanClassroomStudent` 计划任务和旧 Run 项；原配置中的设备 ID 会迁移到 v2 SQLite 状态库。

Windows 7 必须先安装 KB4490628、KB4474419 和 KB2999226。安装器检测不到 Universal C Runtime 时会拒绝继续。

## 本地构建

```powershell
# Win7 发布必须使用官方 CPython 3.8.10，而不是来源不明的 3.8 构建。
uv sync --locked --python C:\Python38\python.exe --group build

uv run --locked pyinstaller --noconfirm --onedir --windowed `
  --name LanClassroom-Teacher teacher/main.py

uv run --locked pyinstaller --noconfirm --onedir `
  --name LanClassroom-Student-Service `
  --hidden-import win32timezone --hidden-import servicemanager `
  --hidden-import win32service --hidden-import win32serviceutil `
  --hidden-import win32pipe --hidden-import win32file `
  --hidden-import win32security --hidden-import pywintypes `
  --hidden-import win32ts --hidden-import win32process `
  --hidden-import win32profile --hidden-import psutil `
  student/service.py

uv run --locked pyinstaller --noconfirm --onedir --windowed `
  --name LanClassroom-Student-Agent `
  --hidden-import win32pipe --hidden-import win32file `
  --hidden-import win32security --hidden-import pywintypes `
  student/agent.py

iscc installer\teacher.iss
iscc installer\student.iss
```

完整隐藏导入和资源参数以 [.github/workflows/release.yml](../.github/workflows/release.yml) 为准。
