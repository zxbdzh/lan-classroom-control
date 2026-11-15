import hashlib
import os
import struct
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from common.file_transfer import CHUNK_SIZE, FILE_HEADER_FORMAT, FileTransferReceiver
from student.core.student_client import StudentClient
from student.core.v2.lease import TemporaryControlKind
from student.core.v2.storage import StudentStateStore
from teacher.core.file_distributor import FileDistributor


def test_failed_network_restore_is_retained_by_lease(tmp_path):
    client = StudentClient(
        save_dir=str(tmp_path / "files"), service_mode=True,
        state_store=StudentStateStore(":memory:"),
    )
    client.net_controller.block_internet = MagicMock(return_value=True)
    client.net_controller.is_blocked = MagicMock(return_value=True)
    client.net_controller.unblock_internet = MagicMock(return_value=False)
    client._v2_network_block({})
    client.temporary_lease._deadline = 0
    results = client.temporary_lease.check_expired()
    assert results and not results[0].success
    assert TemporaryControlKind.INTERNET_BLOCK in client.temporary_lease.active_controls()
    client.net_controller.unblock_internet.return_value = True
    client.net_controller.is_blocked.return_value = False
    client.temporary_lease._deadline = 0
    assert client.temporary_lease.check_expired()[0].success
    assert TemporaryControlKind.INTERNET_BLOCK not in client.temporary_lease.active_controls()
    client.stop()


def test_out_of_order_chunk_cannot_create_false_resume_hole(tmp_path):
    receiver = FileTransferReceiver(str(tmp_path))
    receiver.handle_start({
        "transfer_id": "hole", "file_name": "lesson.bin",
        "file_size": CHUNK_SIZE * 2, "total_chunks": 2,
    })
    assert not receiver.handle_data({
        "transfer_id": "hole", "chunk_index": 1,
        "data": struct.pack(FILE_HEADER_FORMAT, 1, 1, CHUNK_SIZE) + b"x" * CHUNK_SIZE,
    })
    path = receiver._active_transfers["hole"]["save_path"]
    assert os.path.getsize(path) == 0
    receiver.cancel_transfer("hole")


def test_checksum_failure_restarts_from_zero(tmp_path):
    receiver = FileTransferReceiver(str(tmp_path))
    params = {
        "transfer_id": "bad", "file_name": "lesson.txt",
        "file_size": 3, "total_chunks": 1,
        "sha256": hashlib.sha256(b"good").hexdigest(),
    }
    receiver.handle_start(params)
    receiver.handle_data({
        "transfer_id": "bad", "chunk_index": 0,
        "data": struct.pack(FILE_HEADER_FORMAT, 1, 0, 3) + b"bad",
    })
    assert not receiver.handle_end({"transfer_id": "bad"})[0]
    params["transfer_id"] = "retry"
    assert receiver.handle_start(params)["resume_chunk"] == 0
    receiver.cancel_transfer("retry")


def test_multi_device_file_delivery_reports_offline_and_real_disk_hash(tmp_path):
    import threading
    source = tmp_path / "中文讲义.bin"
    source.write_bytes(os.urandom(CHUNK_SIZE * 3 + 17))
    receiver = FileTransferReceiver(str(tmp_path / "received"))
    distributor = FileDistributor(ack_timeout=0.1)
    complete = threading.Event()
    distributor.on_complete = lambda *_args: complete.set()

    class Connection:
        def is_alive(self):
            return True

        def send_message(self, message):
            params = message["params"]
            kind = message["type"]
            if kind == "file_send_start":
                resume = receiver.handle_start(params)
                distributor.record_ack({
                    **resume, "student_id": "online", "stage": "start", "success": True,
                })
            elif kind == "file_send_data":
                assert receiver.handle_data(params)
            elif kind == "file_send_end":
                success, result = receiver.handle_end(params)
                distributor.record_ack({
                    "transfer_id": params["transfer_id"], "student_id": "online",
                    "stage": "end", "success": success, "result": result,
                })
            return True

    transfer_id = distributor.send_file_to_students(str(source), [
        SimpleNamespace(student_id="online", conn=Connection()),
        SimpleNamespace(student_id="offline", conn=None),
    ])
    assert complete.wait(3.0)
    result = distributor.get_completed_transfer(transfer_id)
    assert not result["success"]
    assert result["devices"]["offline"]["state"] == "failed"
    assert result["devices"]["online"]["state"] == "complete"
    with open(result["devices"]["online"]["result"], "rb") as handle:
        assert hashlib.sha256(handle.read()).digest() == hashlib.sha256(source.read_bytes()).digest()


