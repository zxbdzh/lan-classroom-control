import os
import threading
from unittest.mock import MagicMock, call, patch
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import sip
from PyQt5.QtCore import QCoreApplication, QEvent, QMimeData, QPoint, QPointF, Qt, QUrl
from PyQt5.QtWidgets import QApplication, QMessageBox, QPushButton, QTableWidgetItem
from PyQt5.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent, QPixmap

from teacher.core.classroom import AckPhase, InMemoryTransport, TransportAck, TransportDevice
from teacher.core.runtime import TeacherRuntime
from common.v2.model import CommandKind, DeviceIds, InspectionMode
from teacher.ui.main_window import TeacherMainWindow
from teacher.ui.widgets import DeviceNameWidget, EmptySeat, FullScreenViewer, SeatTile
from student.ui.overlay import OverlayWindow


_real_target_confirmation = TeacherMainWindow._confirm_targets

@pytest.fixture(autouse=True)
def accept_scoped_operations(monkeypatch):
    monkeypatch.setattr(TeacherMainWindow, "_confirm_targets", lambda self, action, ids: True)


@pytest.fixture
def file_drop_window(qtbot):
    transport = InMemoryTransport()
    transport.scan_devices = MagicMock()
    transport.file_distributor = MagicMock()
    transport.send_file = MagicMock(return_value="transfer-1")
    transport.connect("pc-01", "PC-01")
    transport.connect("pc-02", "PC-02")
    runtime = TeacherRuntime(server=transport, database_path=":memory:")
    window = TeacherMainWindow(runtime=runtime, auto_start=False)
    qtbot.addWidget(window)
    window.poll_timer.stop()
    window.show()
    window._show_page("files")
    window._poll_runtime()
    QApplication.processEvents()
    yield window
    window.close()


def _drop_on_file_table(window, mime, point, actions=Qt.CopyAction | Qt.MoveAction):
    events = (
        QDragEnterEvent(point, actions, mime, Qt.LeftButton, Qt.NoModifier),
        QDragMoveEvent(point, actions, mime, Qt.LeftButton, Qt.NoModifier),
        QDropEvent(QPointF(point), actions, mime, Qt.LeftButton, Qt.NoModifier),
    )
    for event in events:
        if actions & Qt.MoveAction:
            event.setDropAction(Qt.MoveAction)
        QApplication.sendEvent(window.file_table.viewport(), event)
    return events


@pytest.mark.parametrize("over_existing_row", [False, True])
def test_file_drop_queues_local_paths_then_uses_explicit_send(
    file_drop_window, tmp_path, qtbot, over_existing_row,
):
    window = file_drop_window
    lesson = tmp_path / "中文 课件 #1.txt"
    lesson.write_bytes(b"lesson")
    worksheet = tmp_path / "练习.pdf"
    worksheet.write_bytes(b"worksheet")
    folder = tmp_path / "作业文件夹"
    folder.mkdir()
    (folder / "答案.txt").write_bytes(b"answers")
    paths = [str(lesson), str(worksheet), str(folder)]
    if over_existing_row:
        window._queue_path(paths[0])
        point = window.file_table.visualItemRect(window.file_table.item(0, 0)).center()
    else:
        point = QPoint(20, 20)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(path) for path in paths] + [
        QUrl("https://example.invalid/lesson.txt"),
        QUrl.fromLocalFile(str(tmp_path / "missing.txt")),
    ])

    with qtbot.captureExceptions() as exceptions:
        events = _drop_on_file_table(window, mime, point)

    assert not exceptions
    assert all(event.isAccepted() and event.dropAction() == Qt.CopyAction for event in events)
    assert [item["path"] for item in window.file_queue] == paths
    assert [item["size"] for item in window.file_queue] == [6, 9, 7]
    assert [item["type"] for item in window.file_queue] == ["文件", "文件", "文件夹"]
    assert window.file_table.rowCount() == 3
    assert window.file_table.item(0, 0).text() == lesson.name
    assert all(item["status"] == "等待" for item in window.file_queue)
    window.runtime.server.send_file.assert_not_called()

    _drop_on_file_table(window, mime, point)
    assert window.file_table.rowCount() == 3
    assert len(window.file_queue) == 3
    window._selected_ids = {"pc-01"}
    window.scope_buttons["selected"].setChecked(True)
    window.runtime.server.send_file.side_effect = ["transfer-1", "transfer-2", "transfer-3"]
    send_button = next(button for button in window.findChildren(QPushButton)
                       if button.text() == "开始分发")
    qtbot.mouseClick(send_button, Qt.LeftButton)
    assert window.runtime.server.send_file.call_args_list == [
        call(path, ["pc-01"]) for path in paths
    ]
    assert all(item["status"] == "发送中" for item in window.file_queue)


