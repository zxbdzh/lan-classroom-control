"""Immutable domain types shared by the v2 teacher and student runtimes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Optional, Tuple


def _frozen_mapping(value: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value or {}))


class TargetScope:
    """Marker base for an explicitly resolved command target."""

    @staticmethod
    def all() -> "All":
        return All()

    @staticmethod
    def group(name: str) -> "Group":
        return Group(name)

    @staticmethod
    def devices(device_ids: Iterable[str]) -> "DeviceIds":
        return DeviceIds(device_ids)


@dataclass(frozen=True)
class All(TargetScope):
    """All devices known when the action is submitted."""


@dataclass(frozen=True)
class Group(TargetScope):
    name: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("group name must not be empty")

    @property
    def group_id(self) -> str:
        return self.name


@dataclass(frozen=True, init=False)
class DeviceIds(TargetScope):
    device_ids: Tuple[str, ...]

    def __init__(self, device_ids: Iterable[str]) -> None:
        normalized = tuple(dict.fromkeys(str(item) for item in device_ids if str(item)))
        object.__setattr__(self, "device_ids", normalized)

    @property
    def ids(self) -> Tuple[str, ...]:
        return self.device_ids


AllDevices = All
DeviceGroup = Group


class CommandKind(str, Enum):
    BROADCAST_START = "broadcast.start"
    BROADCAST_STOP = "broadcast.stop"
    SILENCE_START = "silence.start"
    SILENCE_STOP = "silence.stop"
    NETWORK_BLOCK = "network.block"
    NETWORK_RESTORE = "network.restore"
    USB_STORAGE_BLOCK = "usb_storage.block"
    USB_STORAGE_RESTORE = "usb_storage.restore"
    INPUT_LOCK = "input.lock"
    INPUT_UNLOCK = "input.unlock"
    INSPECTION_OPEN = "inspection.open"
    INSPECTION_CLOSE = "inspection.close"
    REMOTE_CONTROL_START = "remote_control.start"
    REMOTE_CONTROL_STOP = "remote_control.stop"
    RESTORE_ALL = "classroom.restore_all"
    PROCESS_TERMINATE = "process.terminate"
    PROCESS_LIST = "process.list"
    REMOTE_INPUT = "remote_control.input"
    BLACKLIST_SET = "blacklist.set"
    BLACKLIST_CLEAR = "blacklist.clear"
    FILE_DISTRIBUTE = "file.distribute"
    POWER_OFF = "power.off"
    REBOOT = "power.reboot"
    LOGOFF = "power.logoff"
    WAKE_ON_LAN = "power.wake_on_lan"
    UPDATE = "device.update"

    # Names retained for callers using the classroom wording rather than the
    # protocol wording.
    BLACK_SCREEN = "silence.start"
    BLACK_SCREEN_CLEAR = "silence.stop"
    NET_BLOCK = "network.block"
    NET_RESTORE = "network.restore"
    WOL = "power.wake_on_lan"


TEMPORARY_COMMANDS = frozenset(
    {
        CommandKind.BROADCAST_START,
        CommandKind.SILENCE_START,
        CommandKind.NETWORK_BLOCK,
        CommandKind.USB_STORAGE_BLOCK,
        CommandKind.INPUT_LOCK,
        CommandKind.INSPECTION_OPEN,
        CommandKind.REMOTE_CONTROL_START,
    }
)

PERMANENT_COMMANDS = frozenset(
    {CommandKind.BLACKLIST_SET, CommandKind.BLACKLIST_CLEAR}
)


@dataclass(frozen=True)
class ClassroomAction:
    kind: CommandKind
    payload: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    permanent: bool = False
    lease_seconds: Optional[float] = None

    def __post_init__(self) -> None:
        kind = self.kind if isinstance(self.kind, CommandKind) else CommandKind(self.kind)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "payload", _frozen_mapping(self.payload))
        if self.lease_seconds is not None and self.lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if self.permanent and kind not in PERMANENT_COMMANDS:
            raise ValueError("only blacklist actions may be permanent")
        if kind in PERMANENT_COMMANDS:
            object.__setattr__(self, "permanent", True)

    @property
    def params(self) -> Mapping[str, Any]:
        return self.payload


class DeviceCommandState(str, Enum):
    QUEUED = "queued"
    SENT = "sent"
    RECEIVED = "received"
    APPLIED = "applied"
    OFFLINE = "offline"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"


@dataclass(frozen=True)
class DeviceOperationStatus:
    device_id: str
    state: DeviceCommandState
    attempts: int = 0
    detail: str = ""
    received_at: Optional[float] = None
    applied_at: Optional[float] = None
    payload: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", _frozen_mapping(self.payload))


PerDeviceStatus = DeviceOperationStatus
OperationStatus = DeviceCommandState


@dataclass(frozen=True)
class OperationReceipt:
    command_id: str
    revision: int
    action: ClassroomAction
    target_ids: Tuple[str, ...]
    statuses: Tuple[DeviceOperationStatus, ...]
    submitted_at: float
    expires_at: Optional[float] = None

    def status_for(self, device_id: str) -> Optional[DeviceOperationStatus]:
        return next((status for status in self.statuses if status.device_id == device_id), None)

    @property
    def per_device(self) -> Mapping[str, DeviceOperationStatus]:
        return MappingProxyType({status.device_id: status for status in self.statuses})


@dataclass(frozen=True)
class StateValue:
    domain: str
    value: Any
    command_revision: int
    expires_at: Optional[float] = None
    permanent: bool = False


@dataclass(frozen=True)
class DeviceSnapshot:
    device_id: str
    hostname: str
    group: str
    online: bool
    last_seen: float
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    expected: Tuple[StateValue, ...] = ()
    actual: Tuple[StateValue, ...] = ()
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _frozen_mapping(self.metadata))

    def expected_for(self, domain: str) -> Optional[StateValue]:
        return next((entry for entry in self.expected if entry.domain == domain), None)

    def actual_for(self, domain: str) -> Optional[StateValue]:
        return next((entry for entry in self.actual if entry.domain == domain), None)


@dataclass(frozen=True)
class ClassroomSnapshot:
    revision: int
    devices: Tuple[DeviceSnapshot, ...]
    operations: Tuple[OperationReceipt, ...]
    observed_at: float
    global_blacklist: Tuple[str, ...] = ()
    application_control_enabled: bool = True
    local_blacklist_rules: Mapping[str, Tuple[str, ...]] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        object.__setattr__(self, "local_blacklist_rules", MappingProxyType({
            identity: tuple(device_ids) for identity, device_ids in self.local_blacklist_rules.items()
        }))

    def device(self, device_id: str) -> Optional[DeviceSnapshot]:
        return next((device for device in self.devices if device.device_id == device_id), None)

    def operation(self, command_id: str) -> Optional[OperationReceipt]:
        return next((item for item in self.operations if item.command_id == command_id), None)


class InspectionMode(str, Enum):
    THUMBNAIL = "thumbnail"
    FOCUS = "focus"
    REMOTE_CONTROL = "remote_control"


@dataclass(frozen=True)
class InspectionSession:
    session_id: str
    command_id: str
    mode: InspectionMode
    target_ids: Tuple[str, ...]
    opened_at: float
    expires_at: float
