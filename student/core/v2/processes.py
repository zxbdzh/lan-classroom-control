import math
import os
import sys
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, List, Optional, Set, Tuple

from common.logger import get_logger
from .storage import normalize_executable_name, normalize_windows_path

logger = get_logger("student_v2_processes")


DEFAULT_PROTECTED_PROCESS_NAMES = frozenset({
    "system",
    "system idle process",
    "registry",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "services.exe",
    "lsass.exe",
    "winlogon.exe",
    "svchost.exe",
    "fontdrvhost.exe",
    "dwm.exe",
    "explorer.exe",
    "lanclassroom-student.exe",
    "lanclassroom-student-service.exe",
    "lanclassroom-student-agent.exe",
    "winvnc.exe",
})


class ProcessTerminationStatus(str, Enum):
    TERMINATED = "terminated"
    NOT_FOUND = "not_found"
    STALE_PROCESS = "stale_process"
    PROTECTED = "protected"
    ACCESS_DENIED = "access_denied"
    TIMEOUT = "timeout"
    FAILED = "failed"


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    name: str
    executable: Optional[str]
    creation_time: float
    username: Optional[str]
    protected: bool = False
    protection_reason: Optional[str] = None


@dataclass(frozen=True)
class ProcessTerminationResult:
    pid: int
    status: ProcessTerminationStatus
    message: str
    forced: bool = False

    @property
    def success(self) -> bool:
        return self.status == ProcessTerminationStatus.TERMINATED


class ProcessController:
    """Inspect and terminate a specific process instance safely."""

    def __init__(
        self,
        process_provider: Any = None,
        own_pid: Optional[int] = None,
        protected_names: Optional[Iterable[str]] = None,
        own_executable: Optional[str] = None,
        creation_time_tolerance: float = 0.01,
    ) -> None:
        if process_provider is None:
            import psutil
            process_provider = psutil
        self._provider = process_provider
        self._own_pid = int(own_pid if own_pid is not None else os.getpid())
        names = DEFAULT_PROTECTED_PROCESS_NAMES if protected_names is None else protected_names
        self._protected_names: Set[str] = {
            normalize_executable_name(name) for name in names
        }
        self._own_executable = (
            normalize_windows_path(own_executable) if own_executable else None
        )
        self._creation_time_tolerance = float(creation_time_tolerance)
        self._lock = threading.RLock()

    def list_processes(self) -> List[ProcessInfo]:
        processes: List[ProcessInfo] = []
        attributes = ["pid", "name", "exe", "create_time", "username"]
        try:
            iterator = self._provider.process_iter(attributes)
        except Exception as exc:
            logger.warning(f"Cannot enumerate processes: {exc}")
            return processes

        for process in iterator:
            try:
                info = getattr(process, "info", {}) or {}
                pid = int(info.get("pid", getattr(process, "pid", 0)))
                name = str(info.get("name") or "")
                executable = info.get("exe") or None
                creation_time = float(info.get("create_time") or process.create_time())
                username = info.get("username") or None
                protected, reason = self._protection_for(pid, name, executable)
                processes.append(ProcessInfo(
                    pid=pid,
                    name=name,
                    executable=str(executable) if executable else None,
                    creation_time=creation_time,
                    username=str(username) if username else None,
                    protected=protected,
                    protection_reason=reason,
                ))
            except Exception as exc:
                if not self._is_provider_exception(exc, "NoSuchProcess"):
                    logger.debug(f"Cannot inspect process: {exc}")
        return processes

    def terminate_process(
        self,
        pid: int,
        expected_creation_time: float,
        timeout: float = 3.0,
        force: bool = False,
    ) -> ProcessTerminationResult:
        pid = int(pid)
        if pid <= 0:
            return ProcessTerminationResult(
                pid, ProcessTerminationStatus.PROTECTED, "invalid or system process ID"
            )

        with self._lock:
            try:
                process = self._provider.Process(pid)
                actual_creation_time = float(process.create_time())
            except Exception as exc:
                return self._failure_from_exception(pid, exc, "open process")

            if not math.isfinite(float(expected_creation_time)) or not math.isclose(
                actual_creation_time,
                float(expected_creation_time),
                rel_tol=0.0,
                abs_tol=self._creation_time_tolerance,
            ):
                return ProcessTerminationResult(
                    pid,
                    ProcessTerminationStatus.STALE_PROCESS,
                    "PID now belongs to a different process instance",
                )

            try:
                name = str(process.name() or "")
            except Exception:
                name = ""
            try:
                executable = process.exe() or None
            except Exception:
                executable = None

            protected, reason = self._protection_for(pid, name, executable)
            if protected:
                return ProcessTerminationResult(
                    pid,
                    ProcessTerminationStatus.PROTECTED,
                    reason or "protected process",
                )

            try:
                process.terminate()
                process.wait(timeout=float(timeout))
                return ProcessTerminationResult(
                    pid,
                    ProcessTerminationStatus.TERMINATED,
                    "process terminated",
                )
            except Exception as exc:
                if self._is_provider_exception(exc, "TimeoutExpired"):
                    if not force:
                        return ProcessTerminationResult(
                            pid,
                            ProcessTerminationStatus.TIMEOUT,
                            "process did not exit before timeout",
                        )
                    try:
                        process.kill()
                        process.wait(timeout=float(timeout))
                        return ProcessTerminationResult(
                            pid,
                            ProcessTerminationStatus.TERMINATED,
                            "process forcefully terminated",
                            forced=True,
                        )
                    except Exception as kill_exc:
                        return self._failure_from_exception(pid, kill_exc, "kill process")
                return self._failure_from_exception(pid, exc, "terminate process")

    def _protection_for(
        self,
        pid: int,
        name: str,
        executable: Optional[str],
    ) -> Tuple[bool, Optional[str]]:
        if pid <= 4:
            return True, "Windows system process"
        if pid == self._own_pid:
            return True, "student service process"
        if name:
            try:
                if normalize_executable_name(name) in self._protected_names:
                    return True, "protected Windows or classroom process"
            except ValueError:
                pass
        if executable and self._own_executable:
            try:
                if normalize_windows_path(executable) == self._own_executable:
                    return True, "student service executable"
            except ValueError:
                pass
        return False, None

    def _failure_from_exception(
        self,
        pid: int,
        exc: Exception,
        operation: str,
    ) -> ProcessTerminationResult:
        if self._is_provider_exception(exc, "NoSuchProcess"):
            status = ProcessTerminationStatus.NOT_FOUND
        elif self._is_provider_exception(exc, "AccessDenied"):
            status = ProcessTerminationStatus.ACCESS_DENIED
        elif self._is_provider_exception(exc, "TimeoutExpired"):
            status = ProcessTerminationStatus.TIMEOUT
        else:
            status = ProcessTerminationStatus.FAILED
            logger.warning(f"Cannot {operation} {pid}: {exc}")
        return ProcessTerminationResult(pid, status, f"cannot {operation}: {exc}")

    def _is_provider_exception(self, exc: Exception, name: str) -> bool:
        exception_type = getattr(self._provider, name, None)
        return exception_type is not None and isinstance(exc, exception_type)
