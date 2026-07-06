import os
import threading

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QToolBar, QListWidget, QListWidgetItem, QTreeWidget, QTreeWidgetItem,
    QLabel, QStatusBar, QFileDialog, QMessageBox, QMenu, QInputDialog,
    QGridLayout, QScrollArea, QFrame, QPushButton, QTabWidget, QAbstractItemView,
    QAction, QSlider, QSizePolicy, QRubberBand
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QSize, QRect, QPoint, QEvent
from PyQt5.QtGui import QIcon, QPixmap, QFont, QColor, QBrush
from typing import List, Optional, Dict, Tuple
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
        self.setFrameShape(QFrame.Box)
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
        self.screen_label.setAlignment(Qt.AlignCenter)
        self.screen_label.setStyleSheet("background: #101010; color: #bdbdbd;")
        self.screen_label.setText("无画面")
        layout.addWidget(self.screen_label)

        self.name_label = QLabel(self.student.display_name)
        self.name_label.setAlignment(Qt.AlignCenter)
        self.name_label.setStyleSheet("color: #ffffff; font-size: 12px; font-weight: bold;")
        layout.addWidget(self.name_label)

        self.status_label = QLabel()
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("font-size: 10px;")
        self._update_status()
        layout.addWidget(self.status_label)

    def _update_status(self):
        statuses = []
        if not self.student.online:
            self.status_label.setStyleSheet("color: #b8b8b8; font-size: 10px;")
            statuses.append("离线")
        else:
            self.status_label.setStyleSheet("color: #35d06f; font-size: 10px;")
            statuses.append("在线")
        if self.student.status.get("black_screen"):
            statuses.append("黑屏")
        if self.student.status.get("broadcasting"):
            statuses.append("广播中")
        self.status_label.setText(" | ".join(statuses))

    def update_student(self, student: StudentInfo):
        self.student = student
        self.name_label.setText(student.display_name)
        self._update_status()

    def update_frame(self, frame_data: bytes):
        pixmap = QPixmap()
        if not pixmap.loadFromData(frame_data):
            return
        scaled = pixmap.scaled(
            self.screen_label.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )
        self.screen_label.setPixmap(scaled)
        self.screen_label.setText("")


class ScreenWallThumbWidget(QFrame):
    clicked = pyqtSignal(object, object)

    def __init__(self, student: StudentInfo, zoom: int = 100, parent=None):
        super().__init__(parent)
        self.student = student
        self._zoom = zoom
        self._last_pixmap = QPixmap()
        self._selected = False
        self._setup_ui()
        self.set_zoom(zoom)

    def _setup_ui(self):
        self.setObjectName("screenWallThumb")
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._apply_card_style()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.screen_label = QLabel()
        self.screen_label.setAlignment(Qt.AlignCenter)
        self.screen_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.screen_label.setStyleSheet("""
            background: #101010;
            color: #bdbdbd;
            border: 1px solid #343434;
            border-radius: 4px;
        """)
        self.screen_label.setText("无画面")
        layout.addWidget(self.screen_label)

        info_row = QHBoxLayout()
        info_row.setContentsMargins(0, 0, 0, 0)
        info_row.setSpacing(6)

        self.name_label = QLabel(self.student.display_name)
        self.name_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.name_label.setStyleSheet("color: #ffffff; font-size: 12px; font-weight: bold;")
        info_row.addWidget(self.name_label, 1)

        self.status_label = QLabel()
        self.status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        info_row.addWidget(self.status_label)
        layout.addLayout(info_row)

        self._update_status()

    def _update_status(self):
        statuses = []
        if not self.student.online:
            self.status_label.setStyleSheet("color: #b8b8b8; font-size: 11px;")
            statuses.append("离线")
        else:
            self.status_label.setStyleSheet("color: #35d06f; font-size: 11px;")
            statuses.append("在线")
        if self.student.status.get("black_screen"):
            statuses.append("黑屏")
        if self.student.status.get("broadcasting"):
            statuses.append("广播")
        self.status_label.setText(" | ".join(statuses))

    def update_student(self, student: StudentInfo):
        self.student = student
        self.name_label.setText(student.display_name)
        self._update_status()

    def set_selected(self, selected: bool):
        if self._selected == selected:
            return
        self._selected = selected
        self._apply_card_style()

    def _apply_card_style(self):
        border = "#2d8cff" if self._selected else "#3f3f3f"
        background = "#263247" if self._selected else "#242424"
        self.setStyleSheet(f"""
            QFrame#screenWallThumb {{
                background: {background};
                border: 2px solid {border};
                border-radius: 6px;
            }}
            QFrame#screenWallThumb:hover {{
                border: 2px solid #2d8cff;
            }}
        """)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.student.student_id, event)
            event.accept()
            return
        super().mousePressEvent(event)

    def update_frame(self, frame_data: bytes):
        pixmap = QPixmap()
        if not pixmap.loadFromData(frame_data):
            return
        self._last_pixmap = pixmap
        self._render_frame()

    def set_zoom(self, zoom: int):
        self._zoom = zoom
        card_width = max(190, int(260 * zoom / 100))
        preview_width = card_width - 16
        preview_height = max(100, int(preview_width * 9 / 16))
        card_height = preview_height + 52
        self.setFixedSize(card_width, card_height)
        self.screen_label.setFixedSize(preview_width, preview_height)
        self._render_frame()

    def card_width(self) -> int:
        return self.width() or max(190, int(260 * self._zoom / 100))

    def _render_frame(self):
        if self._last_pixmap.isNull():
            return
        scaled = self._last_pixmap.scaled(
            self.screen_label.size(),
            Qt.KeepAspectRatio,
            Qt.FastTransformation
        )
        self.screen_label.setPixmap(scaled)
        self.screen_label.setText("")


