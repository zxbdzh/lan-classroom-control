import pytest
import time
from common.heartbeat import TeacherHeartbeatManager, StudentHeartbeatSender


class TestTeacherHeartbeatManager:
    def test_register_and_check_alive(self):
        mgr = TeacherHeartbeatManager(timeout=2, check_interval=1)
        mgr.start()
        try:
            mgr.register_student("stu-001")
            assert mgr.is_alive("stu-001") is True
            assert mgr.is_alive("nonexistent") is False
        finally:
            mgr.stop()

    def test_record_heartbeat(self):
        mgr = TeacherHeartbeatManager(timeout=2, check_interval=1)
        mgr.start()
        try:
            mgr.register_student("stu-001")
            before = mgr.get_last_heartbeat("stu-001")
            time.sleep(0.1)
            mgr.record_heartbeat("stu-001")
            after = mgr.get_last_heartbeat("stu-001")
            assert after > before
        finally:
            mgr.stop()

    def test_timeout_detection(self):
        timeout_students = []
        mgr = TeacherHeartbeatManager(timeout=0.3, check_interval=0.1)
        mgr.on_student_timeout = lambda sid: timeout_students.append(sid)
        mgr.start()
        try:
            mgr.register_student("stu-001")
            time.sleep(0.5)
            assert "stu-001" in timeout_students
            assert mgr.is_alive("stu-001") is False
        finally:
            mgr.stop()

    def test_remove_student(self):
        mgr = TeacherHeartbeatManager(timeout=2, check_interval=1)
        mgr.start()
        try:
            mgr.register_student("stu-001")
            mgr.remove_student("stu-001")
            assert mgr.is_alive("stu-001") is False
        finally:
            mgr.stop()

    def test_multiple_students(self):
        mgr = TeacherHeartbeatManager(timeout=2, check_interval=1)
        mgr.start()
        try:
            for i in range(10):
                mgr.register_student(f"stu-{i:03d}")
            assert mgr.is_alive("stu-000") is True
            assert mgr.is_alive("stu-009") is True
        finally:
            mgr.stop()


class TestStudentHeartbeatSender:
    def test_start_stop(self):
        sender = StudentHeartbeatSender(interval=0.1)
        sent = []

        def send_func(msg):
            sent.append(msg)
            return True

        sender.start(send_func)
        time.sleep(0.25)
        sender.stop()
        assert len(sent) >= 2
        assert sent[0]["type"] == "student_heartbeat"

    def test_send_interval(self):
        sender = StudentHeartbeatSender(interval=0.1)
        timestamps = []

        def send_func(msg):
            timestamps.append(time.time())
            return True

        sender.start(send_func)
        time.sleep(0.35)
        sender.stop()
        assert len(timestamps) >= 3
        intervals = [timestamps[i + 1] - timestamps[i] for i in range(len(timestamps) - 1)]
        for interval in intervals:
            assert abs(interval - 0.1) < 0.05
