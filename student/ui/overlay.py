import sys
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QSize
from PyQt6.QtGui import QPixmap, QImage, QFont, QColor, QPalette
from io import BytesIO
from PIL import Image


class OverlayWindow(QWidget):
    frame_received = pyqtSignal(bytes, tuple)

    def __init__(self):
        super().__init__()
        self._setup_ui()
        self._current_pixmap = None
        self.frame_received.connect(self._on_frame_received)

    def _setup_ui(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setCursor(Qt.CursorShape.BlankCursor)

        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet("background-color: black;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label)

        self._message_label = QLabel("正在接收屏幕广播...", self)
        self._message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message_label.setStyleSheet("""
            color: #666;
            font-size: 24px;
            background: transparent;
        """)
        self._message_label.setGeometry(0, 0, 800, 100)
        self._message_label.hide()

    def show_black_screen(self):
        self._label.setText("")
        self._label.setStyleSheet("background-color: black;")
        self._message_label.setText("")
        self._message_label.hide()
        self.showFullScreen()
        self.raise_()
        self.activateWindow()

    def show_broadcast(self):
        self._message_label.setText("正在连接教师屏幕...")
        self._message_label.show()
        self._label.setStyleSheet("background-color: #111;")
        self.showFullScreen()
        self.raise_()
        self.activateWindow()

    def hide_overlay(self):
        self.hide()

    def update_frame(self, frame_data: bytes, size: tuple):
        self.frame_received.emit(frame_data, size)

    def _on_frame_received(self, frame_data: bytes, size: tuple):
        try:
            img = Image.open(BytesIO(frame_data))
            qimg = QImage(
                img.tobytes(),
                img.width,
                img.height,
                img.width * 3,
                QImage.Format.Format_RGB888
            )
            pixmap = QPixmap.fromImage(qimg)
            self._current_pixmap = pixmap
            self._update_display()
            if self._message_label.isVisible():
                self._message_label.hide()
        except Exception as e:
            pass

    def _update_display(self):
        if self._current_pixmap:
            scaled = self._current_pixmap.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
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
