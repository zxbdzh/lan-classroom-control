import socket
import time
from unittest.mock import MagicMock, patch

import pytest
from PyQt5.QtCore import QEvent, QItemSelectionModel, QPoint, Qt
from PyQt5.QtGui import QContextMenuEvent
from PyQt5.QtWidgets import QApplication, QMessageBox, QPushButton

from common.v2.model import All, ClassroomAction, CommandKind, DeviceIds, Group
from common.protocol import MessageType, build_message, serialize_message
from teacher.core.classroom import Classroom, InMemoryTransport, ManualClock
from teacher.core.runtime import TeacherRuntime
from teacher.core.state_store import SQLiteStateStore
from teacher.core.teacher_server import TeacherServer
from teacher.ui.main_window import TeacherMainWindow
from teacher.ui.widgets import SeatTile


def test_removed_device_stays_out_of_scopes_and_reappears_only_on_new_registration(tmp_path):
    clock = ManualClock(1000)
    transport = InMemoryTransport(clock)
    transport.connect("old", "同名设备", "第一组", metadata={"ip": "192.168.1.10"})
    transport.connect("keep", "同名设备", "第一组")
    store = SQLiteStateStore(str(tmp_path / "devices.db"))
    classroom = Classroom(transport, store, clock)
    classroom.set_device_note("old", "旧记录")
    receipt = classroom.submit(
        ClassroomAction(CommandKind.SILENCE_START), DeviceIds(["old"]),
    )
    assert classroom.remove_devices(["old", "missing", "old"]) == ("old",)
    assert classroom.remove_devices(["old"]) == ()
    assert [device.device_id for device in classroom.snapshot().devices] == ["keep"]
    assert classroom.snapshot().operation(receipt.command_id) is not None
    assert classroom.submit(
        receipt.action, All(),
    ).target_ids == ("keep",)
    assert classroom.submit(receipt.action, Group("第一组")).target_ids == ("keep",)
    assert classroom.submit(receipt.action, DeviceIds(["old"])).target_ids == ()
    transport.disconnect("old")
    assert classroom.snapshot().device("old") is None
    classroom.close()
    store.close()
    store = SQLiteStateStore(str(tmp_path / "devices.db"))
    classroom = Classroom(transport, store, clock)
    try:
        assert classroom.snapshot().device("old") is None
        transport.connect("old", "同名设备", "第一组", metadata={"ip": "192.168.1.11"})
        assert classroom.snapshot().device("old").note == "旧记录"
        assert classroom.snapshot().device("old").group == "第一组"
        assert len(classroom.snapshot().devices) == 2
    finally:
        classroom.close()
        store.close()


def test_runtime_removal_restores_only_target_and_keeps_other_broadcast_receivers():
    transport = InMemoryTransport()
    transport.connect("remove", "旧设备")
    transport.connect("keep", "保留设备")
    runtime = TeacherRuntime(server=transport, database_path=":memory:")
    try:
        runtime._broadcast_target_ids = {"remove", "keep"}
        runtime.remote_windows.stop = MagicMock()
        runtime.broadcast_media.stop = MagicMock()
        runtime._unregister_inspection = MagicMock()
        assert runtime.remove_devices(["remove"]) == ("remove",)
        runtime.remote_windows.stop.assert_called_once_with("remove")
        runtime._unregister_inspection.assert_called_once_with("remove")
        runtime.broadcast_media.stop.assert_not_called()
        assert runtime._broadcast_target_ids == {"keep"}
        assert transport.sent_to("remove")[-1].envelope.action == "classroom.restore_all"
        assert not transport.sent_to("keep")
        assert runtime.snapshot().device("remove") is None
    finally:
        runtime.close()


def test_removed_registration_preserves_group_and_clone_protection_after_restart(tmp_path):
    database = str(tmp_path / "removed-identity.db")
    transport = InMemoryTransport()
    transport.connect("old", "PC-01", "第一组", metadata={
        "mac": "02:12:34:56:78:90", "machine_id": "hardware-01",
    })
    runtime = TeacherRuntime(server=transport, database_path=database)
    runtime.classroom.remove_devices(["old"])
    runtime.close()
    restarted = TeacherRuntime(server=InMemoryTransport(), database_path=database)
    try:
        assert restarted._resolve_registered_identity("old", "02:12:34:56:78:91", "hardware-01") == (
            "old", "第一组",
        )
        assert restarted.snapshot().device("old") is None
        assert restarted._resolve_registered_identity("old", "02:12:34:56:78:92", "hardware-02")[0] != "old"
    finally:
        restarted.close()


