import time
import threading
from typing import Dict, Callable, Optional
from common.protocol import MessageType, build_message
from common.logger import get_logger
from common.tcp_conn import TCPConnection

logger = get_logger("heartbeat")


class TeacherHeartbeatManager:
    def __init__(self, timeout: int = 15, check_interval: int = 3):
        self.timeout = timeout
        self.check_interval = check_interval
        self._last_heartbeat: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._running = False
        self._check_thread = None
        self.on_student_timeout: Optional[Callable] = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._check_thread = threading.Thread(target=self._check_loop, daemon=True)
        self._check_thread.start()
        logger.info("Teacher heartbeat manager started")

    def stop(self):
        self._running = False
        if self._check_thread:
            self._check_thread.join(timeout=5)
        logger.info("Teacher heartbeat manager stopped")

    def record_heartbeat(self, student_id: str):
        with self._lock:
            self._last_heartbeat[student_id] = time.time()

    def register_student(self, student_id: str):
        with self._lock:
            self._last_heartbeat[student_id] = time.time()

    def remove_student(self, student_id: str):
        with self._lock:
            if student_id in self._last_heartbeat:
                del self._last_heartbeat[student_id]

    def _check_loop(self):
        while self._running:
            now = time.time()
            timeout_students = []
            with self._lock:
                for sid, last_time in list(self._last_heartbeat.items()):
                    if now - last_time > self.timeout:
                        timeout_students.append(sid)
            for sid in timeout_students:
                logger.warning(f"Student {sid} heartbeat timeout")
                if self.on_student_timeout:
                    self.on_student_timeout(sid)
                with self._lock:
                    if sid in self._last_heartbeat:
                        del self._last_heartbeat[sid]
            time.sleep(self.check_interval)

    def get_last_heartbeat(self, student_id: str) -> float:
        with self._lock:
            return self._last_heartbeat.get(student_id, 0)

    def is_alive(self, student_id: str) -> bool:
        with self._lock:
            if student_id not in self._last_heartbeat:
                return False
            return time.time() - self._last_heartbeat[student_id] <= self.timeout


class StudentHeartbeatSender:
    def __init__(self, interval: int = 5):
        self.interval = interval
        self._running = False
        self._thread = None
        self._send_func: Optional[Callable] = None

    def start(self, send_func: Callable):
        if self._running:
            return
        self._send_func = send_func
        self._running = True
        self._thread = threading.Thread(target=self._send_loop, daemon=True)
        self._thread.start()
        logger.info("Student heartbeat sender started")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Student heartbeat sender stopped")

    def _send_loop(self):
        while self._running:
            try:
                if self._send_func:
                    msg = build_message(MessageType.STUDENT_HEARTBEAT, {
                        "timestamp": time.time()
                    })
                    self._send_func(msg)
            except Exception as e:
                logger.debug(f"Heartbeat send error: {e}")
            time.sleep(self.interval)
