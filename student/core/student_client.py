import socket
import os
import sys
import importlib
import base64
import json
import threading
import time
from typing import Mapping, Optional, Callable
from common.protocol import MessageType, build_message, parse_message_type
from common.tcp_conn import TCPClient
from common.discover import StudentDiscoverListener
from common.heartbeat import StudentHeartbeatSender
from common.logger import get_logger
from common.config import get_config
from common.version import get_version
from student.core.update_client import UpdateClient
from student.core import autostart
from common.file_transfer import FileTransferReceiver, TARGET_DIR_DEFAULT
from common.v2.control import AckStage, ControlEnvelope, MessageKind, deserialize_control
from common.v2.media_transport import (
    TcpMjpegDownlink, TcpMjpegUplink, UdpMjpegReceiver, UdpMjpegSender,
    device_session_id,
)
from common.v2.model import CommandKind
from student.core.net_control import NetController
from student.core.v2.blacklist import BlacklistEnforcer
from student.core.v2.command import Command, CommandHandlerResult, CommandOutcome, OutcomeStatus
from student.core.v2.lease import TemporaryControlKind, TemporaryControlLease
from student.core.v2.power import PowerAction, PowerController
from student.core.v2.processes import ProcessController
from student.core.v2.runtime import StudentServiceRuntime
from student.core.v2.storage import StudentStateStore, find_legacy_device_id

logger = get_logger("student_core")

V2_CAPABILITIES = (
    "control_ack_v2",
    "control_lease",
    "inspection_primary",
    "broadcast_all_monitors",
    "network_external_only",
    "process_list",
    "process_terminate",
    "process_blacklist",
    "power",
)


