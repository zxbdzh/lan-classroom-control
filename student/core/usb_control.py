"""Windows 可移动磁盘访问策略；不会禁用 USB 控制器或其他 USB 设备。

使用 RemovableStorage.admx 的磁盘类 Deny_Read/Write/Execute，而非 USBSTOR
服务启动项。返回成功仅表示策略落盘并回读通过，不代表已卸载已挂载磁盘。
微软说明访问权限变更可能直到系统重启才生效；本模块不强制重启/弹出设备，
也不保证覆盖被识别为固定磁盘的 USB 设备、MTP/WPD 或域策略的后续覆盖。
策略映射与重启条件：
https://learn.microsoft.com/en-us/windows/client-management/mdm/policy-csp-admx-removablestorage
"""

import ctypes
import json
import os
import sys
import threading
from contextlib import contextmanager
from typing import Optional

from common.logger import get_logger

try:
    import winreg
except ImportError:  # 非 Windows 允许导入，但不能伪报策略应用成功。
    winreg = None

logger = get_logger("usb_control")

POLICY_KEY = (
    r"SOFTWARE\Policies\Microsoft\Windows\RemovableStorageDevices"
    r"\{53f5630d-b6bf-11d0-94f2-00a0c91efb8b}"
)
POLICY_VALUES = ("Deny_Read", "Deny_Write", "Deny_Execute")
POLICY_NOTICE = (
    "仅应用可移动磁盘读写及执行限制，不禁用键鼠、Hub 或网卡；"
    "策略及恢复可能需重插设备或重启系统才生效，不保证即时封禁已挂载磁盘。"
)
_STATE_OWNER = "LanClassroom.UsbStorageController"


