from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QToolBar, QListWidget, QListWidgetItem, QTreeWidget, QTreeWidgetItem,
    QLabel, QStatusBar, QFileDialog, QMessageBox, QMenu, QInputDialog,
    QGridLayout, QScrollArea, QFrame
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QSize
from PyQt6.QtGui import QAction, QIcon, QPixmap, QFont, QColor, QBrush
from typing import List, Optional
from teacher.core.teacher_server import TeacherServer
from teacher.core.student_manager import StudentInfo
from common.logger import get_logger

logger = get_logger("teacher_ui")


class StudentThumbWidget(QFrame):
    def __init__(self, student: StudentInfo, parent=None):
        super().__init__(parent)
        self.student = student
        self._setup_ui()

    def _setup_ui(self):
        self.setFrameShape(QFrame.Shape.Box)
        self.setLineWidth(1)
        self.setStyleSheet("""
            QFrame {
                background: #2a2a2a;
                border: 1px solid #444;
                border-radius: 4px;
            }
            QFrame:hover {
                border: 1px solid #0078d4;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self.screen_label = QLabel()
        self.screen_label.setMinimumSize(160, 120)
        self.screen_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.screen_label.setStyleSheet("background: #1a1a1a; color: #666;")
        self.screen_label.setText("无画面")
        layout.addWidget(self.screen_label)

        self.name_label = QLabel(self.student.display_name)
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_label.setStyleSheet("color: #ddd; font-size: 12px;")
        layout.addWidget(self.name_label)

        self.status_label = QLabel()
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("font-size: 10px;")
        self._update_status()
        layout.addWidget(self.status_label)

    def _update_status(self):
        statuses = []
        if not self.student.online:
            self.status_label.setStyleSheet("color: #888; font-size: 10px;")
            statuses.append("离线")
        else:
            self.status_label.setStyleSheet("color: #4caf50; font-size: 10px;")
            statuses.append("在线")
        if self.student.status.get("black_screen"):
            statuses.append("黑屏")
        if self.student.status.get("broadcasting"):
            statuses.append("广播中")
        if self.student.status.get("net_blocked"):
            statuses.append("禁网")
        self.status_label.setText(" | ".join(statuses))

    def update_student(self, student: StudentInfo):
        self.student = student
        self.name_label.setText(student.display_name)
        self._update_status()


class TeacherMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.server = TeacherServer()
        self._selected_students = set()
        self._thumb_widgets = {}
        self._setup_ui()
        self._setup_callbacks()
        self.server.start()
        self._update_status_bar()

    def _setup_ui(self):
        self.setWindowTitle("局域网机房控制系统 - 教师端")
        self.resize(1200, 800)
        self.setMinimumSize(800, 600)

        self._create_toolbar()
        self._create_central_widget()
        self._create_status_bar()

        self.setStyleSheet("""
            QMainWindow {
                background: #1e1e1e;
            }
            QToolBar {
                background: #2d2d2d;
                border: none;
                padding: 4px;
                spacing: 4px;
            }
            QToolButton {
                background: #3a3a3a;
                color: #ddd;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                margin: 2px;
            }
            QToolButton:hover {
                background: #0078d4;
            }
            QToolButton:checked {
                background: #0078d4;
            }
            QListWidget, QTreeWidget {
                background: #252525;
                color: #ddd;
                border: 1px solid #333;
            }
            QListWidget::item:selected, QTreeWidget::item:selected {
                background: #0078d4;
            }
            QStatusBar {
                background: #2d2d2d;
                color: #aaa;
            }
            QLabel {
                color: #ddd;
            }
            QScrollArea {
                background: #1e1e1e;
                border: none;
            }
        """)

    def _create_toolbar(self):
        toolbar = QToolBar("主工具栏")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(24, 24))
        self.addToolBar(toolbar)

        action_broadcast = QAction("开始广播", self)
        action_broadcast.triggered.connect(self._on_start_broadcast)
        toolbar.addAction(action_broadcast)

        action_stop_broadcast = QAction("停止广播", self)
        action_stop_broadcast.triggered.connect(self._on_stop_broadcast)
        toolbar.addAction(action_stop_broadcast)

        toolbar.addSeparator()

        action_black = QAction("黑屏肃静", self)
        action_black.triggered.connect(self._on_black_screen)
        toolbar.addAction(action_black)

        action_unblack = QAction("解除黑屏", self)
        action_unblack.triggered.connect(self._on_unblack_screen)
        toolbar.addAction(action_unblack)

        toolbar.addSeparator()

        action_block_net = QAction("禁用上网", self)
        action_block_net.triggered.connect(self._on_block_net)
        toolbar.addAction(action_block_net)

        action_unblock_net = QAction("解除禁网", self)
        action_unblock_net.triggered.connect(self._on_unblock_net)
        toolbar.addAction(action_unblock_net)

        toolbar.addSeparator()

        action_send_file = QAction("发送文件", self)
        action_send_file.triggered.connect(self._on_send_file)
        toolbar.addAction(action_send_file)

    def _create_central_widget(self):
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(8, 8, 8, 8)

        title = QLabel("学生列表")
        title.setStyleSheet("font-size: 14px; font-weight: bold; color: #ddd;")
        left_layout.addWidget(title)

        self.student_tree = QTreeWidget()
        self.student_tree.setHeaderLabels(["学生", "状态"])
        self.student_tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.student_tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.student_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.student_tree.customContextMenuRequested.connect(self._on_student_context_menu)
        left_layout.addWidget(self.student_tree)

        splitter.addWidget(left_panel)
        splitter.setStretchFactor(0, 1)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(8, 8, 8, 8)

        right_title = QLabel("屏幕墙")
        right_title.setStyleSheet("font-size: 14px; font-weight: bold; color: #ddd;")
        right_layout.addWidget(right_title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.wall_widget = QWidget()
        self.wall_layout = QGridLayout(self.wall_widget)
        self.wall_layout.setSpacing(8)
        self.wall_layout.setContentsMargins(4, 4, 4, 4)
        scroll.setWidget(self.wall_widget)
        right_layout.addWidget(scroll)

        splitter.addWidget(right_panel)
        splitter.setStretchFactor(1, 3)

        splitter.setSizes([250, 950])
        self.setCentralWidget(splitter)

    def _create_status_bar(self):
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        self.status_label = QLabel("初始化中...")
        self.status_bar.addWidget(self.status_label)

    def _setup_callbacks(self):
        self.server.on_student_added = self._on_student_added
        self.server.on_student_removed = self._on_student_removed
        self.server.on_student_changed = self._on_student_changed

    def _on_student_added(self, student: StudentInfo):
        self._refresh_student_list()
        self._refresh_screen_wall()
        self._update_status_bar()

    def _on_student_removed(self, student: StudentInfo):
        self._refresh_student_list()
        self._refresh_screen_wall()
        self._update_status_bar()

    def _on_student_changed(self):
        self._refresh_student_list()
        self._refresh_screen_wall_status()

    def _refresh_student_list(self):
        self.student_tree.clear()
        groups = self.server.student_manager.get_groups()
        for group in groups:
            group_item = QTreeWidgetItem(self.student_tree, [group, ""])
            students = self.server.student_manager.get_students_by_group(group)
            online_count = sum(1 for s in students if s.online)
            group_item.setText(1, f"{online_count}/{len(students)}")
            for student in students:
                status = "在线" if student.online else "离线"
                if student.status.get("black_screen"):
                    status += " 黑屏"
                if student.status.get("broadcasting"):
                    status += " 广播"
                if student.status.get("net_blocked"):
                    status += " 禁网"
                item = QTreeWidgetItem(group_item, [student.display_name, status])
                item.setData(0, Qt.ItemDataRole.UserRole, student.student_id)
                if not student.online:
                    item.setForeground(0, QBrush(QColor("#888")))
        self.student_tree.expandAll()

    def _refresh_screen_wall(self):
        for i in reversed(range(self.wall_layout.count())):
            self.wall_layout.itemAt(i).widget().setParent(None)
        self._thumb_widgets.clear()

        students = self.server.student_manager.get_all_students()
        cols = 4
        for idx, student in enumerate(students):
            thumb = StudentThumbWidget(student)
            row = idx // cols
            col = idx % cols
            self.wall_layout.addWidget(thumb, row, col)
            self._thumb_widgets[student.student_id] = thumb

    def _refresh_screen_wall_status(self):
        students = self.server.student_manager.get_all_students()
        for student in students:
            if student.student_id in self._thumb_widgets:
                self._thumb_widgets[student.student_id].update_student(student)

    def _get_selected_student_ids(self) -> List[str]:
        sids = []
        for item in self.student_tree.selectedItems():
            sid = item.data(0, Qt.ItemDataRole.UserRole)
            if sid:
                sids.append(sid)
        return sids

    def _on_selection_changed(self):
        self._selected_students = set(self._get_selected_student_ids())

    def _on_student_context_menu(self, pos):
        item = self.student_tree.itemAt(pos)
        if not item:
            return
        sid = item.data(0, Qt.ItemDataRole.UserRole)
        if not sid:
            return

        menu = QMenu(self)
        action_rename = menu.addAction("重命名")
        action_group = menu.addAction("移动到分组")
        menu.addSeparator()
        action_black = menu.addAction("黑屏")
        action_unblack = menu.addAction("解除黑屏")
        menu.addSeparator()
        action_broadcast = menu.addAction("开始广播")
        action_stop = menu.addAction("停止广播")
        menu.addSeparator()
        action_net_block = menu.addAction("禁用上网")
        action_net_unblock = menu.addAction("解除禁网")

        action = menu.exec(self.student_tree.viewport().mapToGlobal(pos))
        if action == action_rename:
            self._rename_student(sid)
        elif action == action_black:
            self.server.send_black_screen([sid], True)
        elif action == action_unblack:
            self.server.send_black_screen([sid], False)
        elif action == action_broadcast:
            self.server.start_broadcast([sid])
        elif action == action_stop:
            self.server.stop_broadcast([sid])
        elif action == action_net_block:
            self.server.send_net_control([sid], True)
        elif action == action_net_unblock:
            self.server.send_net_control([sid], False)

    def _rename_student(self, student_id: str):
        student = self.server.student_manager.get_student(student_id)
        if not student:
            return
        new_name, ok = QInputDialog.getText(
            self, "重命名学生", "请输入新名称:", text=student.display_name
        )
        if ok and new_name.strip():
            self.server.student_manager.set_display_name(student_id, new_name.strip())

    def _on_start_broadcast(self):
        sids = self._get_selected_student_ids()
        if not sids:
            reply = QMessageBox.question(
                self, "屏幕广播", "未选择学生，是否对全体在线学生开始广播？"
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            self.server.start_broadcast()
        else:
            self.server.start_broadcast(sids)

    def _on_stop_broadcast(self):
        sids = self._get_selected_student_ids()
        if not sids:
            self.server.stop_broadcast()
        else:
            self.server.stop_broadcast(sids)

    def _on_black_screen(self):
        sids = self._get_selected_student_ids()
        if not sids:
            reply = QMessageBox.question(
                self, "黑屏肃静", "未选择学生，是否对全体在线学生启用黑屏？"
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            self.server.send_black_screen_all(True)
        else:
            self.server.send_black_screen(sids, True)

    def _on_unblack_screen(self):
        sids = self._get_selected_student_ids()
        if not sids:
            self.server.send_black_screen_all(False)
        else:
            self.server.send_black_screen(sids, False)

    def _on_block_net(self):
        sids = self._get_selected_student_ids()
        if not sids:
            reply = QMessageBox.question(
                self, "禁用上网", "未选择学生，是否对全体在线学生禁用上网？"
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            self.server.send_net_control_all(True)
        else:
            self.server.send_net_control(sids, True)

    def _on_unblock_net(self):
        sids = self._get_selected_student_ids()
        if not sids:
            self.server.send_net_control_all(False)
        else:
            self.server.send_net_control(sids, False)

    def _on_send_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择文件", "", "所有文件 (*)")
        if not file_path:
            return
        sids = self._get_selected_student_ids()
        if not sids:
            reply = QMessageBox.question(
                self, "发送文件", "未选择学生，是否发送给全体在线学生？"
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            self.server.send_file_all(file_path)
        else:
            self.server.send_file(file_path, sids)
        QMessageBox.information(self, "发送文件", "文件已开始发送")

    def _update_status_bar(self):
        online = self.server.student_manager.online_count()
        total = self.server.student_manager.count()
        self.status_label.setText(f"在线学生: {online} / 总数: {total}")

    def closeEvent(self, event):
        reply = QMessageBox.question(
            self, "确认退出", "确定要退出教师端吗？"
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.server.stop()
            event.accept()
        else:
            event.ignore()