@pytest.mark.parametrize("payload", ["web", "text", "internal", "missing", "empty", "move-only"])
def test_file_drop_rejects_non_files_without_changing_queue(file_drop_window, tmp_path, payload):
    window = file_drop_window
    mime = QMimeData()
    actions = Qt.CopyAction | Qt.MoveAction
    if payload == "web":
        mime.setUrls([QUrl("https://example.invalid/lesson.txt")])
    elif payload == "text":
        mime.setText(str(tmp_path))
    elif payload == "internal":
        mime.setData("application/x-qabstractitemmodeldatalist", b"table rows")
    elif payload == "missing":
        mime.setUrls([QUrl.fromLocalFile(str(tmp_path / "missing.txt"))])
    elif payload == "move-only":
        mime.setUrls([QUrl.fromLocalFile(str(tmp_path))])
        actions = Qt.MoveAction

    events = _drop_on_file_table(window, mime, QPoint(20, 20), actions)

    assert not any(event.isAccepted() for event in events)
    assert not window.file_queue
    assert window.file_table.rowCount() == 0
    window.runtime.server.send_file.assert_not_called()


def test_file_queue_normalizes_paths_and_ignores_disappearing_files(
    file_drop_window, tmp_path, monkeypatch,
):
    window = file_drop_window
    lesson = tmp_path / "Lesson.txt"
    lesson.write_bytes(b"lesson")
    window._queue_path(str(lesson))
    window._queue_path(str(tmp_path / "." / lesson.name))
    window._queue_path(str(tmp_path / ".." / tmp_path.name / lesson.name))
    monkeypatch.chdir(tmp_path)
    window._queue_path(lesson.name)
    window._queue_path("")
    window._queue_path(str(tmp_path / "missing.txt"))
    if os.name == "nt":
        window._queue_path(str(lesson).upper())
    unreadable = tmp_path / "unreadable.txt"
    unreadable.write_bytes(b"unreadable")
    with patch("teacher.ui.main_window.os.path.getsize", side_effect=OSError("unavailable")):
        window._queue_path(str(unreadable))

    assert len(window.file_queue) == 1
    assert window.file_table.rowCount() == 1
    assert window.file_queue[0]["path"] == str(lesson)


def test_teacher_console_has_seven_pages_and_stable_1366_layout(qtbot):
    runtime = TeacherRuntime(database_path=":memory:")
    for index in range(1, 25):
        runtime.classroom._on_device(TransportDevice(
            device_id=f"pc-{index:02d}",
            hostname=f"PC-{index:02d}",
            group="一组" if index <= 12 else "二组",
            metadata={"ip": f"192.168.1.{index}", "mac": f"00:11:22:33:44:{index:02x}"},
        ))
    window = TeacherMainWindow(runtime=runtime, auto_start=False)
    qtbot.addWidget(window)
    window.resize(1366, 768)
    window.show()
    window._poll_runtime()
    QApplication.processEvents()

    assert window.pages.count() == 7
    assert window.minimumWidth() <= 1366
    assert window.operation_drawer.width() == 300
    assert len(window.seat_map.findChildren(SeatTile)) == 24
    assert len(window.seat_map.findChildren(EmptySeat)) >= 1
    assert window.pages.geometry().width() > 600
    assert window.operation_drawer.mapTo(window.centralWidget(), window.operation_drawer.rect().topLeft()).x() >= 1000

    window.apply_theme("dark")
    assert "#11151B" in window.styleSheet()
    window.apply_theme("light")
    assert "#F4F6F9" in window.styleSheet()

    runtime.stop_remote_control = MagicMock()
    runtime.open_inspection = MagicMock()
    window._remote_toggled("pc-01", False)
    runtime.stop_remote_control.assert_called_once_with("pc-01")
    runtime.open_inspection.assert_called_once_with(
        DeviceIds(["pc-01"]), InspectionMode.FOCUS
    )
    window.close()


def test_teacher_console_compacts_for_1366_screen_at_125_percent(qtbot):
    runtime = TeacherRuntime(database_path=":memory:")
    window = TeacherMainWindow(runtime=runtime, auto_start=False)
    qtbot.addWidget(window)

    # 1366x768 physical pixels expose roughly 1093x614 logical pixels at 125%.
    window.resize(1093, 614)
    window.show()
    QApplication.processEvents()

    assert window.size().width() == 1093
    assert window.size().height() == 614
    assert not window.operation_drawer.isVisible()
    assert not window.drawer_button.isChecked()
    assert window.pages.geometry().width() > 700
    window.close()


