import pytest
import socket
import time
import threading
from common.discover import TeacherDiscover, StudentDiscoverListener
from common.protocol import MessageType, build_message, serialize_message, deserialize_message


def find_free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class TestTeacherDiscover:
    def test_start_stop(self):
        port = find_free_port()
        discover = TeacherDiscover(broadcast_port=port, tcp_port=9999, broadcast_interval=0.5)
        discover.start()
        assert discover._running is True
        discover.stop()
        assert discover._running is False

    def test_broadcast_content(self):
        port = find_free_port()
        tcp_port = 9999
        discover = TeacherDiscover(broadcast_port=port, tcp_port=tcp_port, broadcast_interval=0.3)
        discover.start()
        time.sleep(0.5)

        recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        recv_sock.bind(("", port))
        recv_sock.settimeout(2)

        try:
            data, addr = recv_sock.recvfrom(4096)
            msg = deserialize_message(data)
            assert msg["type"] == MessageType.TEACHER_DISCOVER.value
            assert msg["params"]["tcp_port"] == tcp_port
        finally:
            recv_sock.close()
            discover.stop()


class TestStudentDiscoverListener:
    def test_start_stop(self):
        port = find_free_port()
        listener = StudentDiscoverListener(listen_port=port)
        listener.start()
        assert listener._running is True
        listener.stop()
        assert listener._running is False

    def test_detect_teacher(self):
        port = find_free_port()
        tcp_port = 8888
        discovered = []
        event = threading.Event()

        def on_discover(ip, tp, name):
            discovered.append((ip, tp, name))
            event.set()

        listener = StudentDiscoverListener(listen_port=port, on_discover_callback=on_discover)
        listener.start()
        time.sleep(0.2)

        send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        msg = build_message(MessageType.TEACHER_DISCOVER, {
            "tcp_port": tcp_port,
            "teacher_name": "TestTeacher"
        })
        data = serialize_message(msg)
        send_sock.sendto(data, ("127.0.0.1", port))
        send_sock.close()

        event.wait(timeout=2)
        assert len(discovered) > 0
        assert discovered[0][1] == tcp_port
        assert discovered[0][2] == "TestTeacher"

        listener.stop()

    def test_ignore_non_teacher_packets(self):
        port = find_free_port()
        discovered = []

        def on_discover(ip, tp, name):
            discovered.append((ip, tp, name))

        listener = StudentDiscoverListener(listen_port=port, on_discover_callback=on_discover)
        listener.start()
        time.sleep(0.2)

        send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        send_sock.sendto(b"garbage data", ("127.0.0.1", port))
        send_sock.close()

        time.sleep(0.3)
        assert len(discovered) == 0
        listener.stop()

    def test_student_announce(self):
        port = find_free_port()
        listener = StudentDiscoverListener(
            listen_port=port,
            announce_port=port,
            student_id="test-stu-001",
            hostname="TestPC",
            mac="00:11:22:33:44:55",
            version="1.0.0",
            announce_interval=0.2
        )
        listener.start()
        time.sleep(0.3)

        recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        recv_sock.bind(("", port))
        recv_sock.settimeout(2)

        try:
            data, addr = recv_sock.recvfrom(4096)
            msg = deserialize_message(data)
            assert msg["type"] == MessageType.STUDENT_ANNOUNCE.value
            assert msg["params"]["student_id"] == "test-stu-001"
            assert msg["params"]["hostname"] == "TestPC"
            assert msg["params"]["mac"] == "00:11:22:33:44:55"
        finally:
            recv_sock.close()
            listener.stop()


