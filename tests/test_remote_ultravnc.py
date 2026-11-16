from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from common.v2 import CommandKind, DeviceIds
from student.core.v2.session_actions import DesktopSessionActions
from teacher.core.classroom import AckPhase, InMemoryTransport, TransportAck
from teacher.core.runtime import TeacherRuntime


@pytest.fixture
def desktop(qtbot):
    actions = DesktopSessionActions("pc-01")
    qtbot.addWidget(actions.overlay)
    actions.input_blocker = MagicMock()
    actions.input_blocker.block.return_value = True
    actions.input_blocker.unblock.return_value = True
    yield actions
    actions.close()


def test_silence_remote_switch_restores_only_still_active_silence(desktop):
    desktop._silence_start({"message": "请听讲"})
    assert desktop.overlay.isVisible()
    desktop._remote_start({})
    assert desktop._remote_active
    desktop.input_blocker.unblock.assert_called()
    assert desktop._capturer is None
    desktop.overlay._hide_if_current(desktop.overlay._visibility_generation)
    assert not desktop.overlay.isVisible()
    desktop._remote_stop({})
    assert desktop._silence
    assert desktop.overlay.isVisible()
    assert desktop.overlay._message_label.text() == "请听讲"
    desktop._remote_start({})
    desktop._silence_stop({})
    desktop._remote_stop({})
    assert not desktop._silence
    assert not desktop._input_reasons


def test_100_switches_and_restore_do_not_leave_input_reasons(desktop):
    for _index in range(100):
        desktop._silence_start({})
        desktop._remote_start({})
        desktop._remote_stop({})
        desktop._silence_stop({})
    desktop.restore_all()
    assert not desktop._remote_active
    assert not desktop._silence
    assert not desktop._input_reasons
    assert desktop._capturer is None


def test_failed_input_lock_does_not_report_silence_success(desktop):
    desktop.input_blocker.block.return_value = False
    with pytest.raises(RuntimeError, match="键鼠"):
        desktop._silence_start({})
    assert not desktop._silence
    assert not desktop._input_reasons


def test_agent_service_timeout_releases_overlay_and_input(desktop):
    desktop._silence_start({})
    desktop._service_seen = 0
    with patch("student.core.v2.session_actions.time.monotonic", return_value=20):
        desktop._check_service()
    assert not desktop._input_reasons
    assert not desktop._silence
    desktop.input_blocker.unblock.assert_called()


def test_external_viewer_waits_for_applied_and_exit_sends_stop(tmp_path):
    transport = InMemoryTransport()
    transport.connect("pc-01", "PC-01", metadata={"ip": "192.168.1.21"})
    transport.connect("pc-02", "PC-02", metadata={"ip": "192.168.1.22"})
    runtime = TeacherRuntime(server=transport, database_path=":memory:")
    executable = tmp_path / "vncviewer.exe"
    executable.write_bytes(b"test")
    runtime.remote_windows.prepare = lambda: str(executable)
    process = MagicMock()
    process.poll.return_value = None
    try:
        session = runtime.start_remote_control("pc-01")
        with patch("teacher.core.remote_control.subprocess.Popen", return_value=process) as launch:
            runtime.poll_remote_windows()
            launch.assert_not_called()
            runtime.classroom._on_ack(TransportAck(
                session.command_id, "pc-01", AckPhase.APPLIED,
                payload={"backend": "ultravnc", "port": 15900},
            ))
            runtime.poll_remote_windows()
            args = launch.call_args.args[0]
            assert "192.168.1.21::15900" in args
            assert "pc-02" not in runtime.remote_windows.sessions
            process.poll.return_value = 0
            runtime.poll_remote_windows()
        assert not runtime.remote_windows.sessions
        assert transport.sent[-1].envelope.action == CommandKind.REMOTE_CONTROL_STOP.value
    finally:
        runtime.close()


def test_missing_viewer_does_not_change_student_state():
    runtime = TeacherRuntime(database_path=":memory:")
    try:
        runtime.remote_windows.prepare = MagicMock(side_effect=RuntimeError("missing viewer"))
        with pytest.raises(RuntimeError, match="missing"):
            runtime.start_remote_control("pc-01")
        assert not runtime.snapshot().operations
    finally:
        runtime.close()


def test_viewer_launch_failure_cleans_remote_session(tmp_path):
    transport = InMemoryTransport()
    transport.connect("pc-01", metadata={"ip": "192.168.1.21"})
    runtime = TeacherRuntime(server=transport, database_path=":memory:")
    runtime.remote_windows.prepare = lambda: "vncviewer.exe"
    try:
        session = runtime.start_remote_control("pc-01")
        runtime.classroom._on_ack(TransportAck(
            session.command_id, "pc-01", AckPhase.APPLIED,
            payload={"backend": "ultravnc", "port": 15900},
        ))
        with patch("teacher.core.remote_control.subprocess.Popen", side_effect=OSError("broken")):
            messages = runtime.poll_remote_windows()
        assert messages and "broken" in messages[0]
        assert not runtime.remote_windows.sessions
        assert transport.sent[-1].envelope.action == CommandKind.REMOTE_CONTROL_STOP.value
    finally:
        runtime.close()