class StudentClient:
    def __init__(
        self,
        save_dir: str = None,
        state_store: StudentStateStore = None,
        service_mode: bool = False,
    ):
        self.config = get_config()
        self.service_mode = bool(service_mode)
        self.state_store = state_store or StudentStateStore()
        preferred_id = self.config.get("student.student_id", "")
        if self.service_mode and not preferred_id:
            preferred_id = find_legacy_device_id()
        self.student_id = self.state_store.get_or_create_device_id(
            preferred_id
        )
        if self.config.get("student.student_id", "") != self.student_id:
            self.config.set("student.student_id", self.student_id)
            self.config.save()
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
        self.heartbeat_sender = StudentHeartbeatSender(
            interval=int(self.config.get("student.heartbeat_interval", 3))
        )
        self.input_blocker = None
        self.net_controller = NetController()
        self.process_controller = ProcessController()
        self.power_controller = PowerController(allow_execution=True)
        self.remote_input_controller = None
        if not self.service_mode:
            InputBlocker = importlib.import_module(
                "student.core.input_blocker"
            ).InputBlocker
            RemoteInputController = importlib.import_module(
                "student.core.v2.remote_input"
            ).RemoteInputController
            self.input_blocker = InputBlocker()
            self.remote_input_controller = RemoteInputController()
        self.temporary_lease = TemporaryControlLease(
            timeout=float(self.config.get("student.control_lease_seconds", 10))
        )
        self.command_runtime = StudentServiceRuntime(temporary_lease=self.temporary_lease)
        self.blacklist_enforcer = BlacklistEnforcer(
            self.state_store, self.process_controller
        )

        if save_dir is None:
            save_dir = self._get_default_save_dir()
        self.file_receiver = FileTransferReceiver(save_dir)
        self.update_client = UpdateClient(
            self.tcp_client.send_message, service_mode=self.service_mode
        )
        self.screen_monitor = None
        if not self.service_mode:
            ScreenCapturer = importlib.import_module(
                "common.screen_capture"
            ).ScreenCapturer
            self.screen_monitor = ScreenCapturer(
                monitor=1,
                fps=int(self.config.get("student.screen_monitor_fps", 1)),
                quality=int(self.config.get("student.screen_monitor_quality", 45)),
                use_diff=False,
                max_width=int(self.config.get("student.screen_monitor_max_width", 640)),
                max_height=int(self.config.get("student.screen_monitor_max_height", 360))
            )

        self._black_screen = False
        self._black_screen_message = ""
        self._broadcasting = False
        self._net_blocked = False
        self._input_lock_reasons = set()
        self._state_lock = threading.RLock()
        self._inspection_mode = None
        self._inspection_sender = None
        self._inspection_tcp_sender = None
        self._broadcast_receiver = None
        self._broadcast_fallback = None
        self._broadcast_fallback_timer = None
        self._broadcast_fallback_generation = 0
        self._last_udp_broadcast_frame = 0.0
        self._session_replay = {}

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
        if not self.service_mode:
            try:
                autostart.ensure_autostart()
            except Exception as e:
                logger.warning(f"Ensure autostart failed: {e}")

        self._setup_callbacks()
        self._setup_v2_handlers()

    def attach_session_agent(self, session_agent) -> None:
        self.command_runtime.session_agent = session_agent
        if session_agent is None:
            return
        # An expired lease must clear desktop controls before any state is
        # replayed to a replacement agent.
        self.temporary_lease.check_expired()
        self._replay_session_state(session_agent)

    def _remember_session_action(
        self, domain: str, action: str, params: Mapping[str, object]
    ) -> None:
        with self._state_lock:
            self._session_replay[str(domain)] = (str(action), dict(params))

    def _forget_session_actions(self, *domains: str) -> None:
        with self._state_lock:
            for domain in domains:
                self._session_replay.pop(str(domain), None)

    def _clear_session_replay(self) -> None:
        with self._state_lock:
            self._session_replay.clear()

    def _replay_session_state(self, session_agent) -> None:
        order = ("broadcast", "silence", "input", "inspection", "remote_control")
        with self._state_lock:
            snapshot = [
                (domain, self._session_replay[domain])
                for domain in order
                if domain in self._session_replay
            ]
        for domain, (action, params) in snapshot:
            payload = dict(params)
            payload.setdefault("device_id", self.student_id)
            payload.setdefault(
                "teacher_host", self.tcp_client.server_host or "127.0.0.1"
            )
            result = session_agent.call(action, payload, timeout=8.0)
            if not result.success:
                logger.warning(
                    f"Cannot replay {domain} state to session agent: "
                    f"{result.error or 'desktop session action failed'}"
                )

    def _restore_tracked_session_action(
        self, action: str, domains: tuple
    ) -> bool:
        result = self._session_action(action, {})
        success = result is None or result.status == OutcomeStatus.SUCCEEDED
        if success:
            self._forget_session_actions(*domains)
            with self._state_lock:
                if "broadcast" in domains:
                    self._broadcasting = False
                if "silence" in domains:
                    self._black_screen = False
                if "inspection" in domains or "remote_control" in domains:
                    self._inspection_mode = None
        return success

    def _session_action(
        self, action: str, params: Mapping[str, object]
    ) -> Optional[CommandHandlerResult]:
        proxy = self.command_runtime.session_agent
        if proxy is None:
            if self.service_mode:
                return CommandHandlerResult.failed("no desktop session agent is connected")
            return None
        payload = dict(params)
        payload.setdefault("device_id", self.student_id)
        payload.setdefault("teacher_host", self.tcp_client.server_host or "127.0.0.1")
        result = proxy.call(action, payload, timeout=8.0)
        if result.success:
            return CommandHandlerResult(payload=dict(result.payload))
        return CommandHandlerResult.failed(
            result.error or "desktop session action failed", result.payload
        )

    def _get_default_save_dir(self) -> str:
        """Return a classroom folder visible to the interactive student."""
        if self.service_mode and sys.platform == "win32":
            public_root = os.environ.get("PUBLIC") or os.path.join(
                os.environ.get("SystemDrive", "C:"), "Users", "Public"
            )
            return os.path.join(public_root, "Desktop", "课堂文件")
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        if os.path.isdir(desktop):
            return os.path.join(desktop, "课堂文件")
        downloads = os.path.join(os.path.expanduser("~"), "Downloads")
        if os.path.isdir(downloads):
            return os.path.join(downloads, "课堂文件")
        return os.path.join(os.path.expanduser("~"), "课堂文件")

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

    def _setup_callbacks(self):
        self.tcp_client.on_connect = self._on_connected
        self.tcp_client.on_message = self._on_message
        self.tcp_client.on_disconnect = self._on_disconnected

    def start(self):
        try:
            self.net_controller.restore_stale_rules()
        except Exception as exc:
            logger.warning(f"Restore stale network rules failed: {exc}")
        self.command_runtime.start()
        self.blacklist_enforcer.start()
        self.discover_listener.start()
        logger.info(f"Student client started: {self.hostname} ({self.local_ip})")

    def stop(self):
        self._stop_screen_monitor()
        self.command_runtime.stop()
        self.blacklist_enforcer.stop()
        self.heartbeat_sender.stop()
        self.tcp_client.disconnect()
        self.discover_listener.stop()
        self._restore_temporary_controls()
        self.state_store.close()
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
            "version": get_version(),
            "protocol_version": 2,
            "capabilities": list(V2_CAPABILITIES),
        })
        self.tcp_client.send_message(register_msg)
        self.heartbeat_sender.start(self.tcp_client.send_message)
        if self.on_connected:
            self.on_connected()

    def _on_disconnected(self):
        logger.info("Disconnected from teacher")
        self._restore_temporary_controls()
        if self.on_disconnected:
            self.on_disconnected()

    def _start_screen_monitor(self):
        if self.screen_monitor is None:
            return
        if os.environ.get("LAN_CLASSROOM_DISABLE_SCREEN_MONITOR") == "1":
            return
        if not self.config.get("student.screen_monitor_enabled", True):
            return
        try:
            self.screen_monitor.start(self._send_screen_frame)
        except Exception as e:
            logger.warning(f"Start screen monitor failed: {e}")

    def _stop_screen_monitor(self):
        if self.screen_monitor is None:
            return
        try:
            self.screen_monitor.stop()
        except Exception as e:
            logger.debug(f"Stop screen monitor failed: {e}")

    def _send_screen_frame(self, frame_data: bytes, frame_size: tuple):
        sender = self._inspection_sender
        tcp_sender = self._inspection_tcp_sender
        if sender is not None:
            try:
                sender.send_frame(frame_data)
            except Exception as e:
                logger.debug(f"Send UDP inspection frame failed: {e}")
        if tcp_sender is not None:
            try:
                tcp_sender.send_frame(frame_data)
            except Exception as e:
                logger.debug(f"Queue TCP inspection frame failed: {e}")
        if sender is not None or tcp_sender is not None:
            return
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
            if msg.get("kind") == MessageKind.COMMAND.value:
                self._handle_v2_command(msg)
                return
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
            elif msg_type == MessageType.TEACHER_HEARTBEAT:
                self.command_runtime.heartbeat()
        except Exception as e:
            logger.error(f"Handle message error: {e}")

    def _handle_black_screen(self, params: dict):
        enable = params.get("enable", False)
        with self._state_lock:
            self._black_screen = enable
            self._black_screen_message = str(params.get("message") or "") if enable else ""
        if enable:
            if self.on_black_screen_changed:
                self.on_black_screen_changed(enable)
            self._set_input_lock("silence", True)
        else:
            self._set_input_lock("silence", False)
            if self.on_black_screen_changed:
                self.on_black_screen_changed(enable)
        logger.info(f"Black screen: {enable}")

    def _handle_broadcast_start(self, params: dict):
        with self._state_lock:
            self._broadcasting = True
        session_id = params.get("session_id")
        if session_id and params.get("transport") == "udp_multicast":
            self._stop_broadcast_receiver()
            fallback_port = int(
                params.get("fallback_port")
                or self.config.get("student.bulk_port", 9531)
            )
            try:
                self._broadcast_receiver = UdpMjpegReceiver(
                    int(params.get("port") or self.config.get("student.broadcast_port", 9529)),
                    self._on_broadcast_media_frame,
                    multicast_group=str(
                        params.get("multicast_group")
                        or self.config.get("student.multicast_group", "239.255.95.29")
                    ),
                    session_id=session_id,
                )
                self._broadcast_receiver.start()
            except Exception as exc:
                self._broadcast_receiver = None
                logger.warning(f"Start multicast broadcast receiver failed: {exc}")
            with self._state_lock:
                generation = self._broadcast_fallback_generation
            self._schedule_broadcast_fallback(
                str(session_id), fallback_port, generation, 0.8
            )
        if self.on_broadcast_started:
            self.on_broadcast_started(params)
        if params.get("lock_input", True):
            self._set_input_lock("broadcast", True)
        logger.info("Broadcast started")

    def _handle_broadcast_stop(self, params: dict):
        with self._state_lock:
            self._broadcasting = False
        self._stop_broadcast_receiver()
        # 异步解除键鼠禁用，避免 pynput stop 阻塞主线程（Win7上较慢）
        self._set_input_lock("broadcast", False)
        if self.on_broadcast_stopped:
            self.on_broadcast_stopped()
        logger.info("Broadcast stopped")

    def _on_broadcast_media_frame(self, frame, _address: tuple) -> None:
        with self._state_lock:
            self._last_udp_broadcast_frame = time.monotonic()
        if self._broadcasting and self.on_screen_frame:
            self.on_screen_frame(frame.jpeg, (0, 0))

    def _schedule_broadcast_fallback(
        self,
        session_id: str,
        port: int,
        generation: int,
        delay: float = 0.25,
    ) -> None:
        timer = threading.Timer(
            delay,
            self._check_broadcast_fallback,
            args=(session_id, port, generation),
        )
        timer.daemon = True
        with self._state_lock:
            if (
                generation != self._broadcast_fallback_generation
                or not self._broadcasting
            ):
                return
            self._broadcast_fallback_timer = timer
        timer.start()

    def _check_broadcast_fallback(
        self, session_id: str, port: int, generation: int
    ) -> None:
        with self._state_lock:
            if (
                generation != self._broadcast_fallback_generation
                or not self._broadcasting
            ):
                return
            udp_stale = (
                not self._last_udp_broadcast_frame
                or time.monotonic() - self._last_udp_broadcast_frame >= 0.8
            )
            if udp_stale and self._broadcast_fallback is None:
                try:
                    fallback = TcpMjpegDownlink(
                        str(self.tcp_client.server_host or "127.0.0.1"),
                        int(port),
                        session_id,
                        self._on_broadcast_fallback_frame,
                    )
                    self._broadcast_fallback = fallback
                    fallback.start()
                except Exception as exc:
                    self._broadcast_fallback = None
                    logger.warning(f"Start TCP broadcast fallback failed: {exc}")
        self._schedule_broadcast_fallback(session_id, port, generation)

    def _on_broadcast_fallback_frame(self, frame) -> None:
        with self._state_lock:
            if not self._broadcasting:
                return
            if (
                self._last_udp_broadcast_frame
                and time.monotonic() - self._last_udp_broadcast_frame < 0.8
            ):
                return
        if self.on_screen_frame:
            self.on_screen_frame(frame.jpeg, (0, 0))

    def _stop_broadcast_receiver(self) -> bool:
        with self._state_lock:
            self._broadcast_fallback_generation += 1
            timer = self._broadcast_fallback_timer
            self._broadcast_fallback_timer = None
            receiver = self._broadcast_receiver
            self._broadcast_receiver = None
            fallback = self._broadcast_fallback
            self._broadcast_fallback = None
            self._last_udp_broadcast_frame = 0.0
        if timer is not None:
            timer.cancel()
        if receiver is not None:
            receiver.stop()
        if fallback is not None:
            fallback.stop()
        return True

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
        allowed = list(params.get("allowed_cidrs") or params.get("whitelist") or [])
        teacher_ip = self.tcp_client.server_host
        if teacher_ip and teacher_ip not in allowed:
            allowed.append(teacher_ip)
        success = (
            self.net_controller.block_internet(allowed)
            if enable else self.net_controller.unblock_internet()
        )
        with self._state_lock:
            self._net_blocked = self.net_controller.is_blocked()
        self._send_command_ack("net_control", success, {
            "enable": enable,
            "net_blocked": self._net_blocked,
        })
        logger.info(f"Net control applied: enable={enable}, success={success}")

    def _set_input_lock(self, reason: str, enabled: bool) -> None:
        with self._state_lock:
            was_locked = bool(self._input_lock_reasons)
            if enabled:
                self._input_lock_reasons.add(reason)
            else:
                self._input_lock_reasons.discard(reason)
            is_locked = bool(self._input_lock_reasons)
        if is_locked and not was_locked:
            if self.input_blocker is not None:
                threading.Thread(target=self.input_blocker.block, daemon=True).start()
        elif was_locked and not is_locked:
            if self.input_blocker is not None:
                threading.Thread(target=self.input_blocker.unblock, daemon=True).start()

    def _setup_v2_handlers(self) -> None:
        handlers = {
            CommandKind.BROADCAST_START.value: self._v2_broadcast_start,
            CommandKind.BROADCAST_STOP.value: self._v2_broadcast_stop,
            CommandKind.SILENCE_START.value: self._v2_silence_start,
            CommandKind.SILENCE_STOP.value: self._v2_silence_stop,
            CommandKind.NETWORK_BLOCK.value: self._v2_network_block,
            CommandKind.NETWORK_RESTORE.value: self._v2_network_restore,
            CommandKind.INPUT_LOCK.value: self._v2_input_lock,
            CommandKind.INPUT_UNLOCK.value: self._v2_input_unlock,
            CommandKind.INSPECTION_OPEN.value: self._v2_inspection_open,
            CommandKind.INSPECTION_CLOSE.value: self._v2_inspection_close,
            CommandKind.REMOTE_CONTROL_START.value: self._v2_remote_start,
            CommandKind.REMOTE_CONTROL_STOP.value: self._v2_remote_stop,
            CommandKind.RESTORE_ALL.value: self._v2_restore_all,
            CommandKind.PROCESS_TERMINATE.value: self._v2_process_terminate,
            CommandKind.PROCESS_LIST.value: self._v2_process_list,
            CommandKind.REMOTE_INPUT.value: self._v2_remote_input,
            CommandKind.BLACKLIST_SET.value: self._v2_blacklist_set,
            CommandKind.BLACKLIST_CLEAR.value: self._v2_blacklist_clear,
            CommandKind.POWER_OFF.value: lambda _: self._v2_power(PowerAction.SHUTDOWN),
            CommandKind.REBOOT.value: lambda _: self._v2_power(PowerAction.RESTART),
            CommandKind.LOGOFF.value: lambda _: self._v2_power(PowerAction.LOGOFF),
        }
        for name, handler in handlers.items():
            self.command_runtime.register_privileged_action(name, handler)

    def _handle_v2_command(self, message: dict) -> None:
        try:
            envelope = deserialize_control(
                json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            )
        except Exception as exc:
            logger.warning(f"Invalid v2 command: {exc}")
            return
        if envelope.kind is not MessageKind.COMMAND or envelope.action is None:
            return
        if envelope.action == CommandKind.REMOTE_INPUT.value:
            self._execute_v2_command(envelope)
            return
        threading.Thread(
            target=self._execute_v2_command,
            args=(envelope,),
            name="student-v2-command",
            daemon=True,
        ).start()

    def _execute_v2_command(self, envelope: ControlEnvelope) -> None:
        payload = dict(envelope.payload)
        if envelope.device_id:
            payload.setdefault("device_id", envelope.device_id)
        command = Command(envelope.command_id, envelope.action or "", payload)
        execution = self.command_runtime.execute(
            command,
            lambda outcome: self._send_v2_ack(envelope, AckStage.RECEIVED, outcome),
        )
        self._send_v2_ack(envelope, AckStage.APPLIED, execution.result)

    def _send_v2_ack(
        self,
        command: ControlEnvelope,
        stage: AckStage,
        outcome: CommandOutcome,
    ) -> None:
        success = outcome.status in {OutcomeStatus.RECEIVED, OutcomeStatus.SUCCEEDED}
        payload = dict(outcome.payload)
        payload.update({"status": outcome.status.value, "duplicate": outcome.duplicate})
        error = None if success else (outcome.error or outcome.status.value)
        try:
            ack = ControlEnvelope.ack(
                command_id=command.command_id,
                device_id=command.device_id or self.student_id,
                revision=command.revision,
                stage=stage,
                payload=payload,
                error=error,
            )
            self.tcp_client.send_message(ack.to_dict())
        except Exception as exc:
            logger.debug(f"Send v2 acknowledgement failed: {exc}")

    def _activate_lease(
        self,
        kind: TemporaryControlKind,
        restore: Callable[[], object],
    ) -> None:
        self.command_runtime.mark_temporary_control(kind, restore)

    def _v2_broadcast_start(self, params: Mapping[str, object]):
        delegated = self._session_action(CommandKind.BROADCAST_START.value, params)
        if delegated is not None:
            if delegated.status != OutcomeStatus.SUCCEEDED:
                return delegated
            self._broadcasting = True
            self._remember_session_action(
                "broadcast", CommandKind.BROADCAST_START.value, params
            )
            self._activate_lease(
                TemporaryControlKind.BROADCAST,
                lambda: self._restore_tracked_session_action(
                    CommandKind.BROADCAST_STOP.value, ("broadcast",)
                ),
            )
            return delegated
        self._handle_broadcast_start(dict(params))
        self._activate_lease(
            TemporaryControlKind.BROADCAST,
            lambda: self._handle_broadcast_stop({}),
        )
        return {"broadcasting": True}

    def _v2_broadcast_stop(self, params: Mapping[str, object]):
        delegated = self._session_action(CommandKind.BROADCAST_STOP.value, params)
        if delegated is not None:
            if delegated.status == OutcomeStatus.SUCCEEDED:
                self._broadcasting = False
                self._forget_session_actions("broadcast")
                self.temporary_lease.deactivate(TemporaryControlKind.BROADCAST)
            return delegated
        self._handle_broadcast_stop(dict(params))
        self.temporary_lease.deactivate(TemporaryControlKind.BROADCAST)
        return {"broadcasting": False}

    def _v2_silence_start(self, params: Mapping[str, object]):
        delegated = self._session_action(CommandKind.SILENCE_START.value, params)
        if delegated is not None:
            if delegated.status != OutcomeStatus.SUCCEEDED:
                return delegated
            self._black_screen = True
            self._remember_session_action(
                "silence", CommandKind.SILENCE_START.value, params
            )
            self._activate_lease(
                TemporaryControlKind.SILENCE,
                lambda: self._restore_tracked_session_action(
                    CommandKind.SILENCE_STOP.value, ("silence",)
                ),
            )
            return delegated
        self._handle_black_screen({"enable": True, **dict(params)})
        self._activate_lease(
            TemporaryControlKind.SILENCE,
            lambda: self._handle_black_screen({"enable": False}),
        )
        return {"silence": True}

    def _v2_silence_stop(self, params: Mapping[str, object]):
        delegated = self._session_action(CommandKind.SILENCE_STOP.value, params)
        if delegated is not None:
            if delegated.status == OutcomeStatus.SUCCEEDED:
                self._black_screen = False
                self._forget_session_actions("silence")
                self.temporary_lease.deactivate(TemporaryControlKind.SILENCE)
            return delegated
        self._handle_black_screen({"enable": False})
        self.temporary_lease.deactivate(TemporaryControlKind.SILENCE)
        return {"silence": False}

    def _v2_network_block(self, params: Mapping[str, object]):
        self._handle_net_control({"enable": True, **dict(params)})
        if not self._net_blocked:
            return CommandHandlerResult.failed("failed to block external network")
        self._activate_lease(
            TemporaryControlKind.INTERNET_BLOCK,
            lambda: self._handle_net_control({"enable": False}),
        )
        return {"network_blocked": True}

    def _v2_network_restore(self, params: Mapping[str, object]):
        self._handle_net_control({"enable": False})
        if self._net_blocked:
            return CommandHandlerResult.failed("failed to restore network")
        self.temporary_lease.deactivate(TemporaryControlKind.INTERNET_BLOCK)
        return {"network_blocked": False}

    def _v2_input_lock(self, params: Mapping[str, object]):
        delegated = self._session_action(CommandKind.INPUT_LOCK.value, params)
        if delegated is not None:
            if delegated.status != OutcomeStatus.SUCCEEDED:
                return delegated
            self._remember_session_action(
                "input", CommandKind.INPUT_LOCK.value, params
            )
            self._activate_lease(
                TemporaryControlKind.INPUT,
                lambda: self._restore_tracked_session_action(
                    CommandKind.INPUT_UNLOCK.value, ("input",)
                ),
            )
            return delegated
        self._set_input_lock("input", True)
        self._activate_lease(
            TemporaryControlKind.INPUT,
            lambda: self._set_input_lock("input", False),
        )
        return {"input_locked": True}

    def _v2_input_unlock(self, params: Mapping[str, object]):
        delegated = self._session_action(CommandKind.INPUT_UNLOCK.value, params)
        if delegated is not None:
            if delegated.status == OutcomeStatus.SUCCEEDED:
                self._forget_session_actions("input")
                self.temporary_lease.deactivate(TemporaryControlKind.INPUT)
            return delegated
        self._set_input_lock("input", False)
        self.temporary_lease.deactivate(TemporaryControlKind.INPUT)
        return {"input_locked": bool(self._input_lock_reasons)}

    def _v2_inspection_open(self, params: Mapping[str, object]):
        delegated = self._session_action(CommandKind.INSPECTION_OPEN.value, params)
        if delegated is not None:
            if delegated.status != OutcomeStatus.SUCCEEDED:
                return delegated
            self._inspection_mode = str(params.get("mode") or "thumbnail")
            self._forget_session_actions("remote_control")
            self._remember_session_action(
                "inspection", CommandKind.INSPECTION_OPEN.value, params
            )
            self._activate_lease(
                TemporaryControlKind.INSPECTION,
                lambda: self._restore_tracked_session_action(
                    CommandKind.INSPECTION_CLOSE.value, ("inspection",)
                ),
            )
            return delegated
        self._inspection_mode = str(params.get("mode") or "thumbnail")
        self._close_inspection()
        base_session_id = str(params.get("session_id") or "")
        if not base_session_id:
            return CommandHandlerResult.failed("inspection session_id is required")
        try:
            stream_id = device_session_id(
                base_session_id, str(params.get("device_id") or self.student_id)
            )
            self._inspection_sender = UdpMjpegSender(
                str(self.tcp_client.server_host or "127.0.0.1"),
                int(params.get("port") or self.config.get("student.inspection_port", 9530)),
                stream_id,
            )
            self._inspection_tcp_sender = TcpMjpegUplink(
                str(self.tcp_client.server_host or "127.0.0.1"),
                int(params.get("fallback_port") or self.config.get("student.bulk_port", 9531)),
                stream_id,
            )
            self._inspection_tcp_sender.start()
        except Exception as exc:
            self._inspection_sender = None
            self._inspection_tcp_sender = None
            return CommandHandlerResult.failed(str(exc))
        self._inspection_mode = str(params.get("mode") or "thumbnail")
        self._start_screen_monitor()
        self._activate_lease(
            TemporaryControlKind.INSPECTION,
            self._close_inspection,
        )
        return {"inspection": True, "mode": self._inspection_mode}

    def _close_inspection(self):
        self._stop_screen_monitor()
        sender = self._inspection_sender
        tcp_sender = self._inspection_tcp_sender
        self._inspection_sender = None
        self._inspection_tcp_sender = None
        if sender is not None:
            sender.close()
        if tcp_sender is not None:
            tcp_sender.stop()
        self._inspection_mode = None
        return True

    def _v2_inspection_close(self, params: Mapping[str, object]):
        delegated = self._session_action(CommandKind.INSPECTION_CLOSE.value, params)
        if delegated is not None:
            if delegated.status == OutcomeStatus.SUCCEEDED:
                self._inspection_mode = None
                self._forget_session_actions("inspection")
                self.temporary_lease.deactivate(TemporaryControlKind.INSPECTION)
            return delegated
        self._close_inspection()
        self.temporary_lease.deactivate(TemporaryControlKind.INSPECTION)
        return {"inspection": False}

    def _v2_remote_start(self, params: Mapping[str, object]):
        delegated = self._session_action(CommandKind.REMOTE_CONTROL_START.value, params)
        if delegated is not None:
            if delegated.status != OutcomeStatus.SUCCEEDED:
                return delegated
            self._inspection_mode = "remote_control"
            self._forget_session_actions("inspection")
            self._remember_session_action(
                "remote_control", CommandKind.REMOTE_CONTROL_START.value, params
            )
            self._activate_lease(
                TemporaryControlKind.REMOTE_CONTROL,
                lambda: self._restore_tracked_session_action(
                    CommandKind.REMOTE_CONTROL_STOP.value,
                    ("remote_control", "inspection"),
                ),
            )
            return delegated
        inspection = self._v2_inspection_open({**dict(params), "mode": "remote_control"})
        if isinstance(inspection, CommandHandlerResult):
            return inspection
        self._set_input_lock("remote_control", True)
        self._activate_lease(
            TemporaryControlKind.REMOTE_CONTROL,
            self._stop_remote_control,
        )
        return {"remote_control": True}

    def _stop_remote_control(self):
        self._set_input_lock("remote_control", False)
        self._close_inspection()
        return True

    def _v2_remote_stop(self, params: Mapping[str, object]):
        delegated = self._session_action(CommandKind.REMOTE_CONTROL_STOP.value, params)
        if delegated is not None:
            if delegated.status == OutcomeStatus.SUCCEEDED:
                self._inspection_mode = None
                self._forget_session_actions("remote_control", "inspection")
                self.temporary_lease.deactivate(TemporaryControlKind.REMOTE_CONTROL)
                self.temporary_lease.deactivate(TemporaryControlKind.INSPECTION)
            return delegated
        self._stop_remote_control()
        self.temporary_lease.deactivate(TemporaryControlKind.REMOTE_CONTROL)
        self.temporary_lease.deactivate(TemporaryControlKind.INSPECTION)
        return {"remote_control": False}

    def _v2_restore_all(self, params: Mapping[str, object]):
        self._restore_temporary_controls()
        return {"restored": True, "blacklist_preserved": True}

    def _restore_temporary_controls(self) -> bool:
        self.temporary_lease.restore_all()
        self._clear_session_replay()
        self._handle_broadcast_stop({})
        self._handle_black_screen({"enable": False})
        self._set_input_lock("input", False)
        self._set_input_lock("remote_control", False)
        self._close_inspection()
        if self.net_controller.is_blocked():
            self._handle_net_control({"enable": False})
        return True

    def _v2_process_list(self, params: Mapping[str, object]):
        processes = [
            {
                "pid": item.pid,
                "name": item.name,
                "path": item.executable,
                "creation_time": item.creation_time,
                "username": item.username,
                "protected": item.protected,
                "protection_reason": item.protection_reason,
            }
            for item in self.process_controller.list_processes()
        ]
        return {"processes": processes}

    def _v2_process_terminate(self, params: Mapping[str, object]):
        result = self.process_controller.terminate_process(
            int(params.get("pid", 0)),
            float(params.get("creation_time", params.get("create_time", 0))),
            force=bool(params.get("force", False)),
        )
        payload = {
            "pid": result.pid,
            "termination_status": result.status.value,
            "message": result.message,
            "forced": result.forced,
        }
        if result.success:
            return payload
        return CommandHandlerResult.failed(result.message, payload)

    def _v2_remote_input(self, params: Mapping[str, object]):
        if TemporaryControlKind.REMOTE_CONTROL not in self.temporary_lease.active_controls():
            return CommandHandlerResult.failed("remote control lease is not active")
        delegated = self._session_action(CommandKind.REMOTE_INPUT.value, params)
        if delegated is not None:
            return delegated
        if self.remote_input_controller is None:
            return CommandHandlerResult.failed("remote input requires a desktop session agent")
        return self.remote_input_controller.apply(params)

    def _v2_blacklist_set(self, params: Mapping[str, object]):
        program = str(params.get("program") or params.get("name") or "")
        path = str(params.get("path") or "") or None
        rule = self.state_store.add_blacklist_rule(program, path)
        self.blacklist_enforcer.enforce_once()
        return {"rule_id": rule.rule_id, "program": rule.executable_name, "path": rule.full_path}

    def _v2_blacklist_clear(self, params: Mapping[str, object]):
        rule_id = params.get("rule_id")
        removed = False
        if rule_id is not None:
            removed = self.state_store.remove_blacklist_rule(int(rule_id))
        else:
            program = str(params.get("program") or params.get("name") or "").casefold()
            path = str(params.get("path") or "").casefold()
            for rule in self.state_store.list_blacklist_rules():
                if rule.executable_name.casefold() != program:
                    continue
                if path and (rule.full_path or "").casefold() != path:
                    continue
                removed = self.state_store.remove_blacklist_rule(rule.rule_id) or removed
        return {"removed": removed}

    def _v2_power(self, action: PowerAction):
        result = self.power_controller.execute(action)
        payload = {
            "action": result.action.value,
            "message": result.message,
            "return_code": result.return_code,
        }
        if result.success:
            return payload
        return CommandHandlerResult.failed(result.message, payload)

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
        file_params = dict(params)
        if self.service_mode:
            file_params["target_dir_type"] = TARGET_DIR_DEFAULT
            file_params["target_dir_path"] = ""
        resume = self.file_receiver.handle_start(file_params)
        self.tcp_client.send_message(build_message(MessageType.FILE_SEND_ACK, {
            "student_id": self.student_id,
            "transfer_id": params.get("transfer_id", ""),
            "stage": "start",
            "success": True,
            "resume_chunk": int(resume.get("resume_chunk", 0)),
        }))
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
            "student_id": self.student_id,
            "transfer_id": params.get("transfer_id", ""),
            "stage": "end",
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

    @property
    def black_screen_message(self) -> str:
        return self._black_screen_message
