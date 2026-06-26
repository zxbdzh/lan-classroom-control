import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor
from PyQt6.QtCore import Qt
from student.core.student_client import StudentClient
from student.ui.overlay import OverlayWindow
from common.logger import get_logger

logger = get_logger("student_app")


class StudentApp:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setApplicationName("局域网机房控制系统 - 学生端")
        self.app.setQuitOnLastWindowClosed(False)

        self.client = StudentClient()
        self.overlay = OverlayWindow()

        self._setup_callbacks()
        self._setup_tray()

    def _setup_callbacks(self):
        self.client.on_black_screen_changed = self._on_black_screen_changed
        self.client.on_broadcast_started = self._on_broadcast_started
        self.client.on_broadcast_stopped = self._on_broadcast_stopped
        self.client.on_screen_frame = self._on_screen_frame
        self.client.on_file_received = self._on_file_received

    def _setup_tray(self):
        tray_icon = self._create_tray_icon()
        self.tray = QSystemTrayIcon(tray_icon, self.app)
        self.tray.setToolTip("机房控制系统 - 学生端")

        menu = QMenu()
        status_action = menu.addAction("状态: 等待连接")
        menu.addSeparator()
        quit_action = menu.addAction("退出")
        quit_action.triggered.connect(self._on_quit)

        self.tray.setContextMenu(menu)
        self.tray.show()
        self._status_action = status_action

        self.client.on_connected = lambda: self._update_tray_status("已连接教师端")
        self.client.on_disconnected = lambda: self._update_tray_status("未连接")

    def _create_tray_icon(self):
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor("#0078d4"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(2, 2, 28, 28, 6, 6)
        painter.setPen(QColor("white"))
        font = painter.font()
        font.setBold(True)
        font.setPointSize(14)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "S")
        painter.end()
        return QIcon(pixmap)

    def _update_tray_status(self, status: str):
        if hasattr(self, '_status_action'):
            self._status_action.setText(f"状态: {status}")

    def _on_black_screen_changed(self, enable: bool):
        if enable:
            self.overlay.show_black_screen()
        else:
            if not self.client.is_broadcasting:
                self.overlay.hide_overlay()

    def _on_broadcast_started(self, params: dict):
        self.overlay.show_broadcast()

    def _on_broadcast_stopped(self):
        if not self.client.is_black_screen:
            self.overlay.hide_overlay()

    def _on_screen_frame(self, frame_data: bytes, size: tuple):
        self.overlay.update_frame(frame_data, size)

    def _on_file_received(self, file_path: str):
        logger.info(f"File received: {file_path}")
        self.tray.showMessage(
            "文件接收完成",
            f"文件已保存到: {file_path}",
            QSystemTrayIcon.MessageIcon.Information,
            5000
        )

    def _on_quit(self):
        self.client.stop()
        self.app.quit()

    def run(self):
        self.client.start()
        logger.info("Student application started")
        return self.app.exec()


def main():
    app = StudentApp()
    sys.exit(app.run())


if __name__ == "__main__":
    main()
