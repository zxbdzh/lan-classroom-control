from pathlib import Path
import os
import sys

repository = Path(SPEC).resolve().parent.parent
sys.path.insert(0, str(repository))
from scripts.check_pe_compat import exclude_host_ucrt

build_name = os.environ["CLASSROOM_FREEZE_ROLE"]
roles = {
    "LanClassroom-Teacher": ("teacher/main.py", False),
    "LanClassroom-Student-Service": ("student/service.py", True),
    "LanClassroom-Student-Agent": ("student/agent.py", False),
}
entry, console = roles[build_name]
analysis = Analysis(
    [str(repository / entry)],
    pathex=[str(repository)],
    binaries=[],
    datas=[
        (str(repository / "version.txt"), "."),
        (str(repository / "common/update_public_key.pem"), "common"),
    ],
    hiddenimports=[
        "win32timezone", "servicemanager", "win32service", "win32serviceutil",
        "win32pipe", "win32file", "win32security", "pywintypes", "win32ts",
        "win32process", "win32profile", "win32event", "win32com.client",
        "pythoncom", "psutil", "pynput", "mss", "PIL",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=None,
    noarchive=False,
)
# Win7 使用安装器预检过的系统 UCRT，不能随包加载构建宿主的新版系统 DLL。
analysis.binaries = exclude_host_ucrt(analysis.binaries)
archive = PYZ(analysis.pure, analysis.zipped_data, cipher=None)
executable = EXE(
    archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name=build_name,
    debug=False,
    strip=False,
    upx=False,
    console=console,
)
collection = COLLECT(
    executable,
    analysis.binaries,
    analysis.zipfiles,
    analysis.datas,
    strip=False,
    upx=False,
    name=build_name,
)
