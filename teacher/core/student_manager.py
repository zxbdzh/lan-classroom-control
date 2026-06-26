import time
import threading
from typing import Dict, List, Optional, Callable
from common.logger import get_logger
from common.tcp_conn import TCPConnection

logger = get_logger("student_manager")


class StudentInfo:
    def __init__(self, student_id: str, hostname: str, ip: str, mac: str,
                 version: str = "", conn: Optional[TCPConnection] = None):
        self.student_id = student_id
        self.hostname = hostname
        self.ip = ip
        self.mac = mac
        self.version = version
        self.conn = conn
        self.online = True
        self.register_time = time.time()
        self.last_heartbeat = time.time()
        self.group = "default"
        self.display_name = hostname
        self.status = {
            "black_screen": False,
            "broadcasting": False,
            "net_blocked": False,
        }

    def to_dict(self) -> dict:
        return {
            "student_id": self.student_id,
            "hostname": self.hostname,
            "ip": self.ip,
            "mac": self.mac,
            "version": self.version,
            "online": self.online,
            "display_name": self.display_name,
            "group": self.group,
            "status": self.status.copy(),
        }


class StudentManager:
    def __init__(self):
        self._students: Dict[str, StudentInfo] = {}
        self._lock = threading.Lock()
        self.on_student_added: Optional[Callable] = None
        self.on_student_removed: Optional[Callable] = None
        self.on_student_changed: Optional[Callable] = None
        self._groups: Dict[str, List[str]] = {"default": []}

    def add_student(self, student: StudentInfo):
        with self._lock:
            if student.student_id in self._students:
                old = self._students[student.student_id]
                student.display_name = old.display_name
                student.group = old.group
            self._students[student.student_id] = student
            if student.group not in self._groups:
                self._groups[student.group] = []
            if student.student_id not in self._groups[student.group]:
                self._groups[student.group].append(student.student_id)
        logger.info(f"Student added: {student.hostname} ({student.ip})")
        if self.on_student_added:
            self.on_student_added(student)
        if self.on_student_changed:
            self.on_student_changed()

    def remove_student(self, student_id: str):
        with self._lock:
            student = self._students.pop(student_id, None)
            if student and student.group in self._groups:
                if student_id in self._groups[student.group]:
                    self._groups[student.group].remove(student_id)
        if student:
            logger.info(f"Student removed: {student.hostname}")
            if self.on_student_removed:
                self.on_student_removed(student)
            if self.on_student_changed:
                self.on_student_changed()

    def get_student(self, student_id: str) -> Optional[StudentInfo]:
        with self._lock:
            return self._students.get(student_id)

    def get_all_students(self) -> List[StudentInfo]:
        with self._lock:
            return list(self._students.values())

    def get_online_students(self) -> List[StudentInfo]:
        with self._lock:
            return [s for s in self._students.values() if s.online]

    def get_students_by_group(self, group: str) -> List[StudentInfo]:
        with self._lock:
            if group not in self._groups:
                return []
            return [self._students[sid] for sid in self._groups[group]
                    if sid in self._students]

    def set_student_offline(self, student_id: str):
        with self._lock:
            student = self._students.get(student_id)
            if student:
                student.online = False
        if student and self.on_student_changed:
            self.on_student_changed()

    def update_heartbeat(self, student_id: str):
        with self._lock:
            student = self._students.get(student_id)
            if student:
                student.last_heartbeat = time.time()
                if not student.online:
                    student.online = True

    def update_student_status(self, student_id: str, status_key: str, value):
        with self._lock:
            student = self._students.get(student_id)
            if student:
                student.status[status_key] = value
        if student and self.on_student_changed:
            self.on_student_changed()

    def set_display_name(self, student_id: str, name: str):
        with self._lock:
            student = self._students.get(student_id)
            if student:
                student.display_name = name
        if student and self.on_student_changed:
            self.on_student_changed()

    def set_group(self, student_id: str, group: str):
        with self._lock:
            student = self._students.get(student_id)
            if student:
                old_group = student.group
                if old_group in self._groups and student_id in self._groups[old_group]:
                    self._groups[old_group].remove(student_id)
                student.group = group
                if group not in self._groups:
                    self._groups[group] = []
                self._groups[group].append(student_id)
        if student and self.on_student_changed:
            self.on_student_changed()

    def get_groups(self) -> List[str]:
        with self._lock:
            return list(self._groups.keys())

    def add_group(self, group: str):
        with self._lock:
            if group not in self._groups:
                self._groups[group] = []

    def remove_group(self, group: str):
        if group == "default":
            return
        with self._lock:
            if group in self._groups:
                for sid in self._groups[group]:
                    if sid in self._students:
                        self._students[sid].group = "default"
                    self._groups["default"].append(sid)
                del self._groups[group]
        if self.on_student_changed:
            self.on_student_changed()

    def find_by_ip(self, ip: str) -> Optional[StudentInfo]:
        with self._lock:
            for student in self._students.values():
                if student.ip == ip:
                    return student
        return None

    def find_by_mac(self, mac: str) -> Optional[StudentInfo]:
        with self._lock:
            mac_lower = mac.lower()
            for student in self._students.values():
                if student.mac.lower() == mac_lower:
                    return student
        return None

    def count(self) -> int:
        with self._lock:
            return len(self._students)

    def online_count(self) -> int:
        with self._lock:
            return sum(1 for s in self._students.values() if s.online)

    def search(self, keyword: str) -> List[StudentInfo]:
        keyword = keyword.lower()
        with self._lock:
            results = []
            for s in self._students.values():
                if (keyword in s.display_name.lower() or
                        keyword in s.hostname.lower() or
                        keyword in s.ip or
                        keyword in s.mac.lower()):
                    results.append(s)
            return results
