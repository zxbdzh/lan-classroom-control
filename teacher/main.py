import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication
from teacher.ui.main_window import TeacherMainWindow
from common.logger import get_logger

logger = get_logger("teacher_app")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("局域网机房控制系统 - 教师端")

    window = TeacherMainWindow()
    window.show()

    logger.info("Teacher application started")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