def test_removal_stops_pending_retries_but_keeps_permanent_rule_for_reconnect():
    clock = ManualClock(1000)
    transport = InMemoryTransport(clock)
    transport.connect("old", "PC-01")
    classroom = Classroom(transport, clock=clock)
    classroom.set_blacklist(DeviceIds(["old"]), "game.exe")
    classroom.remove_devices(["old"])
    transport.clear_sent()
    clock.advance(2)
    assert not transport.sent
    transport.connect("old", "PC-01")
    assert transport.sent_to("old")[-1].envelope.action == "blacklist.set"
    assert classroom.snapshot().device("old") is not None
    classroom.close()


def test_real_registration_reveals_removed_record_but_group_updates_do_not():
    server = TeacherServer(tcp_port=0, broadcast_port=0)
    runtime = TeacherRuntime(server=server, database_path=":memory:")
    connection = MagicMock()
    connection.addr = ("192.168.1.10", 50000)
    connection.student_id = ""
    params = {"student_id": "pc-01", "hostname": "PC-01", "protocol_version": 2}
    try:
        server._handle_student_register(connection, params)
        runtime.classroom.remove_devices(["pc-01"])
        server.set_student_group(["pc-01"], "第一组")
        assert runtime.snapshot().device("pc-01") is None
        server._on_disconnect(connection)
        assert runtime.snapshot().device("pc-01") is None
        replacement = MagicMock()
        replacement.addr = ("192.168.1.10", 50001)
        server._handle_student_register(replacement, params)
        assert runtime.snapshot().device("pc-01") is not None
    finally:
        runtime.close()


def test_loopback_tcp_removal_remains_hidden_until_client_reconnects():
    server = TeacherServer(tcp_port=0, broadcast_port=0)
    runtime = TeacherRuntime(server=server, database_path=":memory:")
    server.tcp_server.start()
    port = server.tcp_server._sock.getsockname()[1]

    def wait_for(predicate):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return False

    registration = serialize_message(build_message(MessageType.STUDENT_REGISTER, {
        "student_id": "pc-01", "hostname": "PC-01", "protocol_version": 2,
        "machine_id": "hardware-01", "mac": "02:12:34:56:78:90",
    }))
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            client.sendall(registration)
            assert wait_for(lambda: runtime.snapshot().device("pc-01") is not None)
            runtime.set_device_note("pc-01", "第一排")
            server.set_student_group(["pc-01"], "一组")
            assert runtime.remove_devices(["pc-01"]) == ("pc-01",)
            client.sendall(serialize_message(build_message(MessageType.STUDENT_HEARTBEAT, {})))
            assert runtime.snapshot().device("pc-01") is None
        assert wait_for(lambda: not server.student_manager.get_student("pc-01").online)
        assert runtime.snapshot().devices == ()
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            client.sendall(registration)
            assert wait_for(lambda: runtime.snapshot().device("pc-01") is not None)
            device = runtime.snapshot().device("pc-01")
            assert device.note == "第一排"
            assert device.group == "一组"
            assert len(runtime.snapshot().devices) == 1
    finally:
        server.tcp_server.stop()
        runtime.close()


@pytest.mark.parametrize("confirm", [QMessageBox.Yes, QMessageBox.No])
def test_device_card_remove_action_confirms_exact_record_and_updates_all_views(qtbot, confirm):
    transport = InMemoryTransport()
    transport.scan_devices = MagicMock()
    transport.file_distributor = MagicMock()
    transport.connect("pc-01", "同名设备", metadata={"ip": "192.168.1.10"})
    transport.connect("pc-02", "同名设备", metadata={"ip": "192.168.1.11"})
    runtime = TeacherRuntime(server=transport, database_path=":memory:")
    window = TeacherMainWindow(runtime=runtime, auto_start=False)
    qtbot.addWidget(window)
    window.poll_timer.stop()
    window._poll_runtime()
    window._selected_ids = {"pc-01", "pc-02"}
    window.config.set("ui.seat_positions", {"pc-01": 0, "pc-02": 1})
    card = window._patrol_cards["pc-01"]
    with patch("teacher.ui.main_window.QMessageBox.question", return_value=confirm) as dialog:
        card.name.remove_action.trigger()
    assert "192.168.1.10" in dialog.call_args.args[2]
    assert "pc-01" in dialog.call_args.args[2]
    assert "重新连接" in dialog.call_args.args[2]
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    if confirm == QMessageBox.Yes:
        assert runtime.snapshot().device("pc-01") is None
        assert set(window._patrol_cards) == {"pc-02"}
        assert [tile.device.device_id for tile in window.seat_map.findChildren(SeatTile)] == ["pc-02"]
        assert window.maintenance_table.rowCount() == 1
        assert window._selected_ids == {"pc-02"}
        assert window.config.get("ui.seat_positions") == {"pc-02": 1}
    else:
        assert len(runtime.snapshot().devices) == 2
        assert not transport.sent
    window.close()
    runtime.close()