class TeacherMainWindow(QMainWindow):
    student_added = pyqtSignal(object)
    student_removed = pyqtSignal(object)
    student_changed = pyqtSignal()
    discovered_student = pyqtSignal(object)
    discovered_lost = pyqtSignal(object)
    student_screen_frame = pyqtSignal(object, object, object)
    file_transfer_progress = pyqtSignal(object, object, object)
    file_transfer_complete = pyqtSignal(object, object, object)
    connection_refresh_complete = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.server = TeacherServer()
        self._selected_students = set()
        self._thumb_widgets = {}
        self._discovered_cache: Dict[str, dict] = {}
        self._wall_zoom = 100
        self._wall_cols = 0
        self._wall_rubber_band = None
        self._wall_drag_origin = QPoint()
        self._syncing_selection = False
        self._file_transfers: Dict[str, dict] = {}
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
                color: #f5f5f5;
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
                color: #f5f5f5;
                border: 1px solid #333;
            }
            QHeaderView::section {
                background: #f2f2f2;
                color: #111;
                border: 1px solid #cfcfcf;
                padding: 4px;
            }
            QListWidget::item:selected, QTreeWidget::item:selected {
                background: #0078d4;
                color: #fff;
            }
            QTabWidget::pane {
                border: 1px solid #333;
                background: #252525;
            }
            QTabBar::tab {
                background: #2d2d2d;
                color: #d6d6d6;
                padding: 8px 16px;
                border: 1px solid #333;
                border-bottom: none;
            }
            QTabBar::tab:selected {
                background: #252525;
                color: #fff;
            }
            QPushButton {
                background: #0078d4;
                color: white;
                border: none;
                padding: 6px 12px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background: #1086e0;
            }
            QPushButton:disabled {
                background: #555;
                color: #888;
            }
            QStatusBar {
                background: #2d2d2d;
                color: #f0f0f0;
            }
            QLabel {
                color: #f2f2f2;
            }
            QScrollArea {
                background: #1e1e1e;
                border: none;
            }
            QMessageBox {
                background: #f6f6f6;
            }
            QMessageBox QLabel {
                color: #202020;
                background: transparent;
                font-size: 13px;
            }
            QMessageBox QPushButton {
                background: #0078d4;
                color: white;
                min-width: 72px;
                padding: 6px 14px;
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

        action_send_file = QAction("发送文件", self)
        action_send_file.triggered.connect(self._on_send_file)
        toolbar.addAction(action_send_file)

        action_push_update = QAction("推送更新", self)
        action_push_update.triggered.connect(self._on_push_update)
        toolbar.addAction(action_push_update)

        toolbar.addSeparator()

        action_scan = QAction("扫描设备", self)
        action_scan.triggered.connect(self._on_scan_devices)
        toolbar.addAction(action_scan)

        action_refresh = QAction("刷新连接", self)
        action_refresh.triggered.connect(self._on_refresh_connections)
        toolbar.addAction(action_refresh)

    def _create_central_widget(self):
        splitter = QSplitter(Qt.Horizontal)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.setSpacing(8)

        self.left_tabs = QTabWidget()

        student_tab = QWidget()
        student_layout = QVBoxLayout(student_tab)
        student_layout.setContentsMargins(4, 4, 4, 4)

        title = QLabel("学生列表")
        title.setStyleSheet("font-size: 14px; font-weight: bold; color: #ddd;")
        student_layout.addWidget(title)

        self.student_tree = QTreeWidget()
        self.student_tree.setHeaderLabels(["学生", "状态"])
        self.student_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.student_tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.student_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.student_tree.customContextMenuRequested.connect(self._on_student_context_menu)
        student_layout.addWidget(self.student_tree)

        self.left_tabs.addTab(student_tab, "学生列表")

        discover_tab = QWidget()
        discover_layout = QVBoxLayout(discover_tab)
        discover_layout.setContentsMargins(4, 4, 4, 4)
        discover_layout.setSpacing(6)

        discover_header = QHBoxLayout()
        discover_title = QLabel("待添加设备")
        discover_title.setStyleSheet("font-size: 14px; font-weight: bold; color: #ddd;")
        discover_header.addWidget(discover_title)
        discover_header.addStretch()
        self.btn_add_selected = QPushButton("添加选中")
        self.btn_add_selected.clicked.connect(self._on_add_selected_devices)
        self.btn_add_selected.setEnabled(False)
        discover_header.addWidget(self.btn_add_selected)
        discover_layout.addLayout(discover_header)

        self.discover_list = QListWidget()
        self.discover_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.discover_list.itemSelectionChanged.connect(self._on_discover_selection_changed)
        self.discover_list.itemDoubleClicked.connect(self._on_discover_double_click)
        discover_layout.addWidget(self.discover_list)

        self.discover_status = QLabel("扫描中...")
        self.discover_status.setStyleSheet("color: #888; font-size: 11px;")
        discover_layout.addWidget(self.discover_status)

        self.left_tabs.addTab(discover_tab, "扫描设备")

        left_layout.addWidget(self.left_tabs)

        splitter.addWidget(left_panel)
        splitter.setStretchFactor(0, 1)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(8, 8, 8, 8)
        right_layout.setSpacing(8)

        right_title = QLabel("屏幕墙")
        right_title.setStyleSheet("font-size: 14px; font-weight: bold; color: #ddd;")
        right_layout.addWidget(right_title)

        wall_controls = QHBoxLayout()
        wall_controls.setContentsMargins(0, 0, 0, 0)
        wall_controls.setSpacing(8)

        self.wall_summary_label = QLabel("0 台在线")
        self.wall_summary_label.setStyleSheet("color: #888; font-size: 12px;")
        wall_controls.addWidget(self.wall_summary_label)
        wall_controls.addStretch()

        zoom_title = QLabel("缩放")
        zoom_title.setStyleSheet("color: #aaa; font-size: 12px;")
        wall_controls.addWidget(zoom_title)

        self.wall_zoom_slider = QSlider(Qt.Horizontal)
        self.wall_zoom_slider.setRange(70, 180)
        self.wall_zoom_slider.setSingleStep(10)
        self.wall_zoom_slider.setPageStep(10)
        self.wall_zoom_slider.setValue(self._wall_zoom)
        self.wall_zoom_slider.setFixedWidth(150)
        self.wall_zoom_slider.valueChanged.connect(self._on_wall_zoom_changed)
        wall_controls.addWidget(self.wall_zoom_slider)

        self.wall_zoom_label = QLabel(f"{self._wall_zoom}%")
        self.wall_zoom_label.setMinimumWidth(42)
        self.wall_zoom_label.setStyleSheet("color: #ddd; font-size: 12px;")
        wall_controls.addWidget(self.wall_zoom_label)

        right_layout.addLayout(wall_controls)

        self.wall_scroll = QScrollArea()
        self.wall_scroll.setWidgetResizable(True)
        self.wall_widget = QWidget()
        self.wall_widget.setStyleSheet("background: #1e1e1e;")
        self.wall_layout = QGridLayout(self.wall_widget)
        self.wall_layout.setSpacing(10)
        self.wall_layout.setContentsMargins(8, 8, 8, 8)
        self.wall_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.wall_widget.installEventFilter(self)
        self.wall_scroll.setWidget(self.wall_widget)
        right_layout.addWidget(self.wall_scroll)

        splitter.addWidget(right_panel)
        splitter.setStretchFactor(1, 3)

        splitter.setSizes([250, 950])
        self.setCentralWidget(splitter)

    def _create_status_bar(self):
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        self.status_label = QLabel("初始化中...")
        self.status_bar.addWidget(self.status_label)

        self.transfer_label = QLabel("")
        self.transfer_label.setMinimumWidth(260)
        self.transfer_label.setStyleSheet("color: #ddd;")
        self.status_bar.addPermanentWidget(self.transfer_label)

    def _setup_callbacks(self):
        self.server.on_student_added = self.student_added.emit
        self.server.on_student_removed = self.student_removed.emit
        self.server.on_student_changed = self.student_changed.emit
        self.server.on_discovered_student = self.discovered_student.emit
        self.server.on_discovered_lost = self.discovered_lost.emit
        self.server.on_student_screen_frame = self.student_screen_frame.emit
        self.server.file_distributor.on_progress = self.file_transfer_progress.emit
        self.server.file_distributor.on_complete = self.file_transfer_complete.emit

        self.student_added.connect(self._on_student_added)
        self.student_removed.connect(self._on_student_removed)
        self.student_changed.connect(self._on_student_changed)
        self.discovered_student.connect(self._on_discovered_student)
        self.discovered_lost.connect(self._on_discovered_lost)
        self.student_screen_frame.connect(self._on_student_screen_frame)
        self.file_transfer_progress.connect(self._on_file_transfer_progress)
        self.file_transfer_complete.connect(self._on_file_transfer_complete)
        self.connection_refresh_complete.connect(self._on_connection_refresh_complete)

    def _on_student_added(self, student: StudentInfo):
        sid = student.student_id
        if sid in self._discovered_cache:
            del self._discovered_cache[sid]
            self._refresh_discover_list()
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
                item = QTreeWidgetItem(group_item, [student.display_name, status])
                item.setData(0, Qt.UserRole, student.student_id)
                if not student.online:
                    item.setForeground(0, QBrush(QColor("#888")))
        self.student_tree.expandAll()
        self._sync_tree_selection_from_selected()

    def _refresh_screen_wall(self):
        self._clear_wall_layout()
        self._thumb_widgets.clear()

        students = self.server.student_manager.get_all_students()
        cols = self._calculate_wall_columns()
        for idx, student in enumerate(students):
            thumb = ScreenWallThumbWidget(student, zoom=self._wall_zoom)
            thumb.clicked.connect(self._on_wall_thumb_clicked)
            thumb.setContextMenuPolicy(Qt.CustomContextMenu)
            thumb.customContextMenuRequested.connect(
                lambda pos, sid=student.student_id, widget=thumb:
                    self._on_wall_context_menu(widget.mapToGlobal(pos), sid)
            )
            thumb.set_selected(student.student_id in self._selected_students)
            row = idx // cols
            col = idx % cols
            self.wall_layout.addWidget(thumb, row, col)
            self._thumb_widgets[student.student_id] = thumb
        self._wall_cols = cols
        self._update_wall_summary()

    def _refresh_screen_wall_status(self):
        students = self.server.student_manager.get_all_students()
        for student in students:
            if student.student_id in self._thumb_widgets:
                thumb = self._thumb_widgets[student.student_id]
                thumb.update_student(student)
                thumb.set_selected(student.student_id in self._selected_students)
        self._update_wall_summary()

    def _on_student_screen_frame(self, student_id: str, frame_data: bytes, frame_size: tuple):
        thumb = self._thumb_widgets.get(student_id)
        if thumb:
            thumb.update_frame(frame_data)

    def _on_file_transfer_progress(self, transfer_id: str, sent_bytes: int, total_bytes: int):
        info = self._file_transfers.setdefault(transfer_id, {
            "file_name": "文件",
            "target_count": 0,
        })
        info["sent_bytes"] = int(sent_bytes)
        info["total_bytes"] = int(total_bytes)
        info["status"] = "sending"
        self._update_transfer_status_label(transfer_id)

    def _on_file_transfer_complete(self, transfer_id: str, success: bool, error: str):
        info = self._file_transfers.setdefault(transfer_id, {
            "file_name": "文件",
            "target_count": 0,
        })
        info["status"] = "done" if success else "failed"
        info["error"] = error
        self._update_transfer_status_label(transfer_id)
        QTimer.singleShot(6000, self._clear_completed_transfer_status)

    def _register_file_transfer(self, transfer_id: str, file_path: str, target_count: int):
        old_info = self._file_transfers.get(transfer_id, {})
        if old_info.get("status") in {"done", "failed"}:
            return
        self._file_transfers[transfer_id] = {
            "file_name": os.path.basename(file_path),
            "target_count": target_count,
            "sent_bytes": old_info.get("sent_bytes", 0),
            "total_bytes": os.path.getsize(file_path) if os.path.exists(file_path) else 0,
            "status": old_info.get("status", "sending"),
            "error": old_info.get("error", ""),
        }
        self._update_transfer_status_label(transfer_id)

    def _update_transfer_status_label(self, transfer_id: str):
        info = self._file_transfers.get(transfer_id)
        if not info or not hasattr(self, "transfer_label"):
            return
        total = max(0, int(info.get("total_bytes", 0)))
        sent = max(0, int(info.get("sent_bytes", 0)))
        percent = int(sent * 100 / total) if total > 0 else 0
        target_count = int(info.get("target_count", 0))
        file_name = info.get("file_name", "文件")
        status = info.get("status", "sending")
        if status == "done":
            self.transfer_label.setText(f"文件传输完成: {file_name} -> {target_count} 台")
        elif status == "failed":
            self.transfer_label.setText(f"文件传输失败: {file_name} {info.get('error', '')}")
        else:
            self.transfer_label.setText(f"正在传输: {file_name} {percent}% -> {target_count} 台")

    def _clear_completed_transfer_status(self):
        if not hasattr(self, "transfer_label"):
            return
        active = [
            (tid, info) for tid, info in self._file_transfers.items()
            if info.get("status") == "sending"
        ]
        if active:
            self._update_transfer_status_label(active[-1][0])
        else:
            self.transfer_label.setText("")

    def _on_wall_zoom_changed(self, value: int):
        self._wall_zoom = value
        self.wall_zoom_label.setText(f"{value}%")
        for thumb in self._thumb_widgets.values():
            thumb.set_zoom(value)
        self._relayout_screen_wall()

    def _calculate_wall_columns(self) -> int:
        viewport_width = max(1, self.wall_scroll.viewport().width() - 20)
        card_width = max(190, int(260 * self._wall_zoom / 100))
        spacing = self.wall_layout.spacing()
        return max(1, viewport_width // (card_width + spacing))

    def _relayout_screen_wall(self):
        students = self.server.student_manager.get_all_students()
        if not students:
            self._update_wall_summary()
            return
        cols = self._calculate_wall_columns()
        if cols == self._wall_cols and self.wall_layout.count() == len(students):
            self._update_wall_summary()
            return

        for idx, student in enumerate(students):
            thumb = self._thumb_widgets.get(student.student_id)
            if not thumb:
                continue
            self.wall_layout.removeWidget(thumb)
            self.wall_layout.addWidget(thumb, idx // cols, idx % cols)
        self._wall_cols = cols
        self._update_wall_summary()

    def _clear_wall_layout(self):
        for i in reversed(range(self.wall_layout.count())):
            widget = self.wall_layout.itemAt(i).widget()
            if widget:
                self.wall_layout.removeWidget(widget)
                widget.setParent(None)

    def _update_wall_summary(self):
        students = self.server.student_manager.get_all_students()
        online = sum(1 for s in students if s.online)
        self.wall_summary_label.setText(f"{online} 台在线 / {len(students)} 台总数")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "wall_scroll"):
            QTimer.singleShot(0, self._relayout_screen_wall)

    def eventFilter(self, watched, event):
        if watched is getattr(self, "wall_widget", None):
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                child = self.wall_widget.childAt(event.pos())
                if child and child is not self.wall_widget:
                    return False
                self._wall_drag_origin = event.pos()
                if self._wall_rubber_band is None:
                    self._wall_rubber_band = QRubberBand(QRubberBand.Rectangle, self.wall_widget)
                self._wall_rubber_band.setGeometry(QRect(self._wall_drag_origin, QSize()))
                self._wall_rubber_band.show()
                return True

            if event.type() == QEvent.MouseMove and self._wall_rubber_band and self._wall_rubber_band.isVisible():
                rect = QRect(self._wall_drag_origin, event.pos()).normalized()
                self._wall_rubber_band.setGeometry(rect)
                return True

            if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                if self._wall_rubber_band and self._wall_rubber_band.isVisible():
                    rect = self._wall_rubber_band.geometry()
                    self._wall_rubber_band.hide()
                    selected = set(self._selected_students) if event.modifiers() & Qt.ControlModifier else set()
                    for sid, thumb in self._thumb_widgets.items():
                        if rect.intersects(thumb.geometry()):
                            selected.add(sid)
                    self._set_selected_students(selected)
                    return True

        return super().eventFilter(watched, event)

    def _get_selected_student_ids(self) -> List[str]:
        return list(self._selected_students)

    def _on_selection_changed(self):
        if self._syncing_selection:
            return
        if self.student_tree.topLevelItemCount() == 0:
            return
        self._selected_students = set(self._get_selected_student_ids())
        selected = set()
        for item in self.student_tree.selectedItems():
            sid = item.data(0, Qt.UserRole)
            if sid:
                selected.add(sid)
        self._selected_students = selected
        self._sync_wall_selection_from_selected()

    def _sync_wall_selection_from_selected(self):
        for sid, thumb in self._thumb_widgets.items():
            thumb.set_selected(sid in self._selected_students)

    def _sync_tree_selection_from_selected(self):
        if not hasattr(self, "student_tree"):
            return
        self._syncing_selection = True
        try:
            self.student_tree.clearSelection()
            root_count = self.student_tree.topLevelItemCount()
            for i in range(root_count):
                group_item = self.student_tree.topLevelItem(i)
                for j in range(group_item.childCount()):
                    item = group_item.child(j)
                    sid = item.data(0, Qt.UserRole)
                    if sid in self._selected_students:
                        item.setSelected(True)
        finally:
            self._syncing_selection = False

    def _set_selected_students(self, student_ids, sync_tree: bool = True):
        self._selected_students = set(student_ids)
        self._sync_wall_selection_from_selected()
        if sync_tree:
            self._sync_tree_selection_from_selected()

    def _on_wall_thumb_clicked(self, student_id: str, event):
        modifiers = event.modifiers()
        selected = set(self._selected_students)
        if modifiers & Qt.ControlModifier:
            if student_id in selected:
                selected.remove(student_id)
            else:
                selected.add(student_id)
        elif modifiers & Qt.ShiftModifier:
            selected.add(student_id)
        else:
            selected = {student_id}
        self._set_selected_students(selected)

    def _on_student_context_menu(self, pos):
        item = self.student_tree.itemAt(pos)
        if not item:
            return
        sid = item.data(0, Qt.UserRole)
        if not sid:
            return
        if sid not in self._selected_students:
            self._set_selected_students({sid})
        self._show_student_control_menu(
            self.student_tree.viewport().mapToGlobal(pos),
            list(self._selected_students)
        )
        return

    def _on_wall_context_menu(self, global_pos, student_id: str):
        if student_id not in self._selected_students:
            self._set_selected_students({student_id})
        self._show_student_control_menu(global_pos, list(self._selected_students))

    def _show_student_control_menu(self, global_pos, student_ids: List[str]):
        if not student_ids:
            return
        menu = QMenu(self)
        action_rename = menu.addAction("重命名")
        action_rename.setEnabled(len(student_ids) == 1)
        menu.addSeparator()
        action_black = menu.addAction("黑屏肃静")
        action_unblack = menu.addAction("解除黑屏")
        menu.addSeparator()
        action_broadcast = menu.addAction("开始广播")
        action_stop = menu.addAction("停止广播")

        action = menu.exec(global_pos)
        if action == action_rename:
            self._rename_student(student_ids[0])
        elif action == action_black:
            self.server.send_black_screen(student_ids, True)
        elif action == action_unblack:
            self.server.send_black_screen(student_ids, False)
        elif action == action_broadcast:
            self.server.start_broadcast(student_ids)
        elif action == action_stop:
            self.server.stop_broadcast(student_ids)

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
            if reply != QMessageBox.Yes:
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
            if reply != QMessageBox.Yes:
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
        logger.info("Network control is disabled in this build")

    def _on_unblock_net(self):
        logger.info("Network control is disabled in this build")

    def _choose_file_target_dir(self) -> Optional[Tuple[str, str]]:
        options = [
            ("学生端默认目录", "default", ""),
            ("学生端桌面", "desktop", ""),
            ("学生端下载目录", "downloads", ""),
            ("学生端文档目录", "documents", ""),
            ("C:\\LanClassroom\\Downloads", "c_downloads", ""),
            ("自定义路径...", "custom", ""),
        ]
        labels = [item[0] for item in options]
        label, ok = QInputDialog.getItem(
            self,
            "选择保存位置",
            "保存到学生端:",
            labels,
            1,
            False
        )
        if not ok:
            return None

        for option_label, target_type, target_path in options:
            if option_label == label:
                if target_type == "custom":
                    target_path, ok = QInputDialog.getText(
                        self,
                        "自定义保存路径",
                        "请输入学生端绝对路径:",
                        text="C:\\LanClassroom\\Downloads"
                    )
                    if not ok or not target_path.strip():
                        return None
                    return target_type, target_path.strip()
                return target_type, target_path
        return "default", ""

    def _on_send_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择文件", "", "所有文件 (*)")
        if not file_path:
            return
        target_dir = self._choose_file_target_dir()
        if target_dir is None:
            return
        target_dir_type, target_dir_path = target_dir
        sids = self._get_selected_student_ids()
        if not sids:
            reply = QMessageBox.question(
                self, "发送文件", "未选择学生，是否发送给全体在线学生？"
            )
            if reply != QMessageBox.Yes:
                return
            target_count = len(self.server.student_manager.get_online_students())
            transfer_id = self.server.send_file_all(
                file_path,
                target_dir_type=target_dir_type,
                target_dir_path=target_dir_path
            )
            self._register_file_transfer(transfer_id, file_path, target_count)
        else:
            target_count = len(sids)
            transfer_id = self.server.send_file(
                file_path,
                sids,
                target_dir_type=target_dir_type,
                target_dir_path=target_dir_path
            )
            self._register_file_transfer(transfer_id, file_path, target_count)
        QMessageBox.information(self, "发送文件", "文件已开始发送")

    def _on_push_update(self):
        # 1. 选择更新包 zip
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择更新包", "", "更新包 (*.zip);;所有文件 (*)"
        )
        if not file_path:
            return
        # 2. 输入版本号
        version, ok = QInputDialog.getText(
            self, "推送更新", "请输入新版本号:", text="1.0.8"
        )
        if not ok or not version.strip():
            return
        version = version.strip()
        # 3. 确认推送范围
        sids = self._get_selected_student_ids()
        if not sids:
            reply = QMessageBox.question(
                self, "推送更新",
                f"未选择学生，是否推送给全体在线学生？\n版本: {version}\n文件: {file_path}"
            )
            if reply != QMessageBox.Yes:
                return
            count = self.server.push_update(file_path, version)
        else:
            reply = QMessageBox.question(
                self, "推送更新",
                f"确认向选中的 {len(sids)} 名学生推送更新？\n版本: {version}\n文件: {file_path}"
            )
            if reply != QMessageBox.Yes:
                return
            count = self.server.push_update(file_path, version, sids)
        QMessageBox.information(
            self, "推送更新",
            f"已通知 {count} 名学生有新版本 {version}\n"
            f"学生端会自动下载并安装重启。"
        )

    def _update_status_bar(self):
        online = self.server.student_manager.online_count()
        total = self.server.student_manager.count()
        discovered = len(self._discovered_cache)
        self.status_label.setText(f"在线学生: {online} / 总数: {total} | 发现设备: {discovered}")

    def _on_scan_devices(self):
        self.left_tabs.setCurrentIndex(1)
        self.discover_status.setText("正在扫描局域网...")
        self.server.scan_devices()
        QTimer.singleShot(3000, self._update_discover_status_text)

    def _on_refresh_connections(self):
        self.status_label.setText("正在检测学生端连接...")

        def worker():
            try:
                result = self.server.refresh_connections()
            except Exception as e:
                logger.warning(f"Refresh connections failed: {e}")
                result = {"error": str(e), "checked": 0, "alive": 0, "offline": 0}
            self.connection_refresh_complete.emit(result)

        threading.Thread(target=worker, daemon=True).start()

    def _on_connection_refresh_complete(self, result: dict):
        self._refresh_student_list()
        self._refresh_screen_wall_status()
        self._update_status_bar()
        if result.get("error"):
            self.status_label.setText(f"连接检测失败: {result['error']}")
            return
        self.status_label.setText(
            f"连接检测完成: 已检查 {result.get('checked', 0)} 台，"
            f"在线响应 {result.get('alive', 0)} 台，标记离线 {result.get('offline', 0)} 台"
        )
        QTimer.singleShot(3500, self._update_status_bar)

    def _update_discover_status_text(self):
        count = len(self._discovered_cache)
        self.discover_status.setText(f"已发现 {count} 台设备")

    def _on_discovered_student(self, info: dict):
        sid = info.get("student_id", "")
        if not sid:
            return
        if self.server.student_manager.get_student(sid):
            return
        self._discovered_cache[sid] = info
        self._refresh_discover_list()
        self._update_status_bar()

    def _on_discovered_lost(self, info: dict):
        sid = info.get("student_id", "")
        if sid in self._discovered_cache:
            del self._discovered_cache[sid]
            self._refresh_discover_list()
            self._update_status_bar()

    def _refresh_discover_list(self):
        self.discover_list.clear()
        for info in self._discovered_cache.values():
            hostname = info.get("hostname", "unknown")
            ip = info.get("ip", "")
            mac = info.get("mac", "")
            item = QListWidgetItem(f"{hostname}  ({ip})")
            item.setData(Qt.UserRole, info)
            item.setToolTip(f"主机名: {hostname}\nIP: {ip}\nMAC: {mac}")
            self.discover_list.addItem(item)
        count = len(self._discovered_cache)
        self.discover_status.setText(f"已发现 {count} 台设备")

    def _on_discover_selection_changed(self):
        items = self.discover_list.selectedItems()
        self.btn_add_selected.setEnabled(len(items) > 0)

    def _on_discover_double_click(self, item):
        info = item.data(Qt.UserRole)
        if info:
            self._add_discovered_device(info)

    def _on_add_selected_devices(self):
        items = self.discover_list.selectedItems()
        if not items:
            return
        added = 0
        for item in items:
            info = item.data(Qt.UserRole)
            if info:
                self._add_discovered_device(info)
                added += 1
        if added > 0:
            QMessageBox.information(self, "添加设备", f"已添加 {added} 台设备")

    def _add_discovered_device(self, info: dict):
        ip = info.get("ip", "")
        if not ip:
            return
        logger.info(f"Adding discovered device: {info.get('hostname')} ({ip})")
        self.server.add_discovered_student(info)
        self.discover_status.setText(f"已请求 {info.get('hostname')} 连接，请稍候...")

    def closeEvent(self, event):
        reply = QMessageBox.question(
            self, "确认退出", "确定要退出教师端吗？"
        )
        if reply == QMessageBox.Yes:
            self.server.stop()
            event.accept()
        else:
            event.ignore()
