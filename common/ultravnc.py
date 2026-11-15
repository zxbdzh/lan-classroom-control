"""Paths and pinned version shared by the two UltraVNC adapters."""

import os
import sys

ULTRAVNC_VERSION = "1.8.3.2"
ULTRAVNC_PORT = 15900
ULTRAVNC_SERVICE = "LanClassroomVNC"


def find_binary(name: str, configured: str = "") -> str:
    if configured:
        candidates = [os.path.abspath(os.path.expandvars(configured))]
    else:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        executable_dir = os.path.dirname(sys.executable)
        candidates = [
            os.path.join(executable_dir, "UltraVNC", name),
            os.path.join(os.path.dirname(executable_dir), "UltraVNC", name),
            os.path.join(root, "third_party", "ultravnc", name),
        ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    raise RuntimeError(f"找不到 UltraVNC {name}，请安装包含 UltraVNC 的完整安装包")