@pytest.mark.parametrize("scope_name", ["all", "group", "selected"])
def test_usb_buttons_use_current_scope_and_show_confirmed_policy(qtbot, scope_name):
    transport = InMemoryTransport()
    transport.scan_devices = MagicMock()
    transport.file_distributor = MagicMock()
    transport.connect("pc-01", "PC-01", "第一组")
    transport.connect("pc-02", "PC-02", "第二组")
    runtime = TeacherRuntime(server=transport, database_path=":memory:")
    window = TeacherMainWindow(runtime=runtime, auto_start=False)
    qtbot.addWidget(window)
    window.resize(1093, 614)
    window.show()
    window._show_page("maintenance")
    window._poll_runtime()
    window._selected_ids = {"pc-01"}
    window.scope_buttons[scope_name].setChecked(True)
    if scope_name == "group":
        window.group_combo.setCurrentText("第一组")
    window._scope_changed()
    QApplication.processEvents()
    assert window.maintenance_table.item(0, 6).text() == "未下发"

    for label, expected_kind in (
        ("限制 USB 存储", CommandKind.USB_STORAGE_BLOCK),
        ("恢复 USB 存储", CommandKind.USB_STORAGE_RESTORE),
    ):
        button = next(item for item in window.findChildren(QPushButton) if item.text() == label)
        assert button.isVisible()
        page = window.pages.currentWidget()
        right = button.mapTo(page, button.rect().bottomRight()).x()
        assert right <= page.width()
        with qtbot.captureExceptions() as exceptions:
            qtbot.mouseClick(button, Qt.LeftButton)
        assert not exceptions
        receipt = runtime.snapshot().operations[-1]
        assert receipt.action.kind is expected_kind
        assert receipt.target_ids == (("pc-01", "pc-02") if scope_name == "all" else ("pc-01",))
        window._poll_runtime()
        assert window.maintenance_table.item(0, 6).text() == "待确认"
        for device_id in receipt.target_ids:
            transport.ack(receipt.command_id, device_id, AckPhase.APPLIED, receipt.revision)
        window._poll_runtime()
        assert window.maintenance_table.item(0, 6).text() == (
            "限制策略已应用" if expected_kind is CommandKind.USB_STORAGE_BLOCK else "原策略已恢复"
        )
        assert label in window.operation_drawer.list.item(0).text()
    transport.disconnect("pc-01")
    window._poll_runtime()
    assert window.maintenance_table.item(0, 6).text() == "离线 / 未确认"
    window.close()


def test_fullscreen_remote_coordinates_ignore_letterbox_bars(qtbot):
    viewer = FullScreenViewer("pc-01", "PC-01")
    qtbot.addWidget(viewer)
    viewer.image.resize(1000, 500)
    viewer.image.setPixmap(QPixmap(400, 300))

    assert viewer._normalized_image_position(299, 250) is None
    assert viewer._normalized_image_position(300, 100) == (0.0, 0.0)
    assert viewer._normalized_image_position(700, 400) == (1.0, 1.0)


def test_student_overlay_coalesces_worker_frames_to_latest(qtbot):
    overlay = OverlayWindow()
    qtbot.addWidget(overlay)
    worker = threading.Thread(
        target=lambda: (
            overlay.update_frame(b"first", (1, 1)),
            overlay.update_frame(b"latest", (2, 2)),
        )
    )
    worker.start()
    worker.join()

    assert overlay._pending_frame == (b"latest", (2, 2))
    assert overlay._frame_dispatch_pending is True
    overlay._on_frame_received()
    assert overlay._pending_frame is None


def test_student_overlay_new_show_cancels_delayed_hide(qtbot):
    overlay = OverlayWindow()
    qtbot.addWidget(overlay)
    overlay.hide = MagicMock()
    overlay._show_fullscreen_topmost = MagicMock()

    overlay.hide_overlay()
    overlay.show_broadcast()
    qtbot.wait(250)

    overlay.hide.assert_not_called()
    assert overlay._active is True


def test_student_overlay_hides_when_no_new_mode_is_shown(qtbot):
    overlay = OverlayWindow()
    qtbot.addWidget(overlay)
    overlay.hide = MagicMock()

    overlay.hide_overlay()
    qtbot.wait(250)

    overlay.hide.assert_called_once_with()


def test_selected_devices_can_be_assigned_to_a_group(qtbot):
    runtime = TeacherRuntime(database_path=":memory:")
    runtime.set_group = MagicMock(return_value=2)
    window = TeacherMainWindow(runtime=runtime, auto_start=False)
    qtbot.addWidget(window)
    window._selected_ids = {"pc-02", "pc-01"}

    with patch(
        "teacher.ui.main_window.QInputDialog.getText",
        return_value=("第一组", True),
    ):
        window._set_selected_group()

    runtime.set_group.assert_called_once_with(["pc-01", "pc-02"], "第一组")
    assert "2" in window.operation_status.text()
    window.close()


