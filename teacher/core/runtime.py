"""Production composition root for the teacher v2 runtime."""

from __future__ import annotations

import os
import uuid
from typing import Dict, Iterable, Mapping, Optional, Set

from common.config import get_config
from common.v2.control import ControlEnvelope
from common.v2.model import (
    All,
    ClassroomAction,
    CommandKind,
    InspectionMode,
    TargetScope,
    InspectionSession,
    Group,
    DeviceIds,
)
from student.core.v2.power import WakeOnLanSender
from teacher.core.classroom import Classroom
from teacher.core.state_store import SQLiteStateStore
from teacher.core.teacher_server import TeacherServer
from teacher.core.media_runtime import BroadcastPublisher, InspectionReceiver
from common.v2.media_transport import TcpMediaHub


def default_teacher_database_path() -> str:
    root = os.environ.get("LOCALAPPDATA") or os.path.join(
        os.path.expanduser("~"), ".lan_classroom"
    )
    return os.path.join(root, "LanClassroom", "teacher-v2.db")


class TeacherRuntime:
    """Expose classroom intent while hiding transport and persistence details."""

    def __init__(
        self,
        *,
        server: Optional[TeacherServer] = None,
        database_path: Optional[str] = None,
    ) -> None:
        config = get_config()
        self.server = server or TeacherServer(
            tcp_port=int(config.get("teacher.tcp_port", 9528)),
            broadcast_port=int(config.get("teacher.udp_broadcast_port", 9527)),
        )
        self.store = SQLiteStateStore(database_path or default_teacher_database_path())
        self.classroom = Classroom(
            self.server,
            self.store,
            lease_seconds=float(config.get("teacher.control_lease_seconds", 10)),
        )
        if hasattr(self.server, "set_identity_resolver"):
            self.server.set_identity_resolver(self._resolve_registered_identity)
        self.inspection_media = InspectionReceiver(
            int(config.get("teacher.inspection_port", 9530))
        )
        self.tcp_media = TcpMediaHub(
            int(config.get("teacher.bulk_port", 9531)),
            self.inspection_media.publish_tcp_frame,
        )
        self.broadcast_media = BroadcastPublisher(
            str(config.get("teacher.multicast_group", "239.255.95.29")),
            int(config.get("teacher.broadcast_port", 9529)),
            int(config.get("teacher.broadcast_fps", 20)),
            int(config.get("teacher.broadcast_quality", 70)),
            self.tcp_media,
            int(config.get("teacher.broadcast_max_width", 1920)),
            int(config.get("teacher.broadcast_max_height", 1080)),
        )
        self._started = False
        self._broadcast_target_ids: Set[str] = set()
        self._inspection_sessions: Dict[str, str] = {}
        self.wol_sender = WakeOnLanSender()

    def start(self) -> None:
        if self._started:
            return
        self.server.start()
        self.inspection_media.start()
        self.tcp_media.start()
        self._started = True

    def close(self) -> None:
        if self._started:
            self.broadcast_media.stop()
            self.inspection_media.stop()
            self.tcp_media.stop()
            self.server.stop()
            self._started = False
        self._broadcast_target_ids.clear()
        self._inspection_sessions.clear()
        self.classroom.close()
        self.store.close()

    def submit(self, action: ClassroomAction, scope: TargetScope):
        return self.classroom.submit(action, scope)

    def snapshot(self, after_revision: int = -1):
        return self.classroom.snapshot(after_revision)

    def open_inspection(
        self, scope: TargetScope, mode: InspectionMode = InspectionMode.THUMBNAIL
    ):
        session = self.classroom.open_inspection(
            scope,
            mode,
            {
                "transport": "udp_tcp_fallback",
                "port": self.inspection_media.port,
                "fallback_port": self.tcp_media.port,
            },
        )
        for device_id in session.target_ids:
            self._unregister_inspection(device_id)
        self.inspection_media.register(session.session_id, session.target_ids)
        for device_id in session.target_ids:
            self._inspection_sessions[device_id] = session.session_id
        return session

    def close_inspection(self, scope: TargetScope):
        receipt = self.submit(ClassroomAction(CommandKind.INSPECTION_CLOSE), scope)
        for device_id in receipt.target_ids:
            self._unregister_inspection(device_id)
        return receipt

    def set_silence(self, scope: TargetScope, enabled: bool, message: str = ""):
        kind = CommandKind.SILENCE_START if enabled else CommandKind.SILENCE_STOP
        return self.submit(ClassroomAction(kind, {"message": message}), scope)

    def set_network_blocked(
        self,
        scope: TargetScope,
        enabled: bool,
        allowed_cidrs: Iterable[str] = (),
    ):
        kind = CommandKind.NETWORK_BLOCK if enabled else CommandKind.NETWORK_RESTORE
        return self.submit(
            ClassroomAction(kind, {"allowed_cidrs": list(allowed_cidrs)}), scope
        )

    def start_broadcast(
        self,
        scope: TargetScope,
        source: Optional[Mapping[str, object]] = None,
        lock_input: bool = True,
    ):
        requested_ids = set(self._resolve_target_ids(scope))
        stale_ids = self._broadcast_target_ids - requested_ids
        if stale_ids:
            self.submit(
                ClassroomAction(CommandKind.BROADCAST_STOP),
                DeviceIds(sorted(stale_ids)),
            )
        session_id = str(uuid.uuid4())
        source_value = dict(source or {"kind": "monitor", "index": 0})
        receipt = self.submit(
            ClassroomAction(
                CommandKind.BROADCAST_START,
                {
                    "source": source_value,
                    "lock_input": bool(lock_input),
                    "session_id": session_id,
                    "transport": "udp_multicast",
                    "multicast_group": self.broadcast_media.group,
                    "port": self.broadcast_media.port,
                    "fallback_port": int(get_config().get("teacher.bulk_port", 9531)),
                },
            ),
            scope,
        )
        self._broadcast_target_ids = set(receipt.target_ids)
        self.broadcast_media.start(session_id, int(source_value.get("index", 0)))
        return receipt

    def stop_broadcast(self, scope: TargetScope):
        effective_scope = (
            DeviceIds(sorted(self._broadcast_target_ids))
            if self._broadcast_target_ids
            else scope
        )
        receipt = self.submit(ClassroomAction(CommandKind.BROADCAST_STOP), effective_scope)
        self._broadcast_target_ids.clear()
        self.broadcast_media.stop()
        return receipt

    def restore_all(self, scope: TargetScope):
        receipt = self.submit(ClassroomAction(CommandKind.RESTORE_ALL), scope)
        restored_ids = set(receipt.target_ids)
        self._broadcast_target_ids.difference_update(restored_ids)
        if not self._broadcast_target_ids:
            self.broadcast_media.stop()
        for device_id in restored_ids:
            self._unregister_inspection(device_id)
        return receipt

    def power(self, scope: TargetScope, kind: CommandKind):
        if kind not in {
            CommandKind.POWER_OFF,
            CommandKind.REBOOT,
            CommandKind.LOGOFF,
            CommandKind.WAKE_ON_LAN,
        }:
            raise ValueError("unsupported power action")
        return self.submit(ClassroomAction(kind), scope)

    def wake(self, scope: TargetScope):
        snapshot = self.snapshot() or self.classroom.snapshot()
        target_ids = set(self._resolve_target_ids(scope, snapshot))
        results = {}
        for device in snapshot.devices:
            if device.device_id not in target_ids:
                continue
            mac = str(device.metadata.get("mac") or "")
            if not mac:
                results[device.device_id] = None
                continue
            results[device.device_id] = self.wol_sender.send(mac)
        return results

    def terminate_process(
        self, scope: TargetScope, pid: int, create_time: float, name: str
    ):
        return self.submit(
            ClassroomAction(
                CommandKind.PROCESS_TERMINATE,
                {"pid": int(pid), "create_time": float(create_time), "name": name},
            ),
            scope,
        )

    def query_processes(self, device_id: str):
        return self.submit(
            ClassroomAction(CommandKind.PROCESS_LIST),
            TargetScope.devices([device_id]),
        )

    def send_remote_input(self, device_id: str, payload: Mapping[str, object]):
        envelope = ControlEnvelope.command(
            CommandKind.REMOTE_INPUT.value,
            dict(payload),
            device_id=str(device_id),
            revision=0,
        )
        return self.server.send(str(device_id), envelope)

    def start_remote_control(self, device_id: str):
        session_id = str(uuid.uuid4())
        receipt = self.submit(
            ClassroomAction(
                CommandKind.REMOTE_CONTROL_START,
                {
                    "mode": InspectionMode.REMOTE_CONTROL.value,
                    "session_id": session_id,
                    "transport": "udp_tcp_fallback",
                    "port": self.inspection_media.port,
                    "fallback_port": self.tcp_media.port,
                },
            ),
            TargetScope.devices([device_id]),
        )
        self._unregister_inspection(device_id)
        self.inspection_media.register(session_id, [device_id])
        self._inspection_sessions[device_id] = session_id
        return InspectionSession(
            session_id=session_id,
            command_id=receipt.command_id,
            mode=InspectionMode.REMOTE_CONTROL,
            target_ids=(device_id,),
            opened_at=receipt.submitted_at,
            expires_at=receipt.expires_at or receipt.submitted_at + 10,
        )

    def stop_remote_control(self, device_id: str):
        receipt = self.submit(
            ClassroomAction(CommandKind.REMOTE_CONTROL_STOP),
            TargetScope.devices([device_id]),
        )
        self._unregister_inspection(device_id)
        return receipt

    def _unregister_inspection(self, device_id: str) -> None:
        session_id = self._inspection_sessions.pop(str(device_id), None)
        if session_id:
            self.inspection_media.unregister(session_id, [str(device_id)])

    def _resolve_target_ids(self, scope: TargetScope, snapshot=None):
        current = snapshot or self.snapshot() or self.classroom.snapshot()
        if isinstance(scope, DeviceIds):
            return tuple(scope.device_ids)
        if isinstance(scope, Group):
            return tuple(
                item.device_id for item in current.devices if item.group == scope.name
            )
        if isinstance(scope, All):
            return tuple(item.device_id for item in current.devices)
        raise TypeError("unsupported target scope")

    def set_blacklist(
        self, scope: TargetScope, program: str, path: str = "", enabled: bool = True
    ):
        kind = CommandKind.BLACKLIST_SET if enabled else CommandKind.BLACKLIST_CLEAR
        return self.submit(
            ClassroomAction(kind, {"program": program, "path": path}), scope
        )

    def set_group(self, device_ids: Iterable[str], group: str) -> int:
        if not hasattr(self.server, "set_student_group"):
            raise RuntimeError("transport does not support device groups")
        return self.server.set_student_group(tuple(device_ids), group)

    def _resolve_registered_identity(self, device_id: str, mac: str):
        snapshot = self.classroom.snapshot()
        direct = snapshot.device(str(device_id))
        if direct is not None:
            return direct.device_id, direct.group
        normalized_mac = str(mac or "").strip().casefold()
        if normalized_mac:
            for device in snapshot.devices:
                if str(device.metadata.get("mac") or "").strip().casefold() == normalized_mac:
                    return device.device_id, device.group
        return str(device_id), "default"


__all__ = ["TeacherRuntime", "default_teacher_database_path"]
