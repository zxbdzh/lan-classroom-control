import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional

from common.logger import get_logger
from .command import (
    Command,
    CommandExecution,
    CommandExecutor,
    CommandHandlerResult,
    CommandOutcome,
)
from .ipc import ChannelClosedError, MessageChannel
from .lease import TemporaryControlKind, TemporaryControlLease

logger = get_logger("student_v2_runtime")


@dataclass(frozen=True)
class AgentCallResult:
    success: bool
    payload: Mapping[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


class SessionAgentProxy:
    """Synchronous service-side proxy for a single desktop session agent."""

    def __init__(
        self,
        channel: MessageChannel,
        default_timeout: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if default_timeout <= 0:
            raise ValueError("default_timeout must be positive")
        self._channel = channel
        self._default_timeout = float(default_timeout)
        self._clock = clock
        self._lock = threading.Lock()

    def call(
        self,
        action: str,
        params: Optional[Mapping[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> AgentCallResult:
        action = (action or "").strip()
        if not action:
            raise ValueError("action must not be empty")
        request_id = str(uuid.uuid4())
        wait_timeout = self._default_timeout if timeout is None else float(timeout)
        if wait_timeout <= 0:
            raise ValueError("timeout must be positive")

        with self._lock:
            try:
                self._channel.send({
                    "version": 1,
                    "kind": "request",
                    "request_id": request_id,
                    "action": action,
                    "params": dict(params or {}),
                })
                deadline = self._clock() + wait_timeout
                while True:
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        return AgentCallResult(False, error="session agent timed out")
                    response = self._channel.receive(remaining)
                    if response is None:
                        return AgentCallResult(False, error="session agent timed out")
                    if (
                        response.get("kind") == "response"
                        and response.get("request_id") == request_id
                    ):
                        return AgentCallResult(
                            bool(response.get("success", False)),
                            dict(response.get("payload") or {}),
                            response.get("error") or None,
                        )
            except ChannelClosedError as exc:
                return AgentCallResult(False, error=str(exc))


class SessionAgentRuntime:
    """Desktop-session request loop with an explicit action allow-list."""

    def __init__(self, channel: MessageChannel, poll_interval: float = 0.1) -> None:
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        self._channel = channel
        self._poll_interval = float(poll_interval)
        self._handlers: Dict[str, Callable[[Mapping[str, Any]], Any]] = {}
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()

    def register_handler(
        self,
        action: str,
        handler: Callable[[Mapping[str, Any]], Any],
    ) -> None:
        name = (action or "").strip().casefold()
        if not name:
            raise ValueError("action must not be empty")
        if not callable(handler):
            raise TypeError("handler must be callable")
        with self._lock:
            self._handlers[name] = handler

    def serve_once(self, timeout: Optional[float] = None) -> bool:
        try:
            request = self._channel.receive(
                self._poll_interval if timeout is None else max(0.0, float(timeout))
            )
        except ChannelClosedError:
            self._stop_event.set()
            return False
        if request is None:
            return False

        request_id = str(request.get("request_id") or "")
        action = str(request.get("action") or "").strip().casefold()
        params = request.get("params") or {}
        response = {
            "version": 1,
            "kind": "response",
            "request_id": request_id,
            "success": False,
            "payload": {},
            "error": None,
        }
        if request.get("kind") != "request" or not request_id or not action:
            response["error"] = "invalid session agent request"
        elif not isinstance(params, Mapping):
            response["error"] = "request params must be an object"
        else:
            with self._lock:
                handler = self._handlers.get(action)
            if handler is None:
                response["error"] = f"unsupported session action: {action}"
            else:
                try:
                    value = handler(dict(params))
                    if isinstance(value, AgentCallResult):
                        response["success"] = value.success
                        response["payload"] = dict(value.payload)
                        response["error"] = value.error
                    elif isinstance(value, Mapping):
                        response["success"] = True
                        response["payload"] = dict(value)
                    elif value is None:
                        response["success"] = True
                    else:
                        response["success"] = True
                        response["payload"] = {"value": value}
                except Exception as exc:
                    response["error"] = str(exc)
                    logger.warning(f"Session action {action} failed: {exc}")
        try:
            self._channel.send(response)
            return True
        except ChannelClosedError:
            self._stop_event.set()
            return False

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="student-session-agent",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        with self._lock:
            thread = self._thread
            self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(timeout)))

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self.serve_once(self._poll_interval)


class StudentServiceRuntime:
    """Small service facade joining command execution, lease, and session IPC."""

    def __init__(
        self,
        command_executor: Optional[CommandExecutor] = None,
        session_agent: Optional[SessionAgentProxy] = None,
        temporary_lease: Optional[TemporaryControlLease] = None,
    ) -> None:
        self.command_executor = command_executor or CommandExecutor()
        self.session_agent = session_agent
        self.temporary_lease = temporary_lease or TemporaryControlLease()

    def register_privileged_action(
        self,
        command_name: str,
        handler: Callable[[Mapping[str, Any]], Any],
    ) -> None:
        self.command_executor.register_handler(command_name, handler)

    def register_session_action(
        self,
        command_name: str,
        agent_action: Optional[str] = None,
        timeout: float = 5.0,
    ) -> None:
        action = agent_action or command_name

        def delegate(params: Mapping[str, Any]) -> CommandHandlerResult:
            if self.session_agent is None:
                return CommandHandlerResult.failed("no desktop session agent is connected")
            result = self.session_agent.call(action, params, timeout)
            if result.success:
                return CommandHandlerResult(payload=dict(result.payload))
            return CommandHandlerResult.failed(
                result.error or "desktop session action failed",
                result.payload,
            )

        self.command_executor.register_handler(command_name, delegate)

    def execute(
        self,
        command: Command,
        on_received: Optional[Callable[[CommandOutcome], None]] = None,
    ) -> CommandExecution:
        return self.command_executor.execute(command, on_received)

    def heartbeat(self) -> Optional[float]:
        return self.temporary_lease.renew()

    def mark_temporary_control(
        self,
        control: TemporaryControlKind,
        restore: Callable[[], object],
    ) -> None:
        self.temporary_lease.activate(control, restore)

    def start(self) -> None:
        self.temporary_lease.start()

    def stop(self) -> None:
        self.temporary_lease.stop(restore=True)
