import pytest
import time
from teacher.core.student_manager import StudentManager, StudentInfo


@pytest.fixture
def sample_student():
    return StudentInfo(
        student_id="test-stu-001",
        hostname="PC-001",
        ip="192.168.1.101",
        mac="00:11:22:33:44:55",
        version="1.0.0"
    )


@pytest.fixture
def sample_student2():
    return StudentInfo(
        student_id="test-stu-002",
        hostname="PC-002",
        ip="192.168.1.102",
        mac="00:11:22:33:44:66",
        version="1.0.0"
    )


class TestStudentInfo:
    def test_student_info_creation(self, sample_student):
        assert sample_student.student_id == "test-stu-001"
        assert sample_student.hostname == "PC-001"
        assert sample_student.ip == "192.168.1.101"
        assert sample_student.mac == "00:11:22:33:44:55"
        assert sample_student.online is True
        assert sample_student.group == "default"
        assert sample_student.display_name == "PC-001"

    def test_student_info_to_dict(self, sample_student):
        d = sample_student.to_dict()
        assert d["student_id"] == "test-stu-001"
        assert d["hostname"] == "PC-001"
        assert d["ip"] == "192.168.1.101"
        assert d["online"] is True
        assert "status" in d


class TestStudentManager:
    def test_add_student(self, sample_student):
        mgr = StudentManager()
        mgr.add_student(sample_student)
        assert mgr.count() == 1
        assert mgr.get_student("test-stu-001") == sample_student

    def test_remove_student(self, sample_student):
        mgr = StudentManager()
        mgr.add_student(sample_student)
        mgr.remove_student("test-stu-001")
        assert mgr.count() == 0
        assert mgr.get_student("test-stu-001") is None

    def test_get_all_students(self, sample_student, sample_student2):
        mgr = StudentManager()
        mgr.add_student(sample_student)
        mgr.add_student(sample_student2)
        all_students = mgr.get_all_students()
        assert len(all_students) == 2

    def test_get_online_students(self, sample_student, sample_student2):
        mgr = StudentManager()
        mgr.add_student(sample_student)
        mgr.add_student(sample_student2)
        mgr.set_student_offline("test-stu-002")
        online = mgr.get_online_students()
        assert len(online) == 1
        assert online[0].student_id == "test-stu-001"

    def test_set_student_offline(self, sample_student):
        mgr = StudentManager()
        mgr.add_student(sample_student)
        mgr.set_student_offline("test-stu-001")
        assert mgr.get_student("test-stu-001").online is False
        assert mgr.online_count() == 0

    def test_update_heartbeat(self, sample_student):
        mgr = StudentManager()
        mgr.add_student(sample_student)
        time.sleep(0.01)
        mgr.update_heartbeat("test-stu-001")
        student = mgr.get_student("test-stu-001")
        assert student.last_heartbeat > student.register_time

    def test_update_status(self, sample_student):
        mgr = StudentManager()
        mgr.add_student(sample_student)
        mgr.update_student_status("test-stu-001", "black_screen", True)
        assert mgr.get_student("test-stu-001").status["black_screen"] is True

    def test_set_display_name(self, sample_student):
        mgr = StudentManager()
        mgr.add_student(sample_student)
        mgr.set_display_name("test-stu-001", "张三的电脑")
        assert mgr.get_student("test-stu-001").display_name == "张三的电脑"

    def test_groups(self, sample_student, sample_student2):
        mgr = StudentManager()
        mgr.add_student(sample_student)
        mgr.add_student(sample_student2)
        mgr.set_group("test-stu-001", "group1")
        mgr.add_group("group2")
        assert "group1" in mgr.get_groups()
        assert "group2" in mgr.get_groups()
        group1_students = mgr.get_students_by_group("group1")
        assert len(group1_students) == 1
        assert group1_students[0].student_id == "test-stu-001"

    def test_remove_group(self, sample_student):
        mgr = StudentManager()
        mgr.add_student(sample_student)
        mgr.set_group("test-stu-001", "group1")
        mgr.remove_group("group1")
        assert "group1" not in mgr.get_groups()
        assert mgr.get_student("test-stu-001").group == "default"

    def test_find_by_ip(self, sample_student):
        mgr = StudentManager()
        mgr.add_student(sample_student)
        found = mgr.find_by_ip("192.168.1.101")
        assert found is not None
        assert found.student_id == "test-stu-001"

    def test_find_by_mac(self, sample_student):
        mgr = StudentManager()
        mgr.add_student(sample_student)
        found = mgr.find_by_mac("00:11:22:33:44:55")
        assert found is not None
        assert found.student_id == "test-stu-001"

    def test_search(self, sample_student, sample_student2):
        mgr = StudentManager()
        mgr.add_student(sample_student)
        mgr.add_student(sample_student2)
        results = mgr.search("PC-001")
        assert len(results) == 1
        assert results[0].student_id == "test-stu-001"
        results = mgr.search("192.168.1.102")
        assert len(results) == 1
        assert results[0].student_id == "test-stu-002"

    def test_count_and_online_count(self, sample_student, sample_student2):
        mgr = StudentManager()
        assert mgr.count() == 0
        assert mgr.online_count() == 0
        mgr.add_student(sample_student)
        mgr.add_student(sample_student2)
        assert mgr.count() == 2
        assert mgr.online_count() == 2
        mgr.set_student_offline("test-stu-001")
        assert mgr.count() == 2
        assert mgr.online_count() == 1

    def test_callbacks(self, sample_student):
        mgr = StudentManager()
        added = []
        removed = []
        changed = []

        def on_added(s):
            added.append(s.student_id)

        def on_removed(s):
            removed.append(s.student_id)

        def on_changed():
            changed.append(1)

        mgr.on_student_added = on_added
        mgr.on_student_removed = on_removed
        mgr.on_student_changed = on_changed

        mgr.add_student(sample_student)
        assert len(added) == 1
        assert len(changed) >= 1

        mgr.remove_student("test-stu-001")
        assert len(removed) == 1
