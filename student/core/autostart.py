import os
import sys
from common.logger import get_logger

logger = get_logger("autostart")

# 注册表自启键
_AUTOSTART_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
_AUTOSTART_NAME = "LanClassroomStudent"


def get_exe_path() -> str:
    """返回学生端启动命令。

    - 打包后：直接返回 exe 路径
    - 开发模式：返回 `pythonw student/main.py`（用当前解释器）
    """
    if getattr(sys, 'frozen', False):
        return sys.executable
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
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            try:
                winreg.DeleteValue(key, _AUTOSTART_NAME)
            except FileNotFoundError:
                pass
        logger.info("Autostart disabled")
        return True
    except Exception as e:
        logger.error(f"Disable autostart failed: {e}")
        return False


def ensure_autostart() -> bool:
    """确保开机自启已开启（每次启动时调用，防止被用户删除）。"""
    if not is_autostart_enabled():
        return enable_autostart()
    return True
