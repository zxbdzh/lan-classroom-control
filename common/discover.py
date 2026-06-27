import socket
import struct
import threading
import time
import ipaddress
from typing import Callable, Optional, List, Dict, Set
from common.protocol import MessageType, build_message, serialize_message, deserialize_message
from common.logger import get_logger

logger = get_logger("discover")

BROADCAST_ADDR = "255.255.255.255"
DEFAULT_BROADCAST_PORT = 9527


def get_local_interfaces() -> List[Dict]:
    try:
        import psutil
        result = []
        for name, addrs in psutil.net_if_addrs().items():
            ipv4 = None
            netmask = None
            for addr in addrs:
                if addr.family == socket.AF_INET:
                    ipv4 = addr.address
                    netmask = addr.netmask
                    break
            if ipv4 and ipv4 != "127.0.0.1" and netmask:
                try:
                    net = ipaddress.IPv4Network(f"{ipv4}/{netmask}", strict=False)
                    broadcast = str(net.broadcast_address)
                    result.append({
                        "name": name,
                        "ip": ipv4,
                        "netmask": netmask,
                        "broadcast": broadcast
                    })
                except (ipaddress.NetmaskValueError, ValueError):
                    pass
        return result
    except ImportError:
        return []


class TeacherDiscover:
    def __init__(self, broadcast_port: int = 9527, tcp_port: int = 9528,
                 broadcast_interval: float = 3.0,
                 student_timeout: float = 15.0,
                 cleanup_interval: float = 3.0):
        self.broadcast_port = broadcast_port
        self.tcp_port = tcp_port
        self.broadcast_interval = broadcast_interval
        self._running = False
        self._broadcast_thread = None
        self._listen_thread = None
        self._send_sock = None
        self._listen_sock = None
        self._discovered_students: Dict[str, dict] = {}
        self._discovered_lock = threading.Lock()
        self._student_timeout = student_timeout
        self._cleanup_interval = cleanup_interval
        self.on_student_discovered: Optional[Callable] = None
        self.on_student_lost: Optional[Callable] = None

    def start(self):
        if self._running:
            return
        self._running = True

        self._send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._send_sock.settimeout(1.0)

        self._listen_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        self._listen_sock.bind(("", self.broadcast_port))
        self._listen_sock.settimeout(1.0)

        self._broadcast_thread = threading.Thread(target=self._broadcast_loop, daemon=True)
        self._broadcast_thread.start()

        self._listen_thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._listen_thread.start()

        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()

        logger.info(f"Teacher discover started, broadcast port: {self.broadcast_port}")

    def stop(self):
        self._running = False
        if self._broadcast_thread:
            self._broadcast_thread.join(timeout=3)
        if self._listen_thread:
            self._listen_thread.join(timeout=3)
        if self._cleanup_thread:
            self._cleanup_thread.join(timeout=3)
        if self._send_sock:
            self._send_sock.close()
            self._send_sock = None
        if self._listen_sock:
            self._listen_sock.close()
            self._listen_sock = None
        logger.info("Teacher discover stopped")

    def _get_broadcast_targets(self) -> List[str]:
        targets = [BROADCAST_ADDR]
        interfaces = get_local_interfaces()
        for iface in interfaces:
            if iface["broadcast"] not in targets:
                targets.append(iface["broadcast"])
        return targets

    def _broadcast_loop(self):
        while self._running:
            self._send_broadcast()
            time.sleep(self.broadcast_interval)

    def _listen_loop(self):
        while self._running:
            try:
                data, addr = self._listen_sock.recvfrom(4096)
                self._handle_packet(data, addr)
            except socket.timeout:
                continue
            except Exception as e:
                logger.debug(f"Listen error: {e}")

    def _handle_packet(self, data: bytes, addr: tuple):
        try:
            msg = deserialize_message(data)
            msg_type = msg.get("type", "")
            if msg_type == MessageType.STUDENT_ANNOUNCE.value:
                self._handle_student_announce(msg, addr)
        except Exception as e:
            logger.debug(f"Invalid packet from {addr}: {e}")

    def _handle_student_announce(self, msg: dict, addr: tuple):
        params = msg.get("params", {})
        student_id = params.get("student_id", "")
        hostname = params.get("hostname", "unknown")
        mac = params.get("mac", "")
        version = params.get("version", "")
        ip = addr[0]

        if not student_id:
            return

        info = {
            "student_id": student_id,
            "hostname": hostname,
            "ip": ip,
            "mac": mac,
            "version": version,
            "last_seen": time.time()
        }

        is_new = False
        with self._discovered_lock:
            if student_id not in self._discovered_students:
                is_new = True
            self._discovered_students[student_id] = info

        if is_new and self.on_student_discovered:
            self.on_student_discovered(info)
            logger.info(f"Discovered student: {hostname} ({ip})")

    def _cleanup_loop(self):
        while self._running:
            time.sleep(self._cleanup_interval)
            now = time.time()
            expired = []
            with self._discovered_lock:
                for sid, info in list(self._discovered_students.items()):
                    if now - info["last_seen"] > self._student_timeout:
                        expired.append((sid, info))
                        del self._discovered_students[sid]
            for sid, info in expired:
                logger.info(f"Student lost: {info['hostname']} ({info['ip']})")
                if self.on_student_lost:
                    self.on_student_lost(info)

    def get_discovered_students(self) -> List[dict]:
        with self._discovered_lock:
            return list(self._discovered_students.values())

    def scan_once(self):
        interfaces = get_local_interfaces()
        logger.info(f"Scanning on {len(interfaces)} interfaces: "
                     f"{[i['name'] for i in interfaces]}")
        self._send_broadcast()

    def _send_broadcast(self):
        try:
            msg = build_message(MessageType.TEACHER_DISCOVER, {
                "tcp_port": self.tcp_port,
                "teacher_name": socket.gethostname()
            })
            data = serialize_message(msg)
            targets = self._get_broadcast_targets()
            for target in targets:
                try:
                    self._send_sock.sendto(data, (target, self.broadcast_port))
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"Broadcast send error: {e}")

    def send_discover_to(self, ip: str):
        try:
            msg = build_message(MessageType.TEACHER_DISCOVER, {
                "tcp_port": self.tcp_port,
                "teacher_name": socket.gethostname()
            })
            data = serialize_message(msg)
            if self._send_sock:
                self._send_sock.sendto(data, (ip, self.broadcast_port))
                logger.info(f"Sent discover to {ip}")
        except Exception as e:
            logger.error(f"Send discover to {ip} error: {e}")


