import socket
import uuid
import os
import base64
import threading
from typing import Optional, Callable
from common.protocol import MessageType, build_message, parse_message_type
from common.tcp_conn import TCPClient
from common.discover import StudentDiscoverListener
from common.heartbeat import StudentHeartbeatSender
from common.logger import get_logger
from common.config import get_config
from common.version import get_version
from student.core.input_blocker import InputBlocker
from student.core.update_client import UpdateClient
from student.core import autostart
from common.file_transfer import FileTransferReceiver
from common.screen_capture import ScreenCapturer

logger = get_logger("student_core")


class StudentClient:
    def __init__(self, save_dir: str = None):
        self.config = get_config()
        self.student_id = self._load_or_generate_student_id()
        self.hostname = socket.gethostname()
        self.local_ip = self._get_local_ip()
        self.mac_address = self._get_mac()

        self.tcp_client = TCPClient()
        self.discover_listener = StudentDiscoverListener(
            on_discover_callback=self._on_teacher_discovered,
            student_id=self.student_id,
            hostname=self.hostname,
            mac=self.mac_address,
            version=get_version()
        )
        self.heartbeat_sender = StudentHeartbeatSender(interval=5)
        self.input_blocker = InputBlocker()

        if save_dir is None:
            save_dir = self._get_default_save_dir()
        self.file_receiver = FileTransferReceiver(save_dir)
        self.update_client = UpdateClient(self.tcp_client.send_message)
        self.screen_monitor = ScreenCapturer(
            fps=int(self.config.get("student.screen_monitor_fps", 1)),
            quality=int(self.config.get("student.screen_monitor_quality", 45)),
            use_diff=False,
            max_width=int(self.config.get("student.screen_monitor_max_width", 640)),
            max_height=int(self.config.get("student.screen_monitor_max_height", 360))
        )

        self._black_screen = False
        self._broadcasting = False
        self._net_blocked = False

        self.on_black_screen_changed: Optional[Callable] = None
        self.on_broadcast_started: Optional[Callable] = None
        self.on_broadcast_stopped: Optional[Callable] = None
        self.on_screen_frame: Optional[Callable] = None
        self.on_file_received: Optional[Callable] = None
        self.on_file_progress: Optional[Callable] = None
        self.on_connected: Optional[Callable] = None
        self.on_disconnected: Optional[Callable] = None
        self.on_update_state: Optional[Callable] = None

        # 启动时确保开机自启已开启
        try:
            autostart.ensure_autostart()
        except Exception as e:
            logger.warning(f"Ensure autostart failed: {e}")

        self._setup_callbacks()

    def _get_default_save_dir(self) -> str:
        """获取桌面路径，fallback到Downloads"""
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        if os.path.isdir(desktop):
            return desktop
        downloads = os.path.join(os.path.expanduser("~"), "Downloads")
        if os.path.isdir(downloads):
            return os.path.join(downloads, "LanClassroom")
        return os.path.expanduser("~")

    def _get_local_ip(self) -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def _get_mac(self) -> str:
        try:
            import psutil
            local_ip = self.local_ip
            target_mac = None
            first_valid_mac = None
            for name, addrs in psutil.net_if_addrs().items():
                has_ip = False
                mac_addr = ""
                for addr in addrs:
                    if addr.family == socket.AF_INET and addr.address == local_ip:
                        has_ip = True
                    try:
                        if (addr.family == getattr(psutil, 'AF_LINK', None) or
                            (hasattr(addr, 'address') and
                             isinstance(addr.address, str) and
                             len(addr.address.split(':')) == 6 and
                             addr.address != '00:00:00:00:00:00')):
                            if addr.address and addr.address != '00:00:00:00:00:00':
                                mac_addr = addr.address
                                if first_valid_mac is None:
                                    first_valid_mac = mac_addr
                    except Exception:
                        pass
                if has_ip and mac_addr:
                    target_mac = mac_addr
                    break
            if target_mac:
                return target_mac
            if first_valid_mac:
                return first_valid_mac
        except Exception as e:
            logger.warning(f"Get MAC via psutil failed: {e}")
        try:
            import uuid
            node = uuid.getnode()
            mac = ':'.join(['{:02x}'.format((node >> i) & 0xff) for i in range(0, 48, 8)][::-1])
            if mac != '00:00:00:00:00:00':
                return mac
        except Exception as e:
            logger.warning(f"Get MAC via uuid failed: {e}")
        return "00:00:00:00:00:00"

    def _load_or_generate_student_id(self) -> str:
        saved_id = self.config.get("student.student_id", "")
        if saved_id:
            return saved_id
        new_id = str(uuid.uuid4())
        self.config.set("student.student_id", new_id)
        self.config.save()
        return new_id

    def _setup_callbacks(self):
        self.tcp_client.on_connect = self._on_connected
        self.tcp_client.on_message = self._on_message
        self.tcp_client.on_disconnect = self._on_disconnected

    def start(self):
        self.discover_listener.start()
        logger.info(f"Student client started: {self.hostname} ({self.local_ip})")

    def stop(self):
        self._stop_screen_monitor()
        self.heartbeat_sender.stop()
        self.tcp_client.disconnect()
        self.discover_listener.stop()
        if self._black_screen:
            self.input_blocker.unblock()
        logger.info("Student client stopped")

    def _on_teacher_discovered(self, teacher_ip: str, tcp_port: int, teacher_name: str):
        if not self.tcp_client.is_connected():
            logger.info(f"Connecting to teacher {teacher_name} at {teacher_ip}:{tcp_port}")
            self.tcp_client.connect(teacher_ip, tcp_port)

    def _on_connected(self):
        register_msg = build_message(MessageType.STUDENT_REGISTER, {
            "student_id": self.student_id,
            "hostname": self.hostname,
            "ip": self.local_ip,
            "mac": self.mac_address,
            "version": get_version()
        })
        self.tcp_client.send_message(register_msg)
        self.heartbeat_sender.start(self.tcp_client.send_message)
        self._start_screen_monitor()
        if self.on_connected:
            self.on_connected()

    def _on_disconnected(self):
        logger.info("Disconnected from teacher")
        self._stop_screen_monitor()
        if self._black_screen:
            self.input_blocker.unblock()
            self._black_screen = False
            if self.on_black_screen_changed:
                self.on_black_screen_changed(False)
        if self._broadcasting:
            self._broadcasting = False
            if self.on_broadcast_stopped:
                self.on_broadcast_stopped()
        if self.on_disconnected:
            self.on_disconnected()

    def _start_screen_monitor(self):
        if os.environ.get("LAN_CLASSROOM_DISABLE_SCREEN_MONITOR") == "1":
            return
        if not self.config.get("student.screen_monitor_enabled", True):
            return
        try:
            self.screen_monitor.start(self._send_screen_frame)
        except Exception as e:
            logger.warning(f"Start screen monitor failed: {e}")

    def _stop_screen_monitor(self):
        try:
            self.screen_monitor.stop()
        except Exception as e:
            logger.debug(f"Stop screen monitor failed: {e}")

    def _send_screen_frame(self, frame_data: bytes, frame_size: tuple):
        if not self.tcp_client.is_connected():
            return
        if not frame_data or not frame_size:
            return
        try:
            width, height = frame_size
            msg = build_message(MessageType.STUDENT_SCREEN_FRAME, {
                "student_id": self.student_id,
                "width": width,
                "height": height,
                "frame_data": base64.b64encode(frame_data).decode("ascii")
            })
            self.tcp_client.send_message(msg)
        except Exception as e:
            logger.debug(f"Send screen frame failed: {e}")

    def _on_message(self, msg: dict):
        try:
            msg_type = parse_message_type(msg)
            params = msg.get("params", {})
            if msg_type == MessageType.BLACK_SCREEN:
                self._handle_black_screen(params)
            elif msg_type == MessageType.BROADCAST_START:
                self._handle_broadcast_start(params)
            elif msg_type == MessageType.BROADCAST_STOP:
                self._handle_broadcast_stop(params)
            elif msg_type == MessageType.BROADCAST_FRAME:
                self._handle_broadcast_frame(params)
            elif msg_type == MessageType.NET_CONTROL:
                self._handle_net_control(params)
            elif msg_type == MessageType.FILE_SEND_START:
                self._handle_file_start(params)
            elif msg_type == MessageType.FILE_SEND_DATA:
                self._handle_file_data(params)
            elif msg_type == MessageType.FILE_SEND_END:
                self._handle_file_end(params)
            elif msg_type == MessageType.UPDATE_CHECK:
                self._handle_update_check(params)
            elif msg_type == MessageType.STUDENT_HEARTBEAT:
                pass
        except Exception as e:
            logger.error(f"Handle message error: {e}")

    def _handle_black_screen(self, params: dict):
        enable = params.get("enable", False)
        self._black_screen = enable
        if enable:
            if self.on_black_screen_changed:
                self.on_black_screen_changed(enable)
            threading.Thread(target=self.input_blocker.block, daemon=True).start()
        else:
            threading.Thread(target=self.input_blocker.unblock, daemon=True).start()
            if self.on_black_screen_changed:
                self.on_black_screen_changed(enable)
        logger.info(f"Black screen: {enable}")

    def _handle_broadcast_start(self, params: dict):
        self._broadcasting = True
        if self.on_broadcast_started:
            self.on_broadcast_started(params)
        threading.Thread(target=self.input_blocker.block, daemon=True).start()
        logger.info("Broadcast started")

    def _handle_broadcast_stop(self, params: dict):
        self._broadcasting = False
        # 异步解除键鼠禁用，避免 pynput stop 阻塞主线程（Win7上较慢）
        threading.Thread(target=self.input_blocker.unblock, daemon=True).start()
        if self.on_broadcast_stopped:
            self.on_broadcast_stopped()
        logger.info("Broadcast stopped")

    def _handle_broadcast_frame(self, params: dict):
        if self._broadcasting and self.on_screen_frame:
            frame_b64 = params.get("frame_data", "")
            width = params.get("width", 0)
            height = params.get("height", 0)
            if frame_b64 and width > 0 and height > 0:
                try:
                    frame_data = base64.b64decode(frame_b64)
                    self.on_screen_frame(frame_data, (width, height))
                except Exception as e:
                    logger.debug(f"Decode frame data error: {e}")

    def _handle_net_control(self, params: dict):
        enable = params.get("enable", False)
        self._net_blocked = False
        self._send_command_ack("net_control", False, {
            "enable": enable,
            "net_blocked": False,
            "admin": False,
            "message": "Net control disabled in this build",
        })
        logger.info(f"Net control ignored because feature is disabled: enable={enable}")

    def _send_command_ack(self, command: str, success: bool, params: dict = None):
        if not self.tcp_client.is_connected():
            return
        ack_params = {
            "student_id": self.student_id,
            "command": command,
            "success": success,
        }
        if params:
            ack_params.update(params)
        try:
            self.tcp_client.send_message(build_message(MessageType.COMMAND_ACK, ack_params))
        except Exception as e:
            logger.debug(f"Send command ack failed: {e}")

    def _handle_file_start(self, params: dict):
        # 更新包走 update_client，普通文件走 file_receiver
        if params.get("is_update", False):
            self.update_client.handle_start(params)
            return
        self.file_receiver.handle_start(params)
        if self.on_file_progress:
            self.on_file_progress(params.get("file_name", ""), 0.0, params.get("file_size", 0))

    def _handle_file_data(self, params: dict):
        # 判断当前 transfer_id 属于哪个接收器
        if self.update_client._active_transfer and \
                self.update_client._active_transfer.get("transfer_id") == params.get("transfer_id", ""):
            self.update_client.handle_data(params)
            return
        self.file_receiver.handle_data(params)
        if self.on_file_progress:
            transfer_id = params.get("transfer_id", "")
            progress = self.file_receiver.get_progress(transfer_id)
            self.on_file_progress("", progress, 1.0)

    def _handle_file_end(self, params: dict):
        # 更新包的结束处理会触发自动重启
        if self.update_client._active_transfer and \
                self.update_client._active_transfer.get("transfer_id") == params.get("transfer_id", ""):
            self.update_client.handle_end(params)
            return
        success, result = self.file_receiver.handle_end(params)
        if success and self.on_file_received:
            self.on_file_received(result)
        ack_msg = build_message(MessageType.FILE_SEND_ACK, {
            "transfer_id": params.get("transfer_id", ""),
            "success": success,
            "result": result if success else str(result)
        })
        self.tcp_client.send_message(ack_msg)

    def _handle_update_check(self, params: dict):
        """收到教师端的更新通知，对比版本后自动请求下载。"""
        self.update_client.check_and_request(params)

    @property
    def is_black_screen(self) -> bool:
        return self._black_screen

    @property
    def is_broadcasting(self) -> bool:
        return self._broadcasting

    @property
    def is_net_blocked(self) -> bool:
        return self._net_blocked