def test_pressing_seat_preserves_drag_source_until_mouse_release(qtbot):
    runtime = TeacherRuntime(database_path=":memory:")
    runtime.classroom._on_device(TransportDevice(device_id="pc-01", hostname="PC-01"))
    window = TeacherMainWindow(runtime=runtime, auto_start=False)
    qtbot.addWidget(window)
    window.show()
    window._poll_runtime()
    QApplication.processEvents()
    source = window.seat_map.findChild(SeatTile)

    qtbot.mousePress(source, Qt.LeftButton)
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    # 按下后还要由同一控件接收移动和释放，选中反馈不能销毁拖动起点。
    assert not sip.isdeleted(source)
    assert source.property("selected") is True
    assert window._selected_ids == {"pc-01"}
    qtbot.mouseRelease(source, Qt.LeftButton)
    window.close()


def test_patrol_drag_and_click_scope_commands(file_drop_window, qtbot):
    window = file_drop_window
    window._show_page("inspection")
    QApplication.processEvents()
    canvas = window.patrol_canvas
    cards = [window._patrol_cards[k] for k in ("pc-01", "pc-02")]
    qtbot.mouseClick(cards[1].image, Qt.LeftButton)
    assert window._current_scope() == DeviceIds(["pc-02"])
    with patch.object(window, "_confirm_targets", return_value=False) as confirm:
        window._start_silence()
        confirm.assert_called_once_with("黑屏肃静", ["pc-02"])
        assert not window.runtime.snapshot().operations
    window._start_silence()
    assert window.runtime.snapshot().operations[-1].target_ids == ("pc-02",)
    qtbot.mousePress(canvas, Qt.LeftButton, pos=cards[1].geometry().center())
    qtbot.mouseRelease(canvas, Qt.LeftButton, pos=cards[0].geometry().center())
    assert window._current_scope() == DeviceIds(["pc-01", "pc-02"])
    qtbot.mouseClick(cards[1].image, Qt.LeftButton, Qt.ControlModifier)
    assert window._current_scope() == DeviceIds(["pc-01"])
    qtbot.mouseClick(cards[0].image, Qt.LeftButton, Qt.ControlModifier)
    with pytest.raises(ValueError):
        window._current_scope()


@pytest.mark.parametrize("label, kind", [
    ("关机", CommandKind.POWER_OFF),
    ("重启", CommandKind.REBOOT),
    ("注销", CommandKind.LOGOFF),
])
@pytest.mark.parametrize("answer", [QMessageBox.Yes, QMessageBox.No])
def test_power_buttons_confirm_current_scope_without_slot_errors(qtbot, label, kind, answer):
    runtime = TeacherRuntime(database_path=":memory:")
    # 宿主只验证按钮和确认链路，电源执行使用替身；真实效果仅在实验来宾验收。
    runtime.power = MagicMock()
    window = TeacherMainWindow(runtime=runtime, auto_start=False)
    qtbot.addWidget(window)
    window.show()
    window._show_page("maintenance")
    window.scope_buttons["all"].setChecked(True)
    button = next(item for item in window.findChildren(QPushButton) if item.text() == label)

    with patch.object(window, "_confirm_targets", return_value=answer == QMessageBox.Yes) as confirm:
        with qtbot.captureExceptions() as exceptions:
            qtbot.mouseClick(button, Qt.LeftButton)
        assert not exceptions

    confirm.assert_called_once_with(label, window._resolve_scope_ids(window._current_scope()))
    if answer == QMessageBox.Yes:
        runtime.power.assert_called_once_with(DeviceIds(window._resolve_scope_ids(window._current_scope())), kind)
    else:
        runtime.power.assert_not_called()
    window.close()


@pytest.mark.parametrize("device_disappears", [False, True])
def test_process_device_change_clears_old_rows_and_pending_query(qtbot, device_disappears):
    runtime = TeacherRuntime(database_path=":memory:")
    for device_id in ("pc-01", "pc-02"):
        runtime.classroom._on_device(TransportDevice(device_id=device_id, hostname=device_id))
    window = TeacherMainWindow(runtime=runtime, auto_start=False)
    qtbot.addWidget(window)
    window._poll_runtime()
    window.process_table.setRowCount(1)
    window.process_table.setItem(0, 0, QTableWidgetItem("1234"))
    window._process_command_id = "old-device-query"

    if device_disappears:
        remaining = window._snapshot.device("pc-02")
        window._snapshot = MagicMock(devices=(remaining,))
        window._refresh_device_tables()
    else:
        window.process_device.setCurrentIndex(1)

    assert window.process_device.currentData() == "pc-02"
    assert window.process_table.rowCount() == 0
    assert window._process_command_id is None
    # 旧设备迟到的查询结果不能重新填回当前设备的表格。
    late_snapshot = MagicMock()
    window._consume_process_result(late_snapshot)
    late_snapshot.operation.assert_not_called()
    window.close()


