import os
import threading
from unittest.mock import MagicMock, patch
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import sip
from PyQt5.QtCore import QCoreApplication, QEvent, Qt
from PyQt5.QtWidgets import QApplication, QMessageBox, QPushButton, QTableWidgetItem
from PyQt5.QtGui import QPixmap

from teacher.core.classroom import TransportDevice
from teacher.core.runtime import TeacherRuntime
from common.v2.model import CommandKind, DeviceIds, InspectionMode
from teacher.ui.main_window import TeacherMainWindow
from teacher.ui.widgets import EmptySeat, FullScreenViewer, SeatTile
from student.ui.overlay import OverlayWindow


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
    button = next(item for item in window.findChildren(QPushButton) if item.text() == label)

    with patch("teacher.ui.main_window.QMessageBox.question", return_value=answer) as confirm:
        with qtbot.captureExceptions() as exceptions:
            qtbot.mouseClick(button, Qt.LeftButton)
        assert not exceptions

    confirm.assert_called_once_with(window, label, f"确定对当前作用域执行{label}？")
    if answer == QMessageBox.Yes:
        runtime.power.assert_called_once_with(window._current_scope(), kind)
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
