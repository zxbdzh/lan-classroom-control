import os
import sys
from typing import Optional

# 内置默认版本号，作为 version.txt 缺失时的兜底
APP_VERSION = "1.0.7"


def _get_base_dir() -> str:
    """获取程序根目录：打包后为 exe 所在目录，开发时为项目根。"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包后
        return os.path.dirname(sys.executable)
    # 开发模式：common/version.py 上级目录即项目根
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_version() -> str:
    """读取当前版本号，优先读取 version.txt，缺失时返回 APP_VERSION。"""
    base = _get_base_dir()
    path = os.path.join(base, "version.txt")
    try:
        with open(path, "r", encoding="utf-8") as f:
            v = f.read().strip()
            if v:
                return v
    except Exception:
        pass
    return APP_VERSION


def set_version(version: str) -> bool:
    """写入版本号到 version.txt（更新完成后由 updater 调用）。"""
    base = _get_base_dir()
    path = os.path.join(base, "version.txt")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(version.strip())
        return True
    except Exception:
        return False


def version_compare(v1: str, v2: str) -> int:
    """版本号比较：返回 1 表示 v1>v2，-1 表示 v1<v2，0 表示相等。

    支持任意长度的点分版本号，例如 1.0.7 vs 1.1。
    """
    try:
        parts1 = [int(x) for x in v1.split('.')]
        parts2 = [int(x) for x in v2.split('.')]
    except ValueError:
        # 非数字版本号直接字符串比较
        if v1 > v2:
            return 1
        if v1 < v2:
            return -1
        return 0

    max_len = max(len(parts1), len(parts2))
    parts1 += [0] * (max_len - len(parts1))
    parts2 += [0] * (max_len - len(parts2))
    for a, b in zip(parts1, parts2):
        if a > b:
            return 1
        if a < b:
            return -1
    return 0


def is_newer(candidate: str, current: Optional[str] = None) -> bool:
    """判断 candidate 是否比 current 新。current 为空时读取当前版本。"""
    if not candidate:
        return False
    if current is None:
        current = get_version()
    return version_compare(candidate, current) > 0
