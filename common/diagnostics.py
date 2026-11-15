"""Read-only frozen-startup probe usable inside Win7/Win10 lab machines."""

import importlib
import json
import platform
import sys

from common.ultravnc import find_binary


def startup_probe(role: str, output_path: str) -> None:
    report = {
        "role": role, "os": platform.platform(),
        "python": platform.python_version(), "frozen": bool(getattr(sys, "frozen", False)),
        "checks": {}, "scope": "startup_only_no_control_actions",
    }
    modules = ["psutil", "win32service", "win32event", "pythoncom", "win32com.client"]
    if role != "service":
        modules += ["PyQt5.QtCore", "PyQt5.QtWidgets", "pynput"]
    for name in modules:
        try:
            importlib.import_module(name)
            report["checks"][name] = "ok"
        except Exception as exc:
            report["checks"][name] = str(exc)
    binary = "vncviewer.exe" if role == "teacher" else "winvnc.exe"
    try:
        report["ultravnc"] = find_binary(binary)
    except Exception as exc:
        report["checks"]["ultravnc"] = str(exc)
    report["success"] = all(value == "ok" for value in report["checks"].values())
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    if not report["success"]:
        raise SystemExit(1)
