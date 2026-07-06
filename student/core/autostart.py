import os
import sys
import subprocess
from common.logger import get_logger

logger = get_logger("autostart")

# 注册表自启键
_AUTOSTART_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
_AUTOSTART_NAME = "LanClassroomStudent"
_TASK_NAME = "LanClassroomStudent"


def _quote_command(path: str) -> str:
    if path.startswith('"') and path.endswith('"'):
        return path
    return f'"{path}"'


def get_exe_path() -> str:
    """返回学生端启动命令。

    - 打包后：直接返回 exe 路径
    - 开发模式：返回 `pythonw student/main.py`（用当前解释器）
    """
    if getattr(sys, 'frozen', False):
        return _quote_command(sys.executable)
    # 开发模式：使用当前 Python 解释器运行 student/main.py
    python_exe = sys.executable
    main_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "main.py")
    return f'"{python_exe}" "{main_path}"'


def is_autostart_enabled() -> bool:
    """检查开机自启是否已开启。"""
    if sys.platform != "win32":
        logger.debug("Autostart only supported on Windows")
        return False
    if _is_task_enabled():
        _remove_registry_autostart()
        return True
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_KEY, 0, winreg.KEY_READ) as key:
            try:
                winreg.QueryValueEx(key, _AUTOSTART_NAME)
                return True
            except FileNotFoundError:
                return False
    except Exception as e:
        logger.warning(f"Check autostart failed: {e}")
        return False


def enable_autostart() -> bool:
    """开启开机自启。"""
    if sys.platform != "win32":
        logger.warning("Autostart only supported on Windows")
        return False
    if _enable_task_autostart():
        _remove_registry_autostart()
        return True
    try:
        import winreg
        exe_path = get_exe_path()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, _AUTOSTART_NAME, 0, winreg.REG_SZ, exe_path)
        logger.info(f"Autostart enabled: {exe_path}")
        return True
    except Exception as e:
        logger.error(f"Enable autostart failed: {e}")
        return False


def disable_autostart() -> bool:
    """关闭开机自启（学生端通常不允许调用）。"""
    if sys.platform != "win32":
        return False
    task_removed = _disable_task_autostart()
    registry_removed = False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            try:
                winreg.DeleteValue(key, _AUTOSTART_NAME)
            except FileNotFoundError:
                pass
        logger.info("Autostart disabled")
        registry_removed = True
    except Exception as e:
        logger.error(f"Disable autostart failed: {e}")
    return task_removed or registry_removed


def ensure_autostart() -> bool:
    """确保开机自启已开启（每次启动时调用，防止被用户删除）。"""
    if not is_autostart_enabled():
        return enable_autostart()
    return True


def _is_task_enabled() -> bool:
    try:
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", _TASK_NAME],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5
        )
        return result.returncode == 0
    except Exception as e:
        logger.debug(f"Check scheduled task failed: {e}")
        return False


def _enable_task_autostart() -> bool:
    try:
        exe_cmd = get_exe_path()
        result = subprocess.run(
            [
                "schtasks", "/Create",
                "/TN", _TASK_NAME,
                "/TR", exe_cmd,
                "/SC", "ONLOGON",
                "/RL", "HIGHEST",
                "/F"
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10
        )
        if result.returncode == 0:
            logger.info(f"Autostart scheduled task enabled: {exe_cmd}")
            return True
    except Exception as e:
        logger.debug(f"Enable scheduled task failed: {e}")
    return False


def _disable_task_autostart() -> bool:
    try:
        result = subprocess.run(
            ["schtasks", "/Delete", "/TN", _TASK_NAME, "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10
        )
        return result.returncode == 0
    except Exception as e:
        logger.debug(f"Disable scheduled task failed: {e}")
        return False


def _remove_registry_autostart() -> bool:
    removed = False
    try:
        import winreg
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(root, _AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE) as key:
                    try:
                        winreg.DeleteValue(key, _AUTOSTART_NAME)
                        removed = True
                    except FileNotFoundError:
                        pass
            except PermissionError:
                pass
            except FileNotFoundError:
                pass
    except Exception as e:
        logger.debug(f"Remove registry autostart failed: {e}")
    return removed