def test_cloned_device_id_with_different_mac_keeps_both_devices():
    transport = InMemoryTransport()
    transport.connect("cloned", metadata={"mac": "00:11:22:33:44:01"})
    runtime = TeacherRuntime(server=transport, database_path=":memory:")
    try:
        second_id, group = runtime._resolve_registered_identity(
            "cloned", "00:11:22:33:44:02"
        )
        assert second_id != "cloned"
        transport.connect(second_id, metadata={"mac": "00:11:22:33:44:02"})
        assert runtime._resolve_registered_identity(
            "cloned", "00:11:22:33:44:02"
        )[0] == second_id
        receipt = runtime.set_silence(DeviceIds(["cloned", second_id]), True)
        assert len(receipt.target_ids) == 2
    finally:
        runtime.close()


def test_restarted_device_keeps_identity_after_network_adapter_changes():
    transport = InMemoryTransport()
    transport.connect("stable-id", "PC-01", "第一组", metadata={"mac": "00:11:22:33:44:01"})
    runtime = TeacherRuntime(server=transport, database_path=":memory:")
    try:
        transport.disconnect("stable-id")
        assert runtime._resolve_registered_identity(
            "stable-id", "00:11:22:33:44:02"
        ) == ("stable-id", "第一组")
    finally:
        runtime.close()


def test_matching_hardware_and_persisted_id_reconnect_before_old_socket_times_out():
    transport = InMemoryTransport()
    transport.connect("stable-id", "PC-01", "第一组", metadata={
        "mac": "02:10:20:30:40:01", "machine_id": "hardware-01",
    })
    runtime = TeacherRuntime(server=transport, database_path=":memory:")
    try:
        assert runtime._resolve_registered_identity(
            "stable-id", "02:10:20:30:40:02", "hardware-01"
        ) == ("stable-id", "第一组")
    finally:
        runtime.close()


def test_offline_cloned_device_with_different_hardware_keeps_its_own_identity():
    transport = InMemoryTransport()
    transport.connect("cloned", "PC-01", metadata={
        "mac": "02:10:20:30:40:01", "machine_id": "hardware-01",
    })
    runtime = TeacherRuntime(server=transport, database_path=":memory:")
    try:
        transport.disconnect("cloned")
        clone_id, _group = runtime._resolve_registered_identity(
            "cloned", "02:10:20:30:40:02", "hardware-02"
        )
        assert clone_id != "cloned"
        transport.connect(clone_id, "PC-01", metadata={
            "mac": "02:10:20:30:40:02", "machine_id": "hardware-02",
        })
        assert runtime._resolve_registered_identity(
            "cloned", "02:10:20:30:40:02", "hardware-02"
        )[0] == clone_id
        assert len(runtime.snapshot().devices) == 2
    finally:
        runtime.close()


def test_shared_firmware_uuid_alone_does_not_merge_distinct_installations():
    transport = InMemoryTransport()
    transport.connect("first-id", "PC-01", metadata={
        "mac": "02:10:20:30:40:01", "machine_id": "generic-firmware",
    })
    runtime = TeacherRuntime(server=transport, database_path=":memory:")
    try:
        assert runtime._resolve_registered_identity(
            "second-id", "02:10:20:30:40:02", "generic-firmware"
        ) == ("second-id", "default")
    finally:
        runtime.close()


@pytest.mark.parametrize("mac", ["00-11-22-33-44-AB", "0011223344ab", "0011.2233.44ab"])
def test_restarted_device_matches_equivalent_mac_without_creating_a_new_identity(mac):
    transport = InMemoryTransport()
    transport.connect("stable-id", "PC-01", "第一组", metadata={"mac": "00:11:22:33:44:ab"})
    runtime = TeacherRuntime(server=transport, database_path=":memory:")
    try:
        transport.disconnect("stable-id")
        assert runtime._resolve_registered_identity("regenerated-id", mac) == (
            "stable-id", "第一组"
        )
    finally:
        runtime.close()


