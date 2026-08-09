"""Deep classroom orchestration module for the v2 teacher runtime.

Callers submit an immutable action and read immutable snapshots.  Target
resolution, acknowledgements, retries, supersession, leases, and persistence
stay behind that interface.  Transport, clock, and state-store adapters are
injected at internal seams so the same behaviour is deterministic in tests.
"""

from __future__ import annotations

import heapq
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from common.v2.control import AckStage as AckPhase
from common.v2.control import ControlEnvelope
from common.v2.model import (
    All,
    ClassroomAction,
    ClassroomSnapshot,
    CommandKind,
    DeviceCommandState,
    DeviceIds,
    DeviceOperationStatus,
    DeviceSnapshot,
    Group,
    InspectionMode,
    InspectionSession,
    OperationReceipt,
    PERMANENT_COMMANDS,
    StateValue,
    TEMPORARY_COMMANDS,
    TargetScope,
)
from teacher.core.state_store import AUDIT_RETENTION_SECONDS, InMemoryStateStore


DEFAULT_LEASE_SECONDS = 10.0
RETRY_DELAYS = (0.3, 0.7)
RECEIVE_TIMEOUT_SECONDS = 1.5
PERSIST_DEBOUNCE_SECONDS = 0.05
PRUNE_INTERVAL_SECONDS = 60 * 60


@dataclass(frozen=True)
class TransportAck:
    command_id: str
    device_id: str
    phase: AckPhase
    revision: Optional[int] = None
    success: bool = True
    detail: str = ""
    payload: Mapping[str, Any] = None

    def __post_init__(self) -> None:
        if self.payload is None:
            object.__setattr__(self, "payload", {})


@dataclass(frozen=True)
class TransportDevice:
    device_id: str
    hostname: str
    group: str = "default"
    online: bool = True
    last_seen: float = 0.0
    metadata: Mapping[str, Any] = None

    def __post_init__(self) -> None:
        if not self.device_id:
            raise ValueError("device_id must not be empty")
        if self.metadata is None:
            object.__setattr__(self, "metadata", {})


@dataclass(frozen=True)
class SentCommand:
    device_id: str
    envelope: Any
    sent_at: float