@pytest.mark.parametrize("success", [False, True])
def test_network_button_reports_applied_or_failure_instead_of_only_submission(qtbot, success):
    transport = InMemoryTransport()
    transport.scan_devices = MagicMock()
    transport.file_distributor = MagicMock()
    transport.connect("pc-01", "PC-01")
    runtime = TeacherRuntime(server=transport, database_path=":memory:")
    window = TeacherMainWindow(runtime=runtime, auto_start=False)
    qtbot.addWidget(window)
    window._poll_runtime()
    window.scope_buttons["all"].setChecked(True)
    window._block_network()
    operation = runtime.snapshot().operations[-1]
    detail = "请先启用 Windows 防火墙的全部网络配置" if not success else ""
    transport.ack(operation.command_id, "pc-01", AckPhase.APPLIED,
                  operation.revision, success=success, detail=detail)

    window._poll_runtime()

    if success:
        assert "策略已应用" in window.operation_status.text()
    else:
        assert "失败" in window.operation_status.text()
        assert detail in window.operation_status.text()
        assert detail in window.operation_drawer.list.item(0).toolTip()
    window.close()


@pytest.mark.parametrize("surface", ["classroom", "inspection", "maintenance"])
def test_device_name_copy_and_note_controls_are_per_device_and_update_views(
    file_drop_window, qtbot, surface,
):
    window = file_drop_window
    window.runtime.server.connect("pc-02", "PC-01")
    window._poll_runtime()
    window._show_page(surface)

    def device_name_widget():
        if surface == "classroom":
            return next(tile for tile in window.seat_map.findChildren(SeatTile)
                        if tile.device.device_id == "pc-01").findChild(DeviceNameWidget)
        if surface == "inspection":
            return window._patrol_cards["pc-01"].findChild(DeviceNameWidget)
        for row in range(window.maintenance_table.rowCount()):
            if window.maintenance_table.item(row, 0).data(Qt.UserRole) == "pc-01":
                return window.maintenance_table.cellWidget(row, 0)
        raise AssertionError("设备行不存在")

    widget = device_name_widget()
    clipboard = MagicMock()
    with patch("teacher.ui.main_window.QApplication.clipboard", return_value=clipboard):
        qtbot.mouseClick(widget.copy_button, Qt.LeftButton)
    clipboard.setText.assert_called_once_with("PC-01")
    assert "复制" in window.operation_status.text()

    with patch("teacher.ui.main_window.QInputDialog.getText", return_value=("第一排 / 李同学", True)):
        qtbot.mouseClick(widget.note_button, Qt.LeftButton)
    window._poll_runtime()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert window.runtime.snapshot().device("pc-01").note == "第一排 / 李同学"
    assert window.runtime.snapshot().device("pc-02").note == ""
    for container in (window.seat_map, window._patrol_cards["pc-01"], window.maintenance_table):
        matching = [name for name in container.findChildren(DeviceNameWidget)
                    if name.device_id == "pc-01"]
        assert len(matching) == 1
        assert matching[0].note_label.text() == "第一排 / 李同学"
    with patch("teacher.ui.main_window.QInputDialog.getText", return_value=("不保存", False)) as dialog:
        qtbot.mouseClick(device_name_widget().note_button, Qt.LeftButton)
    assert dialog.call_args.kwargs["text"] == "第一排 / 李同学"
    assert window.runtime.snapshot().device("pc-01").note == "第一排 / 李同学"
    window.classroom_search.setText("李同学")
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert [tile.device.device_id for tile in window.seat_map.findChildren(SeatTile)
            if not sip.isdeleted(tile)] == ["pc-01"]
    window.classroom_search.clear()
    window._show_page(surface)
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    with patch("teacher.ui.main_window.QInputDialog.getText", return_value=("", True)):
        qtbot.mouseClick(device_name_widget().note_button, Qt.LeftButton)
    window._poll_runtime()
    assert window.runtime.snapshot().device("pc-01").note == ""
    assert not window.runtime.server.sent


def test_device_names_only_copy_on_explicit_click_and_show_full_name_in_tooltip(
    file_drop_window, qtbot,
):
    window = file_drop_window
    hostname = "PC-20230202LWWA-VERY-LONG-DEVICE-NAME"
    clipboard = MagicMock()
    with patch("teacher.ui.main_window.QApplication.clipboard", return_value=clipboard):
        window.runtime.server.connect("pc-01", hostname)
        window._poll_runtime()
        window._show_page("inspection")
        window.resize(1093, 614)
        QApplication.processEvents()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        clipboard.setText.assert_not_called()
        name = window._patrol_cards["pc-01"].findChild(DeviceNameWidget)
        assert hostname in name.copy_button.toolTip()
        assert name.copy_button.fontMetrics().horizontalAdvance(name.copy_button.text()) <= name.copy_button.width()
        assert name.note_button.isVisible()
        qtbot.mouseClick(name.copy_button, Qt.LeftButton)
        clipboard.setText.assert_called_once_with(hostname)