class UsbStorageController:
    """is_blocked 表示本项目仍持有待恢复策略，不代表设备即时封禁状态。"""

    POLICY_NOTICE = POLICY_NOTICE

    def __init__(self, state_file: Optional[str] = None) -> None:
        self._lock = threading.RLock()
        self._blocked = False
        self._baseline: Optional[dict] = None
        self.last_error = ""
        self._state_file = os.path.abspath(
            os.fspath(state_file) if state_file is not None else self._default_state_file()
        )

    @staticmethod
    def _default_state_file() -> str:
        program_data = os.environ.get("ProgramData") or os.path.join(
            os.environ.get("SystemDrive", "C:"), os.sep, "ProgramData"
        )
        return os.path.join(program_data, "LanClassroom", "usb_control_state.json")

    def block_storage(self) -> bool:
        with self._lock:
            self.last_error = ""
            if not self._supported():
                return False
            try:
                with self._policy_mutex():
                    state = self._load_state()
                    if state is None:
                        if self._blocked:
                            self._reconcile_missing_baseline()
                        key_exists, values = self._read_policy()
                        state = {
                            "owner": _STATE_OWNER, "version": 1, "registry_view": 64,
                            "key_existed": key_exists, "values": values,
                        }
                        self._validate_state(state)
                        os.makedirs(os.path.dirname(self._state_file), exist_ok=True)
                        try:
                            # 独占创建，重复命令或新实例不得用已封禁值覆盖原始基线。
                            with open(self._state_file, "x", encoding="utf-8") as stream:
                                json.dump(state, stream, ensure_ascii=False)
                                stream.flush()
                                os.fsync(stream.fileno())
                        except FileExistsError:
                            state = self._load_state()
                            if state is None:
                                raise OSError("USB 策略备份在读取前消失")
                    self._blocked = True
                    self._cache_durable_baseline(state)
                    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, POLICY_KEY, 0,
                                            self._access(write=True)) as key:
                        for name in POLICY_VALUES:
                            current = self._read_value(key, name)
                            if current != {"exists": True, "type": winreg.REG_DWORD, "value": 1}:
                                winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 1)
                        winreg.FlushKey(key)
                    _, applied = self._read_policy()
                    if any(applied[name] != {"exists": True, "type": winreg.REG_DWORD,
                                             "value": 1} for name in POLICY_VALUES):
                        raise OSError("USB 存储限制回读验证失败")
                    logger.info("Removable disk access policy applied; %s", POLICY_NOTICE)
                    return True
            except Exception as exc:
                # 哪怕只写成功一个值，也保留完整基线供租约/watchdog 重试恢复。
                self._blocked = self._blocked or os.path.lexists(self._state_file)
                return self._fail("应用 USB 存储策略失败", exc)

    def restore_storage(self) -> bool:
        with self._lock:
            self.last_error = ""
            try:
                with self._policy_mutex():
                    state = self._load_state()
                    if state is None:
                        if self._blocked:
                            self._reconcile_missing_baseline()
                        # 无备份的恢复在任何平台都是安全 no-op，不拖累其他控制的恢复链。
                        return True
                    if not self._supported():
                        return False
                    self._blocked = True
                    self._cache_durable_baseline(state)
                    key_exists, current = self._read_policy()
                    originals = state["values"]
                    errors = []
                    if key_exists or any(value["exists"] for value in originals.values()):
                        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, POLICY_KEY, 0,
                                                self._access(write=True)) as key:
                            for name in POLICY_VALUES:
                                try:
                                    original = originals[name]
                                    if current[name] == original:
                                        continue
                                    if original["exists"]:
                                        winreg.SetValueEx(key, name, 0, original["type"],
                                                          original["value"])
                                    else:
                                        try:
                                            winreg.DeleteValue(key, name)
                                        except FileNotFoundError:
                                            pass
                                except OSError as exc:
                                    errors.append("{}: {}".format(name, exc))
                            try:
                                winreg.FlushKey(key)
                            except OSError as exc:
                                errors.append(str(exc))
                    if errors:
                        raise OSError("; ".join(errors))
                    _, restored = self._read_policy()
                    if restored != originals:
                        raise OSError("USB 存储策略恢复回读验证失败")
                    # 只清理由本项目创建且仍为空的磁盘子键，不递归删除其他程序的策略。
                    if not state["key_existed"]:
                        self._remove_empty_key()
                    os.remove(self._state_file)
                    self._blocked = False
                    self._baseline = None
                    logger.info("Original removable disk policy restored")
                    return True
            except Exception as exc:
                self._blocked = self._blocked or os.path.lexists(self._state_file)
                return self._fail("恢复 USB 存储策略失败", exc)

    def restore_stale_policy(self) -> bool:
        """进程重启后也仅按本项目持久备份恢复，不以系统默认值代替原值。"""
        with self._lock:
            return self.restore_storage()

    def is_blocked(self) -> bool:
        with self._lock:
            return self._blocked or os.path.lexists(self._state_file)

    def _cache_durable_baseline(self, state: dict) -> None:
        # 恢复进程也必须先确认备份可持久恢复，不能等到部分恢复失败后才缓存。
        self._baseline = None
        with open(self._state_file, "r+", encoding="utf-8") as stream:
            saved = json.load(stream)
            self._validate_state(saved)
            if saved != state:
                raise OSError("USB 策略备份在校验后发生变化")
            os.fsync(stream.fileno())
        self._baseline = {name: dict(original) for name, original in state["values"].items()}

    def _reconcile_missing_baseline(self) -> None:
        # watchdog 可能已在其他实例恢复；只有已知原值与系统逐项相同才确认完成，
        # 文件消失、内存标志或“当前未封禁”本身都不是恢复成功的证据。
        if self._baseline is None or not self._supported():
            raise OSError("USB 策略原始备份丢失且无可核实基线，拒绝猜测恢复值")
        _, current = self._read_policy()
        if current != self._baseline:
            raise OSError("USB 策略原始备份丢失且当前值不等于已知原值，拒绝猜测恢复值")
        self._blocked = False
        self._baseline = None

    def _supported(self) -> bool:
        if sys.platform != "win32" or winreg is None:
            self.last_error = "USB 存储管控仅支持 Windows，当前平台不支持"
            return False
        return True

    @staticmethod
    def _kernel32():
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # 显式声明 HANDLE，避免 64 位 Python 的默认 c_int 截断内核句柄。
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        for name in ("ReleaseMutex", "CloseHandle"):
            function = getattr(kernel32, name)
            function.argtypes = [wintypes.HANDLE]
            function.restype = wintypes.BOOL
        return kernel32

    @contextmanager
    def _policy_mutex(self):
        if sys.platform != "win32":
            yield
            return
        kernel32 = self._kernel32()
        # 服务与 watchdog 跨会话共享此锁，避免恢复删备份时另一进程重新封禁。
        handle = kernel32.CreateMutexW(None, False, r"Global\LanClassroomUsbStoragePolicyV1")
        if not handle:
            raise OSError("无法创建 Windows USB 策略互斥锁")
        acquired = False
        try:
            result = kernel32.WaitForSingleObject(handle, 10000)
            if result not in (0, 0x80):
                raise OSError("等待 Windows USB 策略互斥锁失败（状态 {}）".format(result))
            # WAIT_ABANDONED 表示前一持有进程崩溃；仍只用磁盘基线恢复，不猜系统值。
            acquired = True
            yield
        finally:
            try:
                if acquired and not kernel32.ReleaseMutex(handle):
                    raise OSError("释放 Windows USB 策略互斥锁失败")
            finally:
                kernel32.CloseHandle(handle)

    @staticmethod
    def _access(write: bool = False) -> int:
        # 固定使用原生 64 位策略视图，避免 32 位学生端和 watchdog 操作不同位置；
        # 在 32 位 Windows 上该 WOW64 标志由系统忽略。
        access = winreg.KEY_QUERY_VALUE | winreg.KEY_WOW64_64KEY
        return access | winreg.KEY_SET_VALUE if write else access

    @staticmethod
    def _read_value(key, name: str) -> dict:
        try:
            value, value_type = winreg.QueryValueEx(key, name)
            return {"exists": True, "type": value_type, "value": value}
        except FileNotFoundError:
            return {"exists": False}

    def _read_policy(self) -> tuple:
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, POLICY_KEY, 0, self._access())
        except FileNotFoundError:
            return False, {name: {"exists": False} for name in POLICY_VALUES}
        with key:
            return True, {name: self._read_value(key, name) for name in POLICY_VALUES}

    def _load_state(self) -> Optional[dict]:
        try:
            with open(self._state_file, "r", encoding="utf-8") as stream:
                state = json.load(stream)
        except FileNotFoundError:
            return None
        self._validate_state(state)
        return state

    @staticmethod
    def _validate_state(state: dict) -> None:
        if (not isinstance(state, dict) or state.get("owner") != _STATE_OWNER
                or type(state.get("version")) is not int or state["version"] != 1
                or type(state.get("registry_view")) is not int or state["registry_view"] != 64
                or type(state.get("key_existed")) is not bool
                or not isinstance(state.get("values"), dict)
                or set(state["values"]) != set(POLICY_VALUES)):
            raise ValueError("USB 策略备份格式或归属无效，拒绝修改注册表")
        for original in state["values"].values():
            if not isinstance(original, dict) or type(original.get("exists")) is not bool:
                raise ValueError("USB 策略备份缺少原值状态")
            if original["exists"]:
                # 这些受支持策略必须是 DWORD；遇到异常类型时安全失败，不丢失原数据。
                if (set(original) != {"exists", "type", "value"}
                        or type(original["type"]) is not int or original["type"] != 4
                        or type(original["value"]) is not int
                        or not 0 <= original["value"] <= 0xFFFFFFFF):
                    raise ValueError("USB 策略原值不是有效 REG_DWORD，拒绝覆盖")
            elif set(original) != {"exists"}:
                raise ValueError("USB 策略备份的缺省值状态无效")
        if not state["key_existed"] and any(v["exists"] for v in state["values"].values()):
            raise ValueError("USB 策略备份的键与原值状态矛盾")

    def _remove_empty_key(self) -> None:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, POLICY_KEY, 0, self._access()) as key:
                subkeys, values, _ = winreg.QueryInfoKey(key)
            if not subkeys and not values:
                winreg.DeleteKeyEx(winreg.HKEY_LOCAL_MACHINE, POLICY_KEY,
                                    winreg.KEY_WOW64_64KEY, 0)
        except FileNotFoundError:
            pass

    def _fail(self, message: str, exc: Exception) -> bool:
        self.last_error = "{}: {}".format(message, exc)
        logger.error("%s", self.last_error)
        return False
