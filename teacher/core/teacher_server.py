import os
import base64
import time
import threading
from dataclasses import replace
from typing import List, Optional, Callable
from common.protocol import MessageType, build_message, parse_message_type
from common.tcp_conn import TCPServer, TCPConnection
from common.discover import TeacherDiscover
from common.heartbeat import TeacherHeartbeatManager
from common.logger import get_logger
from common.config import get_config
from common.v2.control import AckStage, ControlEnvelope, MessageKind
from teacher.core.classroom import TransportAck, TransportDevice
from teacher.core.student_manager import StudentManager, StudentInfo
from teacher.core.screen_broadcast import ScreenBroadcaster
from teacher.core.file_distributor import FileDistributor
from teacher.core.update_server import UpdateServer

logger = get_logger("teacher_core")


class TeacherServer:
    def __init__(self, tcp_port: int = 9528, broadcast_port: int = 9527):
        self.config = get_config()
        self.tcp_port = tcp_port
        self.broadcast_port = broadcast_port

        self.tcp_server = TCPServer(port=tcp_port)
        self.discover = TeacherDiscover(
            broadcast_port=broadcast_port,
            tcp_port=tcp_port
        )
        self.heartbeat_manager = TeacherHeartbeatManager(
            timeout=int(self.config.get("teacher.heartbeat_timeout", 10))
        )
        self.student_manager = StudentManager()
        self.screen_broadcaster = ScreenBroadcaster(fps=20, quality=70)
        self.file_distributor = FileDistributor()
        self.update_server = UpdateServer(self.student_manager, self.file_distributor)

        self.on_student_added: Optional[Callable] = None
        self.on_student_removed: Optional[Callable] = None
        self.on_student_changed: Optional[Callable] = None
        self.on_discovered_student: Optional[Callable] = None
        self.on_discovered_lost: Optional[Callable] = None
        self.on_student_screen_frame: Optional[Callable] = None
        self._v2_ack_handler: Optional[Callable[[TransportAck], None]] = None
        self._v2_device_handler: Optional[Callable[[TransportDevice], None]] = None
        self._v2_identity_resolver: Optional[Callable[[str, str, str], tuple]] = None
        self._teacher_heartbeat_stop = threading.Event()
        self._teacher_heartbeat_thread = None
        self._heartbeat_pending = set()
        self._heartbeat_pending_lock = threading.Lock()
        self._registration_lock = threading.RLock()

        self._setup_callbacks()

    def _setup_callbacks(self):
        self.tcp_server.on_connect = self._on_connect
        self.tcp_server.on_message = self._on_message
        self.tcp_server.on_disconnect = self._on_disconnect

        self.heartbeat_manager.on_student_timeout = self._on_student_timeout

        self.student_manager.on_student_added = self._forward_student_added
        self.student_manager.on_student_removed = self._forward_student_removed
        self.student_manager.on_student_changed = self._forward_student_changed

        self.discover.on_student_discovered = self._on_discovered_student
        self.discover.on_student_lost = self._on_discovered_lost

    def start(self):
        self.tcp_server.start()
        self.discover.start()
        self.heartbeat_manager.start()
        self._start_teacher_heartbeat()
        logger.info(f"Teacher server started, TCP port: {self.tcp_port}")

    def stop(self):
        self._stop_teacher_heartbeat()
        self.screen_broadcaster.stop()
        self.heartbeat_manager.stop()
        self.discover.stop()
        self.tcp_server.stop()
        logger.info("Teacher server stopped")

    def _start_teacher_heartbeat(self):
        if self._teacher_heartbeat_thread and self._teacher_heartbeat_thread.is_alive():
            return
        self._teacher_heartbeat_stop.clear()
        self._teacher_heartbeat_thread = threading.Thread(
            target=self._teacher_heartbeat_loop,
            name="teacher-lease-heartbeat",
            daemon=True,
        )
        self._teacher_heartbeat_thread.start()

    def _stop_teacher_heartbeat(self):
        self._teacher_heartbeat_stop.set()
        thread = self._teacher_heartbeat_thread
        self._teacher_heartbeat_thread = None
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2)

    def _teacher_heartbeat_loop(self):
        interval = max(1.0, float(self.config.get("teacher.heartbeat_interval", 3)))
        lease_seconds = float(self.config.get("teacher.control_lease_seconds", 10))
        while not self._teacher_heartbeat_stop.is_set():
            message = build_message(
                MessageType.TEACHER_HEARTBEAT,
                {"timestamp": time.time(), "lease_seconds": lease_seconds},
            )
            self._send_teacher_heartbeat(message)
            self._teacher_heartbeat_stop.wait(interval)

    def _send_teacher_heartbeat(self, message: dict) -> None:
        for connection in self.tcp_server.get_all_connections():
            with self._heartbeat_pending_lock:
                if connection in self._heartbeat_pending:
                    continue
                self._heartbeat_pending.add(connection)
            threading.Thread(
                target=self._send_one_heartbeat, args=(connection, message),
                name="teacher-device-heartbeat", daemon=True,
            ).start()

    def _send_one_heartbeat(self, connection, message: dict) -> None:
        try:
            connection.send_message(message)
        finally:
            with self._heartbeat_pending_lock:
                self._heartbeat_pending.discard(connection)

    def _on_connect(self, conn: TCPConnection):
        pass

    def _on_message(self, conn: TCPConnection, msg: dict):
        try:
            if msg.get("kind") == MessageKind.ACK.value:
                self._handle_v2_ack(conn, msg)
                return
            msg_type = parse_message_type(msg)
            params = msg.get("params", {})
            if msg_type == MessageType.STUDENT_REGISTER:
                self._handle_student_register(conn, params)
            elif msg_type == MessageType.STUDENT_HEARTBEAT:
                self._handle_heartbeat(conn, params)
            elif msg_type == MessageType.FILE_SEND_ACK:
                self._handle_file_ack(conn, params)
            elif msg_type == MessageType.UPDATE_REQUEST:
                self.update_server.on_update_request(conn, params)
            elif msg_type == MessageType.UPDATE_PROGRESS:
                self.update_server.on_update_progress(conn, params)
            elif msg_type == MessageType.STUDENT_SCREEN_FRAME:
                self._handle_student_screen_frame(conn, params)
            elif msg_type == MessageType.COMMAND_ACK:
                self._handle_command_ack(conn, params)
        except Exception as e:
            logger.error(f"Handle message error from {conn.addr}: {e}")

    def _on_disconnect(self, conn: TCPConnection):
        with self._registration_lock:
            self._disconnect_student(conn)

    def _disconnect_student(self, conn: TCPConnection):
        if conn.student_id:
            current = self.student_manager.get_student(conn.student_id)
            if current is not None and current.conn is not conn:
                self.screen_broadcaster.remove_target(conn)
                return
            self.student_manager.set_student_offline(conn.student_id)
            self.heartbeat_manager.remove_student(conn.student_id)
            self.screen_broadcaster.remove_target(conn)
            self._emit_v2_device(conn.student_id, online=False)

    def _on_student_timeout(self, student_id: str):
        self.student_manager.set_student_offline(student_id)
        conn = self.tcp_server.get_connection(student_id)
        if conn:
            conn.stop()

    def _handle_student_register(self, conn: TCPConnection, params: dict):
        # 身份解析与连接替换必须串行，否则同时重连会各自创建一个座位。
        with self._registration_lock:
            self._register_student(conn, params)

    def _register_student(self, conn: TCPConnection, params: dict):
        student_id = params.get("student_id", "")
        hostname = params.get("hostname", "unknown")
        ip = conn.addr[0] if conn.addr and conn.addr[0] else params.get("ip", "")
        mac = params.get("mac", "")
        machine_id = str(params.get("machine_id") or "").strip()
        version = params.get("version", "")
        protocol_version = int(params.get("protocol_version", 1) or 1)

        existing_by_mac = self.student_manager.find_by_mac(mac) if mac else None
        replaced_connection = None
        resolved_group = "default"
        if self._v2_identity_resolver is not None:
            resolved = self._v2_identity_resolver(str(student_id), str(mac), machine_id)
            if resolved:
                student_id, resolved_group = str(resolved[0]), str(resolved[1])
        elif existing_by_mac and existing_by_mac.student_id != student_id:
            student_id = existing_by_mac.student_id
            resolved_group = existing_by_mac.group

        existing = self.student_manager.get_student(student_id)
        if existing is not None and existing.conn is not conn:
            replaced_connection = existing.conn
        if existing is not None and not machine_id:
            machine_id = str(existing.status.get("machine_id") or "")

        conn.student_id = student_id
        student = StudentInfo(
            student_id=student_id,
            hostname=hostname,
            ip=ip,
            mac=mac,
            version=version,
            conn=conn
        )
        student.group = resolved_group
        student.status["protocol_version"] = protocol_version
        student.status["capabilities"] = list(params.get("capabilities", []))
        student.status["machine_id"] = machine_id
        self.student_manager.add_student(student)
        self.heartbeat_manager.register_student(student_id)
        logger.info(f"Student registered: {hostname} ({ip}) [{mac}]")
        self._emit_v2_device(student_id, online=True)
        if replaced_connection is not None:
            replaced_connection.stop()

        # 学生注册成功后，如果有待推送的更新包，自动通知新注册的学生
        # 覆盖离线重连后补更新场景
        if self.update_server.update_file_path and self.update_server.update_version:
            try:
                self.update_server.notify_update([student_id])
            except Exception as e:
                logger.warning(f"Auto notify update to {hostname} failed: {e}")

    def _handle_heartbeat(self, conn: TCPConnection, params: dict):
        with self._registration_lock:
            current = self.student_manager.get_student(conn.student_id) if conn.student_id else None
            if current is None or current.conn is not conn:
                return
            # 被替换连接的迟到心跳不能把已离线的新会话重新标记为在线。
            self.heartbeat_manager.record_heartbeat(conn.student_id)
            self.student_manager.update_heartbeat(conn.student_id)

    def _handle_file_ack(self, conn: TCPConnection, params: dict):
        transfer_id = params.get("transfer_id", "")
        success = params.get("success", False)
        normalized = dict(params)
        if conn.student_id:
            normalized["student_id"] = conn.student_id
        self.file_distributor.record_ack(normalized)
        logger.debug(f"File ack: {transfer_id}, success={success}")

    def _handle_command_ack(self, conn: TCPConnection, params: dict):
        command = params.get("command", "")
        if command == "net_control":
            logger.info("Ignored net_control ack because network control is disabled")
        return

    def _handle_v2_ack(self, conn: TCPConnection, msg: dict):
        if self._v2_ack_handler is None:
            return
        try:
            envelope = ControlEnvelope(
                version=int(msg.get("version", 0)),
                kind=MessageKind(msg.get("kind")),
                command_id=str(msg.get("command_id", "")),
                device_id=conn.student_id or msg.get("device_id"),
                revision=int(msg.get("revision", 0)),
                issued_at=int((msg.get("timestamps") or {}).get("issued_at", 0)),
                lease_expires_at=(msg.get("timestamps") or {}).get("lease_expires_at"),
                action=msg.get("action"),
                stage=AckStage(msg.get("stage")),
                error=msg.get("error"),
                payload=dict(msg.get("payload") or {}),
            )
            if envelope.kind is not MessageKind.ACK or not envelope.device_id:
                return
            self._v2_ack_handler(
                TransportAck(
                    command_id=envelope.command_id,
                    device_id=envelope.device_id,
                    phase=envelope.stage,
                    revision=envelope.revision,
                    success=envelope.error is None,
                    detail=envelope.error or str(envelope.payload.get("detail", "")),
                    payload=dict(envelope.payload),
                )
            )
        except Exception as e:
            logger.warning(f"Invalid v2 ACK from {conn.addr}: {e}")

    # Classroom control transport interface.  The orchestration module owns
    # command semantics; this adapter only maps devices and envelopes to TCP.
    def set_ack_handler(self, handler: Callable[[TransportAck], None]):
        self._v2_ack_handler = handler

    def set_device_handler(self, handler: Callable[[TransportDevice], None]):
        self._v2_device_handler = handler

    def set_identity_resolver(self, resolver: Callable[[str, str, str], tuple]):
        self._v2_identity_resolver = resolver

    def set_student_group(self, student_ids, group: str) -> int:
        group_name = str(group or "").strip()
        if not group_name:
            raise ValueError("group must not be empty")
        changed = 0
        for student_id in student_ids:
            if self.student_manager.get_student(str(student_id)) is None:
                continue
            self.student_manager.set_group(str(student_id), group_name)
            self._emit_v2_device(str(student_id), online=True)
            changed += 1
        return changed

    def devices(self):
        return tuple(self._to_transport_device(student) for student in self.student_manager.get_all_students())

    def send(self, device_id: str, envelope: ControlEnvelope) -> bool:
        student = self.student_manager.get_student(device_id)
        if not student or not student.conn or not student.conn.is_alive():
            return False
        targeted = replace(envelope, device_id=str(device_id))
        return student.conn.send_message(targeted.to_dict())

    def _emit_v2_device(self, student_id: str, online: bool):
        if self._v2_device_handler is None:
            return
        student = self.student_manager.get_student(student_id)
        if student is None:
            return
        student.online = online
        self._v2_device_handler(self._to_transport_device(student))

    @staticmethod
    def _to_transport_device(student: StudentInfo) -> TransportDevice:
        return TransportDevice(
            device_id=student.student_id,
            hostname=student.hostname,
            group=student.group,
            online=student.online,
            last_seen=student.last_heartbeat,
            metadata={
                "ip": student.ip,
                "mac": student.mac,
                "machine_id": student.status.get("machine_id", ""),
                "version": student.version,
                "display_name": student.display_name,
                "protocol_version": int(student.status.get("protocol_version", 1)),
                "capabilities": list(student.status.get("capabilities", [])),
            },
        )

    def _handle_student_screen_frame(self, conn: TCPConnection, params: dict):
        student_id = conn.student_id or params.get("student_id")
        frame_b64 = params.get("frame_data", "")
        width = params.get("width", 0)
        height = params.get("height", 0)
        if not student_id or not frame_b64 or width <= 0 or height <= 0:
            return
        try:
            frame_data = base64.b64decode(frame_b64)
        except Exception as e:
            logger.debug(f"Decode student screen frame failed: {e}")
            return
        student = self.student_manager.get_student(student_id)
        if student:
            student.status["last_screen_frame_time"] = time.time()
        if self.on_student_screen_frame:
            self.on_student_screen_frame(student_id, frame_data, (width, height))

    def _forward_student_added(self, student: StudentInfo):
        if self.on_student_added:
            self.on_student_added(student)

    def _forward_student_removed(self, student: StudentInfo):
        if self.on_student_removed:
            self.on_student_removed(student)

    def _forward_student_changed(self):
        if self.on_student_changed:
            self.on_student_changed()

    def send_black_screen(self, student_ids: List[str], enable: bool):
        msg = build_message(MessageType.BLACK_SCREEN, {"enable": enable})
        for sid in student_ids:
            student = self.student_manager.get_student(sid)
            if student and student.conn and student.conn.is_alive():
                student.conn.send_message(msg)
                student.status["black_screen"] = enable
        if self.on_student_changed:
            self.on_student_changed()

    def send_black_screen_all(self, enable: bool):
        students = self.student_manager.get_online_students()
        sids = [s.student_id for s in students]
        self.send_black_screen(sids, enable)

    def start_broadcast(self, student_ids: List[str] = None):
        if not self.screen_broadcaster.is_running:
            self.screen_broadcaster.start()
        if student_ids is None:
            students = self.student_manager.get_online_students()
        else:
            students = [self.student_manager.get_student(sid) for sid in student_ids
                        if self.student_manager.get_student(sid)]
        targets = []
        start_msg = build_message(MessageType.BROADCAST_START, {
            "fps": self.screen_broadcaster.fps,
            "quality": self.screen_broadcaster.quality,
        })
        for student in students:
            if student and student.conn and student.conn.is_alive():
                student.conn.send_message(start_msg)
                targets.append(student.conn)
                student.status["broadcasting"] = True
        self.screen_broadcaster.set_targets(targets)
        if self.on_student_changed:
            self.on_student_changed()
        logger.info(f"Broadcast started to {len(targets)} students")

    def stop_broadcast(self, student_ids: List[str] = None):
        if student_ids is None:
            students = self.student_manager.get_online_students()
            self.screen_broadcaster.stop()
        else:
            students = [self.student_manager.get_student(sid) for sid in student_ids
                        if self.student_manager.get_student(sid)]
        stop_msg = build_message(MessageType.BROADCAST_STOP, {})
        for student in students:
            if student and student.conn and student.conn.is_alive():
                student.conn.send_message(stop_msg)
                student.status["broadcasting"] = False
                if student_ids is not None:
                    self.screen_broadcaster.remove_target(student.conn)
        if self.on_student_changed:
            self.on_student_changed()
        logger.info("Broadcast stopped")

    def send_net_control(self, student_ids: List[str], enable: bool, whitelist: List[str] = None):
        logger.info(
            "Network control is disabled; ignored request for %d students, enable=%s",
            len(student_ids or []),
            enable,
        )

    def send_net_control_all(self, enable: bool, whitelist: List[str] = None):
        students = self.student_manager.get_online_students()
        sids = [s.student_id for s in students]
        self.send_net_control(sids, enable, whitelist)

    def refresh_connections(self) -> dict:
        checked = 0
        refreshed = 0
        marked_offline = 0
        probe_msg = build_message(MessageType.STUDENT_HEARTBEAT, {
            "probe": True,
            "timestamp": time.time(),
        })

        for student in self.student_manager.get_all_students():
            checked += 1
            conn = student.conn or self.tcp_server.get_connection(student.student_id)
            alive = bool(conn and conn.is_alive())
            if student.online and alive:
                if conn.send_message(probe_msg):
                    refreshed += 1
                    self.heartbeat_manager.record_heartbeat(student.student_id)
                    continue
                alive = False

            if not alive:
                if student.online:
                    self.student_manager.set_student_offline(student.student_id)
                    self.heartbeat_manager.remove_student(student.student_id)
                    marked_offline += 1
                if conn:
                    conn.stop()
                if student.ip:
                    self.discover.send_discover_to(student.ip)

        self.discover.scan_once()
        logger.info(
            "Refresh connections: checked=%d, alive=%d, offline=%d",
            checked,
            refreshed,
            marked_offline,
        )
        return {
            "checked": checked,
            "alive": refreshed,
            "offline": marked_offline,
        }

    def send_file(self, file_path: str, student_ids: List[str] = None,
                  progress_callback: Optional[Callable] = None,
                  target_dir_type: str = "default",
                  target_dir_path: str = "") -> str:
        if student_ids is None:
            students = self.student_manager.get_online_students()
        else:
            students = [self.student_manager.get_student(sid) for sid in student_ids
                        if self.student_manager.get_student(sid)]
        return self.file_distributor.send_file_to_students(
            file_path, students, progress_callback,
            target_dir_type=target_dir_type,
            target_dir_path=target_dir_path
        )

    def send_file_all(self, file_path: str, progress_callback=None,
                      target_dir_type: str = "default",
                      target_dir_path: str = "") -> str:
        students = self.student_manager.get_online_students()
        return self.file_distributor.send_file_to_students(
            file_path, students, progress_callback,
            target_dir_type=target_dir_type,
            target_dir_path=target_dir_path
        )

    def push_update(self, file_path: str, version: str,
                    student_ids: Optional[List[str]] = None) -> int:
        """设置更新包并向学生端通知有新版本。

        Args:
            file_path: 更新包 zip 文件路径
            version: 新版本号
            student_ids: 指定学生 ID 列表，None 表示所有在线学生

        Returns: 通知到的学生数量
        """
        if not self.update_server.set_update_package(file_path, version):
            return 0
        return self.update_server.notify_update(student_ids)

    def get_update_progress(self) -> dict:
        """获取各学生端的更新进度。"""
        return self.update_server.get_update_progress()

    def _on_discovered_student(self, info: dict):
        if self.on_discovered_student:
            self.on_discovered_student(info)

    def _on_discovered_lost(self, info: dict):
        if self.on_discovered_lost:
            self.on_discovered_lost(info)

    def get_discovered_students(self) -> List[dict]:
        return self.discover.get_discovered_students()

    def add_discovered_student(self, info: dict):
        ip = info.get("ip", "")
        if ip:
            self.discover.send_discover_to(ip)
            logger.info(f"Adding discovered student: {info.get('hostname')} ({ip})")

    def scan_devices(self):
        self.discover.scan_once()
