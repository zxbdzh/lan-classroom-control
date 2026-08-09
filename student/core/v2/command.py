import copy
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Dict, Mapping, Optional

from common.logger import get_logger

logger = get_logger("student_v2_command")


class OutcomePhase(str, Enum):
    RECEIVED = "received"
    RESULT = "result"


class OutcomeStatus(str, Enum):
    RECEIVED = "received"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class Command:
    command_id: str
    name: str
    params: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "params",
            MappingProxyType(copy.deepcopy(dict(self.params))),
        )


@dataclass(frozen=True)
class CommandOutcome:
    command_id: str
    name: str
    phase: OutcomePhase
    status: OutcomeStatus
    payload: Mapping[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    duplicate: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "payload",
            MappingProxyType(copy.deepcopy(dict(self.payload))),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command_id": self.command_id,
            "name": self.name,
            "phase": self.phase.value,
            "status": self.status.value,
            "payload": copy.deepcopy(dict(self.payload)),
            "error": self.error,
            "duplicate": self.duplicate,
        }


@dataclass(frozen=True)
class CommandHandlerResult:
    status: OutcomeStatus = OutcomeStatus.SUCCEEDED
    payload: Mapping[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "payload",
            MappingProxyType(copy.deepcopy(dict(self.payload))),
        )

    @classmethod
    def failed(
        cls,
        error: str,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> "CommandHandlerResult":
        return cls(OutcomeStatus.FAILED, payload or {}, error)


@dataclass(frozen=True)
class CommandExecution:
    received: CommandOutcome
    result: CommandOutcome


@dataclass
class _ExecutionEntry:
    command: Command
    event: threading.Event
    result: Optional[CommandOutcome] = None


class CommandExecutor:
    """Execute stable command IDs once and replay the cached result on retry."""

    def __init__(
        self,
        cache_size: int = 1024,
        duplicate_wait_timeout: float = 30.0,
    ) -> None:
        if cache_size <= 0:
            raise ValueError("cache_size must be positive")
        if duplicate_wait_timeout <= 0:
            raise ValueError("duplicate_wait_timeout must be positive")
        self._cache_size = int(cache_size)
        self._duplicate_wait_timeout = float(duplicate_wait_timeout)
        self._handlers: Dict[str, Callable[[Mapping[str, Any]], Any]] = {}
        self._entries = OrderedDict()
        self._lock = threading.RLock()

    def register_handler(
        self,
        name: str,
        handler: Callable[[Mapping[str, Any]], Any],
    ) -> None:
        normalized_name = self._normalize_name(name)
        if not callable(handler):
            raise TypeError("handler must be callable")
        with self._lock:
            self._handlers[normalized_name] = handler

    def execute(
        self,
        command: Command,
        on_received: Optional[Callable[[CommandOutcome], None]] = None,
    ) -> CommandExecution:
        normalized = self._normalize_command(command)
        with self._lock:
            entry = self._entries.get(normalized.command_id)
            first_execution = entry is None
            if first_execution:
                entry = _ExecutionEntry(normalized, threading.Event())
                self._entries[normalized.command_id] = entry
            else:
                self._entries.move_to_end(normalized.command_id)

        duplicate = not first_execution
        received = CommandOutcome(
            command_id=normalized.command_id,
            name=normalized.name,
            phase=OutcomePhase.RECEIVED,
            status=OutcomeStatus.RECEIVED,
            duplicate=duplicate,
        )
        if on_received is not None:
            try:
                on_received(received)
            except Exception as exc:
                logger.warning(f"Received callback failed: {exc}")

        if duplicate:
            if not self._same_command(entry.command, normalized):
                collision = CommandOutcome(
                    normalized.command_id,
                    normalized.name,
                    OutcomePhase.RESULT,
                    OutcomeStatus.FAILED,
                    error="command ID was reused with different content",
                    duplicate=True,
                )
                return CommandExecution(received, collision)
            if not entry.event.wait(self._duplicate_wait_timeout):
                timeout = CommandOutcome(
                    normalized.command_id,
                    normalized.name,
                    OutcomePhase.RESULT,
                    OutcomeStatus.FAILED,
                    error="original command is still running",
                    duplicate=True,
                )
                return CommandExecution(received, timeout)
            if entry.result is None:
                missing = CommandOutcome(
                    normalized.command_id,
                    normalized.name,
                    OutcomePhase.RESULT,
                    OutcomeStatus.FAILED,
                    error="original command completed without a result",
                    duplicate=True,
                )
                return CommandExecution(received, missing)
            return CommandExecution(received, self._as_duplicate(entry.result))

        result = self._run_handler(normalized)
        with self._lock:
            entry.result = result
            entry.event.set()
            self._trim_cache()
        return CommandExecution(received, result)

    def clear_completed(self) -> None:
        with self._lock:
            completed = [key for key, entry in self._entries.items() if entry.event.is_set()]
            for key in completed:
                self._entries.pop(key, None)

    def _run_handler(self, command: Command) -> CommandOutcome:
        with self._lock:
            handler = self._handlers.get(command.name)
        if handler is None:
            return CommandOutcome(
                command.command_id,
                command.name,
                OutcomePhase.RESULT,
                OutcomeStatus.UNSUPPORTED,
                error=f"unsupported command: {command.name}",
            )
        try:
            value = handler(copy.deepcopy(dict(command.params)))
            if isinstance(value, CommandHandlerResult):
                if value.status == OutcomeStatus.RECEIVED:
                    raise ValueError("handler result cannot have received status")
                status = value.status
                payload = copy.deepcopy(dict(value.payload))
                error = value.error
            elif isinstance(value, Mapping):
                status = OutcomeStatus.SUCCEEDED
                payload = copy.deepcopy(dict(value))
                error = None
            elif value is None:
                status = OutcomeStatus.SUCCEEDED
                payload = {}
                error = None
            else:
                status = OutcomeStatus.SUCCEEDED
                payload = {"value": value}
                error = None
            return CommandOutcome(
                command.command_id,
                command.name,
                OutcomePhase.RESULT,
                status,
                payload,
                error,
            )
        except Exception as exc:
            logger.warning(f"Command {command.name} failed: {exc}")
            return CommandOutcome(
                command.command_id,
                command.name,
                OutcomePhase.RESULT,
                OutcomeStatus.FAILED,
                error=str(exc),
            )

    def _trim_cache(self) -> None:
        while len(self._entries) > self._cache_size:
            removable = next(
                (key for key, entry in self._entries.items() if entry.event.is_set()),
                None,
            )
            if removable is None:
                break
            self._entries.pop(removable, None)

    @staticmethod
    def _normalize_command(command: Command) -> Command:
        if not isinstance(command, Command):
            raise TypeError("command must be a Command")
        command_id = (command.command_id or "").strip()
        if not command_id:
            raise ValueError("command_id must not be empty")
        name = CommandExecutor._normalize_name(command.name)
        if not isinstance(command.params, Mapping):
            raise TypeError("command params must be a mapping")
        return Command(command_id, name, copy.deepcopy(dict(command.params)))

    @staticmethod
    def _normalize_name(name: str) -> str:
        value = (name or "").strip().casefold()
        if not value:
            raise ValueError("command name must not be empty")
        return value

    @staticmethod
    def _same_command(left: Command, right: Command) -> bool:
        return left.name == right.name and dict(left.params) == dict(right.params)

    @staticmethod
    def _as_duplicate(outcome: CommandOutcome) -> CommandOutcome:
        return CommandOutcome(
            outcome.command_id,
            outcome.name,
            outcome.phase,
            outcome.status,
            copy.deepcopy(dict(outcome.payload)),
            outcome.error,
            duplicate=True,
        )
