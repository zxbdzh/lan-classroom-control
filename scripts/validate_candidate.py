"""Probe frozen programs on a private, never-activated Windows desktop."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import uuid


def validate(root: Path) -> None:
    import win32api
    import win32con
    import win32event
    import win32process
    import win32service
    desktop_name = "LanClassroomProbe_" + uuid.uuid4().hex
    desktop = win32service.CreateDesktop(
        desktop_name, 0, win32con.GENERIC_ALL, None
    )
    reports = root / "validation"
    reports.mkdir(parents=True, exist_ok=True)
    targets = {
        "teacher": root / "LanClassroom-Teacher/LanClassroom-Teacher.exe",
        "service": root / "Student/Service/LanClassroom-Student-Service.exe",
        "agent": root / "Student/Agent/LanClassroom-Student-Agent.exe",
    }
    try:
        for role, executable in targets.items():
            output = reports / f"{role}-startup.json"
            startup = win32process.STARTUPINFO()
            # 不调用 SwitchDesktop；即使启动失败弹出错误框，也不会占用用户桌面。
            startup.lpDesktop = "winsta0\\" + desktop_name
            process, thread, _pid, _tid = win32process.CreateProcess(
                str(executable),
                subprocess.list2cmdline([str(executable), "--diagnostics", str(output)]),
                None, None, False, win32con.CREATE_NO_WINDOW, None,
                str(executable.parent), startup,
            )
            try:
                if win32event.WaitForSingleObject(process, 20000) != win32con.WAIT_OBJECT_0:
                    win32process.TerminateProcess(process, 1)
                    win32event.WaitForSingleObject(process, 3000)
                    raise RuntimeError(f"{role}: startup probe timed out")
                code = win32process.GetExitCodeProcess(process)
                if code != 0 or not output.is_file():
                    raise RuntimeError(f"{role}: exit={code}, report={output.is_file()}")
                report = json.loads(output.read_text(encoding="utf-8"))
                if not report["success"]:
                    raise RuntimeError(f"{role}: {report['checks']}")
                print(json.dumps(report, ensure_ascii=False))
            finally:
                win32api.CloseHandle(thread)
                win32api.CloseHandle(process)
    finally:
        desktop.CloseDesktop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    validate(parser.parse_args().root.resolve())
