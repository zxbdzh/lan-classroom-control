import pytest
import base64
import socket
import time
import threading
from unittest.mock import patch, MagicMock
from teacher.core.teacher_server import TeacherServer
from student.core.student_client import StudentClient
from common.protocol import MessageType
from common.protocol import build_message, serialize_message
from common.v2.control import AckStage, ControlEnvelope
from student.core.v2.storage import StudentStateStore


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
    def test_discovery_packet_auto_connects_and_registers_without_credentials(
        self, teacher_server
    ):
        server, tcp_port = teacher_server
        listener_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        listener_socket.bind(("127.0.0.1", 0))
        discovery_port = listener_socket.getsockname()[1]
        listener_socket.close()

        client = StudentClient(state_store=StudentStateStore(":memory:"))
        client.tcp_client.auto_reconnect = False
        client.discover_listener.listen_port = discovery_port
        client.discover_listener.announce_port = discovery_port
        registered = threading.Event()
        server.on_student_added = lambda _student: registered.set()
        client.discover_listener.start()
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            message = build_message(
                MessageType.TEACHER_DISCOVER,
                {"tcp_port": tcp_port, "teacher_name": "Teacher"},
            )
            sender.sendto(
                serialize_message(message),
                ("127.0.0.1", discovery_port),
            )

            assert registered.wait(2.0)
            student = server.student_manager.get_student(client.student_id)
            assert student is not None
            assert student.online is True
        finally:
            sender.close()
            client.stop()

    def test_registration_restores_persistent_identity_and_group(self):
        server = TeacherServer(tcp_port=0, broadcast_port=0)
        server.set_identity_resolver(
            lambda device_id, mac, machine_id: ("stable-id", "第一组")
        )
        connection = MagicMock()
        connection.addr = ("192.168.10.21", 50000)

        server._handle_student_register(
            connection,
            {
                "student_id": "new-id",
                "hostname": "PC-21",
                "mac": "00:11:22:33:44:55",
                "protocol_version": 2,
            },
        )

        student = server.student_manager.get_student("stable-id")
        assert connection.student_id == "stable-id"
        assert student is not None
        assert student.group == "第一组"

    def test_registration_reuses_existing_identity_for_same_mac(self):
        server = TeacherServer(tcp_port=0, broadcast_port=0)
        first_connection = MagicMock()
        first_connection.addr = ("192.168.10.21", 50000)
        server._handle_student_register(
            first_connection,
            {
                "student_id": "stable-id",
                "hostname": "PC-21",
                "mac": "00:11:22:33:44:55",
            },
        )
        server.set_student_group(["stable-id"], "第一组")

        replacement_connection = MagicMock()
        replacement_connection.addr = ("192.168.10.21", 50001)
        server._handle_student_register(
            replacement_connection,
            {
                "student_id": "regenerated-id",
                "hostname": "PC-21",
                "mac": "00:11:22:33:44:55",
            },
        )

        assert replacement_connection.student_id == "stable-id"
        assert server.student_manager.get_student("stable-id").group == "第一组"
        assert server.student_manager.get_student("regenerated-id") is None
        first_connection.stop.assert_called_once_with()

        server._on_disconnect(first_connection)

        current = server.student_manager.get_student("stable-id")
        assert current.conn is replacement_connection
        assert current.online is True

    def test_connection_identity_overrides_stale_payload_identity(self):
        server = TeacherServer(tcp_port=0, broadcast_port=0)
        connection = MagicMock()
        connection.student_id = "stable-id"
        connection.addr = ("192.168.10.21", 50000)
        server.file_distributor.record_ack = MagicMock()

        server._handle_file_ack(connection, {
            "student_id": "regenerated-id",
            "transfer_id": "transfer-1",
            "stage": "end",
            "success": True,
        })

        recorded = server.file_distributor.record_ack.call_args.args[0]
        assert recorded["student_id"] == "stable-id"

        acknowledgements = []
        server.set_ack_handler(acknowledgements.append)
        command = ControlEnvelope.command(
            "silence.start",
            command_id="5afbcf40-648a-4afd-b6c8-558c551afdd1",
            device_id="stable-id",
            revision=7,
        )
        ack = ControlEnvelope.ack(
            command_id=command.command_id,
            device_id="regenerated-id",
            revision=command.revision,
            stage=AckStage.APPLIED,
        )
        server._handle_v2_ack(connection, ack.to_dict())

        assert acknowledgements[0].device_id == "stable-id"

        frames = []
        server.on_student_screen_frame = lambda *args: frames.append(args)
        server._handle_student_screen_frame(connection, {
            "student_id": "regenerated-id",
            "frame_data": base64.b64encode(b"jpeg").decode("ascii"),
            "width": 320,
            "height": 180,
        })

        assert frames == [("stable-id", b"jpeg", (320, 180))]

    def test_reconnect_reuses_same_mac_despite_separator_and_case_changes(self):
        server = TeacherServer(tcp_port=0, broadcast_port=0)
        original = MagicMock()
        original.addr = ("192.168.10.21", 50000)
        server._handle_student_register(original, {
            "student_id": "stable-id", "hostname": "PC-21",
            "mac": "00:11:22:33:44:ab",
        })
        server.set_student_group(["stable-id"], "第一组")
        replacement = MagicMock()
        replacement.addr = ("192.168.10.22", 50001)

        server._handle_student_register(replacement, {
            "student_id": "regenerated-id", "hostname": "PC-21",
            "mac": "00-11-22-33-44-AB",
        })

        assert server.student_manager.count() == 1
        assert replacement.student_id == "stable-id"
        assert server.student_manager.get_student("stable-id").group == "第一组"
        original.stop.assert_called_once_with()
        server._on_disconnect(original)
        assert server.student_manager.get_student("stable-id").online

    def test_reconnect_retains_known_machine_hint_for_legacy_registration(self):
        server = TeacherServer(tcp_port=0, broadcast_port=0)
        original = MagicMock()
        original.addr = ("192.168.10.21", 50000)
        server._handle_student_register(original, {
            "student_id": "stable-id", "hostname": "PC-21",
            "mac": "02:10:20:30:40:01", "machine_id": "hardware-01",
        })
        replacement = MagicMock()
        replacement.addr = ("192.168.10.21", 50001)
        server._handle_student_register(replacement, {
            "student_id": "stable-id", "hostname": "PC-21",
            "mac": "02:10:20:30:40:01",
        })

        assert server.devices()[0].metadata["machine_id"] == "hardware-01"

    def test_old_socket_heartbeat_cannot_revive_disconnected_replacement(self):
        server = TeacherServer(tcp_port=0, broadcast_port=0)
        original = MagicMock()
        original.addr = ("192.168.10.21", 50000)
        params = {"student_id": "stable-id", "hostname": "PC-21", "mac": "02:10:20:30:40:01"}
        server._handle_student_register(original, params)
        replacement = MagicMock()
        replacement.addr = ("192.168.10.21", 50001)
        server._handle_student_register(replacement, params)
        server._on_disconnect(replacement)

        server._handle_heartbeat(original, {})

        assert not server.student_manager.get_student("stable-id").online

    def test_repeated_tcp_reconnect_and_teacher_restart_keep_one_device(self, tmp_path):
        from teacher.core.runtime import TeacherRuntime

        database = str(tmp_path / "teacher.db")
        student_database = str(tmp_path / "student.db")
        server = TeacherServer(tcp_port=0, broadcast_port=0)
        server.tcp_server.host = "127.0.0.1"
        runtime = TeacherRuntime(server=server, database_path=database)
        server.tcp_server.start()
        port = server.tcp_server._sock.getsockname()[1]
        stable_id = None

        def wait_until(predicate):
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if predicate():
                    return True
                time.sleep(0.01)
            return False

        try:
            for iteration in range(4):
                with StudentStateStore(student_database) as state:
                    device_id = state.get_or_create_device_id()
                stable_id = stable_id or device_id
                assert device_id == stable_id
                with socket.create_connection(("127.0.0.1", port), timeout=2.0) as connection:
                    connection.sendall(serialize_message(build_message(
                        MessageType.STUDENT_REGISTER, {
                            "student_id": device_id, "hostname": "PC-RECONNECT",
                            "mac": "02:10:20:30:40:{:02x}".format(iteration + 1),
                            "machine_id": "hardware-reconnect", "protocol_version": 2,
                        },
                    )))
                    assert wait_until(lambda: (
                        len(runtime.snapshot().devices) == 1
                        and runtime.snapshot().device(stable_id).online
                    ))
                    if iteration == 0:
                        server.set_student_group([stable_id], "第一组")
                    assert runtime.snapshot().device(stable_id).group == "第一组"
                    assert server.student_manager.count() == 1
                assert wait_until(lambda: not runtime.snapshot().device(stable_id).online)
                if iteration == 1:
                    runtime.close()
                    runtime = TeacherRuntime(server=server, database_path=database)
            assert len(runtime.snapshot().devices) == 1
        finally:
            server.tcp_server.stop()
            runtime.close()

    def test_concurrent_reconnections_do_not_create_extra_devices(self):
        from concurrent.futures import ThreadPoolExecutor
        from teacher.core.runtime import TeacherRuntime

        server = TeacherServer(tcp_port=0, broadcast_port=0)
        runtime = TeacherRuntime(server=server, database_path=":memory:")
        connections = []
        for iteration in range(6):
            connection = MagicMock()
            connection.addr = ("192.168.10.21", 50000 + iteration)
            connections.append(connection)
        try:
            def register(connection):
                server._handle_student_register(connection, {
                    "student_id": "stable-id", "hostname": "PC-21",
                    "mac": "02:10:20:30:40:01", "machine_id": "hardware-01",
                })

            with ThreadPoolExecutor(max_workers=6) as executor:
                list(executor.map(register, connections))
            assert server.student_manager.count() == 1
            assert len(runtime.snapshot().devices) == 1
            assert all(connection.student_id == "stable-id" for connection in connections)
            assert sum(connection.stop.call_count for connection in connections) == 5
        finally:
            runtime.close()

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

    def test_net_control_disabled_noop(self, teacher_server):
        server, tcp_port = teacher_server
        client = StudentClient()
        client.discover_listener.stop()
        client.tcp_client.auto_reconnect = False

        client.tcp_client.connect("127.0.0.1", tcp_port)
        time.sleep(0.3)

        students = server.student_manager.get_all_students()
        sid = students[0].student_id

        server.send_net_control([sid], True)
        time.sleep(0.3)
        assert client.is_net_blocked is False
        assert students[0].status.get("net_control_pending") is None
        assert students[0].status.get("net_blocked") is False

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

    def test_student_screen_frame_report(self, teacher_server):
        server, tcp_port = teacher_server
        client = StudentClient()
        client.discover_listener.stop()
        client.tcp_client.auto_reconnect = False

        received = []
        event = threading.Event()
        server.on_student_screen_frame = lambda sid, data, size: (
            received.append((sid, data, size)),
            event.set()
        )

        client.tcp_client.connect("127.0.0.1", tcp_port)
        time.sleep(0.3)
        client._send_screen_frame(b"fake-jpeg-frame", (320, 180))

        assert event.wait(timeout=2)
        assert received[0][0] == client.student_id
        assert received[0][1] == b"fake-jpeg-frame"
        assert received[0][2] == (320, 180)

        client.stop()
        time.sleep(0.2)