class TestTeacherDiscoverStudentAnnounce:
    def test_teacher_receives_student_announce(self):
        port = find_free_port()
        tcp_port = 9999
        discovered = []
        event = threading.Event()

        def on_discovered(info):
            discovered.append(info)
            event.set()

        teacher = TeacherDiscover(
            broadcast_port=port,
            tcp_port=tcp_port,
            broadcast_interval=0.5
        )
        teacher.on_student_discovered = on_discovered
        teacher.start()
        time.sleep(0.2)

        send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        msg = build_message(MessageType.STUDENT_ANNOUNCE, {
            "student_id": "stu-001",
            "hostname": "PC-001",
            "mac": "aa:bb:cc:dd:ee:ff",
            "version": "1.0.0"
        })
        data = serialize_message(msg)
        send_sock.sendto(data, ("127.0.0.1", port))
        send_sock.close()

        event.wait(timeout=2)
        assert len(discovered) > 0
        assert discovered[0]["student_id"] == "stu-001"
        assert discovered[0]["hostname"] == "PC-001"
        assert discovered[0]["mac"] == "aa:bb:cc:dd:ee:ff"

        teacher.stop()

    def test_teacher_get_discovered_students(self):
        port = find_free_port()
        teacher = TeacherDiscover(
            broadcast_port=port,
            tcp_port=9999,
            broadcast_interval=0.5
        )
        teacher.start()
        time.sleep(0.2)

        send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        msg = build_message(MessageType.STUDENT_ANNOUNCE, {
            "student_id": "stu-002",
            "hostname": "PC-002",
            "mac": "11:22:33:44:55:66",
            "version": "1.0.0"
        })
        data = serialize_message(msg)
        send_sock.sendto(data, ("127.0.0.1", port))
        send_sock.close()

        time.sleep(0.3)
        students = teacher.get_discovered_students()
        assert len(students) == 1
        assert students[0]["student_id"] == "stu-002"

        teacher.stop()

    def test_teacher_student_lost_timeout(self):
        port = find_free_port()
        lost = []
        event = threading.Event()

        def on_lost(info):
            lost.append(info)
            event.set()

        teacher = TeacherDiscover(
            broadcast_port=port,
            tcp_port=9999,
            broadcast_interval=0.5,
            student_timeout=0.5,
            cleanup_interval=0.2
        )
        teacher.on_student_lost = on_lost
        teacher.start()
        time.sleep(0.2)

        send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        msg = build_message(MessageType.STUDENT_ANNOUNCE, {
            "student_id": "stu-003",
            "hostname": "PC-003",
            "mac": "22:33:44:55:66:77",
            "version": "1.0.0"
        })
        data = serialize_message(msg)
        send_sock.sendto(data, ("127.0.0.1", port))
        send_sock.close()

        time.sleep(0.3)
        assert len(teacher.get_discovered_students()) == 1

        event.wait(timeout=2)
        assert len(lost) > 0
        assert lost[0]["student_id"] == "stu-003"

        teacher.stop()

    def test_send_discover_to(self):
        port = find_free_port()
        teacher = TeacherDiscover(
            broadcast_port=port,
            tcp_port=9999,
            broadcast_interval=0.5
        )
        teacher.start()
        time.sleep(0.2)

        recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        recv_sock.bind(("", port))
        recv_sock.settimeout(2)

        teacher.send_discover_to("127.0.0.1")

        try:
            data, addr = recv_sock.recvfrom(4096)
            msg = deserialize_message(data)
            assert msg["type"] == MessageType.TEACHER_DISCOVER.value
            assert msg["params"]["tcp_port"] == 9999
        finally:
            recv_sock.close()
            teacher.stop()

    def test_scan_once(self):
        port = find_free_port()
        teacher = TeacherDiscover(
            broadcast_port=port,
            tcp_port=9999,
            broadcast_interval=10.0
        )
        teacher.start()
        time.sleep(0.2)

        recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        recv_sock.bind(("", port))
        recv_sock.settimeout(2)

        teacher.scan_once()

        try:
            data, addr = recv_sock.recvfrom(4096)
            msg = deserialize_message(data)
            assert msg["type"] == MessageType.TEACHER_DISCOVER.value
        finally:
            recv_sock.close()
            teacher.stop()
