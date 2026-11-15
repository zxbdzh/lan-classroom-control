import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("LAN_CLASSROOM_DISABLE_SCREEN_MONITOR", "1")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(autouse=True)
def isolate_host_controls(monkeypatch, tmp_path):
    from common import config
    from student.core.net_control import NetController
    from student.core import autostart
    from student.core.v2 import storage
    instance = config.Config(str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "_config_instance", instance)
    monkeypatch.setattr(autostart, "ensure_autostart", lambda: True)
    monkeypatch.setattr(
        storage, "default_state_database_path", lambda: str(tmp_path / "student.db")
    )

    def forbidden_policy():
        raise AssertionError("单元测试必须使用防火墙替身；真实禁网仅允许在虚拟机验收")

    monkeypatch.setattr(NetController, "_windows_policy", staticmethod(forbidden_policy))
    if sys.platform == "win32":
        import ctypes
        monkeypatch.setattr(ctypes.windll.user32, "BlockInput", lambda enabled: True)
        monkeypatch.setattr(ctypes.windll.user32, "SetWindowPos", lambda *args: True)
