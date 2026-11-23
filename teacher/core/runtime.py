"""Production composition root for the teacher v2 runtime."""

from __future__ import annotations

import os
import uuid
from typing import Dict, Iterable, Mapping, Optional, Set, Tuple

from common.config import get_config
from common.device_identity import normalize_mac
from common.v2.control import ControlEnvelope
from common.v2.model import (
    All,
    ClassroomAction,
    CommandKind,
    InspectionMode,
    TargetScope,
    InspectionSession,
    OperationReceipt,
    Group,
    DeviceIds,
)
from student.core.v2.power import WakeOnLanSender
from teacher.core.classroom import Classroom
from teacher.core.state_store import SQLiteStateStore
from teacher.core.teacher_server import TeacherServer
from teacher.core.media_runtime import BroadcastPublisher, InspectionReceiver
from common.v2.media_transport import TcpMediaHub
from teacher.core.remote_control import RemoteControlWindows


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
        self._remote_inspection: Set[str] = set()
        self.remote_windows = RemoteControlWindows(self._remote_closed)
        self.wol_sender = WakeOnLanSender()

    def start(self) -> None:
        if self._started:
            return
        self.server.start()
        self.inspection_media.start()
        self.tcp_media.start()
        self._started = True

    def close(self) -> None:
        self.remote_windows.close()
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
        available = [
            device_id for device_id in self._resolve_target_ids(scope)
            if device_id not in self.remote_windows.sessions
        ]
        session = self.classroom.open_inspection(
            DeviceIds(available),
            mode,
            {
                "transport": "tcp",
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
        self._remote_inspection.difference_update(receipt.target_ids)
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

    def set_usb_storage_blocked(
        self, scope: TargetScope, enabled: bool
    ) -> OperationReceipt:
        kind = (
            CommandKind.USB_STORAGE_BLOCK if enabled
            else CommandKind.USB_STORAGE_RESTORE
        )
        return self.submit(ClassroomAction(kind), scope)

    def start_broadcast(
        self,
        scope: TargetScope,
        source: Optional[Mapping[str, object]] = None,
        lock_input: bool = True,
    ):
        requested_ids = set(self._resolve_target_ids(scope))
        if not requested_ids:
            raise ValueError("当前范围没有设备")
        session_id = self._broadcast_session if self._broadcast_target_ids else str(uuid.uuid4())
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
        if not self._broadcast_target_ids:
            self.broadcast_media.start(session_id, int(source_value.get("index", 0)))
            self._broadcast_session = session_id
        self._broadcast_target_ids.update(receipt.target_ids)
        return receipt

    def stop_broadcast(self, scope: TargetScope):
        receipt = self.submit(ClassroomAction(CommandKind.BROADCAST_STOP), scope)
        self._broadcast_target_ids.difference_update(receipt.target_ids)
        if not self._broadcast_target_ids:
            self.broadcast_media.stop()
        return receipt

    def restore_all(self, scope: TargetScope):
        for device_id in self._resolve_target_ids(scope):
            self.remote_windows.stop(device_id)
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
        if device_id in self.remote_windows.sessions:
            return False
        envelope = ControlEnvelope.command(
            CommandKind.REMOTE_INPUT.value,
            dict(payload),
            device_id=str(device_id),
            revision=0,
        )
        return self.server.send(str(device_id), envelope)

    def start_remote_control(self, device_id: str):
        if device_id in self.remote_windows.sessions:
            raise RuntimeError("该设备已在 UltraVNC 会话中")
        executable = self.remote_windows.prepare()
        device = self.classroom.snapshot().device(device_id)
        if device is None or not device.online:
            raise RuntimeError("该设备不在线")
        host = str(device.metadata.get("ip") or "")
        if not host:
            raise RuntimeError("该设备没有可连接的 IP 地址")
        session_id = str(uuid.uuid4())
        receipt = self.submit(
            ClassroomAction(
                CommandKind.REMOTE_CONTROL_START,
                {
                    "mode": InspectionMode.REMOTE_CONTROL.value,
                    "session_id": session_id,
                    "backend": "ultravnc",
                },
            ),
            TargetScope.devices([device_id]),
        )
        if device_id in self._inspection_sessions:
            self._remote_inspection.add(device_id)
        self._unregister_inspection(device_id)
        self.remote_windows.add(device_id, host, receipt.command_id, executable)
        return InspectionSession(
            session_id=session_id,
            command_id=receipt.command_id,
            mode=InspectionMode.REMOTE_CONTROL,
            target_ids=(device_id,),
            opened_at=receipt.submitted_at,
            expires_at=receipt.expires_at or receipt.submitted_at + 10,
        )

    def stop_remote_control(self, device_id: str):
        if device_id in self.remote_windows.sessions:
            self.remote_windows.stop(device_id)
            return
        return self._remote_closed(device_id)

    def _remote_closed(self, device_id: str):
        receipt = self.submit(
            ClassroomAction(CommandKind.REMOTE_CONTROL_STOP),
            TargetScope.devices([device_id]),
        )
        self._unregister_inspection(device_id)
        if device_id in self._remote_inspection:
            self._remote_inspection.discard(device_id)
            self.open_inspection(DeviceIds([device_id]))
        return receipt

    def poll_remote_windows(self) -> tuple:
        self.remote_windows.poll(self.classroom.snapshot())
        messages = tuple(self.remote_windows.messages)
        self.remote_windows.messages.clear()
        return messages

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

    def set_global_blacklist(self, program: str, enabled: bool = True) -> OperationReceipt:
        return self.classroom.set_global_blacklist(program, enabled)

    def set_application_control_enabled(self, enabled: bool) -> Tuple[OperationReceipt, ...]:
        return self.classroom.set_application_control_enabled(enabled)

    def set_blacklist(
        self, scope: TargetScope, program: str, path: str = "", enabled: bool = True
    ) -> OperationReceipt:
        return self.classroom.set_blacklist(scope, program, path, enabled)

    def set_device_note(self, device_id: str, note: str) -> bool:
        return self.classroom.set_device_note(device_id, note)

    def set_group(self, device_ids: Iterable[str], group: str) -> int:
        if not hasattr(self.server, "set_student_group"):
            raise RuntimeError("transport does not support device groups")
        return self.server.set_student_group(tuple(device_ids), group)

    def _resolve_registered_identity(
        self, device_id: str, mac: str, machine_id: str = ""
    ):
        snapshot = self.classroom.snapshot()
        normalized_mac = normalize_mac(mac)
        machine_id = str(machine_id or "").strip()
        direct = snapshot.device(self.classroom.resolve_device_alias(str(device_id)))
        if direct is not None and machine_id:
            if direct.metadata.get("machine_id") == machine_id:
                self.classroom.consolidate_duplicate_devices(direct.device_id)
                return direct.device_id, direct.group
        if direct is not None and normalized_mac:
            previous_machine = str(direct.metadata.get("machine_id") or "")
            incompatible = machine_id and previous_machine and machine_id != previous_machine
            if not incompatible and normalize_mac(direct.metadata.get("mac")) == normalized_mac:
                self.classroom.consolidate_duplicate_devices(direct.device_id)
                return direct.device_id, direct.group
        if normalized_mac:
            for device in snapshot.devices:
                previous_machine = str(device.metadata.get("machine_id") or "")
                if machine_id and previous_machine and machine_id != previous_machine:
                    continue
                if normalize_mac(device.metadata.get("mac")) == normalized_mac:
                    self.classroom.consolidate_duplicate_devices(device.device_id)
                    return device.device_id, device.group
        if direct is not None:
            previous_machine = str(direct.metadata.get("machine_id") or "")
            if machine_id and previous_machine and machine_id != previous_machine:
                # 不同硬件即使复制同一学生数据库、且原机离线，也不能吞并座位。
                return str(uuid.uuid5(
                    uuid.NAMESPACE_OID, f"lan-classroom:{device_id}:machine:{machine_id}"
                )), "default"
            previous_mac = normalize_mac(direct.metadata.get("mac"))
            if direct.online and previous_mac and normalized_mac and previous_mac != normalized_mac:
                # 旧客户端缺少硬件提示时，仅在在线冲突时按不同网卡区分克隆机。
                return str(uuid.uuid5(
                    uuid.NAMESPACE_OID, f"lan-classroom:{device_id}:{normalized_mac}"
                )), "default"
            return direct.device_id, direct.group
        return str(device_id), "default"


__all__ = ["TeacherRuntime", "default_teacher_database_path"]
