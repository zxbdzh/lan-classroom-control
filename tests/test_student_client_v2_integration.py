import os
import sys
import tempfile
import time
from unittest.mock import MagicMock, patch

from common.v2.control import AckStage, ControlEnvelope
from common.v2.model import CommandKind
from common.file_transfer import TARGET_DIR_DEFAULT
from student.core.student_client import StudentClient
from student.core.v2.ipc import create_memory_channel_pair
from student.core.v2.runtime import AgentCallResult, SessionAgentProxy, SessionAgentRuntime
from student.core.v2.storage import StudentStateStore


def wait_for(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def client_with_memory_state(service_mode=False):
    with patch("student.core.student_client.autostart.ensure_autostart"):
        return StudentClient(
            save_dir=os.path.join(tempfile.gettempdir(), "lan-classroom-test-files"),
            state_store=StudentStateStore(":memory:"),
            service_mode=service_mode,
        )


def test_service_mode_file_delivery_uses_public_desktop():
    client = object.__new__(StudentClient)
    client.service_mode = True
    with patch.object(sys, "platform", "win32"), patch.dict(
        os.environ, {"PUBLIC": r"C:\Users\Public"}
    ):
        path = client._get_default_save_dir()

    assert path == os.path.join(r"C:\Users\Public", "Desktop", "课堂文件")


def test_service_mode_ignores_remote_custom_file_destination():
    client = client_with_memory_state(service_mode=True)
    client.file_receiver.handle_start = MagicMock(
        return_value={"resume_chunk": 0}
    )
    client.tcp_client.send_message = MagicMock(return_value=True)

    client._handle_file_start({
        "transfer_id": "transfer-1",
        "file_name": "lesson.txt",
        "file_size": 0,
        "total_chunks": 0,
        "target_dir_type": "custom",
        "target_dir_path": r"C:\Windows\System32",
    })

    received = client.file_receiver.handle_start.call_args.args[0]
    assert received["target_dir_type"] == TARGET_DIR_DEFAULT
    assert received["target_dir_path"] == ""
    client.stop()


def test_student_client_sends_received_and_applied_ack_for_v2_command():
    client = client_with_memory_state()
    client.tcp_client.send_message = MagicMock(return_value=True)
    client.tcp_client.is_connected = MagicMock(return_value=True)
    client.input_blocker.block = MagicMock()
    command = ControlEnvelope.command(
        CommandKind.SILENCE_START.value,
        {"message": "请保持安静"},
        command_id="5afbcf40-648a-4afd-b6c8-558c551afdd1",
        device_id="seat-bound-id",
        revision=7,
    )

    client._on_message(command.to_dict())

    assert wait_for(lambda: client.tcp_client.send_message.call_count >= 2)
    acknowledgements = [
        call.args[0] for call in client.tcp_client.send_message.call_args_list
        if call.args[0].get("kind") == "ack"
    ]
    assert [item["stage"] for item in acknowledgements] == [
        AckStage.RECEIVED.value,
        AckStage.APPLIED.value,
    ]
    assert acknowledgements[-1]["revision"] == 7
    assert acknowledgements[-1]["device_id"] == "seat-bound-id"
    assert client.is_black_screen is True
    client.stop()


def test_remote_input_executes_inline_to_preserve_tcp_order():
    client = client_with_memory_state()
    client._execute_v2_command = MagicMock()
    command = ControlEnvelope.command(
        CommandKind.REMOTE_INPUT.value,
        {"type": "mouse_button", "button": "left", "pressed": True},
    )

    client._handle_v2_command(command.to_dict())

    client._execute_v2_command.assert_called_once()
    client.stop()


def test_service_mode_delegates_desktop_action_over_ipc():
    service_channel, agent_channel = create_memory_channel_pair()
    agent = SessionAgentRuntime(agent_channel)
    calls = []
    agent.register_handler(
        CommandKind.SILENCE_START.value,
        lambda params: calls.append(dict(params)) or {"silence": True},
    )
    agent.register_handler(
        CommandKind.SILENCE_STOP.value,
        lambda params: {"silence": False},
    )
    agent.start()
    client = client_with_memory_state(service_mode=True)
    client.attach_session_agent(SessionAgentProxy(service_channel))

    result = client._v2_silence_start({"message": "课堂提示"})

    assert result.status.value == "succeeded"
    assert calls[0]["message"] == "课堂提示"
    assert calls[0]["device_id"] == client.student_id
    client.command_runtime.stop()
    agent.stop()
    service_channel.close()
    agent_channel.close()
    client.state_store.close()


def test_source_client_starts_tcp_fallback_after_midstream_udp_loss():
    client = client_with_memory_state()
    client._broadcasting = True
    client._broadcast_fallback_generation = 3
    client._broadcast_fallback = None
    client._last_udp_broadcast_frame = 9.0
    client._schedule_broadcast_fallback = MagicMock()
    client.tcp_client.server_host = "192.168.1.2"
    fallback = MagicMock()

    with patch(
        "student.core.student_client.TcpMjpegDownlink",
        return_value=fallback,
    ) as downlink, patch(
        "student.core.student_client.time.monotonic",
        return_value=10.0,
    ):
        client._check_broadcast_fallback(
            "6f304b4b-3d14-4fd0-97e8-97f786436e91", 9531, 3
        )

    downlink.assert_called_once()
    fallback.start.assert_called_once_with()
    assert client._broadcast_fallback is fallback
    client._schedule_broadcast_fallback.assert_called_once_with(
        "6f304b4b-3d14-4fd0-97e8-97f786436e91", 9531, 3
    )
    client.stop()


def test_service_replays_active_desktop_controls_to_restarted_agent():
    class RecordingAgent:
        def __init__(self):
            self.calls = []

        def call(self, action, params, timeout=0):
            self.calls.append((action, dict(params), timeout))
            return AgentCallResult(True, {"applied": True})

    client = client_with_memory_state(service_mode=True)
    first_agent = RecordingAgent()
    client.attach_session_agent(first_agent)
    broadcast = {
        "session_id": "6f304b4b-3d14-4fd0-97e8-97f786436e91",
        "multicast_group": "239.255.95.29",
        "port": 9529,
        "fallback_port": 9531,
    }
    assert client._v2_broadcast_start(broadcast).status.value == "succeeded"
    assert client._v2_silence_start({"message": "Quiet"}).status.value == "succeeded"

    client.attach_session_agent(None)
    replacement = RecordingAgent()
    client.attach_session_agent(replacement)

    assert [item[0] for item in replacement.calls] == [
        CommandKind.BROADCAST_START.value,
        CommandKind.SILENCE_START.value,
    ]
    assert all(item[1]["device_id"] == client.student_id for item in replacement.calls)
    assert all(item[1]["teacher_host"] == "127.0.0.1" for item in replacement.calls)

    client._restore_temporary_controls()
    assert client._session_replay == {}
    client.stop()
