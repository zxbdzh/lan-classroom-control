import sys
import os
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QMessageBox
from PyQt5.QtGui import QIcon, QPixmap, QPainter, QColor
from PyQt5.QtCore import Qt, QObject, pyqtSignal
from student.core.student_client import StudentClient
from student.ui.overlay import OverlayWindow
from common.logger import get_logger

logger = get_logger("student_app")


class StudentAppUI(QObject):
    black_screen_changed = pyqtSignal(bool)
    broadcast_started = pyqtSignal(dict)
    broadcast_stopped = pyqtSignal()
    screen_frame = pyqtSignal(bytes, tuple)
    file_received = pyqtSignal(str)
    file_progress = pyqtSignal(str, float, float)  # file_name, progress(0-1), total_bytes
    status_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.app = QApplication(sys.argv)
        self.app.setApplicationName("局域网机房控制系统 - 学生端")
        self.app.setQuitOnLastWindowClosed(False)

        self.client = None
        self.overlay = None
        self.tray = None
        self._status_action = None

        self._setup_signals()

        try:
            logger.info("Initializing student client...")
            self.client = StudentClient()
            logger.info(f"Student client initialized: {self.client.hostname} ({self.client.local_ip}) [{self.client.mac_address}]")
        except Exception as e:
            logger.error(f"Failed to initialize student client: {e}\n{traceback.format_exc()}")
            QMessageBox.critical(None, "启动失败", f"学生端初始化失败:\n{e}")
            sys.exit(1)

        try:
            self.overlay = OverlayWindow()
        except Exception as e:
            logger.error(f"Failed to create overlay window: {e}")

        self._setup_tray()
        self._setup_client_callbacks()
        self._check_admin_warning()

    def _setup_signals(self):
        self.black_screen_changed.connect(self._on_black_screen_changed_ui)
        self.broadcast_started.connect(self._on_broadcast_started_ui)
        self.broadcast_stopped.connect(self._on_broadcast_stopped_ui)
        self.screen_frame.connect(self._on_screen_frame_ui)
        self.file_received.connect(self._on_file_received_ui)
        self.file_progress.connect(self._on_file_progress_ui)
        self.status_changed.connect(self._on_status_changed_ui)

    def _setup_client_callbacks(self):
        if self.client:
            self.client.on_black_screen_changed = lambda enable: self.black_screen_changed.emit(enable)
            self.client.on_broadcast_started = lambda params: self.broadcast_started.emit(params)
            self.client.on_broadcast_stopped = lambda: self.broadcast_stopped.emit()
            self.client.on_screen_frame = lambda data, size: self.screen_frame.emit(data, size)
            self.client.on_file_received = lambda path: self.file_received.emit(path)
            self.client.on_file_progress = lambda name, progress, total: self.file_progress.emit(name, progress, total)
            self.client.on_connected = lambda: self.status_changed.emit("已连接教师端")
            self.client.on_disconnected = lambda: self.status_changed.emit("未连接")

    def _check_admin_warning(self):
        if not self.client:
            return
        if sys.platform == "win32":
            has_admin = self.client.net_controller.check_admin_privilege()
            if not has_admin:
                logger.warning("Student running without administrator privilege")
                try:
                    if self.tray and QSystemTrayIcon.supportsMessages():
                        self.tray.showMessage(
                            "提示",
                            "当前未以管理员身份运行，禁用上网等功能可能无法正常工作。\n建议右键选择'以管理员身份运行'。",
                            QSystemTrayIcon.Information,
                            5000
                        )
                except Exception:
                    pass

    def _setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.warning("System tray not available, running without tray icon")
            return

        tray_icon = self._create_tray_icon()
        self.tray = QSystemTrayIcon(tray_icon, self.app)
        self.tray.setToolTip("机房控制系统 - 学生端")

        menu = QMenu()
        self._status_action = menu.addAction("状态: 等待连接")
        self._status_action.setEnabled(False)
        menu.addSeparator()
        about_action = menu.addAction("关于")
        about_action.triggered.connect(self._on_about)
        # 学生端不允许通过托盘退出，移除"退出"选项

        self.tray.setContextMenu(menu)
        self.tray.show()

    def _create_tray_icon(self):
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor("#0078d4"))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(2, 2, 28, 28, 6, 6)
        painter.setPen(QColor("white"))
        font = painter.font()
        font.setBold(True)
        font.setPointSize(14)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignCenter, "S")
        painter.end()
        return QIcon(pixmap)

    def _on_status_changed_ui(self, status: str):
        if self._status_action:
            self._status_action.setText(f"状态: {status}")
        if self.tray:
            self.tray.setToolTip(f"机房控制系统 - 学生端 - {status}")

    def _on_black_screen_changed_ui(self, enable: bool):
        if not self.overlay:
            return
        try:
            if enable:
                self.overlay.show_black_screen()
            else:
                if not self.client.is_broadcasting:
                    self.overlay.hide_overlay()
        except Exception as e:
            logger.error(f"Black screen change error: {e}")

    def _on_broadcast_started_ui(self, params: dict):
        if not self.overlay:
            return
        try:
            self.overlay.show_broadcast()
        except Exception as e:
            logger.error(f"Broadcast start error: {e}")

    def _on_broadcast_stopped_ui(self):
        if not self.overlay:
            return
        try:
            if not self.client.is_black_screen:
                self.overlay.hide_overlay()
        except Exception as e:
            logger.error(f"Broadcast stop error: {e}")

    def _on_screen_frame_ui(self, frame_data: bytes, size: tuple):
        if not self.overlay:
            return
        try:
            self.overlay.update_frame(frame_data, size)
        except Exception as e:
            logger.debug(f"Screen frame update error: {e}")

    def _on_file_received_ui(self, file_path: str):
        logger.info(f"File received: {file_path}")
        if self.tray and QSystemTrayIcon.supportsMessages():
            try:
                self.tray.showMessage(
                    "文件接收完成",
                    f"文件已保存到桌面: {os.path.basename(file_path)}",
                    QSystemTrayIcon.Information,
                    5000
                )
            except Exception as e:
                logger.warning(f"Tray message error: {e}")

    def _on_file_progress_ui(self, file_name: str, progress: float, total: float):
        if not (self.tray and QSystemTrayIcon.supportsMessages()):
            return
        try:
            percent = int(progress * 100)
            # 进度通知只在整十的百分比或开始时显示，避免刷屏
            if percent % 10 == 0 or percent < 5:
                display_name = file_name if file_name else "文件"
                self.tray.showMessage(
                    "文件接收中",
                    f"{display_name} {percent}%",
                    QSystemTrayIcon.Information,
                    1500
                )
        except Exception as e:
            logger.debug(f"Progress tray message error: {e}")

    def _on_about(self):
        from common.version import get_version
        QMessageBox.information(
            None, "关于",
            f"局域网机房控制系统 - 学生端\n版本: {get_version()}\n\n"
            f"本程序由教师端统一管理，开机自启动。"
        )

    def _on_quit(self):
        # 学生端不允许退出，此方法仅作内部兜底
        logger.warning("Student quit attempted, but student app is not allowed to quit")

    def run(self):
        try:
            if self.client:
                self.client.start()
            logger.info("Student application started")
            return self.app.exec()
        except Exception as e:
            logger.error(f"Fatal error: {e}\n{traceback.format_exc()}")
            QMessageBox.critical(None, "运行错误", f"程序运行出错:\n{e}")
            return 1


def main():
    try:
        app = StudentAppUI()
        sys.exit(app.run())
    except Exception as e:
        logger.error(f"Failed to start application: {e}\n{traceback.format_exc()}")
        try:
            QMessageBox.critical(None, "启动失败", f"程序启动失败:\n{e}")
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
