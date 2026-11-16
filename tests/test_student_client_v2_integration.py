import os
import socket
import sys
import tempfile
import time
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

import pytest

from student.core.usb_control import UsbStorageController
from student.core.v2.lease import TemporaryControlKind

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


def test_mac_identity_does_not_get_overwritten_by_ipv6_address():
    import psutil

    client = object.__new__(StudentClient)
    client.local_ip = "10.0.2.15"
    addresses = [
        SimpleNamespace(family=psutil.AF_LINK, address="08-00-27-12-34-56"),
        SimpleNamespace(family=socket.AF_INET, address=client.local_ip),
        SimpleNamespace(family=socket.AF_INET6, address="fe80::e133:29d6:e94b:279e"),
    ]
    with patch("psutil.net_if_addrs", return_value={"Ethernet": addresses}):
        assert client._get_mac().replace("-", ":").lower() == "08:00:27:12:34:56"


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


@pytest.fixture
def usb_client():
    client = client_with_memory_state(service_mode=True)
    controller = MagicMock(spec=UsbStorageController)
    controller.POLICY_NOTICE = UsbStorageController.POLICY_NOTICE
    controller.last_error = "access denied"
    controller.is_blocked.return_value = False

    def block():
        controller.is_blocked.return_value = True
        return True

    def restore():
        controller.is_blocked.return_value = False
        return True

    controller.block_storage.side_effect = block
    controller.restore_storage.side_effect = restore
    client.usb_controller = controller
    client.tcp_client.send_message = MagicMock(return_value=True)
    try:
        yield client, controller
    finally:
        controller.restore_storage.side_effect = restore
        client.stop()


def usb_acks(client):
    return [call.args[0] for call in client.tcp_client.send_message.call_args_list
            if call.args[0].get("kind") == "ack"]


def test_usb_commands_execute_in_service_without_desktop_and_deduplicate(usb_client):
    client, controller = usb_client
    command = ControlEnvelope.command(CommandKind.USB_STORAGE_BLOCK.value, {}, revision=4)
    client._execute_v2_command(command)
    client._execute_v2_command(command)
    controller.block_storage.assert_called_once_with()
    acks = usb_acks(client)
    assert [item["stage"] for item in acks] == ["received", "applied"] * 2
    assert acks[-1]["payload"]["duplicate"] is True
    assert acks[-1]["payload"]["usb_storage_policy_applied"] is True
    assert "notice" in acks[-1]["payload"]
    assert TemporaryControlKind.USB_STORAGE_BLOCK in client.temporary_lease.active_controls()

    client._execute_v2_command(ControlEnvelope.command(CommandKind.USB_STORAGE_RESTORE.value, {}))
    controller.restore_storage.assert_called_once_with()
    assert usb_acks(client)[-1]["payload"]["usb_storage_policy_applied"] is False
    assert TemporaryControlKind.USB_STORAGE_BLOCK not in client.temporary_lease.active_controls()


@pytest.mark.parametrize("residual", [False, True])
def test_usb_block_failure_reports_failed_ack_and_keeps_residual_recovery(usb_client, residual):
    client, controller = usb_client
    controller.block_storage.side_effect = None
    controller.block_storage.return_value = False
    controller.is_blocked.return_value = residual
    client._execute_v2_command(ControlEnvelope.command(CommandKind.USB_STORAGE_BLOCK.value, {}))
    ack = usb_acks(client)[-1]
    assert ack["stage"] == "applied"
    assert ack["error"] == "access denied"
    assert ack["payload"]["status"] == "failed"
    assert (TemporaryControlKind.USB_STORAGE_BLOCK in client.temporary_lease.active_controls()) is residual


@pytest.mark.parametrize("trigger", ["command", "disconnect", "timeout", "stop"])
def test_usb_policy_restores_through_all_temporary_control_exit_paths(usb_client, trigger):
    client, controller = usb_client
    client._v2_usb_storage_block({})
    if trigger == "command":
        assert client._v2_restore_all({})["blacklist_preserved"] is True
    elif trigger == "disconnect":
        client._on_disconnected()
    elif trigger == "timeout":
        client.temporary_lease._deadline = 0
        assert client.temporary_lease.check_expired()[0].success
    else:
        client.stop()
    controller.restore_storage.assert_called_once_with()
    assert TemporaryControlKind.USB_STORAGE_BLOCK not in client.temporary_lease.active_controls()


def test_usb_restore_failure_returns_failed_ack_and_retries_on_lease(usb_client):
    client, controller = usb_client
    client._v2_usb_storage_block({})
    controller.restore_storage.side_effect = [False, True]
    client._execute_v2_command(ControlEnvelope.command(CommandKind.USB_STORAGE_RESTORE.value, {}))
    assert usb_acks(client)[-1]["payload"]["status"] == "failed"
    assert TemporaryControlKind.USB_STORAGE_BLOCK in client.temporary_lease.active_controls()
    client.temporary_lease._deadline = 0
    assert client.temporary_lease.check_expired()[0].success
    assert TemporaryControlKind.USB_STORAGE_BLOCK not in client.temporary_lease.active_controls()


def test_stale_usb_lease_callback_cannot_release_a_new_policy(usb_client):
    client, controller = usb_client
    client._v2_usb_storage_block({})
    old_restore = client.temporary_lease._active[TemporaryControlKind.USB_STORAGE_BLOCK]
    client._v2_usb_storage_block({})
    assert old_restore() is True
    controller.restore_storage.assert_not_called()
    assert TemporaryControlKind.USB_STORAGE_BLOCK in client.temporary_lease.active_controls()


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


def test_registration_includes_machine_hint_without_replacing_persisted_device_id():
    with patch("student.core.student_client.machine_fingerprint", return_value="hardware-01"):
        client = client_with_memory_state(service_mode=True)
    try:
        original_id = client.student_id
        client.tcp_client.send_message = MagicMock(return_value=True)
        client.heartbeat_sender.start = MagicMock()
        client._on_connected()
        registration = client.tcp_client.send_message.call_args.args[0]["params"]
        assert registration["student_id"] == original_id
        assert registration["machine_id"] == "hardware-01"
        assert client.state_store.get_or_create_device_id() == original_id
    finally:
        client.stop()
