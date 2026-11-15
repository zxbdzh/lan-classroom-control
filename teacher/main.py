import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt
from teacher.ui.main_window import TeacherMainWindow
from common.logger import get_logger

logger = get_logger("teacher_app")


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--diagnostics":
        from common.diagnostics import startup_probe
        startup_probe("teacher", sys.argv[2])
        return
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName("局域网机房控制系统 - 教师端")

    window = TeacherMainWindow()
    window.show()

    logger.info("Teacher application started")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
