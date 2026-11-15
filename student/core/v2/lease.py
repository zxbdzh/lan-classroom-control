import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple, Union

from common.logger import get_logger

logger = get_logger("student_v2_lease")


class TemporaryControlKind(str, Enum):
    BROADCAST = "broadcast"
    SILENCE = "silence"
    INPUT = "input"
    INSPECTION = "inspection"
    REMOTE_CONTROL = "remote_control"
    INTERNET_BLOCK = "internet_block"
    USB_STORAGE_BLOCK = "usb_storage_block"


@dataclass(frozen=True)
class LeaseRestoreResult:
    control: TemporaryControlKind
    success: bool
    error: Optional[str] = None


class TemporaryControlLease:
    """Fail-safe lease for temporary classroom controls.

    Only values from :class:`TemporaryControlKind` can be registered. Permanent
    policy, especially the process blacklist, therefore cannot be removed by a
    heartbeat timeout.
    """

    def __init__(
        self,
        timeout: float = 10.0,
        check_interval: float = 0.25,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if check_interval <= 0:
            raise ValueError("check_interval must be positive")
        self.timeout = float(timeout)
        self._check_interval = float(check_interval)
        self._clock = clock
        self._active: Dict[TemporaryControlKind, Callable[[], object]] = {}
        self._deadline: Optional[float] = None
        self._lock = threading.RLock()
        self._restore_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def activate(
        self,
        control: Union[TemporaryControlKind, str],
        restore: Callable[[], object],
    ) -> None:
        kind = self._coerce_control(control)
        if not callable(restore):
            raise TypeError("restore must be callable")
        with self._lock:
            self._active[kind] = restore
            self._deadline = self._clock() + self.timeout

    def deactivate(self, control: Union[TemporaryControlKind, str]) -> bool:
        kind = self._coerce_control(control)
        with self._lock:
            removed = self._active.pop(kind, None) is not None
            if not self._active:
                self._deadline = None
            return removed

    def renew(self) -> Optional[float]:
        with self._lock:
            if not self._active:
                self._deadline = None
                return None
            self._deadline = self._clock() + self.timeout
            return self._deadline

    def check_expired(self) -> List[LeaseRestoreResult]:
        with self._lock:
            if (
                not self._active
                or self._deadline is None
                or self._clock() < self._deadline
            ):
                return []
            snapshot = list(self._active.items())
        return self._restore_snapshot(snapshot)

    def restore_all(self) -> List[LeaseRestoreResult]:
        with self._lock:
            snapshot = list(self._active.items())
        return self._restore_snapshot(snapshot)

    def active_controls(self) -> Tuple[TemporaryControlKind, ...]:
        with self._lock:
            return tuple(self._active.keys())

    @property
    def deadline(self) -> Optional[float]:
        with self._lock:
            return self._deadline

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="student-control-lease",
                daemon=True,
            )
            self._thread.start()

    def stop(self, restore: bool = True, timeout: float = 3.0) -> None:
        with self._lock:
            thread = self._thread
            self._thread = None
            self._stop_event.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(timeout)))
        if restore:
            self.restore_all()

    def _restore_snapshot(
        self,
        snapshot: List[Tuple[TemporaryControlKind, Callable[[], object]]],
    ) -> List[LeaseRestoreResult]:
        with self._restore_lock:
            with self._lock:
                current = [
                    (kind, restore)
                    for kind, restore in snapshot
                    if self._active.get(kind) is restore
                ]

            results: List[LeaseRestoreResult] = []
            successful: List[Tuple[TemporaryControlKind, Callable[[], object]]] = []
            for kind, restore in current:
                try:
                    value = restore()
                    success = value is not False
                    error = None if success else "restore callback returned false"
                except Exception as exc:
                    success = False
                    error = str(exc)
                    logger.warning(f"Failed to restore {kind.value}: {exc}")
                results.append(LeaseRestoreResult(kind, success, error))
                if success:
                    successful.append((kind, restore))

            with self._lock:
                for kind, restore in successful:
                    if self._active.get(kind) is restore:
                        self._active.pop(kind, None)
                if self._active:
                    self._deadline = self._clock() + min(self._check_interval, 1.0)
                else:
                    self._deadline = None
            return results

    def _run(self) -> None:
        while not self._stop_event.wait(self._check_interval):
            self.check_expired()

    @staticmethod
    def _coerce_control(
        control: Union[TemporaryControlKind, str],
    ) -> TemporaryControlKind:
        try:
            return TemporaryControlKind(control)
        except (TypeError, ValueError):
            raise ValueError(f"not a temporary control: {control}")