def test_stalled_student_does_not_delay_other_teacher_heartbeats():
    import threading
    from teacher.core.teacher_server import TeacherServer
    blocked, release, delivered = threading.Event(), threading.Event(), threading.Event()
    slow = MagicMock()
    slow.send_message.side_effect = lambda _msg: (blocked.set(), release.wait(2))
    fast = MagicMock()
    fast.send_message.side_effect = lambda _msg: delivered.set()
    server = TeacherServer()
    server.tcp_server.get_all_connections = lambda: [slow, fast]
    try:
        server._send_teacher_heartbeat({"type": "teacher_heartbeat"})
        assert blocked.wait(1)
        assert delivered.wait(0.5)
        server._send_teacher_heartbeat({"type": "teacher_heartbeat"})
        assert slow.send_message.call_count == 1
    finally:
        release.set()


def test_watchdog_recovers_dead_service_without_running_native_controls():
    from contextlib import nullcontext
    import psutil
    from student.core.v2 import recovery_watchdog as watchdog
    state = {"owner": "old", "pid": 123, "created": 1.0, "active": True, "deadline": 30}
    with patch.object(watchdog, "recovery_lock", side_effect=nullcontext), patch.object(
        watchdog, "read_state", return_value=state
    ), patch.object(watchdog, "write_state") as saved, patch.object(
        watchdog, "NetController"
    ) as network, patch.object(watchdog, "UsbStorageController") as usb, patch.object(
        watchdog, "UltraVNCServer"
    ) as remote, patch.object(
        watchdog, "release_agent"
    ) as release, patch("psutil.Process", side_effect=psutil.NoSuchProcess(123)):
        network.return_value.restore_stale_rules.return_value = True
        usb.return_value.restore_stale_policy.return_value = True
        remote.return_value.stop.return_value = True
        watchdog.watch("old")
    network.return_value.restore_stale_rules.assert_called_once()
    usb.return_value.restore_stale_policy.assert_called_once()
    remote.return_value.stop.assert_called_once()
    release.assert_called_once()
    assert saved.call_args.args[0]["active"] is False


def test_watchdog_retries_usb_restore_before_marking_recovered():
    from contextlib import nullcontext
    import psutil
    from student.core.v2 import recovery_watchdog as watchdog
    state = {"owner": "old", "pid": 123, "created": 1.0, "active": True, "deadline": 30}
    with patch.object(watchdog, "recovery_lock", side_effect=nullcontext), patch.object(
        watchdog, "read_state", return_value=state
    ), patch.object(watchdog, "write_state") as saved, patch.object(
        watchdog, "NetController"
    ) as network, patch.object(watchdog, "UsbStorageController") as usb, patch.object(
        watchdog, "UltraVNCServer"
    ) as remote, patch.object(watchdog, "release_agent"), patch(
        "psutil.Process", side_effect=psutil.NoSuchProcess(123)
    ), patch.object(watchdog.time, "sleep") as wait:
        network.return_value.restore_stale_rules.return_value = True
        remote.return_value.stop.return_value = True
        usb.return_value.restore_stale_policy.side_effect = [False, True]
        watchdog.watch("old")
    assert usb.return_value.restore_stale_policy.call_count == 2
    wait.assert_called_once_with(0.5)
    saved.assert_called_once()
    assert saved.call_args.args[0]["active"] is False


def test_old_watchdog_does_not_release_restarted_service_controls():
    from contextlib import nullcontext
    from student.core.v2 import recovery_watchdog as watchdog
    with patch.object(watchdog, "recovery_lock", side_effect=nullcontext), patch.object(
        watchdog, "read_state", return_value={"owner": "new"}
    ), patch.object(watchdog, "NetController") as network:
        watchdog.watch("old")
    network.assert_not_called()


def test_real_owned_child_process_can_be_terminated_without_touching_other_processes():
    import subprocess
    import sys
    import psutil
    from student.core.v2.processes import ProcessController, ProcessTerminationStatus
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        created = psutil.Process(child.pid).create_time()
        controller = ProcessController()
        stale = controller.terminate_process(child.pid, created - 1.0)
        assert stale.status == ProcessTerminationStatus.STALE_PROCESS
        assert child.poll() is None
        result = controller.terminate_process(child.pid, created, timeout=1.0, force=True)
        assert result.success
        child.wait(timeout=2.0)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=2.0)