class _CancelledHandle:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class ManualClock:
    """Clock adapter whose callbacks run synchronously during ``advance``."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = float(start)
        self._sequence = 0
        self._scheduled: List[Tuple[float, int, _CancelledHandle, Callable[[], None]]] = []

    def now(self) -> float:
        return self._now

    def call_later(self, delay: float, callback: Callable[[], None]) -> _CancelledHandle:
        handle = _CancelledHandle()
        self._sequence += 1
        heapq.heappush(
            self._scheduled,
            (self._now + max(0.0, delay), self._sequence, handle, callback),
        )
        return handle

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("clock cannot move backwards")
        target = self._now + seconds
        while self._scheduled and self._scheduled[0][0] <= target:
            due, _, handle, callback = heapq.heappop(self._scheduled)
            self._now = due
            if not handle.cancelled:
                callback()
        self._now = target


FakeClock = ManualClock


class _SystemHandle:
    def __init__(self, timer: threading.Timer) -> None:
        self._timer = timer

    def cancel(self) -> None:
        self._timer.cancel()


class SystemClock:
    def now(self) -> float:
        return time.time()

    def call_later(self, delay: float, callback: Callable[[], None]) -> _SystemHandle:
        timer = threading.Timer(delay, callback)
        timer.daemon = True
        timer.start()
        return _SystemHandle(timer)


class InMemoryTransport:
    """Transport adapter that records sends and exposes explicit ACK input."""

    def __init__(self, clock: Optional[Any] = None) -> None:
        self._clock = clock or SystemClock()
        self._devices: Dict[str, TransportDevice] = {}
        self._ack_handler: Optional[Callable[[TransportAck], None]] = None
        self._device_handler: Optional[Callable[[TransportDevice], None]] = None
        self._sent: List[SentCommand] = []
        self._lock = threading.RLock()

    def set_ack_handler(self, handler: Callable[[TransportAck], None]) -> None:
        self._ack_handler = handler

    def set_device_handler(self, handler: Callable[[TransportDevice], None]) -> None:
        self._device_handler = handler

    def devices(self) -> Tuple[TransportDevice, ...]:
        with self._lock:
            return tuple(self._devices.values())

    @property
    def sent(self) -> Tuple[SentCommand, ...]:
        with self._lock:
            return tuple(self._sent)

    def sent_to(self, device_id: str) -> Tuple[SentCommand, ...]:
        return tuple(item for item in self.sent if item.device_id == device_id)

    def clear_sent(self) -> None:
        with self._lock:
            self._sent.clear()

    def connect(
        self,
        device_id: str,
        hostname: Optional[str] = None,
        group: str = "default",
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> TransportDevice:
        device = TransportDevice(
            device_id=device_id,
            hostname=hostname or device_id,
            group=group,
            online=True,
            last_seen=self._clock.now(),
            metadata=dict(metadata or {}),
        )
        with self._lock:
            self._devices[device_id] = device
        if self._device_handler is not None:
            self._device_handler(device)
        return device

    register = connect

    def disconnect(self, device_id: str) -> None:
        with self._lock:
            old = self._devices.get(device_id)
            if old is None:
                return
            device = TransportDevice(
                device_id=old.device_id,
                hostname=old.hostname,
                group=old.group,
                online=False,
                last_seen=old.last_seen,
                metadata=old.metadata,
            )
            self._devices[device_id] = device
        if self._device_handler is not None:
            self._device_handler(device)

    def send(self, device_id: str, envelope: Any) -> bool:
        with self._lock:
            self._sent.append(SentCommand(device_id, envelope, self._clock.now()))
        return True

    def acknowledge(
        self,
        command_id: str,
        device_id: str,
        phase: Any,
        revision: Optional[int] = None,
        success: bool = True,
        detail: str = "",
    ) -> None:
        if self._ack_handler is None:
            raise RuntimeError("transport is not attached to a Classroom")
        phase_value = phase.value if hasattr(phase, "value") else str(phase)
        self._ack_handler(
            TransportAck(
                command_id=command_id,
                device_id=device_id,
                phase=AckPhase(phase_value),
                revision=revision,
                success=success,
                detail=detail,
            )
        )

    ack = acknowledge


class Classroom:
    """Submit classroom actions and observe their immutable state projection."""

    def __init__(
        self,
        transport: Any,
        store: Optional[Any] = None,
        clock: Optional[Any] = None,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self._transport = transport
        self._store = store or InMemoryStateStore()
        self._clock = clock or SystemClock()
        self._lease_seconds = float(lease_seconds)
        self._lock = threading.RLock()
        self._handles: List[Any] = []
        self._persist_scheduled = False
        self._last_prune_at = self._clock.now()
        self._closed = False

        persisted = self._store.load()
        self._snapshot_revision = int(persisted.get("revision", 0))
        self._command_revision = int(persisted.get("command_revision", 0))
        self._devices: Dict[str, Dict[str, Any]] = dict(persisted.get("devices", {}))
        self._operations: Dict[str, Dict[str, Any]] = dict(
            persisted.get("operations", {})
        )
        self._desired: Dict[str, Dict[str, Dict[str, Any]]] = dict(
            persisted.get("desired", {})
        )
        self._actual: Dict[str, Dict[str, Dict[str, Any]]] = dict(
            persisted.get("actual", {})
        )
        self._current: Dict[Tuple[str, str], Tuple[str, int]] = {}
        self._envelopes: Dict[str, Any] = {}

        for device in self._devices.values():
            device["online"] = False
        self._rebuild_current_index()
        for device_id in tuple(self._devices):
            self._expire_disconnected_temporary_controls(device_id)
        self._prune_operations(force=True)

        self._transport.set_ack_handler(self._on_ack)
        if hasattr(self._transport, "set_device_handler"):
            self._transport.set_device_handler(self._on_device)
        for device in self._transport.devices():
            self._merge_device(device, notify=False)
            if device.online:
                self._sync_permanent(device.device_id)
        self._persist()

    def submit(self, action: ClassroomAction, scope: TargetScope) -> OperationReceipt:
        if not isinstance(action, ClassroomAction):
            raise TypeError("action must be a ClassroomAction")
        if not isinstance(scope, TargetScope):
            raise TypeError("scope must be All, Group, or DeviceIds")
        with self._lock:
            self._ensure_open()
            now = self._clock.now()
            command_id = str(uuid.uuid4())
            self._command_revision += 1
            command_revision = self._command_revision
            targets = self._resolve(scope)
            expires_at = self._expiry_for(action, now)
            operation = {
                "revision": command_revision,
                "action": self._serialize_action(action),
                "target_ids": list(targets),
                "submitted_at": now,
                "expires_at": expires_at,
                "statuses": {},
            }
            self._operations[command_id] = operation
            envelope = self._build_envelope(
                command_id, command_revision, action, now, expires_at
            )
            self._envelopes[command_id] = envelope

            for device_id in targets:
                self._prepare_target(command_id, device_id, action, operation)
            self._bump_and_persist()
            return self._receipt(command_id)

    def snapshot(self, after_revision: int = -1) -> Optional[ClassroomSnapshot]:
        with self._lock:
            self._ensure_open()
            if self._snapshot_revision <= after_revision:
                return None
            devices = tuple(self._device_snapshot(device_id) for device_id in sorted(self._devices))
            operations = tuple(
                self._receipt(command_id)
                for command_id, _ in sorted(
                    self._operations.items(), key=lambda item: item[1]["revision"]
                )
            )
            return ClassroomSnapshot(
                revision=self._snapshot_revision,
                devices=devices,
                operations=operations,
                observed_at=self._clock.now(),
            )

    def open_inspection(
        self,
        scope: TargetScope,
        mode: Any = InspectionMode.THUMBNAIL,
        media: Optional[Mapping[str, Any]] = None,
    ) -> InspectionSession:
        inspection_mode = mode if isinstance(mode, InspectionMode) else InspectionMode(mode)
        session_id = str(uuid.uuid4())
        payload = {"mode": inspection_mode.value, "session_id": session_id}
        payload.update(dict(media or {}))
        receipt = self.submit(
            ClassroomAction(
                CommandKind.INSPECTION_OPEN,
                payload,
                lease_seconds=self._lease_seconds,
            ),
            scope,
        )
        return InspectionSession(
            session_id=session_id,
            command_id=receipt.command_id,
            mode=inspection_mode,
            target_ids=receipt.target_ids,
            opened_at=receipt.submitted_at,
            expires_at=receipt.expires_at or receipt.submitted_at + self._lease_seconds,
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for handle in self._handles:
                handle.cancel()
            self._handles.clear()
            self._persist()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Classroom is closed")

    def _resolve(self, scope: TargetScope) -> Tuple[str, ...]:
        if isinstance(scope, All):
            return tuple(sorted(self._devices))
        if isinstance(scope, Group):
            return tuple(
                sorted(
                    device_id
                    for device_id, device in self._devices.items()
                    if device.get("group", "default") == scope.name
                )
            )
        if isinstance(scope, DeviceIds):
            for device_id in scope.device_ids:
                self._devices.setdefault(
                    device_id,
                    {
                        "hostname": device_id,
                        "group": "default",
                        "online": False,
                        "last_seen": 0.0,
                        "metadata": {},
                    },
                )
            return scope.device_ids
        raise TypeError("unknown target scope")

    def _prepare_target(
        self,
        command_id: str,
        device_id: str,
        action: ClassroomAction,
        operation: Dict[str, Any],
    ) -> None:
        domains = self._domains_for(action)
        for domain, value in domains:
            self._supersede(device_id, domain, command_id, operation["revision"])
            self._current[(device_id, domain)] = (command_id, operation["revision"])
            self._set_desired(device_id, domain, value, action, operation)

        online = bool(self._devices[device_id].get("online", False))
        should_send = online or action.kind == CommandKind.WAKE_ON_LAN
        durable = action.kind in PERMANENT_COMMANDS or action.permanent
        if not should_send and not durable:
            operation["statuses"][device_id] = self._status_dict(
                DeviceCommandState.OFFLINE, detail="device was offline at submission"
            )
            return
        if not should_send:
            operation["statuses"][device_id] = self._status_dict(
                DeviceCommandState.QUEUED, detail="permanent policy queued until reconnect"
            )
            return
        operation["statuses"][device_id] = self._status_dict(DeviceCommandState.SENT)
        self._send(command_id, device_id)
        for delay in RETRY_DELAYS:
            self._schedule(
                delay,
                lambda cid=command_id, did=device_id: self._retry(cid, did),
            )
        self._schedule(
            RECEIVE_TIMEOUT_SECONDS,
            lambda cid=command_id, did=device_id: self._receive_timeout(cid, did),
        )

    def _send(self, command_id: str, device_id: str) -> None:
        operation = self._operations[command_id]
        status = operation["statuses"][device_id]
        status["attempts"] = int(status.get("attempts", 0)) + 1
        try:
            sent = self._transport.send(device_id, self._envelopes[command_id])
        except Exception as exc:
            sent = False
            status["detail"] = str(exc)
        if not sent:
            status["state"] = DeviceCommandState.FAILED.value
            if not status.get("detail"):
                status["detail"] = "transport rejected command"

    def _retry(self, command_id: str, device_id: str) -> None:
        with self._lock:
            operation = self._operations.get(command_id)
            if operation is None:
                return
            status = operation["statuses"].get(device_id)
            if status is None or status["state"] != DeviceCommandState.SENT.value:
                return
            self._send(command_id, device_id)
            self._bump_and_persist()

    def _receive_timeout(self, command_id: str, device_id: str) -> None:
        with self._lock:
            operation = self._operations.get(command_id)
            if operation is None:
                return
            status = operation["statuses"].get(device_id)
            if status is None or status["state"] != DeviceCommandState.SENT.value:
                return
            status["state"] = DeviceCommandState.TIMED_OUT.value
            status["detail"] = "no RECEIVED acknowledgement"
            self._bump_and_persist()

    def _on_ack(self, ack: TransportAck) -> None:
        with self._lock:
            operation = self._operations.get(ack.command_id)
            if operation is None:
                return
            if ack.revision is not None and int(ack.revision) != int(operation["revision"]):
                return
            status = operation["statuses"].get(ack.device_id)
            if status is None:
                return
            if status["state"] in {
                DeviceCommandState.APPLIED.value,
                DeviceCommandState.FAILED.value,
                DeviceCommandState.OFFLINE.value,
                DeviceCommandState.SUPERSEDED.value,
                DeviceCommandState.EXPIRED.value,
            }:
                return
            if not self._ack_is_current(ack.device_id, ack.command_id, operation):
                return
            now = self._clock.now()
            if not ack.success:
                status["state"] = DeviceCommandState.FAILED.value
                status["detail"] = ack.detail or "device rejected command"
            elif ack.phase == AckPhase.RECEIVED:
                if status["state"] != DeviceCommandState.APPLIED.value:
                    status["state"] = DeviceCommandState.RECEIVED.value
                    status["received_at"] = now
                    status["detail"] = ack.detail
            elif ack.phase == AckPhase.APPLIED:
                status["state"] = DeviceCommandState.APPLIED.value
                status["received_at"] = status.get("received_at") or now
                status["applied_at"] = now
                status["detail"] = ack.detail
                status["payload"] = dict(ack.payload or {})
                self._apply_actual(ack.device_id, operation)
            self._bump_and_persist(deferred=True)

    def _ack_is_current(
        self, device_id: str, command_id: str, operation: Mapping[str, Any]
    ) -> bool:
        domains = self._domains_for(self._deserialize_action(operation["action"]))
        if not domains:
            return True
        return all(
            self._current.get((device_id, domain)) == (command_id, operation["revision"])
            for domain, _ in domains
        )

    def _apply_actual(self, device_id: str, operation: Mapping[str, Any]) -> None:
        action = self._deserialize_action(operation["action"])
        for domain, value in self._domains_for(action):
            self._actual.setdefault(device_id, {})[domain] = {
                "value": value,
                "command_revision": operation["revision"],
                "expires_at": operation.get("expires_at"),
                "permanent": bool(action.permanent or action.kind in PERMANENT_COMMANDS),
            }

    def _supersede(
        self, device_id: str, domain: str, new_command_id: str, revision: int
    ) -> None:
        current = self._current.get((device_id, domain))
        if current is None or current == (new_command_id, revision):
            return
        old_command_id, _ = current
        old = self._operations.get(old_command_id)
        if old is None:
            return
        status = old["statuses"].get(device_id)
        if status is not None and status["state"] in {
            DeviceCommandState.QUEUED.value,
            DeviceCommandState.SENT.value,
            DeviceCommandState.RECEIVED.value,
            DeviceCommandState.TIMED_OUT.value,
        }:
            status["state"] = DeviceCommandState.SUPERSEDED.value
            status["detail"] = "replaced by revision {}".format(revision)

    def _set_desired(
        self,
        device_id: str,
        domain: str,
        value: Any,
        action: ClassroomAction,
        operation: Mapping[str, Any],
    ) -> None:
        self._desired.setdefault(device_id, {})[domain] = {
            "value": value,
            "command_revision": operation["revision"],
            "expires_at": operation.get("expires_at"),
            "permanent": bool(action.permanent or action.kind in PERMANENT_COMMANDS),
        }

    def _domains_for(self, action: ClassroomAction) -> Tuple[Tuple[str, Any], ...]:
        kind = action.kind
        if kind in (CommandKind.BROADCAST_START, CommandKind.BROADCAST_STOP):
            return (("broadcast", kind == CommandKind.BROADCAST_START),)
        if kind in (CommandKind.SILENCE_START, CommandKind.SILENCE_STOP):
            return (("silence", kind == CommandKind.SILENCE_START),)
        if kind in (CommandKind.NETWORK_BLOCK, CommandKind.NETWORK_RESTORE):
            return (("network", kind == CommandKind.NETWORK_BLOCK),)
        if kind in (CommandKind.INPUT_LOCK, CommandKind.INPUT_UNLOCK):
            return (("input", kind == CommandKind.INPUT_LOCK),)
        if kind in (CommandKind.INSPECTION_OPEN, CommandKind.INSPECTION_CLOSE):
            return (("inspection", kind == CommandKind.INSPECTION_OPEN),)
        if kind in (CommandKind.REMOTE_CONTROL_START, CommandKind.REMOTE_CONTROL_STOP):
            return (("remote_control", kind == CommandKind.REMOTE_CONTROL_START),)
        if kind in (CommandKind.BLACKLIST_SET, CommandKind.BLACKLIST_CLEAR):
            identity = str(
                action.payload.get("path")
                or action.payload.get("program")
                or action.payload.get("name")
                or "default"
            ).lower()
            return (("blacklist:{}".format(identity), kind == CommandKind.BLACKLIST_SET),)
        if kind == CommandKind.RESTORE_ALL:
            return tuple(
                (domain, False)
                for domain in (
                    "broadcast",
                    "silence",
                    "network",
                    "input",
                    "inspection",
                    "remote_control",
                )
            )
        return ()

    def _expiry_for(self, action: ClassroomAction, now: float) -> Optional[float]:
        if action.kind not in TEMPORARY_COMMANDS:
            return None
        return now + (action.lease_seconds or self._lease_seconds)

    def _on_device(self, device: TransportDevice) -> None:
        with self._lock:
            was_online = bool(self._devices.get(device.device_id, {}).get("online", False))
            self._merge_device(device, notify=False)
            if device.online and not was_online:
                self._sync_permanent(device.device_id)
            elif was_online and not device.online:
                self._expire_disconnected_temporary_controls(device.device_id)
            self._bump_and_persist()

    def _expire_disconnected_temporary_controls(self, device_id: str) -> None:
        """Clear controls that the student's heartbeat lease will restore."""
        for (current_device_id, domain), current in tuple(self._current.items()):
            if current_device_id != device_id:
                continue
            command_id, revision = current
            operation = self._operations.get(command_id)
            if operation is None or int(operation["revision"]) != int(revision):
                continue
            action = self._deserialize_action(operation["action"])
            if action.kind not in TEMPORARY_COMMANDS:
                continue
            status = operation["statuses"].get(device_id)
            if status is not None and status["state"] not in {
                DeviceCommandState.FAILED.value,
                DeviceCommandState.OFFLINE.value,
                DeviceCommandState.SUPERSEDED.value,
                DeviceCommandState.TIMED_OUT.value,
            }:
                status["state"] = DeviceCommandState.EXPIRED.value
                status["detail"] = "student disconnected; temporary control lease expired"
            self._current.pop((device_id, domain), None)
            self._desired.get(device_id, {}).pop(domain, None)
            self._actual.get(device_id, {}).pop(domain, None)

    def _merge_device(self, device: TransportDevice, notify: bool = True) -> None:
        self._devices[device.device_id] = {
            "hostname": device.hostname,
            "group": device.group,
            "online": bool(device.online),
            "last_seen": float(device.last_seen or self._clock.now()),
            "metadata": dict(device.metadata or {}),
        }
        if notify:
            self._bump_and_persist()

    def _sync_permanent(self, device_id: str) -> None:
        for domain, desired in self._desired.get(device_id, {}).items():
            if not desired.get("permanent", False):
                continue
            current = self._current.get((device_id, domain))
            if current is None:
                continue
            command_id, revision = current
            operation = self._operations.get(command_id)
            if operation is None or int(operation["revision"]) != int(revision):
                continue
            status = operation["statuses"].get(device_id)
            if status is None or status["state"] == DeviceCommandState.SUPERSEDED.value:
                continue
            action = self._deserialize_action(operation["action"])
            if command_id not in self._envelopes:
                self._envelopes[command_id] = self._build_envelope(
                    command_id,
                    operation["revision"],
                    action,
                    operation["submitted_at"],
                    operation.get("expires_at"),
                )
            needs_tracking = status["state"] != DeviceCommandState.APPLIED.value
            if needs_tracking:
                status["state"] = DeviceCommandState.SENT.value
                status["detail"] = ""
            self._send(command_id, device_id)
            if needs_tracking:
                for delay in RETRY_DELAYS:
                    self._schedule(
                        delay,
                        lambda cid=command_id, did=device_id: self._retry(cid, did),
                    )
                self._schedule(
                    RECEIVE_TIMEOUT_SECONDS,
                    lambda cid=command_id, did=device_id: self._receive_timeout(cid, did),
                )

    def _build_envelope(
        self,
        command_id: str,
        revision: int,
        action: ClassroomAction,
        issued_at: float,
        expires_at: Optional[float],
    ) -> Any:
        return ControlEnvelope.command(
            action=action.kind.value,
            command_id=command_id,
            revision=revision,
            payload=dict(action.payload),
            issued_at=int(issued_at * 1000),
            lease_expires_at=(
                int(expires_at * 1000) if expires_at is not None else None
            ),
        )

    def _receipt(self, command_id: str) -> OperationReceipt:
        operation = self._operations[command_id]
        statuses = tuple(
            self._status(device_id, value)
            for device_id, value in sorted(operation["statuses"].items())
        )
        return OperationReceipt(
            command_id=command_id,
            revision=int(operation["revision"]),
            action=self._deserialize_action(operation["action"]),
            target_ids=tuple(operation["target_ids"]),
            statuses=statuses,
            submitted_at=float(operation["submitted_at"]),
            expires_at=operation.get("expires_at"),
        )

    def _device_snapshot(self, device_id: str) -> DeviceSnapshot:
        device = self._devices[device_id]
        return DeviceSnapshot(
            device_id=device_id,
            hostname=str(device.get("hostname", device_id)),
            group=str(device.get("group", "default")),
            online=bool(device.get("online", False)),
            last_seen=float(device.get("last_seen", 0.0)),
            metadata=dict(device.get("metadata", {})),
            expected=self._state_values(self._desired.get(device_id, {})),
            actual=self._state_values(self._actual.get(device_id, {})),
        )

    @staticmethod
    def _state_values(domains: Mapping[str, Mapping[str, Any]]) -> Tuple[StateValue, ...]:
        return tuple(
            StateValue(
                domain=domain,
                value=value.get("value"),
                command_revision=int(value.get("command_revision", 0)),
                expires_at=value.get("expires_at"),
                permanent=bool(value.get("permanent", False)),
            )
            for domain, value in sorted(domains.items())
        )

    @staticmethod
    def _status(device_id: str, value: Mapping[str, Any]) -> DeviceOperationStatus:
        return DeviceOperationStatus(
            device_id=device_id,
            state=DeviceCommandState(value["state"]),
            attempts=int(value.get("attempts", 0)),
            detail=str(value.get("detail", "")),
            received_at=value.get("received_at"),
            applied_at=value.get("applied_at"),
            payload=dict(value.get("payload") or {}),
        )

    @staticmethod
    def _status_dict(
        state: DeviceCommandState, attempts: int = 0, detail: str = ""
    ) -> Dict[str, Any]:
        return {
            "state": state.value,
            "attempts": attempts,
            "detail": detail,
            "received_at": None,
            "applied_at": None,
            "payload": {},
        }

    @staticmethod
    def _serialize_action(action: ClassroomAction) -> Dict[str, Any]:
        return {
            "kind": action.kind.value,
            "payload": dict(action.payload),
            "permanent": bool(action.permanent),
            "lease_seconds": action.lease_seconds,
        }

    @staticmethod
    def _deserialize_action(value: Mapping[str, Any]) -> ClassroomAction:
        return ClassroomAction(
            CommandKind(value["kind"]),
            value.get("payload", {}),
            permanent=bool(value.get("permanent", False)),
            lease_seconds=value.get("lease_seconds"),
        )

    def _rebuild_current_index(self) -> None:
        for command_id, operation in sorted(
            self._operations.items(), key=lambda item: int(item[1]["revision"])
        ):
            action = self._deserialize_action(operation["action"])
            for device_id in operation.get("target_ids", []):
                for domain, _ in self._domains_for(action):
                    self._current[(device_id, domain)] = (
                        command_id,
                        int(operation["revision"]),
                    )
            self._command_revision = max(
                self._command_revision, int(operation.get("revision", 0))
            )

    def _schedule(self, delay: float, callback: Callable[[], None]) -> None:
        self._handles.append(self._clock.call_later(max(0.0, delay), callback))

    def _bump_and_persist(self, deferred: bool = False) -> None:
        self._snapshot_revision += 1
        self._prune_operations()
        if not deferred:
            self._persist()
            return
        if self._persist_scheduled:
            return
        self._persist_scheduled = True
        self._schedule(PERSIST_DEBOUNCE_SECONDS, self._flush_deferred_persist)

    def _flush_deferred_persist(self) -> None:
        with self._lock:
            if not self._persist_scheduled or self._closed:
                return
            self._persist_scheduled = False
            self._persist()

    def _prune_operations(self, force: bool = False) -> None:
        now = self._clock.now()
        if not force and now - self._last_prune_at < PRUNE_INTERVAL_SECONDS:
            return
        self._last_prune_at = now
        cutoff = now - AUDIT_RETENTION_SECONDS
        for command_id, operation in tuple(self._operations.items()):
            if float(operation.get("submitted_at", now)) >= cutoff:
                continue
            action = self._deserialize_action(operation["action"])
            if action.permanent or action.kind in PERMANENT_COMMANDS:
                continue
            for identity, current in tuple(self._current.items()):
                if current[0] != command_id:
                    continue
                device_id, domain = identity
                self._current.pop(identity, None)
                self._desired.get(device_id, {}).pop(domain, None)
                self._actual.get(device_id, {}).pop(domain, None)
            self._operations.pop(command_id, None)
            self._envelopes.pop(command_id, None)

    def _persist(self) -> None:
        self._store.save(
            {
                "revision": self._snapshot_revision,
                "command_revision": self._command_revision,
                "devices": self._devices,
                "operations": self._operations,
                "desired": self._desired,
                "actual": self._actual,
            }
        )


__all__ = [
    "AckPhase",
    "Classroom",
    "DEFAULT_LEASE_SECONDS",
    "FakeClock",
    "InMemoryTransport",
    "ManualClock",
    "RETRY_DELAYS",
    "SentCommand",
    "SystemClock",
    "TransportAck",
    "TransportDevice",
]