def test_device_note_save_failure_is_reported_without_renaming_device(file_drop_window):
    window = file_drop_window
    with patch("teacher.ui.main_window.QInputDialog.getText", return_value=("备注", True)), \
            patch.object(window.runtime, "set_device_note", side_effect=OSError("磁盘写入失败")), \
            patch("teacher.ui.main_window.QMessageBox.warning") as warning:
        window._edit_device_note("pc-01")
    warning.assert_called_once_with(window, "备注保存失败", "磁盘写入失败")
    assert window.runtime.snapshot().device("pc-01").hostname == "PC-01"
    assert window.runtime.snapshot().device("pc-01").note == ""


def test_global_application_form_ignores_topbar_scope_and_can_clear_all(file_drop_window, qtbot):
    window = file_drop_window
    window._show_page("policies")
    window._selected_ids = {"pc-01"}
    window.scope_buttons["selected"].setChecked(True)
    window.runtime.server.disconnect("pc-02")
    window._poll_runtime()
    window.application_program.setText("Chrome.EXE")
    qtbot.mouseClick(window.application_block_button, Qt.LeftButton)
    snapshot = window.runtime.snapshot()
    operation = snapshot.operations[-1]
    assert operation.target_ids == ("pc-01", "pc-02")
    assert snapshot.global_blacklist == ("chrome.exe",)
    assert window.blacklist_table.rowCount() == 1
    assert "全班" in window.blacklist_table.item(0, 1).text()
    assert "离线" in window.blacklist_table.item(0, 2).text()
    assert "永久生效" not in window.blacklist_table.item(0, 2).text()
    window.runtime.server.ack(
        operation.command_id, "pc-01", AckPhase.APPLIED, operation.revision,
        success=False, detail="学生端拒绝执行",
    )
    window._poll_runtime()
    assert "失败" in window.blacklist_table.item(0, 2).text()
    assert "学生端拒绝执行" in window.blacklist_table.item(0, 2).toolTip()
    clear = window.blacklist_table.cellWidget(0, 3).findChild(QPushButton)
    with patch("teacher.ui.main_window.QMessageBox.question", return_value=QMessageBox.Yes):
        qtbot.mouseClick(clear, Qt.LeftButton)
    assert window.runtime.snapshot().global_blacklist == ()
    assert window.runtime.snapshot().operations[-1].action.kind is CommandKind.BLACKLIST_CLEAR


def test_process_browser_filters_system_processes_and_uses_name_for_global_rule(file_drop_window, qtbot):
    window = file_drop_window
    window._show_page("policies")
    window.policy_tabs.setCurrentIndex(1)
    window._query_processes()
    operation = window.runtime.snapshot().operations[-1]
    processes = [
        {"pid": 10, "name": "chrome.exe", "path": r"C:\Program Files\Chrome\chrome.exe",
         "creation_time": 10.0, "username": "学生", "protected": False},
        {"pid": 11, "name": "svchost.exe", "path": r"C:\Windows\svchost.exe",
         "creation_time": 11.0, "protected": True, "protection_reason": "system"},
    ]
    window.runtime.classroom._on_ack(TransportAck(
        operation.command_id, "pc-01", AckPhase.APPLIED, operation.revision,
        payload={"processes": processes},
    ))
    window._poll_runtime()
    assert window.process_table.rowCount() == 1
    window.process_search.setText("找不到")
    assert window.process_table.rowCount() == 0
    window.process_search.clear()
    window.show_system_processes.setChecked(True)
    assert window.process_table.rowCount() == 2
    window.show_system_processes.setChecked(False)
    window.process_table.selectRow(0)
    qtbot.mouseClick(window.process_block_button, Qt.LeftButton)
    assert window.policy_tabs.currentIndex() == 0
    assert window.application_program.text() == "chrome.exe"
    assert not window.runtime.snapshot().global_blacklist
    qtbot.mouseClick(window.application_block_button, Qt.LeftButton)
    assert window.runtime.snapshot().global_blacklist == ("chrome.exe",)
    assert window.runtime.snapshot().operations[-1].action.payload["path"] == ""


@pytest.mark.parametrize("width,height", [(1366, 768), (1093, 614)])
def test_application_page_actions_fit_without_horizontal_scrolling(file_drop_window, width, height):
    window = file_drop_window
    window.resize(width, height)
    window._show_page("policies")
    QApplication.processEvents()
    for index, buttons in (
        (0, [window.application_block_button]),
        (1, [window.process_block_button, window.process_terminate_button]),
    ):
        window.policy_tabs.setCurrentIndex(index)
        QApplication.processEvents()
        for button in buttons:
            assert button.isVisible()
            assert button.mapTo(window.pages, button.rect().topRight()).x() <= window.pages.width()


