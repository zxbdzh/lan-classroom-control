import socket
import struct
import threading
import time
from typing import Callable, Optional
from common.protocol import MessageType, build_message, serialize_message, deserialize_message
from common.logger import get_logger

logger = get_logger("discover")

BROADCAST_ADDR = "255.255.255.255"


class TeacherDiscover:
    def __init__(self, broadcast_port: int = 9527, tcp_port: int = 9528,
                 broadcast_interval: float = 3.0):
        self.broadcast_port = broadcast_port
        self.tcp_port = tcp_port
        self.broadcast_interval = broadcast_interval
        self._running = False
        self._thread = None
        self._sock = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._sock.settimeout(1.0)
        self._thread = threading.Thread(target=self._broadcast_loop, daemon=True)
        self._thread.start()
        logger.info(f"Teacher discover started, broadcast port: {self.broadcast_port}")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        if self._sock:
            self._sock.close()
            self._sock = None
        logger.info("Teacher discover stopped")

    def _broadcast_loop(self):
        msg = build_message(MessageType.TEACHER_DISCOVER, {
            "tcp_port": self.tcp_port,
            "teacher_name": socket.gethostname()
        })
        data = serialize_message(msg)
        while self._running:
            try:
                self._sock.sendto(data, (BROADCAST_ADDR, self.broadcast_port))
            except Exception as e:
                logger.debug(f"Broadcast send error: {e}")
            time.sleep(self.broadcast_interval)


class StudentDiscoverListener:
    def __init__(self, listen_port: int = 9527,
                 on_discover_callback: Optional[Callable] = None):
        self.listen_port = listen_port
        self.on_discover_callback = on_discover_callback
        self._running = False
        self._thread = None
        self._sock = None
        self._last_teacher_addr = None
        self._last_teacher_time = 0
        self._dedup_interval = 5.0

    def start(self):
        if self._running:
            return
        self._running = True
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("", self.listen_port))
        self._sock.settimeout(1.0)
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        logger.info(f"Student discover listener started, port: {self.listen_port}")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        if self._sock:
            self._sock.close()
            self._sock = None
        logger.info("Student discover listener stopped")

    def _listen_loop(self):
        while self._running:
            try:
                data, addr = self._sock.recvfrom(4096)
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

    @property
    def last_teacher(self):
        if self._last_teacher_addr:
            return self._last_teacher_addr[0]
        return None
