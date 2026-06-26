import pytest
import time
import threading
from unittest.mock import patch, MagicMock
from teacher.core.teacher_server import TeacherServer
from student.core.student_client import StudentClient
from common.protocol import MessageType


@pytest.fixture
def teacher_server():
    server = TeacherServer(tcp_port=0, broadcast_port=0)
    server.tcp_port = 0
    server.start()
    time.sleep(0.1)
    actual_tcp_port = server.tcp_server._sock.getsockname()[1]
    yield server, actual_tcp_port
    server.stop()
    time.sleep(0.1)


class TestStudentClientBasics:
    def test_student_info(self):
        client = StudentClient()
        assert client.student_id is not None
        assert len(client.student_id) > 0
        assert client.hostname is not None
        assert client.local_ip is not None

    def test_initial_state(self):
        client = StudentClient()
        assert client.is_black_screen is False
        assert client.is_broadcasting is False
        assert client.is_net_blocked is False


class TestTeacherStudentIntegration:
    def test_student_register(self, teacher_server):
        server, tcp_port = teacher_server
        client = StudentClient()
        client.discover_listener.stop()
        client.tcp_client.auto_reconnect = False

        assert client.tcp_client.connect("127.0.0.1", tcp_port) is True
        time.sleep(0.3)

        students = server.student_manager.get_all_students()
        assert len(students) == 1
        assert students[0].student_id == client.student_id
        assert students[0].hostname == client.hostname

        client.stop()
        time.sleep(0.2)

    def test_black_screen_command(self, teacher_server):
        server, tcp_port = teacher_server
        client = StudentClient()
        client.discover_listener.stop()
        client.tcp_client.auto_reconnect = False

        black_screen_states = []
        client.on_black_screen_changed = lambda v: black_screen_states.append(v)

        client.tcp_client.connect("127.0.0.1", tcp_port)
        time.sleep(0.3)

        students = server.student_manager.get_all_students()
        assert len(students) == 1
        sid = students[0].student_id

        with patch.object(client.input_blocker, 'block') as mock_block, \
             patch.object(client.input_blocker, 'unblock') as mock_unblock:
            server.send_black_screen([sid], True)
            time.sleep(0.3)
            assert client.is_black_screen is True
            mock_block.assert_called_once()
            assert len(black_screen_states) >= 1
            assert black_screen_states[-1] is True

            server.send_black_screen([sid], False)
            time.sleep(0.3)
            assert client.is_black_screen is False
            mock_unblock.assert_called_once()

        client.stop()
        time.sleep(0.2)

    def test_net_control_command(self, teacher_server):
        server, tcp_port = teacher_server
        client = StudentClient()
        client.discover_listener.stop()
        client.tcp_client.auto_reconnect = False

        client.tcp_client.connect("127.0.0.1", tcp_port)
        time.sleep(0.3)

        students = server.student_manager.get_all_students()
        sid = students[0].student_id

        with patch.object(client.net_controller, 'block_internet') as mock_block, \
             patch.object(client.net_controller, 'unblock_internet') as mock_unblock:
            mock_block.return_value = True
            mock_unblock.return_value = True

            server.send_net_control([sid], True)
            time.sleep(0.3)
            assert client.is_net_blocked is True
            mock_block.assert_called_once()

            server.send_net_control([sid], False)
            time.sleep(0.3)
            assert client.is_net_blocked is False
            mock_unblock.assert_called_once()

        client.stop()
        time.sleep(0.2)

    def test_broadcast_start_stop(self, teacher_server):
        server, tcp_port = teacher_server
        client = StudentClient()
        client.discover_listener.stop()
        client.tcp_client.auto_reconnect = False

        broadcast_started = []
        broadcast_stopped = []
        client.on_broadcast_started = lambda p: broadcast_started.append(p)
        client.on_broadcast_stopped = lambda: broadcast_stopped.append(True)

        client.tcp_client.connect("127.0.0.1", tcp_port)
        time.sleep(0.3)

        students = server.student_manager.get_all_students()
        sid = students[0].student_id

        with patch.object(client.input_blocker, 'block'), \
             patch.object(client.input_blocker, 'unblock'):
            server.start_broadcast([sid])
            time.sleep(0.3)
            assert client.is_broadcasting is True
            assert len(broadcast_started) >= 1

            server.stop_broadcast([sid])
            time.sleep(0.3)
            assert client.is_broadcasting is False
            assert len(broadcast_stopped) >= 1

        client.stop()
        time.sleep(0.2)

    def test_student_disconnect(self, teacher_server):
        server, tcp_port = teacher_server
        client = StudentClient()
        client.discover_listener.stop()
        client.tcp_client.auto_reconnect = False

        client.tcp_client.connect("127.0.0.1", tcp_port)
        time.sleep(0.3)
        assert server.student_manager.online_count() == 1

        client.tcp_client.disconnect()
        time.sleep(0.3)
        assert server.student_manager.online_count() == 0