def test_application_rule_reports_only_confirmed_results_and_retains_restore_progress(file_drop_window, qtbot):
    window = file_drop_window
    window._show_page("policies")
    window.application_program.setText("notepad.exe")
    qtbot.mouseClick(window.application_block_button, Qt.LeftButton)
    operation = window.runtime.snapshot().operations[-1]
    assert "已禁用 0/2" in window.blacklist_table.item(0, 2).text()
    window.runtime.server.ack(operation.command_id, "pc-01", AckPhase.RECEIVED, operation.revision)
    window._poll_runtime()
    assert "已禁用 0/2" in window.blacklist_table.item(0, 2).text()
    for device_id in ("pc-01", "pc-02"):
        window.runtime.server.ack(operation.command_id, device_id, AckPhase.APPLIED, operation.revision)
    window._poll_runtime()
    assert window.blacklist_table.item(0, 2).text() == "已禁用 2/2"
    value = window.blacklist_table.item(0, 0).data(Qt.UserRole)
    with patch("teacher.ui.main_window.QMessageBox.question", return_value=QMessageBox.No):
        window._remove_blacklist(value)
    assert window.runtime.snapshot().global_blacklist == ("notepad.exe",)
    with patch("teacher.ui.main_window.QMessageBox.question", return_value=QMessageBox.Yes):
        window._remove_blacklist(value)
    restored = window.runtime.snapshot().operations[-1]
    assert "解除中" in window.blacklist_table.item(0, 2).text()
    for device_id in ("pc-01", "pc-02"):
        window.runtime.server.ack(restored.command_id, device_id, AckPhase.APPLIED, restored.revision)
    window._poll_runtime()
    assert window.blacklist_table.rowCount() == 0
    assert window.application_empty.isVisible()


def test_application_rule_current_scope_is_distinct_from_persistent_global_rule(file_drop_window, qtbot):
    window = file_drop_window
    window._show_page("policies")
    window.scope_buttons["selected"].setChecked(True)
    window._selected_ids = {"pc-01"}
    window.application_scope.setCurrentIndex(1)
    window.application_program.setText("game.exe")
    qtbot.mouseClick(window.application_block_button, Qt.LeftButton)
    operation = window.runtime.snapshot().operations[-1]
    assert operation.target_ids == ("pc-01",)
    assert window.runtime.snapshot().global_blacklist == ()
    window.runtime.server.connect("pc-03", "新设备")
    assert not window.runtime.server.sent_to("pc-03")
    window.application_program.setText("explorer.exe")
    qtbot.mouseClick(window.application_block_button, Qt.LeftButton)
    assert "受保护" in window.application_feedback.text()
    assert len(window.runtime.snapshot().operations) == 1


def test_application_control_switch_preserves_visible_rules_and_resumes_them(file_drop_window, qtbot):
    window = file_drop_window
    window._show_page("policies")
    window.application_program.setText("game.exe")
    qtbot.mouseClick(window.application_block_button, Qt.LeftButton)
    assert window.application_control_switch.isChecked()
    qtbot.mouseClick(window.application_control_switch, Qt.LeftButton)
    assert window.runtime.snapshot().application_control_enabled is False
    assert not window.application_control_switch.isChecked()
    assert window.blacklist_table.rowCount() == 1
    assert window.blacklist_table.item(0, 0).text() == "game.exe"
    assert "暂停" in window.blacklist_table.item(0, 2).text()
    assert window.application_block_button.text() == "保存规则"
    assert window.runtime.snapshot().operations[-1].action.kind is CommandKind.BLACKLIST_CLEAR
    window.application_program.setText("editor.exe")
    qtbot.mouseClick(window.application_block_button, Qt.LeftButton)
    assert window.blacklist_table.rowCount() == 2
    assert window.runtime.snapshot().operations[-1].action.kind is CommandKind.BLACKLIST_CLEAR
    count = len(window.runtime.snapshot().operations)
    window._poll_runtime()
    assert len(window.runtime.snapshot().operations) == count
    qtbot.mouseClick(window.application_control_switch, Qt.LeftButton)
    assert window.runtime.snapshot().application_control_enabled is True
    assert window.runtime.snapshot().global_blacklist == ("editor.exe", "game.exe")
    assert window.blacklist_table.rowCount() == 2
    assert window.application_block_button.text() == "全班禁用"
    assert window.runtime.snapshot().operations[-1].action.kind is CommandKind.BLACKLIST_SET


def test_application_control_switch_failure_reverts_visual_state(file_drop_window, qtbot):
    window = file_drop_window
    window._show_page("policies")
    with patch.object(window.runtime, "set_application_control_enabled", side_effect=OSError("保存失败")):
        qtbot.mouseClick(window.application_control_switch, Qt.LeftButton)
    assert window.application_control_switch.isChecked()
    assert window.runtime.snapshot().application_control_enabled is True
    assert "保存失败" in window.application_feedback.text()