class StudentDiscoverListener:
    def __init__(self, listen_port: int = 9527,
                 announce_port: int = 9527,
                 on_discover_callback: Optional[Callable] = None,
                 student_id: str = "",
                 hostname: str = "",
                 mac: str = "",
                 version: str = "",
                 announce_interval: float = 5.0):
        self.listen_port = listen_port
        self.announce_port = announce_port
        self.on_discover_callback = on_discover_callback
        self.student_id = student_id
        self.hostname = hostname
        self.mac = mac
        self.version = version
        self.announce_interval = announce_interval

        self._running = False
        self._listen_thread = None
        self._announce_thread = None
        self._listen_sock = None
        self._send_sock = None
        self._last_teacher_addr = None
        self._last_teacher_time = 0
        self._dedup_interval = 5.0

    def start(self):
        if self._running:
            return
        self._running = True

        self._listen_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        self._listen_sock.bind(("", self.listen_port))
        self._listen_sock.settimeout(1.0)

        self._send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._send_sock.settimeout(1.0)

        self._listen_thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._listen_thread.start()

        self._announce_thread = threading.Thread(target=self._announce_loop, daemon=True)
        self._announce_thread.start()

        logger.info(f"Student discover listener started, port: {self.listen_port}")

    def stop(self):
        self._running = False
        if self._listen_thread:
            self._listen_thread.join(timeout=3)
        if self._announce_thread:
            self._announce_thread.join(timeout=3)
        if self._listen_sock:
            self._listen_sock.close()
            self._listen_sock = None
        if self._send_sock:
            self._send_sock.close()
            self._send_sock = None
        logger.info("Student discover listener stopped")

    def _get_broadcast_targets(self) -> List[str]:
        targets = [BROADCAST_ADDR]
        interfaces = get_local_interfaces()
        for iface in interfaces:
            if iface["broadcast"] not in targets:
                targets.append(iface["broadcast"])
        return targets

    def _listen_loop(self):
        while self._running:
            try:
                data, addr = self._listen_sock.recvfrom(4096)
                self._handle_packet(data, addr)
            except socket.timeout:
                continue
            except Exception as e:
                logger.debug(f"Listen error: {e}")

    def _handle_packet(self, data: bytes, addr: tuple):
        try:
            msg = deserialize_message(data)
            if msg.get("type") != MessageType.TEACHER_DISCOVER.value:
                return
            now = time.time()
            if (self._last_teacher_addr == addr and
                    now - self._last_teacher_time < self._dedup_interval):
                return
            self._last_teacher_addr = addr
            self._last_teacher_time = now
            params = msg.get("params", {})
            tcp_port = params.get("tcp_port", 9528)
            teacher_name = params.get("teacher_name", "unknown")
            logger.info(f"Discovered teacher: {teacher_name} at {addr[0]}:{tcp_port}")
            if self.on_discover_callback:
                self.on_discover_callback(addr[0], tcp_port, teacher_name)
        except Exception as e:
            logger.debug(f"Invalid packet from {addr}: {e}")

    def _announce_loop(self):
        while self._running:
            try:
                msg = build_message(MessageType.STUDENT_ANNOUNCE, {
                    "student_id": self.student_id,
                    "hostname": self.hostname,
                    "mac": self.mac,
                    "version": self.version
                })
                data = serialize_message(msg)
                targets = self._get_broadcast_targets()
                for target in targets:
                    try:
                        self._send_sock.sendto(data, (target, self.announce_port))
                    except Exception:
                        pass
            except Exception as e:
                logger.debug(f"Announce error: {e}")
            time.sleep(self.announce_interval)

    @property
    def last_teacher(self):
        if self._last_teacher_addr:
            return self._last_teacher_addr[0]
        return None