def test_server_service_readiness_is_rfb_banner_not_only_process_start(tmp_path, monkeypatch):
    import configparser
    import win32service
    from student.core.v2.ultravnc import UltraVNCServer
    executable = str(tmp_path / "winvnc.exe")
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.recv.return_value = b"RFB 003.008\n"
    service_config = (0, 0, 0, f'"{executable}" -service')
    with patch(
        "student.core.v2.ultravnc.find_binary", return_value=executable
    ), patch.multiple(
        win32service, OpenSCManager=MagicMock(), OpenService=MagicMock(),
        QueryServiceConfig=MagicMock(return_value=service_config),
        QueryServiceStatus=MagicMock(return_value=(0, win32service.SERVICE_STOPPED)),
        StartService=MagicMock(), CloseServiceHandle=MagicMock(),
    ), patch("win32serviceutil.WaitForServiceStatus"), patch(
        "win32serviceutil.QueryServiceStatus", return_value=(0, win32service.SERVICE_RUNNING)
    ), patch("win32serviceutil.StopService"), patch.object(
        UltraVNCServer, "_firewall"
    ), patch("student.core.v2.ultravnc.socket.create_connection", return_value=connection):
        server = UltraVNCServer()
        ready = server.start()
        assert ready["backend"] == "ultravnc"
        assert ready["ready"]
        assert server.active
    config = configparser.ConfigParser()
    config.read(str(tmp_path / "UltraVNC" / "LanClassroomVNC.ini"))
    assert config.getint("admin", "PortNumber") == 15900
    assert config.getint("admin", "HTTPConnect") == 0
    assert config.getint("admin", "RemoveWallpaper") == 0


@pytest.mark.parametrize("pending", [False, True])
def test_server_stop_bounds_slow_shutdown_without_resending_pending_stop(pending):
    import win32service
    from student.core.v2.ultravnc import UltraVNCServer

    server = UltraVNCServer()
    server.active = True
    state = win32service.SERVICE_STOP_PENDING if pending else win32service.SERVICE_RUNNING

    process = MagicMock()
    executable = r"C:\Classroom\UltraVNC\winvnc.exe"
    with patch.multiple(
        win32service, OpenSCManager=MagicMock(), OpenService=MagicMock(),
        QueryServiceStatusEx=MagicMock(return_value={"CurrentState": state, "ProcessId": 42}),
        QueryServiceConfig=MagicMock(return_value=(0, 0, 0, f'"{executable}" -service')),
        CloseServiceHandle=MagicMock(),
    ), patch("student.core.v2.ultravnc.find_binary", return_value=executable), patch(
        "win32service.ControlService"
    ) as send_stop, patch.object(
        server, "_service_processes", return_value=[process]
    ), patch.object(server, "_wait_stopped", side_effect=[False, True]) as wait, patch(
        "student.core.v2.ultravnc.psutil.wait_procs", return_value=([process], [])
    ), patch.object(server, "_firewall"):
        assert server.stop()
        assert not server.active
        assert send_stop.call_count == (0 if pending else 1)
        process.terminate.assert_called_once()
        assert all(call.args[1] <= 0.6 for call in wait.call_args_list)


def test_server_stop_timeout_keeps_active_state_for_recovery():
    import win32service
    from student.core.v2.ultravnc import UltraVNCServer

    server = UltraVNCServer()
    server.active = True
    executable = r"C:\Classroom\UltraVNC\winvnc.exe"
    process = MagicMock()
    with patch.multiple(
        win32service, OpenSCManager=MagicMock(), OpenService=MagicMock(),
        QueryServiceStatusEx=MagicMock(return_value={
            "CurrentState": win32service.SERVICE_RUNNING, "ProcessId": 42,
        }),
        QueryServiceConfig=MagicMock(return_value=(0, 0, 0, f'"{executable}" -service')),
        ControlService=MagicMock(), CloseServiceHandle=MagicMock(),
    ), patch("student.core.v2.ultravnc.find_binary", return_value=executable), patch.object(
        server, "_service_processes", return_value=[process]
    ), patch.object(server, "_wait_stopped", return_value=False), patch(
        "student.core.v2.ultravnc.psutil.wait_procs", return_value=([], [process])
    ), patch.object(server, "_firewall") as firewall:
        with pytest.raises(RuntimeError, match="尚未完全退出"):
            server.stop()
        assert server.active
        firewall.assert_called_once_with(False)


def test_service_process_collection_excludes_other_vnc_and_rejects_reused_pid():
    from student.core.v2.ultravnc import UltraVNCServer
    executable = r"C:\Classroom\UltraVNC\winvnc.exe"
    root, owned, other = MagicMock(), MagicMock(), MagicMock()
    root.exe.return_value = owned.exe.return_value = executable
    root.cmdline.return_value = [executable, "-service"]
    other.exe.return_value = r"C:\OtherVNC\winvnc.exe"
    root.children.return_value = [owned, other]
    with patch("student.core.v2.ultravnc.psutil.Process", return_value=root):
        assert UltraVNCServer._service_processes(42, executable) == [root, owned]
        root.exe.return_value = other.exe.return_value
        with pytest.raises(RuntimeError, match="路径不匹配"):
            UltraVNCServer._service_processes(42, executable)
    root.terminate.assert_not_called()
    other.terminate.assert_not_called()
