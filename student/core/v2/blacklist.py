import threading
from dataclasses import dataclass
from typing import List, Optional, Sequence

from common.logger import get_logger
from .processes import ProcessController, ProcessInfo, ProcessTerminationResult
from .storage import (
    ProcessBlacklistRule,
    StudentStateStore,
    normalize_executable_name,
    normalize_windows_path,
)

logger = get_logger("student_v2_blacklist")


class BlacklistMatcher:
    @staticmethod
    def matches(process: ProcessInfo, rule: ProcessBlacklistRule) -> bool:
        try:
            if normalize_executable_name(process.name) != normalize_executable_name(
                rule.executable_name
            ):
                return False
        except ValueError:
            return False

        if rule.full_path is None:
            return True
        if not process.executable:
            return False
        try:
            return normalize_windows_path(process.executable) == normalize_windows_path(
                rule.full_path
            )
        except ValueError:
            return False


@dataclass(frozen=True)
class EnforcementResult:
    rule: ProcessBlacklistRule
    process: ProcessInfo
    termination: ProcessTerminationResult


class BlacklistEnforcer:
    """Continuously enforces durable process policy from ``StudentStateStore``."""

    def __init__(
        self,
        store: StudentStateStore,
        process_controller: ProcessController,
        interval: float = 1.0,
    ) -> None:
        if interval <= 0:
            raise ValueError("interval must be positive")
        self._store = store
        self._process_controller = process_controller
        self._interval = float(interval)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._enforce_lock = threading.Lock()

    def enforce_once(self) -> List[EnforcementResult]:
        with self._enforce_lock:
            return self._enforce_once()

    def _enforce_once(self) -> List[EnforcementResult]:
        rules = self._store.list_blacklist_rules()
        if not rules:
            return []
        processes = self._process_controller.list_processes()
        results: List[EnforcementResult] = []
        for process in processes:
            if process.protected:
                continue
            rule = self._first_match(process, rules)
            if rule is None:
                continue
            termination = self._process_controller.terminate_process(
                process.pid,
                process.creation_time,
                timeout=0.25,
                force=True,
            )
            results.append(EnforcementResult(rule, process, termination))
            if not termination.success:
                logger.debug(
                    f"Blacklist could not terminate {process.pid}: {termination.status.value}"
                )
        return results

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="student-blacklist-enforcer",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        with self._lock:
            thread = self._thread
            self._thread = None
            self._stop_event.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(timeout)))

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.enforce_once()
            except Exception as exc:
                logger.warning(f"Blacklist enforcement failed: {exc}")
            self._stop_event.wait(self._interval)

    @staticmethod
    def _first_match(
        process: ProcessInfo,
        rules: Sequence[ProcessBlacklistRule],
    ) -> Optional[ProcessBlacklistRule]:
        for rule in rules:
            if BlacklistMatcher.matches(process, rule):
                return rule
        return None