@pytest.mark.parametrize("page", ["inspection", "classroom"])
def test_device_menu_uses_clicked_seat_and_preserves_multi_selection(file_drop_window, qtbot, page):
    from PyQt5.QtCore import QTimer
    from PyQt5.QtGui import QContextMenuEvent
    from PyQt5.QtWidgets import QMenu
    window = file_drop_window
    window._show_page(page)
    card = window._patrol_cards["pc-02"] if page == "inspection" else next(
        tile for tile in window.seat_map.canvas.findChildren(SeatTile) if tile.device.device_id == "pc-02")
    for previous, expected in ((("pc-01",), ("pc-02",)), (("pc-01", "pc-02"), ("pc-01", "pc-02"))):
        window._selection_changed(previous)
        def choose():
            menu = QApplication.activePopupWidget()
            assert isinstance(menu, QMenu)
            commands = {a.text(): a for a in menu.actions()}
            assert not commands["删除这台离线设备"].isEnabled()
            commands["黑屏肃静"].trigger()
            menu.close()
        QTimer.singleShot(0, choose)
        pos = card.rect().center()
        QApplication.sendEvent(card, QContextMenuEvent(QContextMenuEvent.Mouse, pos, card.mapToGlobal(pos)))
        assert window.runtime.snapshot().operations[-1].target_ids == expected


def test_views_share_unlabelled_checks_and_preview_starts_once(file_drop_window, qtbot):
    window = file_drop_window
    window._auto_preview = True
    window._last_revision = -1
    window._poll_runtime()
    window._poll_runtime()
    assert [item.envelope.action for item in window.runtime.server.sent_to("pc-01")] == ["inspection.open"]
    window._show_page("inspection")
    qtbot.mouseClick(window._patrol_cards["pc-02"].check, Qt.LeftButton)
    window.view_switch.setCurrentIndex(1)
    tile = next(t for t in window.seat_map.findChildren(SeatTile) if t.device.device_id == "pc-02")
    assert tile.check.isChecked() and not tile.check.text()
    assert "PC-02" in window.target_summary.text()
    qtbot.mouseClick(tile.check, Qt.LeftButton)
    assert not window._patrol_cards["pc-02"].check.isChecked()

def test_confirmation_lists_devices_and_defaults_to_cancel(file_drop_window):
    from PyQt5.QtCore import QTimer
    window = file_drop_window
    def dismiss():
        dialog = QApplication.activeModalWidget()
        from PyQt5.QtWidgets import QListWidget, QDialogButtonBox
        assert dialog.windowTitle() == "黑屏肃静"
        roster = dialog.findChild(QListWidget)
        assert roster.count() == 1 and "PC-02" in roster.item(0).text()
        cancel = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Cancel)
        assert cancel.isDefault()
        cancel.click()
    QTimer.singleShot(0, dismiss)
    assert not _real_target_confirmation(window, "黑屏肃静", ["pc-02"])


def test_patrol_refresh_checks_all_online_devices_without_confirmation(file_drop_window, qtbot):
    window = file_drop_window
    window._show_page("inspection")
    window._selection_changed(("pc-01",))
    window.runtime.server.connect("offline", "OFFLINE")
    window.runtime.server.disconnect("offline")
    window._poll_runtime()
    window.runtime.server.clear_sent()
    with patch.object(window, "_confirm_targets", side_effect=AssertionError("unexpected confirmation")):
        start = next(b for b in window.findChildren(QPushButton) if b.text() == "开始巡视")
        qtbot.mouseClick(start, Qt.LeftButton)
        window.runtime.server.scan_devices.assert_called_once()
        assert window._selected_ids == {"pc-01"}
        operation = window.runtime.snapshot().operations[-1]
        assert operation.action.kind == CommandKind.INSPECTION_OPEN
        assert operation.target_ids == ("pc-01", "pc-02")
        assert not window.runtime.server.sent_to("offline")

        window.runtime.server.connect("new", "NEW")
        window._poll_runtime()
        assert window.runtime.server.sent_to("new")[-1].envelope.action == "inspection.open"
        stop = next(b for b in window.findChildren(QPushButton) if b.text() == "停止巡视")
        qtbot.mouseClick(stop, Qt.LeftButton)
        assert window.runtime.snapshot().operations[-1].target_ids == ("new", "pc-01", "pc-02")
        assert not window._auto_preview
        window.runtime.server.clear_sent()
        window.runtime.server.connect("later", "LATER")
        window._poll_runtime()
        assert not window.runtime.server.sent_to("later")


def test_patrol_refresh_with_no_online_devices_still_scans(file_drop_window):
    window = file_drop_window
    for key in ("pc-01", "pc-02"):
        window.runtime.server.disconnect(key)
    with patch.object(window, "_confirm_targets", side_effect=AssertionError("unexpected confirmation")):
        window._start_inspection()
    window.runtime.server.scan_devices.assert_called_once()
    assert window._auto_preview
    assert "等待学生机连接" in window.operation_status.text()
