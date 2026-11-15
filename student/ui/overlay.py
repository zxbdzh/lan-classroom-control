import sys
import threading
from PyQt5.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QSize
from PyQt5.QtGui import QPixmap, QImage, QFont, QColor, QPalette
from io import BytesIO
from PIL import Image
from common.logger import get_logger

logger = get_logger("overlay")


class OverlayWindow(QWidget):
    frame_received = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._setup_ui()
        self._current_pixmap = None
        self._active = False  # 是否正在接收帧
        self._frame_data_ref = None  # 持有帧数据引用，防止Qt底层指针悬空
        self._pending_frame = None
        self._frame_dispatch_pending = False
        self._visibility_generation = 0
        self._pending_lock = threading.Lock()
        self.frame_received.connect(self._on_frame_received)

    def _setup_ui(self):
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Window
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setCursor(Qt.BlankCursor)

        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setStyleSheet("background-color: black;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label)

        self._message_label = QLabel("正在接收屏幕广播...", self)
        self._message_label.setAlignment(Qt.AlignCenter)
        self._message_label.setStyleSheet("""
            color: #666;
            font-size: 24px;
            background: transparent;
        """)
        self._message_label.setGeometry(0, 0, 800, 100)
        self._message_label.hide()

    def show_black_screen(self, message: str = ""):
        self._visibility_generation += 1
        self._active = False
        self._label.clear()
        self._label.setText("")
        self._label.setStyleSheet("background-color: black;")
        self._message_label.setText(message or "请保持安静，注意教师屏幕")
        self._message_label.setStyleSheet("color: white; font-size: 28px; background: transparent;")
        self._message_label.show()
        self._show_fullscreen_topmost()

    def show_broadcast(self):
        self._visibility_generation += 1
        self._active = True
        self._message_label.setText("正在连接教师屏幕...")
        self._message_label.show()
        self._label.setStyleSheet("background-color: #111;")
        self._show_fullscreen_topmost()

    def hide_overlay(self):
        self._visibility_generation += 1
        generation = self._visibility_generation
        self._active = False
        self._current_pixmap = None
        self._frame_data_ref = None
        with self._pending_lock:
            self._pending_frame = None
            self._frame_dispatch_pending = False
        self._label.clear()
        # 延迟隐藏，让积压的帧先被丢弃；新的显示请求会使该任务失效。
        QTimer.singleShot(200, lambda: self._hide_if_current(generation))

    def _hide_if_current(self, generation: int) -> None:
        if generation == self._visibility_generation:
            self.hide()

    def _show_fullscreen_topmost(self):
        screens = QApplication.screens()
        if screens:
            virtual_geometry = screens[0].geometry()
            for screen in screens[1:]:
                virtual_geometry = virtual_geometry.united(screen.geometry())
            self.setGeometry(virtual_geometry)
        self.show()
        self.raise_()
        self._force_windows_topmost()
        QTimer.singleShot(120, self._force_windows_topmost)
        QTimer.singleShot(500, self._force_windows_topmost)

    def _force_windows_topmost(self):
        if sys.platform != "win32" or not self.isVisible():
            return
        try:
            import ctypes
            hwnd = int(self.winId())
            HWND_TOPMOST = -1
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_SHOWWINDOW = 0x0040
            SWP_NOACTIVATE = 0x0010
            from ctypes import wintypes
            ctypes.windll.user32.SetWindowPos.argtypes = (
                wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, wintypes.UINT,
            )
            ctypes.windll.user32.SetWindowPos(
                hwnd,
                HWND_TOPMOST,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW | SWP_NOACTIVATE,
            )
        except Exception as e:
            logger.debug(f"Force overlay topmost failed: {e}")

    def update_frame(self, frame_data: bytes, size: tuple):
        should_emit = False
        with self._pending_lock:
            self._pending_frame = (bytes(frame_data), tuple(size))
            if not self._frame_dispatch_pending:
                self._frame_dispatch_pending = True
                should_emit = True
        if should_emit:
            self.frame_received.emit()

    def _on_frame_received(self):
        with self._pending_lock:
            pending = self._pending_frame
            self._pending_frame = None
            self._frame_dispatch_pending = False
        if pending is None:
            return
        frame_data, _size = pending
        if not self._active:
            return
        try:
            img = Image.open(BytesIO(frame_data))
            img = img.convert("RGB")
            data = img.tobytes("raw", "RGB")
            qimg = QImage(
                data,
                img.width,
                img.height,
                img.width * 3,
                QImage.Format_RGB888
            )
            # 必须持有 data 引用，否则 QImage 底层数据会被回收
            self._frame_data_ref = data
            pixmap = QPixmap.fromImage(qimg)
            self._current_pixmap = pixmap
            self._update_display()
            if self._message_label.isVisible():
                self._message_label.hide()
        except Exception as e:
            logger.warning(f"Frame decode error: {e}")

    def _update_display(self):
        if self._current_pixmap:
            scaled = self._current_pixmap.scaled(
                self.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )
            self._label.setPixmap(scaled)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._message_label.setGeometry(
            0, self.height() // 2 - 50,
            self.width(), 100
        )
        self._update_display()

    def keyPressEvent(self, event):
        event.ignore()

    def keyReleaseEvent(self, event):
        event.ignore()

    def mousePressEvent(self, event):
        event.ignore()

    def mouseReleaseEvent(self, event):
        event.ignore()

    def mouseMoveEvent(self, event):
        event.ignore()

    def contextMenuEvent(self, event):
        event.ignore()

    def closeEvent(self, event):
        event.ignore()
