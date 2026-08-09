import os
import threading
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QPixmap

from teacher.core.classroom import TransportDevice
from teacher.core.runtime import TeacherRuntime
from common.v2.model import DeviceIds, InspectionMode
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
