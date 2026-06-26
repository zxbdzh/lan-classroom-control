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