def test_remove_offline_button_does_not_remove_online_devices(qtbot):
    transport = InMemoryTransport()
    transport.scan_devices = MagicMock()
    transport.file_distributor = MagicMock()
    transport.connect("offline", "离线设备")
    transport.connect("online", "在线设备")
    transport.disconnect("offline")
    runtime = TeacherRuntime(server=transport, database_path=":memory:")
    window = TeacherMainWindow(runtime=runtime, auto_start=False)
    qtbot.addWidget(window)
    window.poll_timer.stop()
    window._poll_runtime()
    button = next(button for button in window.findChildren(QPushButton) if button.text() == "清理离线设备")
    with patch("teacher.ui.main_window.QMessageBox.question", return_value=QMessageBox.Yes):
        qtbot.mouseClick(button, Qt.LeftButton)
    assert [device.device_id for device in runtime.snapshot().devices] == ["online"]
    window.close()
    runtime.close()


def test_offline_cleanup_rechecks_online_status_after_confirmation(qtbot):
    transport = InMemoryTransport()
    transport.scan_devices = MagicMock()
    transport.file_distributor = MagicMock()
    transport.connect("offline", "离线设备")
    transport.disconnect("offline")
    runtime = TeacherRuntime(server=transport, database_path=":memory:")
    window = TeacherMainWindow(runtime=runtime, auto_start=False)
    qtbot.addWidget(window)
    window.poll_timer.stop()
    window._poll_runtime()

    def confirm(*_args):
        transport.connect("offline", "已重新上线")
        return QMessageBox.Yes

    with patch("teacher.ui.main_window.QMessageBox.question", side_effect=confirm):
        window._remove_offline_devices()
    assert runtime.snapshot().device("offline").online is True
    window.close()
    runtime.close()


@pytest.mark.parametrize("surface", ["classroom", "inspection", "maintenance"])
def test_remove_menu_is_available_on_each_device_surface(qtbot, surface):
    transport = InMemoryTransport()
    transport.scan_devices = MagicMock()
    transport.file_distributor = MagicMock()
    transport.connect("pc-01", "PC-01")
    runtime = TeacherRuntime(server=transport, database_path=":memory:")
    window = TeacherMainWindow(runtime=runtime, auto_start=False)
    qtbot.addWidget(window)
    window.poll_timer.stop()
    window._poll_runtime()
    window._show_page(surface)
    if surface == "classroom":
        tile = window.seat_map.findChild(SeatTile)
        target = tile
        name = tile.name
    elif surface == "inspection":
        card = window._patrol_cards["pc-01"]
        target = card.image
        name = card.name
    else:
        name = window.maintenance_table.cellWidget(0, 0)
        target = name.copy_button
    event = QContextMenuEvent(QContextMenuEvent.Mouse, QPoint(5, 5), target.mapToGlobal(QPoint(5, 5)))
    with patch.object(name.device_menu, "popup") as popup:
        QApplication.sendEvent(target, event)
    popup.assert_called_once()
    assert name.remove_action.text() == "移除设备"
    window.close()
    runtime.close()


def test_maintenance_batch_removal_uses_table_selection_and_preserves_other_devices(qtbot):
    transport = InMemoryTransport()
    transport.scan_devices = MagicMock()
    transport.file_distributor = MagicMock()
    for device_id in ("pc-01", "pc-02", "pc-03"):
        transport.connect(device_id, device_id)
    runtime = TeacherRuntime(server=transport, database_path=":memory:")
    window = TeacherMainWindow(runtime=runtime, auto_start=False)
    qtbot.addWidget(window)
    window.poll_timer.stop()
    window._poll_runtime()
    window._selected_ids = {"pc-02"}
    selection = window.maintenance_table.selectionModel()
    for row in (0, 2):
        selection.select(
            window.maintenance_table.model().index(row, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows,
        )
    with patch("teacher.ui.main_window.QMessageBox.question", return_value=QMessageBox.Yes):
        window._remove_selected_devices()
    assert [device.device_id for device in runtime.snapshot().devices] == ["pc-02"]
    assert window._selected_ids == {"pc-02"}
    window.close()
    runtime.close()
