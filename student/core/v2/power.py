import re
import socket
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, List, Optional, Tuple

from common.logger import get_logger

logger = get_logger("student_v2_power")


class PowerAction(str, Enum):
    SHUTDOWN = "shutdown"
    RESTART = "restart"
    LOGOFF = "logoff"


@dataclass(frozen=True)
class PowerResult:
    action: PowerAction
    success: bool
    message: str
    return_code: Optional[int] = None


class PowerController:
    """Run Windows power commands only after explicit runtime enablement."""

    _COMMANDS = {
        PowerAction.SHUTDOWN: ["shutdown.exe", "/s", "/t", "0"],
        PowerAction.RESTART: ["shutdown.exe", "/r", "/t", "0"],
    }

    def __init__(
        self,
        allow_execution: bool = False,
        runner: Callable[..., Any] = subprocess.run,
        platform: Optional[str] = None,
        session_api: Any = None,
    ) -> None:
        self._allow_execution = bool(allow_execution)
        self._runner = runner
        self._platform = platform if platform is not None else sys.platform
        self._session_api = session_api

    def execute(self, action: PowerAction) -> PowerResult:
        try:
            action = PowerAction(action)
        except (TypeError, ValueError):
            raise ValueError(f"unsupported power action: {action}")

        if not self._allow_execution:
            return PowerResult(action, False, "power execution is disabled")
        if self._platform != "win32":
            return PowerResult(action, False, "power control is only supported on Windows")

        try:
            if action == PowerAction.LOGOFF:
                return self._logoff_session()
            command = list(self._COMMANDS[action])
            kwargs = {
                "capture_output": True,
                "text": True,
                "timeout": 10,
                "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
            }
            result = self._runner(command, **kwargs)
            return_code = int(getattr(result, "returncode", 1))
            if return_code == 0:
                return PowerResult(action, True, "power command accepted", return_code)
            error = str(getattr(result, "stderr", "") or "").strip()
            return PowerResult(
                action,
                False,
                error or "power command failed",
                return_code,
            )
        except Exception as exc:
            logger.warning(f"Power command {action.value} failed: {exc}")
            return PowerResult(action, False, str(exc))

    def _logoff_session(self) -> PowerResult:
        session_api = self._session_api
        if session_api is None:
            import win32ts
            session_api = win32ts
        from .session_launcher import _active_session_id

        session_id = _active_session_id(session_api)
        if session_id is None or session_id <= 0:
            return PowerResult(PowerAction.LOGOFF, False, "no active desktop session")
        # 服务运行在会话 0，shutdown /l 只作用于调用会话；必须显式定位学生桌面。
        session_api.WTSLogoffSession(None, session_id, False)
        return PowerResult(PowerAction.LOGOFF, True, "user session logoff accepted", 0)

    def shutdown(self) -> PowerResult:
        return self.execute(PowerAction.SHUTDOWN)

    def restart(self) -> PowerResult:
        return self.execute(PowerAction.RESTART)

    def logoff(self) -> PowerResult:
        return self.execute(PowerAction.LOGOFF)


def build_magic_packet(mac_address: str) -> bytes:
    compact = re.sub(r"[:-]", "", (mac_address or "").strip())
    if not re.fullmatch(r"[0-9A-Fa-f]{12}", compact):
        raise ValueError("MAC address must contain exactly 12 hexadecimal digits")
    hardware_address = bytes.fromhex(compact)
    return b"\xff" * 6 + hardware_address * 16


@dataclass(frozen=True)
class WakeOnLanResult:
    success: bool
    packets_sent: int
    destination: Tuple[str, int]
    error: Optional[str] = None


class WakeOnLanSender:
    def __init__(self, socket_factory: Callable[..., Any] = socket.socket) -> None:
        self._socket_factory = socket_factory

    def send(
        self,
        mac_address: str,
        broadcast_address: str = "255.255.255.255",
        port: int = 9,
        repetitions: int = 3,
    ) -> WakeOnLanResult:
        if not 1 <= int(port) <= 65535:
            raise ValueError("port must be between 1 and 65535")
        if int(repetitions) <= 0:
            raise ValueError("repetitions must be positive")
        packet = build_magic_packet(mac_address)
        destination = (broadcast_address, int(port))
        sender = None
        sent = 0
        try:
            sender = self._socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
            sender.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            for _ in range(int(repetitions)):
                sender.sendto(packet, destination)
                sent += 1
            return WakeOnLanResult(True, sent, destination)
        except Exception as exc:
            logger.warning(f"Wake-on-LAN send failed: {exc}")
            return WakeOnLanResult(False, sent, destination, str(exc))
        finally:
            if sender is not None:
                try:
                    sender.close()
                except Exception:
                    pass
