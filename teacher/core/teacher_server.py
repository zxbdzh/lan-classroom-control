import os
from typing import List, Optional, Callable
from common.protocol import MessageType, build_message, parse_message_type
from common.tcp_conn import TCPServer, TCPConnection
from common.discover import TeacherDiscover
from common.heartbeat import TeacherHeartbeatManager
from common.logger import get_logger
from teacher.core.student_manager import StudentManager, StudentInfo
from teacher.core.screen_broadcast import ScreenBroadcaster
from teacher.core.file_distributor import FileDistributor

logger = get_logger("teacher_core")


class TeacherServer:
    def __init__(self, tcp_port: int = 9528, broadcast_port: int = 9527):
        self.tcp_port = tcp_port
        self.broadcast_port = broadcast_port

        self.tcp_server = TCPServer(port=tcp_port)
        self.discover = TeacherDiscover(
            broadcast_port=broadcast_port,
            tcp_port=tcp_port
        )
        self.heartbeat_manager = TeacherHeartbeatManager(timeout=15)
        self.student_manager = StudentManager()
        self.screen_broadcaster = ScreenBroadcaster(fps=20, quality=70)
        self.file_distributor = FileDistributor()

        self.on_student_added: Optional[Callable] = None
        self.on_student_removed: Optional[Callable] = None
        self.on_student_changed: Optional[Callable] = None
        self.on_discovered_student: Optional[Callable] = None
        self.on_discovered_lost: Optional[Callable] = None

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
        logger.info(f"Teacher server started, TCP port: {self.tcp_port}")

    def stop(self):
        self.screen_broadcaster.stop()
        self.heartbeat_manager.stop()
        self.discover.stop()
        self.tcp_server.stop()
        logger.info("Teacher server stopped")

    def _on_connect(self, conn: TCPConnection):
        pass

    def _on_message(self, conn: TCPConnection, msg: dict):
        try:
            msg_type = parse_message_type(msg)
            params = msg.get("params", {})
            if msg_type == MessageType.STUDENT_REGISTER:
                self._handle_student_register(conn, params)
            elif msg_type == MessageType.STUDENT_HEARTBEAT:
                self._handle_heartbeat(conn, params)
            elif msg_type == MessageType.FILE_SEND_ACK:
                self._handle_file_ack(params)
        except Exception as e:
            logger.error(f"Handle message error from {conn.addr}: {e}")

    def _on_disconnect(self, conn: TCPConnection):
        if conn.student_id:
            self.student_manager.set_student_offline(conn.student_id)
            self.heartbeat_manager.remove_student(conn.student_id)
            self.screen_broadcaster.remove_target(conn)

    def _on_student_timeout(self, student_id: str):
        self.student_manager.set_student_offline(student_id)
        conn = self.tcp_server.get_connection(student_id)
        if conn:
            conn.stop()

    def _handle_student_register(self, conn: TCPConnection, params: dict):
        student_id = params.get("student_id", "")
        hostname = params.get("hostname", "unknown")
        ip = params.get("ip", conn.addr[0])
        mac = params.get("mac", "")
        version = params.get("version", "")

        existing_by_mac = self.student_manager.find_by_mac(mac) if mac else None
        if existing_by_mac and existing_by_mac.student_id != student_id:
            student_id = existing_by_mac.student_id

        conn.student_id = student_id
        student = StudentInfo(
            student_id=student_id,
            hostname=hostname,
            ip=ip,
            mac=mac,
            version=version,
            conn=conn
        )
        self.student_manager.add_student(student)
        self.heartbeat_manager.register_student(student_id)
        logger.info(f"Student registered: {hostname} ({ip}) [{mac}]")

    def _handle_heartbeat(self, conn: TCPConnection, params: dict):
        if conn.student_id:
            self.heartbeat_manager.record_heartbeat(conn.student_id)

    def _handle_file_ack(self, params: dict):
        transfer_id = params.get("transfer_id", "")
        success = params.get("success", False)
        logger.debug(f"File ack: {transfer_id}, success={success}")

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
        msg = build_message(MessageType.NET_CONTROL, {
            "enable": enable,
            "whitelist": whitelist or []
        })
        for sid in student_ids:
            student = self.student_manager.get_student(sid)
            if student and student.conn and student.conn.is_alive():
                student.conn.send_message(msg)
                student.status["net_blocked"] = enable
        if self.on_student_changed:
            self.on_student_changed()

    def send_net_control_all(self, enable: bool, whitelist: List[str] = None):
        students = self.student_manager.get_online_students()
        sids = [s.student_id for s in students]
        self.send_net_control(sids, enable, whitelist)

    def send_file(self, file_path: str, student_ids: List[str] = None,
                  progress_callback: Optional[Callable] = None) -> str:
        if student_ids is None:
            students = self.student_manager.get_online_students()
        else:
            students = [self.student_manager.get_student(sid) for sid in student_ids
                        if self.student_manager.get_student(sid)]
        return self.file_distributor.send_file_to_students(
            file_path, students, progress_callback
        )

    def send_file_all(self, file_path: str, progress_callback=None) -> str:
        students = self.student_manager.get_online_students()
        return self.file_distributor.send_file_to_students(
            file_path, students, progress_callback
        )

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
