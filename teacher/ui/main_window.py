"""v2 teacher console: seat-first classroom operations for 20-60 PCs."""

from __future__ import annotations

import csv
import json
import os
import ntpath
from datetime import datetime
from typing import Dict, List, Optional

from PyQt5.QtCore import QObject, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QCloseEvent, QPalette
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from common.config import get_config
from common.application_policy import normalize_program_name
from common.logger import get_logger
from common.version import get_version
from common.v2.model import (
    ClassroomAction,
    CommandKind,
    DeviceIds,
    InspectionMode,
    TargetScope,
)
from teacher.core.runtime import TeacherRuntime
from teacher.ui.theme import stylesheet
from teacher.ui.widgets import (
    DeviceNameWidget,
    FileDropTable,
    FullScreenViewer,
    OperationDrawer,
    PatrolCanvas,
    PatrolCard,
    SeatMapWidget,
    SeatTile,
    ToggleSwitch,
)

logger = get_logger("teacher_ui")


class UiBridge(QObject):
    transfer_progress = pyqtSignal(str, int, int)
    transfer_complete = pyqtSignal(str, bool, str)


class TeacherMainWindow(QMainWindow):
    NAV_ITEMS = (
        ("巡视", "inspection"),
        ("文件", "files"),
        ("应用策略", "policies"),
        ("设备维护", "maintenance"),
        ("操作记录", "audit"),
        ("设置", "settings"),
    )

    def __init__(self, runtime: Optional[TeacherRuntime] = None, auto_start: bool = True):
        super().__init__()
        self.config = get_config()
        self.runtime = runtime or TeacherRuntime()
        self._owns_runtime = runtime is None
        self._last_revision = -1
        self._snapshot = None
        self._selected_ids = set()
        self._device_signature = None
        self._patrol_cards: Dict[str, PatrolCard] = {}
        self._patrol_target_ids = set()
        self._viewers: Dict[str, FullScreenViewer] = {}
        self._file_rows: Dict[str, int] = {}
        self._process_command_id = None
        self._processes: List[dict] = []
        self._network_command_id = None
        self._active_page = "inspection"
        self._auto_preview = auto_start
        self._preview_seen = set()
        self.bridge = UiBridge()

        self.setWindowTitle("局域网机房控制系统 · 教师端")
        self.setMinimumSize(1000, 600)
        self.resize(1440, 860)
        self._build_ui()
        self._show_page("inspection")
        self._connect_runtime()
        self.apply_theme(str(self.config.get("ui.theme", "light")))

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(350)
        self.poll_timer.timeout.connect(self._poll_runtime)
        self.poll_timer.start()
        if auto_start:
            try:
                self.runtime.start()
            except Exception as exc:
                logger.error(f"Teacher runtime start failed: {exc}")
                QMessageBox.critical(self, "启动失败", f"教师服务无法启动：\n{exc}")

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_sidebar())
        work = QWidget()
        work_layout = QVBoxLayout(work)
        work_layout.setContentsMargins(0, 0, 0, 0)
        work_layout.setSpacing(0)
        work_layout.addWidget(self._build_command_bar())
        summary = QHBoxLayout()
        summary.setContentsMargins(18, 3, 18, 3)
        self.view_switch = QComboBox()
        self.view_switch.addItem("屏幕视图", "inspection")
        self.view_switch.addItem("IP 座位视图", "classroom")
        self.view_switch.currentIndexChanged.connect(lambda: self._show_page(self.view_switch.currentData()))
        summary.addWidget(self.view_switch)
        self.target_summary = QPushButton("已选 0 台")
        self.target_summary.setProperty("compact", True)
        self.target_summary.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.target_summary.clicked.connect(self._show_targets)
        summary.addWidget(self.target_summary, 1)
        work_layout.addLayout(summary)
        self.pages = QStackedWidget()
        self.page_indexes = {}
        builders = {
            "classroom": self._build_classroom_page,
            "inspection": self._build_inspection_page,
            "files": self._build_files_page,
            "policies": self._build_policy_page,
            "maintenance": self._build_maintenance_page,
            "audit": self._build_audit_page,
            "settings": self._build_settings_page,
        }
        for key, builder in builders.items():
            self.page_indexes[key] = self.pages.addWidget(builder())
        work_layout.addWidget(self.pages, 1)
        outer.addWidget(work, 1)
        self.operation_drawer = OperationDrawer()
        outer.addWidget(self.operation_drawer)

        status = QStatusBar()
        self.connection_status = QLabel("0 台在线")
        self.scope_status = QLabel("作用域：已选 0 台")
        self.operation_status = QLabel("就绪")
        self.operation_status.setProperty("muted", True)
        status.addWidget(self.connection_status)
        status.addWidget(self.scope_status)
        status.addPermanentWidget(self.operation_status, 1)
        self.setStatusBar(status)

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(184)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 16, 12, 14)
        layout.setSpacing(5)
        brand = QLabel("机房控制台")
        brand.setObjectName("Brand")
        brand.setContentsMargins(8, 4, 0, 14)
        layout.addWidget(brand)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for index, (label, key) in enumerate(self.NAV_ITEMS):
            button = QPushButton(label)
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.setProperty("page", key)
            button.clicked.connect(lambda _checked, value=key: self._show_page(value))
            self.nav_group.addButton(button)
            layout.addWidget(button)
            if index == 0:
                button.setChecked(True)
        layout.addStretch()
        self.version_label = QLabel(f"v{get_version()}")
        self.version_label.setProperty("muted", True)
        self.version_label.setContentsMargins(8, 0, 0, 0)
        layout.addWidget(self.version_label)
        return sidebar

    def _build_command_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("CommandBar")
        bar.setFixedHeight(62)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(18, 10, 18, 10)
        layout.setSpacing(8)
        scope_label = QLabel("作用域")
        scope_label.setProperty("muted", True)
        layout.addWidget(scope_label)
        self.scope_group = QButtonGroup(self)
        self.scope_group.setExclusive(True)
        self.scope_buttons = {}
        for key, label in (("all", "全班"), ("group", "分组"), ("selected", "已选")):
            button = QPushButton(label)
            button.setObjectName("ScopeButton")
            button.setCheckable(True)
            button.clicked.connect(self._scope_changed)
            self.scope_group.addButton(button)
            self.scope_buttons[key] = button
            layout.addWidget(button)
        self.scope_buttons["selected"].setChecked(True)
        self.group_combo = QComboBox()
        self.group_combo.setFixedWidth(120)
        self.group_combo.setVisible(False)
        self.group_combo.currentTextChanged.connect(self._scope_changed)
        layout.addWidget(self.group_combo)
        layout.addSpacing(8)
        layout.addWidget(self._vertical_divider())
        actions = (
            ("开始广播", self._start_broadcast, True, False),
            ("停止广播", self._stop_broadcast, False, False),
            ("黑屏肃静", self._start_silence, False, False),
            ("禁止外网", self._block_network, False, False),
            ("全部恢复", self._restore_all, False, True),
        )
        for text, callback, primary, danger in actions:
            button = QPushButton(text)
            button.setProperty("primary", primary)
            button.setProperty("danger", danger)
            button.clicked.connect(callback)
            layout.addWidget(button)
        layout.addStretch()
        self.drawer_button = QPushButton("操作")
        self.drawer_button.setCheckable(True)
        self.drawer_button.setChecked(True)
        self.drawer_button.toggled.connect(self.operation_drawer_visibility)
        layout.addWidget(self.drawer_button)
        return bar

    def _page_shell(self, title: str, right_widget: QWidget = None):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 18)
        layout.setSpacing(12)
        header = QWidget()
        header.setObjectName("PageHeader")
        row = QHBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 0)
        label = QLabel(title)
        label.setObjectName("PageTitle")
        row.addWidget(label)
        row.addStretch()
        if right_widget:
            row.addWidget(right_widget)
        layout.addWidget(header)
        return page, layout

    def _build_classroom_page(self) -> QWidget:
        search = QLineEdit()
        search.setPlaceholderText("搜索主机名、备注或 IP")
        search.setFixedWidth(240)
        search.textChanged.connect(self._refresh_devices)
        actions = QWidget()
        action_row = QHBoxLayout(actions)
        action_row.setContentsMargins(0, 0, 0, 0)
        set_group = QPushButton("设置分组")
        set_group.clicked.connect(self._set_selected_group)
        action_row.addWidget(search)
        action_row.addWidget(set_group)
        page, layout = self._page_shell("教室", actions)
        hint = QLabel("单击座位选机，Ctrl + 单击可增减选择。")
        hint.setProperty("muted", True)
        layout.addWidget(hint)
        self.seat_map = SeatMapWidget(
            int(self.config.get("ui.seat_columns", 6)),
            int(self.config.get("ui.seat_rows", 10)),
        )
        self.seat_map.selection_changed.connect(self._selection_changed)
        self.seat_map.menu_requested.connect(self._device_menu)
        self.seat_map.positions_changed.connect(self._save_seat_positions)
        self.seat_map.copy_requested.connect(self._copy_device_name)
        self.seat_map.note_requested.connect(self._edit_device_note)
        self.classroom_search = search
        layout.addWidget(self.seat_map, 1)
        return page

    def _build_inspection_page(self) -> QWidget:
        actions = QWidget()
        row = QHBoxLayout(actions)
        row.setContentsMargins(0, 0, 0, 0)
        start = QPushButton("开始巡视")
        start.setProperty("primary", True)
        start.clicked.connect(self._start_inspection)
        stop = QPushButton("停止巡视")
        stop.clicked.connect(self._stop_inspection)
        group = QPushButton("设置分组")
        group.clicked.connect(self._set_selected_group)
        row.addWidget(group)
        row.addWidget(start)
        row.addWidget(stop)
        page, layout = self._page_shell("巡视", actions)
        layout.addWidget(QLabel("单击画面选一台，拖动框选多台，Ctrl 可增减选择。"))
        self.patrol_scroll = QScrollArea()
        self.patrol_scroll.setWidgetResizable(True)
        self.patrol_canvas = PatrolCanvas()
        self.patrol_canvas.selection_requested.connect(self._patrol_selection)
        self.patrol_grid = QGridLayout(self.patrol_canvas)
        self.patrol_grid.setContentsMargins(0, 0, 0, 0)
        self.patrol_grid.setSpacing(12)
        self.patrol_scroll.setWidget(self.patrol_canvas)
        layout.addWidget(self.patrol_scroll, 1)
        return page

    def _build_files_page(self) -> QWidget:
        actions = QWidget()
        row = QHBoxLayout(actions)
        row.setContentsMargins(0, 0, 0, 0)
        add_file = QPushButton("添加文件")
        add_file.clicked.connect(self._add_files)
        add_folder = QPushButton("添加文件夹")
        add_folder.clicked.connect(self._add_folder)
        send = QPushButton("开始分发")
        send.setProperty("primary", True)
        send.clicked.connect(self._send_queue)
        row.addWidget(add_file)
        row.addWidget(add_folder)
        row.addWidget(send)
        page, layout = self._page_shell("文件", actions)
        self.file_table = self._table(("名称", "类型", "大小", "进度", "状态"), FileDropTable)
        self.file_table.paths_dropped.connect(self._queue_paths)
        self.file_table.setToolTip("拖入文件或文件夹加入列表，再点击“开始分发”发送")
        layout.addWidget(self.file_table, 1)
        self.file_hint = QLabel("拖入文件或文件夹加入列表，点击“开始分发”发送 · 接收位置：桌面 / 课堂文件")
        self.file_hint.setWordWrap(True)
        self.file_hint.setProperty("muted", True)
        layout.addWidget(self.file_hint)
        self.file_queue: List[dict] = []
        return page

    def _build_policy_page(self) -> QWidget:
        controls = QWidget()
        control_layout = QHBoxLayout(controls)
        control_layout.setContentsMargins(0, 0, 0, 0)
        control_layout.addWidget(QLabel("软件禁用"))
        self.application_control_status = QLabel("已开启")
        self.application_control_status.setProperty("muted", True)
        control_layout.addWidget(self.application_control_status)
        self.application_control_switch = ToggleSwitch()
        self.application_control_switch.setAccessibleName("软件禁用总开关")
        self.application_control_switch.setToolTip("关闭后暂停全部软件限制，规则保留；重新开启可恢复")
        self.application_control_switch.setChecked(True)
        self.application_control_switch.toggled.connect(self._toggle_application_control)
        control_layout.addWidget(self.application_control_switch)
        page, layout = self._page_shell("应用策略", controls)
        self.policy_tabs = QTabWidget()
        self.policy_tabs.setObjectName("ApplicationTabs")
        rules_page = QWidget()
        rules_layout = QVBoxLayout(rules_page)
        rules_layout.setContentsMargins(16, 16, 16, 16)
        rules_layout.setSpacing(12)
        composer = QFrame()
        composer.setObjectName("ApplicationComposer")
        composer.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        composer_layout = QVBoxLayout(composer)
        composer_layout.setContentsMargins(14, 12, 14, 12)
        title = QLabel("禁止软件运行")
        title.setObjectName("SectionTitle")
        composer_layout.addWidget(title)
        hint = QLabel("填写程序名即可禁用，无需先找某台电脑的进程；不同安装位置同样生效。")
        hint.setProperty("muted", True)
        hint.setWordWrap(True)
        composer_layout.addWidget(hint)
        form = QHBoxLayout()
        form.setSpacing(8)
        self.application_program = QLineEdit()
        self.application_program.setPlaceholderText("程序名，例如 chrome.exe、msedge.exe")
        self.application_program.setClearButtonEnabled(True)
        self.application_program.setMinimumWidth(140)
        self.application_scope = QComboBox()
        self.application_scope.addItem("全班 · 包含新设备", "global")
        self.application_scope.addItem("仅当前作用域", "scope")
        self.application_block_button = QPushButton("全班禁用")
        self.application_block_button.setProperty("primary", True)
        self.application_block_button.setEnabled(False)
        self.application_block_button.clicked.connect(self._apply_application_rule)
        self.application_program.textChanged.connect(
            lambda text: self.application_block_button.setEnabled(bool(text.strip()))
        )
        self.application_scope.currentIndexChanged.connect(self._application_scope_changed)
        form.addWidget(self.application_program, 1)
        form.addWidget(self.application_scope)
        form.addWidget(self.application_block_button)
        composer_layout.addLayout(form)
        self.application_feedback = QLabel("全班规则持续生效，离线设备上线后补发，新加入的设备自动继承。")
        self.application_feedback.setProperty("muted", True)
        self.application_feedback.setWordWrap(True)
        composer_layout.addWidget(self.application_feedback)
        rules_layout.addWidget(composer)
        rules_header = QHBoxLayout()
        self.application_rule_count = QLabel("禁用规则")
        self.application_rule_count.setObjectName("SectionTitle")
        self.application_rule_search = QLineEdit()
        self.application_rule_search.setPlaceholderText("搜索禁用规则")
        self.application_rule_search.setMaximumWidth(220)
        self.application_rule_search.setClearButtonEnabled(True)
        self.application_rule_search.textChanged.connect(self._refresh_application_rules)
        rules_header.addWidget(self.application_rule_count)
        rules_header.addStretch()
        rules_header.addWidget(self.application_rule_search)
        rules_layout.addLayout(rules_header)
        self.blacklist_table = self._table(("程序", "禁用范围", "执行情况", "操作"))
        self.blacklist_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        for column, width in ((0, 165), (1, 170), (3, 82)):
            self.blacklist_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.Interactive)
            self.blacklist_table.setColumnWidth(column, width)
        self.application_empty = QLabel("尚未添加禁用规则。在上方输入程序名，或从“进程管理”选择软件。")
        self.application_empty.setProperty("muted", True)
        self.application_empty.setWordWrap(True)
        self.application_rules_stack = QStackedWidget()
        empty_page = QWidget()
        empty_layout = QVBoxLayout(empty_page)
        empty_layout.setContentsMargins(0, 12, 0, 0)
        empty_layout.addWidget(self.application_empty)
        empty_layout.addStretch()
        self.application_rules_stack.addWidget(self.blacklist_table)
        self.application_rules_stack.addWidget(empty_page)
        self.application_rules_stack.setCurrentIndex(1)
        rules_layout.addWidget(self.application_rules_stack, 1)
        self.policy_tabs.addTab(rules_page, "禁用规则")

        processes_page = QWidget()
        processes_layout = QVBoxLayout(processes_page)
        processes_layout.setContentsMargins(16, 16, 16, 16)
        processes_layout.setSpacing(10)
        query_row = QHBoxLayout()
        query_row.addWidget(QLabel("查看设备"))
        self.process_device = QComboBox()
        self.process_device.setMinimumWidth(140)
        self.process_device.setMaximumWidth(240)
        self.process_search = QLineEdit()
        self.process_search.setPlaceholderText("搜索程序名、路径或 PID")
        self.process_search.setClearButtonEnabled(True)
        self.process_search.textChanged.connect(self._render_processes)
        refresh = QPushButton("刷新进程")
        refresh.clicked.connect(self._query_processes)
        query_row.addWidget(self.process_device)
        query_row.addWidget(self.process_search, 1)
        query_row.addWidget(refresh)
        processes_layout.addLayout(query_row)
        process_actions = QHBoxLayout()
        self.show_system_processes = QCheckBox("显示受保护进程")
        self.show_system_processes.toggled.connect(self._render_processes)
        self.process_count = QLabel("选择设备后点击“刷新进程”")
        self.process_count.setProperty("muted", True)
        self.process_block_button = QPushButton("禁用此软件")
        self.process_block_button.setProperty("primary", True)
        self.process_block_button.clicked.connect(self._blacklist_selected_process)
        self.process_terminate_button = QPushButton("结束所选进程")
        self.process_terminate_button.setProperty("danger", True)
        self.process_terminate_button.clicked.connect(self._terminate_process)
        process_actions.addWidget(self.show_system_processes)
        process_actions.addWidget(self.process_count)
        process_actions.addStretch()
        process_actions.addWidget(self.process_block_button)
        process_actions.addWidget(self.process_terminate_button)
        processes_layout.addLayout(process_actions)
        self.process_table = self._table(("程序", "PID", "安装路径", "运行用户", "状态"))
        self.process_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        for column, width in ((0, 155), (1, 65), (3, 120), (4, 95)):
            self.process_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.Interactive)
            self.process_table.setColumnWidth(column, width)
        self.process_table.itemSelectionChanged.connect(self._process_selection_changed)
        self.process_device.currentIndexChanged.connect(self._process_device_changed)
        processes_layout.addWidget(self.process_table, 1)
        process_hint = QLabel("“结束所选进程”仅操作当前设备；“禁用此软件”进入规则页，可一次应用到全班。")
        process_hint.setProperty("muted", True)
        process_hint.setWordWrap(True)
        processes_layout.addWidget(process_hint)
        self.policy_tabs.addTab(processes_page, "进程管理")
        self._process_selection_changed()
        layout.addWidget(self.policy_tabs, 1)
        return page

    def _build_maintenance_page(self) -> QWidget:
        page, layout = self._page_shell("设备维护")
        band = QWidget()
        row = QHBoxLayout(band)
        row.setContentsMargins(0, 6, 0, 6)
        for text, kind in (
            ("关机", CommandKind.POWER_OFF),
            ("重启", CommandKind.REBOOT),
            ("注销", CommandKind.LOGOFF),
        ):
            button = QPushButton(text)
            button.setProperty("danger", True)
            button.clicked.connect(lambda _checked, value=kind: self._power(value))
            row.addWidget(button)
        wake = QPushButton("网络唤醒")
        wake.clicked.connect(self._wake)
        row.addWidget(wake)
        scan = QPushButton("扫描设备")
        scan.clicked.connect(self.runtime.server.scan_devices)
        row.addWidget(scan)
        update = QPushButton("部署签名更新")
        update.clicked.connect(self._push_update)
        row.addWidget(update)
        row.addStretch()
        layout.addWidget(band)
        usb_band = QWidget()
        usb_row = QHBoxLayout(usb_band)
        usb_row.setContentsMargins(0, 0, 0, 0)
        usb_row.addWidget(QLabel("USB 存储"))
        for text, enabled in (("限制 USB 存储", True), ("恢复 USB 存储", False)):
            button = QPushButton(text)
            button.clicked.connect(
                lambda _checked, value=enabled: self._set_usb_storage_blocked(value)
            )
            usb_row.addWidget(button)
        usb_row.addStretch()
        layout.addWidget(usb_band)
        usb_hint = QLabel("限制可移动磁盘的读写和执行，不影响键鼠；已挂载设备可能需重新插拔或重启。")
        usb_hint.setWordWrap(True)
        usb_hint.setProperty("muted", True)
        layout.addWidget(usb_hint)
        self.maintenance_table = self._table(("设备", "IP", "MAC", "系统", "版本", "状态", "USB 策略"))
        self.maintenance_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.maintenance_table.setColumnWidth(0, 235)
        layout.addWidget(self.maintenance_table, 1)
        return page

    def _build_audit_page(self) -> QWidget:
        export = QPushButton("导出 CSV")
        export.clicked.connect(self._export_audit)
        page, layout = self._page_shell("操作记录", export)
        self.audit_table = self._table(("时间", "操作", "作用域", "成功", "失败", "命令 ID"))
        layout.addWidget(self.audit_table, 1)
        return page

    def _build_settings_page(self) -> QWidget:
        save = QPushButton("保存设置")
        save.setProperty("primary", True)
        save.clicked.connect(self._save_settings)
        page, layout = self._page_shell("设置", save)
        form = QWidget()
        grid = QGridLayout(form)
        grid.setContentsMargins(0, 8, 0, 0)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(14)
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("浅色", "light")
        self.theme_combo.addItem("深色", "dark")
        self.theme_combo.setCurrentIndex(1 if self.config.get("ui.theme") == "dark" else 0)
        self.theme_combo.currentIndexChanged.connect(
            lambda: self.apply_theme(str(self.theme_combo.currentData()))
        )
        self.seat_columns = QSpinBox()
        self.seat_columns.setRange(2, 12)
        self.seat_columns.setValue(int(self.config.get("ui.seat_columns", 6)))
        self.seat_rows = QSpinBox()
        self.seat_rows.setRange(1, 20)
        self.seat_rows.setValue(int(self.config.get("ui.seat_rows", 10)))
        self.internal_cidrs = QLineEdit(", ".join(self.config.get("teacher.internal_cidrs", [])))
        self.silence_message = QLineEdit(
            str(self.config.get("teacher.silence_message", "请保持安静，注意教师屏幕"))
        )
        fields = (
            ("界面主题", self.theme_combo),
            ("座位列数", self.seat_columns),
            ("座位行数", self.seat_rows),
            ("黑屏提示语", self.silence_message),
            ("保留内网 CIDR", self.internal_cidrs),
        )
        for index, (label, widget) in enumerate(fields):
            grid.addWidget(QLabel(label), index, 0)
            grid.addWidget(widget, index, 1)
        grid.setColumnStretch(1, 1)
        layout.addWidget(form)
        ports = QLabel("发现 9527  ·  控制 9528  ·  广播 9529  ·  巡视 9530  ·  批量 9531")
        ports.setProperty("muted", True)
        layout.addWidget(ports)
        layout.addStretch()
        return page

    @staticmethod
    def _table(headers, table_class=QTableWidget) -> QTableWidget:
        table = table_class(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setAlternatingRowColors(False)
        return table

    @staticmethod
    def _vertical_divider() -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.VLine)
        line.setFixedWidth(1)
        return line

    def _connect_runtime(self) -> None:
        distributor = self.runtime.server.file_distributor
        distributor.on_progress = lambda transfer_id, sent, total: self.bridge.transfer_progress.emit(
            transfer_id, sent, total
        )
        distributor.on_complete = lambda transfer_id, success, error: self.bridge.transfer_complete.emit(
            transfer_id, success, error
        )
        self.bridge.transfer_progress.connect(self._transfer_progress)
        self.bridge.transfer_complete.connect(self._transfer_complete)

    def apply_theme(self, theme: str) -> None:
        self.setStyleSheet(stylesheet(theme))

    def operation_drawer_visibility(self, visible: bool) -> None:
        self.operation_drawer.setVisible(visible)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if (
            event.size().width() < 1280
            and hasattr(self, "drawer_button")
            and self.drawer_button.isChecked()
        ):
            self.drawer_button.setChecked(False)

    def _show_page(self, key: str) -> None:
        self._active_page = key
        self.view_switch.setVisible(key in {"inspection", "classroom"})
        if key in {"inspection", "classroom"}:
            self.view_switch.blockSignals(True)
            self.view_switch.setCurrentIndex(self.view_switch.findData(key))
            self.view_switch.blockSignals(False)
        self.pages.setCurrentIndex(self.page_indexes[key])
        if key == "inspection":
            self._refresh_patrol()

    def _scope_changed(self, *_args) -> None:
        group_mode = self.scope_buttons["group"].isChecked()
        self.group_combo.setVisible(group_mode)
        if self.scope_buttons["all"].isChecked():
            label = "全班"
        elif group_mode:
            label = self.group_combo.currentText() or "分组"
        else:
            label = f"已选 {len(self._selected_ids)} 台"
        self.scope_status.setText(f"作用域：{label}")
        names = self._target_names(sorted(self._selected_ids))
        self.target_summary.setText(f"已选 {len(names)} 台  " + "、".join(names[:2]) + (" …" if len(names) > 2 else ""))
        self.target_summary.setToolTip("\n".join(names))

    def _target_names(self, ids):
        devices = {item.device_id: item for item in self._snapshot.devices} if self._snapshot else {}
        labels = []
        for key in ids:
            device = devices.get(key)
            if device:
                address = device.metadata.get("ip", "")
                labels.append("  ".join(filter(None, (device.hostname, address, device.note, device.metadata.get("mac")))))
        return labels

    def _show_targets(self):
        QMessageBox.information(self, "已选设备", "\n".join(self._target_names(sorted(self._selected_ids))) or "尚未选择设备")

    def _confirm_targets(self, action, ids):
        if not ids:
            raise ValueError("当前范围没有设备")
        names = self._target_names(ids)
        dialog = QMessageBox(QMessageBox.Question, action, f"对以下 {len(ids)} 台设备执行“{action}”？",
                             QMessageBox.Yes | QMessageBox.No, self)
        dialog.setTextFormat(Qt.PlainText)
        dialog.setInformativeText("\n".join(names[:5]) + ("\n…" if len(names) > 5 else ""))
        if len(names) > 5:
            dialog.setDetailedText("\n".join(names))
        dialog.setDefaultButton(QMessageBox.No)
        return dialog.exec_() == QMessageBox.Yes

    def _current_scope(self):
        if self.scope_buttons["all"].isChecked():
            return TargetScope.all()
        if self.scope_buttons["group"].isChecked():
            group = self.group_combo.currentText()
            if not group:
                raise ValueError("当前没有可用分组")
            return TargetScope.group(group)
        if not self._selected_ids:
            raise ValueError("请先选择学生设备")
        return TargetScope.devices(sorted(self._selected_ids))

    def _submit(self, callback, success_text: str, action: str):
        try:
            ids = self._resolve_scope_ids(self._current_scope())
            if not self._confirm_targets(action, ids):
                return None
            receipt = callback(DeviceIds(ids))
            self.operation_status.setText(f"{success_text} · {len(receipt.target_ids)} 台")
            self._last_revision = -1
            return receipt
        except Exception as exc:
            QMessageBox.warning(self, "无法执行", str(exc))
            return None

    def _start_broadcast(self) -> None:
        self._submit(self.runtime.start_broadcast, "广播已下发", "开始广播")

    def _stop_broadcast(self) -> None:
        self._submit(self.runtime.stop_broadcast, "广播已停止", "停止广播")

    def _start_silence(self) -> None:
        message = str(
            self.config.get("teacher.silence_message", "请保持安静，注意教师屏幕")
        ).strip()
        self._submit(
            lambda scope: self.runtime.set_silence(scope, True, message),
            "肃静已下发", "黑屏肃静",
        )

    def _block_network(self) -> None:
        cidrs = [item.strip() for item in self.config.get("teacher.internal_cidrs", []) if item.strip()]
        receipt = self._submit(
            lambda scope: self.runtime.set_network_blocked(scope, True, cidrs),
            "外网限制已下发，等待学生确认", "禁止外网",
        )
        if receipt is not None:
            self._network_command_id = receipt.command_id

    def _consume_network_result(self, snapshot) -> None:
        if not self._network_command_id:
            return
        operation = snapshot.operation(self._network_command_id)
        if operation is None:
            return
        states = tuple(operation.statuses)
        applied = sum(status.state.value == "applied" for status in states)
        pending = {"pending", "sent", "received", "queued"}
        failures = [
            status for status in states
            if status.state.value not in pending | {"applied"}
        ]
        # 下发成功不等于防火墙生效；直接展示学生回执，不能让失败藏在计数里。
        text = f"禁止外网：{applied}/{len(states)} 台策略已应用"
        if failures:
            status = failures[0]
            device = snapshot.device(status.device_id)
            hostname = device.hostname if device else status.device_id
            text += f" · {len(failures)} 台失败 · {hostname}：{status.detail or status.state.value}"
        elif applied < len(states):
            text += " · 等待确认"
        self.operation_status.setText(text)
        self.operation_status.setToolTip(text)
        if all(status.state.value not in pending for status in states):
            self._network_command_id = None

    def _set_usb_storage_blocked(self, enabled: bool) -> None:
        self._submit(
            lambda scope: self.runtime.set_usb_storage_blocked(scope, enabled),
            "USB 存储限制已下发" if enabled else "USB 存储恢复已下发",
            "限制 USB 存储" if enabled else "恢复 USB 存储",
        )

    def _restore_all(self) -> None:
        self._submit(self.runtime.restore_all, "临时控制已恢复", "全部恢复")

    def _start_inspection(self) -> None:
        session = self._submit(
            lambda scope: self.runtime.open_inspection(scope, InspectionMode.THUMBNAIL),
            "巡视已开始", "开始巡视",
        )
        if session is not None:
            self._patrol_target_ids.update(session.target_ids)

    def _stop_inspection(self) -> None:
        receipt = self._submit(self.runtime.close_inspection, "巡视已停止", "停止巡视")
        if receipt is not None:
            self._patrol_target_ids.difference_update(receipt.target_ids)

    def _poll_runtime(self) -> None:
        try:
            for message in self.runtime.poll_remote_windows():
                self.operation_status.setText(message)
            snapshot = self.runtime.snapshot(self._last_revision)
        except Exception as exc:
            self.operation_status.setText(f"状态读取失败：{exc}")
            return
        if snapshot is not None:
            self._snapshot = snapshot
            self._last_revision = snapshot.revision
            self._refresh_devices()
            if self._auto_preview:
                online = {d.device_id for d in snapshot.devices if d.online}
                self._preview_seen.intersection_update(online)
                fresh = online - self._preview_seen - set(self._viewers) - set(self.runtime.remote_windows.sessions)
                if fresh:
                    self._preview_seen.update(fresh)
                    try:
                        session = self.runtime.open_inspection(DeviceIds(sorted(fresh)), InspectionMode.THUMBNAIL)
                        self._patrol_target_ids.update(session.target_ids)
                    except Exception as exc:
                        self.operation_status.setText(f"自动巡视失败：{exc}")
            self.operation_drawer.set_operations(snapshot.operations)
            self._refresh_audit(snapshot.operations)
            self._consume_process_result(snapshot)
            self._consume_network_result(snapshot)
        if self._snapshot:
            for device in self._snapshot.devices:
                frame = self.runtime.inspection_media.take(device.device_id)
                if frame is None:
                    continue
                card = self._patrol_cards.get(device.device_id)
                if card:
                    card.update_frame(frame.jpeg)
                viewer = self._viewers.get(device.device_id)
                if viewer:
                    viewer.update_frame(frame.jpeg)

    def _refresh_devices(self, *_args) -> None:
        if not self._snapshot:
            return
        valid = self._selected_ids.intersection(d.device_id for d in self._snapshot.devices)
        if valid != self._selected_ids:
            self._selection_changed(tuple(valid))
        search = self.classroom_search.text().strip().casefold()
        devices = [
            item for item in self._snapshot.devices
            if not search or search in item.hostname.casefold()
            or search in item.note.casefold()
            or search in str(item.metadata.get("ip", "")).casefold()
        ]
        signature = tuple(
            (item.device_id, item.hostname, item.note, item.online, item.group,
             str(item.metadata.get("ip", "")))
            for item in self._snapshot.devices
        ) + (search,)
        if signature != self._device_signature:
            self._device_signature = signature
            positions = self.config.get("ui.seat_positions", {})
            self.seat_map.selected = set(self._selected_ids)
            self.seat_map.set_devices(devices, positions)
            self._refresh_patrol()
        self._scope_changed()
        online = sum(1 for item in self._snapshot.devices if item.online)
        self.connection_status.setText(f"{online}/{len(self._snapshot.devices)} 台在线")
        groups = sorted({item.group for item in self._snapshot.devices if item.group})
        current = self.group_combo.currentText()
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        self.group_combo.addItems(groups)
        if current in groups:
            self.group_combo.setCurrentText(current)
        self.group_combo.blockSignals(False)
        self._refresh_device_tables()

    def _selection_changed(self, device_ids: tuple) -> None:
        self._selected_ids = set(device_ids)
        self.seat_map.selected = set(device_ids)
        for tile in self.seat_map.canvas.findChildren(SeatTile):
            tile.set_selected(tile.device.device_id in self._selected_ids)
        for identity, card in self._patrol_cards.items():
            card.set_selected(identity in self._selected_ids)
        self.scope_buttons["selected"].setChecked(True)
        self._scope_changed()

    def _patrol_selection(self, hits: tuple, additive: bool) -> None:
        selected = set(hits)
        if additive:
            selected.symmetric_difference_update(self._selected_ids)
        self._selection_changed(tuple(sorted(selected)))

    def _device_menu(self, device_id, point) -> None:
        device = self._snapshot.device(device_id)
        if not device:
            return
        targets = self._selected_ids if device_id in self._selected_ids else {device_id}
        self._selection_changed(tuple(targets))
        menu = QMenu(self)
        menu.addSection(f"已选 {len(targets)} 台")
        for label, callback in (
            ("设置分组", self._set_selected_group),
            ("开始广播", self._start_broadcast), ("停止广播", self._stop_broadcast),
            ("黑屏肃静", self._start_silence),
            ("解除黑屏", lambda: self._submit(lambda scope: self.runtime.set_silence(scope, False), "解除黑屏已下发", "解除黑屏")),
        ):
            menu.addAction(label, callback)
        menu.addSection(device.hostname)
        menu.addAction("打开画面", lambda: self._open_viewer(device_id)).setEnabled(device.online)
        menu.addAction("删除这台离线设备", lambda: self._forget_device(device_id)).setEnabled(not device.online)
        menu.exec_(point)
        menu.deleteLater()

    def _forget_device(self, device_id) -> None:
        device = self._snapshot.device(device_id)
        if device is None or device.online:
            return
        details = (f"{device.hostname}  {device.note}\n"
                   f"IP  {device.metadata.get('ip') or '未知'}   MAC  {device.metadata.get('mac') or '未知'}\n"
                   f"最后在线  {datetime.fromtimestamp(device.last_seen):%Y-%m-%d %H:%M}")
        if QMessageBox.question(self, "删除离线设备", f"从教师端移除 {details}？再次连接时会重新出现。") != QMessageBox.Yes:
            return
        try:
            self.runtime.classroom.forget_offline_device(device_id)
        except ValueError as exc:
            QMessageBox.warning(self, "无法移除", str(exc))
            return
        self._poll_runtime()

    def _save_seat_positions(self, positions: dict) -> None:
        self.config.set("ui.seat_positions", positions)
        self.config.save()

    def _copy_device_name(self, device_id: str) -> None:
        device = self._snapshot.device(device_id) if self._snapshot else None
        if device is None:
            return
        # 只有用户点击设备名才写剪贴板，刷新和自动巡视不能打扰用户剪贴板。
        QApplication.clipboard().setText(device.hostname)
        self.operation_status.setText(f"已复制设备名：{device.hostname}")

    def _edit_device_note(self, device_id: str) -> None:
        device = self._snapshot.device(device_id) if self._snapshot else None
        if device is None:
            return
        note, accepted = QInputDialog.getText(
            self, "设备备注", f"{device.hostname}\n备注（留空清除）：", text=device.note,
        )
        if not accepted:
            return
        try:
            changed = self.runtime.set_device_note(device_id, note)
            self.operation_status.setText(
                ("设备备注已保存" if note.strip() else "设备备注已清除")
                if changed else "设备备注未更改"
            )
            self._poll_runtime()
        except Exception as exc:
            logger.warning(f"Cannot save device note for {device_id}: {exc}")
            QMessageBox.warning(self, "备注保存失败", str(exc))

    def _set_selected_group(self) -> None:
        if not self._selected_ids:
            QMessageBox.warning(self, "无法分组", "请先选择学生设备")
            return
        targets = sorted(self._selected_ids)
        group, accepted = QInputDialog.getText(self, "设置分组", f"为已选 {len(targets)} 台设备设置分组名称：")
        group = group.strip()
        if not accepted or not group:
            return
        try:
            changed = self.runtime.set_group(targets, group)
            self.operation_status.setText(f"已将 {changed} 台设备移入 {group}")
            self._last_revision = -1
        except Exception as exc:
            QMessageBox.warning(self, "分组失败", str(exc))

    def _refresh_patrol(self) -> None:
        while self.patrol_grid.count():
            item = self.patrol_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._patrol_cards.clear()
        if not self._snapshot:
            return
        for index, device in enumerate(self._snapshot.devices):
            card = PatrolCard(device)
            card.set_selected(device.device_id in self._selected_ids)
            card.toggle_requested.connect(lambda key: self._patrol_selection((key,), True))
            card.menu_requested.connect(self._device_menu)
            card.opened.connect(self._open_viewer)
            card.copy_requested.connect(self._copy_device_name)
            card.note_requested.connect(self._edit_device_note)
            self._patrol_cards[device.device_id] = card
            self.patrol_grid.addWidget(card, index // 4, index % 4)
        self.patrol_grid.setRowStretch((len(self._snapshot.devices) + 3) // 4, 1)

    def _open_viewer(self, device_id: str) -> None:
        if device_id in self.runtime.remote_windows.sessions:
            self.operation_status.setText("该设备已有 UltraVNC 会话，请使用独立窗口")
            return
        if not self._snapshot:
            return
        device = self._snapshot.device(device_id)
        if not device:
            return
        try:
            self.runtime.open_inspection(DeviceIds([device_id]), InspectionMode.FOCUS)
        except Exception as exc:
            QMessageBox.warning(self, "无法查看", str(exc))
            return
        viewer = FullScreenViewer(device_id, device.hostname, self)
        viewer.remote_input.connect(self.runtime.send_remote_input)
        viewer.remote_toggled.connect(self._remote_toggled)
        viewer.finished.connect(lambda _result, value=device_id: self._viewer_closed(value))
        self._viewers[device_id] = viewer
        viewer.showFullScreen()

    def _remote_toggled(self, device_id: str, enabled: bool) -> None:
        try:
            if enabled:
                if not self._confirm_targets("远程控制", [device_id]):
                    viewer = self._viewers.get(device_id)
                    if viewer:
                        viewer.remote_enabled = False
                        viewer.remote_button.blockSignals(True)
                        viewer.remote_button.setChecked(False)
                        viewer.remote_button.blockSignals(False)
                    return
                self.runtime.start_remote_control(device_id)
                viewer = self._viewers.get(device_id)
                if viewer:
                    # 外部 Viewer 接管会话后关闭旧画面，避免继续转发键鼠。
                    viewer.remote_enabled = False
                    viewer.close()
            else:
                self.runtime.stop_remote_control(device_id)
                self.runtime.open_inspection(
                    DeviceIds([device_id]), InspectionMode.FOCUS
                )
        except Exception as exc:
            viewer = self._viewers.get(device_id)
            if viewer:
                viewer.remote_enabled = False
                viewer.remote_button.blockSignals(True)
                viewer.remote_button.setChecked(False)
                viewer.remote_button.blockSignals(False)
            QMessageBox.warning(self, "远程控制失败", str(exc))

    def _viewer_closed(self, device_id: str) -> None:
        viewer = self._viewers.pop(device_id, None)
        if device_id in self.runtime.remote_windows.sessions:
            return
        try:
            if viewer and viewer.remote_enabled:
                self.runtime.stop_remote_control(device_id)
            if device_id in self._patrol_target_ids:
                self.runtime.open_inspection(
                    DeviceIds([device_id]), InspectionMode.THUMBNAIL
                )
            else:
                self.runtime.close_inspection(DeviceIds([device_id]))
        except Exception:
            pass

    def _add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "添加文件")
        self._queue_paths(paths)

    def _add_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "添加文件夹")
        if path:
            self._queue_path(path)

    def _queue_paths(self, paths: List[str]) -> None:
        for path in paths:
            self._queue_path(path)

    def _queue_path(self, path: str) -> None:
        if not path:
            return
        try:
            path = os.path.abspath(os.path.normpath(path))
            # 选择框与拖放共用同一队列，避免大小写、相对路径造成重复发送。
            if any(os.path.normcase(item["path"]) == os.path.normcase(path)
                   for item in self.file_queue):
                return
            is_dir = os.path.isdir(path)
            if not is_dir and not os.path.isfile(path):
                raise OSError("文件或文件夹不存在")
            size = 0
            if is_dir:
                for root, _dirs, files in os.walk(path):
                    for name in files:
                        try:
                            size += os.path.getsize(os.path.join(root, name))
                        except OSError:
                            pass
            else:
                size = os.path.getsize(path)
        except (OSError, ValueError) as exc:
            # 拖入后文件仍可能被移走；只报告这一项，不弹窗或中断后续文件入队。
            logger.warning(f"Cannot queue file {path!r}: {exc}")
            self.operation_status.setText(f"未添加：{os.path.basename(path)}（{exc}）")
            return
        self.file_queue.append({"path": path, "size": size, "type": "文件夹" if is_dir else "文件", "status": "等待"})
        row = self.file_table.rowCount()
        self.file_table.insertRow(row)
        values = (os.path.basename(path), "文件夹" if is_dir else "文件", self._format_size(size), "0%", "等待")
        for column, value in enumerate(values):
            self.file_table.setItem(row, column, QTableWidgetItem(value))

    def _send_queue(self) -> None:
        try:
            scope = self._current_scope()
            target_ids = self._resolve_scope_ids(scope)
            if not self._confirm_targets("分发文件", target_ids):
                return
        except Exception as exc:
            QMessageBox.warning(self, "无法分发", str(exc))
            return
        for index, item in enumerate(self.file_queue):
            if item["status"] not in {"等待", "失败"}:
                continue
            retry_ids = item.get("retry_ids") if item["status"] == "失败" else None
            effective_ids = (
                [device_id for device_id in retry_ids if device_id in target_ids]
                if retry_ids else target_ids
            )
            if not effective_ids:
                continue
            transfer_id = self.runtime.server.send_file(item["path"], effective_ids)
            item["status"] = "发送中"
            item["transfer_id"] = transfer_id
            self._file_rows[transfer_id] = index
            self.file_table.item(index, 4).setText("发送中")

    def _transfer_progress(self, transfer_id: str, sent: int, total: int) -> None:
        row = self._file_rows.get(transfer_id)
        if row is None:
            return
        percent = int(sent * 100 / total) if total else 100
        self.file_table.item(row, 3).setText(f"{percent}%")

    def _transfer_complete(self, transfer_id: str, success: bool, error: str) -> None:
        row = self._file_rows.get(transfer_id)
        if row is None:
            return
        self.file_queue[row]["status"] = "完成" if success else "失败"
        self.file_table.item(row, 4).setText("完成" if success else f"失败：{error}")
        result = self.runtime.server.file_distributor.get_completed_transfer(transfer_id)
        if result:
            self.file_queue[row]["retry_ids"] = [
                device_id for device_id, status in result["devices"].items()
                if status["state"] != "complete"
            ]
            self.file_table.item(row, 4).setToolTip("\n".join(
                f"{device_id}：{status['state']} {status['result']}"
                for device_id, status in result["devices"].items()
            ))
        if success:
            self.file_table.item(row, 3).setText("100%")

    def _query_processes(self) -> None:
        device_id = self.process_device.currentData()
        if not device_id:
            return
        try:
            receipt = self.runtime.query_processes(str(device_id))
            self._process_command_id = receipt.command_id
            self.operation_status.setText("正在读取进程列表")
        except Exception as exc:
            QMessageBox.warning(self, "读取失败", str(exc))

    def _process_device_changed(self, *_args) -> None:
        # 进程身份只属于查询时的设备，切换或离线后不能继续操作旧列表。
        self._process_command_id = None
        self._processes = []
        self.process_table.setRowCount(0)
        self.process_count.setText("选择设备后点击“刷新进程”")
        self._process_selection_changed()

    def _consume_process_result(self, snapshot) -> None:
        if not self._process_command_id:
            return
        operation = snapshot.operation(self._process_command_id)
        if not operation or not operation.statuses:
            return
        status = operation.statuses[0]
        if status.state.value == "applied":
            self._processes = list(status.payload.get("processes", []))
            self._render_processes()
            self._process_command_id = None
            self.operation_status.setText(f"已读取 {len(self._processes)} 个进程")
        elif status.state.value in {"failed", "timed_out"}:
            self._process_command_id = None
            self.operation_status.setText(f"进程读取失败：{status.detail}")

    def _render_processes(self, *_args) -> None:
        search = self.process_search.text().strip().casefold()
        show_protected = self.show_system_processes.isChecked()
        self.process_table.setRowCount(0)
        for process in self._processes:
            if process.get("protected") and not show_protected:
                continue
            values = (
                process.get("name", ""), process.get("pid", ""),
                process.get("path", ""), process.get("username", ""),
                "受保护" if process.get("protected") else "可管理",
            )
            if search and not any(search in str(value).casefold() for value in values[:3]):
                continue
            row = self.process_table.rowCount()
            self.process_table.insertRow(row)
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value or ""))
                item.setData(Qt.UserRole, process)
                item.setToolTip(str(process.get("protection_reason", "")) if column == 4 else str(value or ""))
                self.process_table.setItem(row, column, item)
        self.process_count.setText(f"显示 {self.process_table.rowCount()} / {len(self._processes)} 项")
        self._process_selection_changed()

    def _process_selection_changed(self) -> None:
        row = self.process_table.currentRow()
        item = self.process_table.item(row, 0) if row >= 0 else None
        process = item.data(Qt.UserRole) if item else None
        editable = bool(process and not process.get("protected"))
        self.process_block_button.setEnabled(editable)
        self.process_terminate_button.setEnabled(editable)

    def _selected_process(self):
        row = self.process_table.currentRow()
        if row < 0:
            raise ValueError("请先选择一个进程")
        return self.process_table.item(row, 0).data(Qt.UserRole)

    def _terminate_process(self) -> None:
        try:
            process = self._selected_process()
            device_id = str(self.process_device.currentData())
            if process.get("protected"):
                raise ValueError("该进程受保护，不能结束")
            if QMessageBox.question(self, "结束进程", f"确定结束 {process.get('name')}（PID {process.get('pid')}）？") != QMessageBox.Yes:
                return
            self.runtime.terminate_process(
                DeviceIds([device_id]), int(process["pid"]),
                float(process["creation_time"]), str(process.get("name") or ""),
            )
        except Exception as exc:
            QMessageBox.warning(self, "无法结束进程", str(exc))

    def _blacklist_selected_process(self) -> None:
        try:
            process = self._selected_process()
            if process.get("protected"):
                raise ValueError("该进程受保护，不能添加禁用规则")
            # 跨机器安装路径不同；从进程列表带入程序名，避免只禁中一台机器。
            self.application_program.setText(str(process.get("name") or ""))
            self.application_scope.setCurrentIndex(0)
            self.policy_tabs.setCurrentIndex(0)
            self.application_feedback.setText("已填入程序名，点击“全班禁用”后生效。")
        except Exception as exc:
            QMessageBox.warning(self, "无法添加策略", str(exc))

    def _application_scope_changed(self, *_args) -> None:
        global_rule = self.application_scope.currentData() == "global"
        enabled = self._snapshot.application_control_enabled if self._snapshot else True
        self.application_block_button.setText(
            ("全班禁用" if global_rule else "禁用当前范围") if enabled else "保存规则"
        )
        if not enabled:
            self.application_feedback.setText("软件禁用已暂停，规则仍可编辑；开启总开关后统一生效。")
            return
        self.application_feedback.setText(
            "全班规则持续生效，离线设备上线后补发，新加入的设备自动继承。"
            if global_rule else "仅应用到顶栏当前选定的范围，新加入的设备不自动继承。"
        )

    def _apply_application_rule(self) -> None:
        try:
            program = normalize_program_name(self.application_program.text())
            if self.application_scope.currentData() == "global":
                receipt = self.runtime.set_global_blacklist(program, True)
                message = f"{program} 全班禁用规则已保存，已向 {len(receipt.target_ids)} 台设备下发。"
            else:
                scope = self._current_scope()
                if not self._resolve_scope_ids(scope):
                    raise ValueError("当前作用域没有设备，请先选择设备或使用全班规则")
                ids = self._resolve_scope_ids(scope)
                if not self._confirm_targets(f"禁用 {program}", ids):
                    return
                receipt = self.runtime.set_blacklist(DeviceIds(ids), program, "", True)
                message = f"{program} 禁用指令已向 {len(receipt.target_ids)} 台设备下发。"
            if not self.runtime.snapshot().application_control_enabled:
                message = f"{program} 规则已保存，软件禁用已暂停，开启总开关后生效。"
            self.application_program.setText(program)
            self.application_feedback.setText(message + "执行结果见下方；离线设备上线后补发。")
            self.operation_status.setText(message)
            self._poll_runtime()
        except Exception as exc:
            logger.warning(f"Cannot apply application rule: {exc}")
            self.application_feedback.setText(str(exc))

    def _sync_application_control_switch(self) -> None:
        enabled = self._snapshot.application_control_enabled if self._snapshot else True
        self.application_control_switch.blockSignals(True)
        self.application_control_switch.setChecked(enabled)
        self.application_control_switch.blockSignals(False)
        self.application_control_status.setText("已开启" if enabled else "已暂停")
        global_rule = self.application_scope.currentData() == "global"
        self.application_block_button.setText(
            ("全班禁用" if global_rule else "禁用当前范围") if enabled else "保存规则"
        )

    def _toggle_application_control(self, enabled: bool) -> None:
        try:
            self.runtime.set_application_control_enabled(enabled)
            self._poll_runtime()
            message = (
                "软件禁用已开启，正在恢复已保存的禁用规则。"
                if enabled else "软件禁用已暂停，正在通知学生恢复运行；禁用规则已保留。"
            )
            self.application_feedback.setText(message + "离线设备上线后同步。")
            self.operation_status.setText(message)
        except Exception as exc:
            logger.warning(f"Cannot toggle application control: {exc}")
            self._snapshot = self.runtime.snapshot()
            self._sync_application_control_switch()
            self.application_feedback.setText(f"切换失败：{exc}")

    def _remove_blacklist(self, value=None) -> None:
        if not isinstance(value, dict):
            row = self.blacklist_table.currentRow()
            if row < 0:
                return
            value = self.blacklist_table.item(row, 0).data(Qt.UserRole)
        program = value["program"]
        if QMessageBox.question(
            self, "解除禁用", f"确定解除 {program} 的禁用规则？\n离线设备将在下次上线时恢复。",
        ) != QMessageBox.Yes:
            return
        try:
            if value["global"]:
                self.runtime.set_global_blacklist(program, False)
            else:
                self.runtime.set_blacklist(
                    DeviceIds(value["device_ids"]), program, value["path"], False,
                )
            self.application_feedback.setText(f"{program} 解除指令已下发，离线设备上线后恢复。")
            self._poll_runtime()
        except Exception as exc:
            QMessageBox.warning(self, "无法解除", str(exc))

    def _power(self, kind: CommandKind) -> None:
        names = {CommandKind.POWER_OFF: "关机", CommandKind.REBOOT: "重启", CommandKind.LOGOFF: "注销"}
        try:
            ids = self._resolve_scope_ids(self._current_scope())
            if self._confirm_targets(names[kind], ids):
                self.runtime.power(DeviceIds(ids), kind)
        except Exception as exc:
            QMessageBox.warning(self, "无法执行", str(exc))

    def _wake(self) -> None:
        try:
            ids = self._resolve_scope_ids(self._current_scope())
            if not self._confirm_targets("网络唤醒", ids):
                return
            results = self.runtime.wake(DeviceIds(ids))
            sent = sum(bool(result and result.success) for result in results.values())
            self.operation_status.setText(f"网络唤醒已发送 · {sent}/{len(results)} 台")
        except Exception as exc:
            QMessageBox.warning(self, "网络唤醒失败", str(exc))

    def _push_update(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择签名更新包", filter="ZIP (*.zip)")
        if not path:
            return
        try:
            with open(path + ".manifest.json", "r", encoding="utf-8") as handle:
                version = str(json.load(handle)["version"])
            target_ids = self._resolve_scope_ids(self._current_scope())
            if not self._confirm_targets("部署签名更新", target_ids):
                return
            count = self.runtime.server.push_update(path, version, target_ids)
            if count <= 0:
                raise ValueError("更新包签名无效，或当前作用域没有在线设备")
            self.operation_status.setText(f"更新通知已发送 · {count} 台")
        except Exception as exc:
            QMessageBox.warning(self, "无法部署更新", str(exc))

    @staticmethod
    def _usb_storage_status(device) -> str:
        # 回执只证明策略落盘，不能把“已下发”或离线设备显示成即时封禁成功。
        if not device.online:
            return "离线 / 未确认"
        expected = device.expected_for("usb_storage")
        actual = device.actual_for("usb_storage")
        if expected is not None and (
            actual is None or actual.command_revision != expected.command_revision
        ):
            return "待确认"
        if actual is not None:
            return "限制策略已应用" if actual.value else "原策略已恢复"
        return "未下发"

    def _refresh_device_tables(self) -> None:
        devices = tuple(self._snapshot.devices) if self._snapshot else ()
        current = self.process_device.currentData()
        self.process_device.blockSignals(True)
        self.process_device.clear()
        for device in devices:
            if device.online:
                self.process_device.addItem(device.hostname, device.device_id)
        index = self.process_device.findData(current)
        if index >= 0:
            self.process_device.setCurrentIndex(index)
        self.process_device.blockSignals(False)
        if self.process_device.currentData() != current:
            self._process_device_changed()
        self.maintenance_table.setRowCount(0)
        for device in devices:
            row = self.maintenance_table.rowCount()
            self.maintenance_table.insertRow(row)
            meta = device.metadata
            values = (
                device.hostname, meta.get("ip", ""), meta.get("mac", ""),
                meta.get("os", "Windows"), meta.get("version", ""),
                "在线" if device.online else "离线",
                self._usb_storage_status(device),
            )
            for column, value in enumerate(values):
                self.maintenance_table.setItem(row, column, QTableWidgetItem(str(value)))
            self.maintenance_table.item(row, 0).setData(Qt.UserRole, device.device_id)
            name = DeviceNameWidget(device)
            # 单元格控件要遮住下方原始文本，避免主机名与备注叠字。
            name.setBackgroundRole(QPalette.Base)
            name.setAutoFillBackground(True)
            name.copy_requested.connect(self._copy_device_name)
            name.note_requested.connect(self._edit_device_note)
            self.maintenance_table.setCellWidget(row, 0, name)
            self.maintenance_table.setRowHeight(row, max(36, name.sizeHint().height() + 6))
        self._refresh_application_rules()

    def _refresh_application_rules(self, *_args) -> None:
        snapshot = self._snapshot
        self.blacklist_table.setRowCount(0)
        if snapshot is None:
            return
        self._sync_application_control_switch()
        enabled = snapshot.application_control_enabled
        operations = {operation.revision: operation for operation in snapshot.operations}
        globals_active = set(snapshot.global_blacklist)
        rules = {
            (program, enabled): {"global": True, "configured": True, "devices": list(snapshot.devices)}
            for program in globals_active
        }
        for identity, device_ids in snapshot.local_blacklist_rules.items():
            if identity in globals_active:
                continue
            rules[(identity, enabled)] = {
                "global": False, "configured": True,
                "devices": [device for device in snapshot.devices if device.device_id in device_ids],
            }
        for device in snapshot.devices:
            for expected in device.expected:
                if not expected.domain.startswith("blacklist:"):
                    continue
                identity = expected.domain.split(":", 1)[1]
                source = rules.get((identity, enabled))
                if source and any(item.device_id == device.device_id for item in source["devices"]):
                    continue
                actual = device.actual_for(expected.domain)
                if (not expected.value and actual is not None and not actual.value
                        and actual.command_revision == expected.command_revision):
                    continue
                rule = rules.setdefault(
                    (identity, bool(expected.value)), {"global": False, "configured": False, "devices": []},
                )
                rule["devices"].append(device)
        search = self.application_rule_search.text().strip().casefold()
        for (identity, blocked), rule in sorted(rules.items()):
            if search and search not in identity.casefold():
                continue
            devices = rule["devices"]
            applied = offline = failed = pending = 0
            details = []
            for device in devices:
                expected = device.expected_for("blacklist:" + identity)
                actual = device.actual_for("blacklist:" + identity)
                operation = operations.get(expected.command_revision) if expected else None
                status = operation.status_for(device.device_id) if operation else None
                if (expected and actual and actual.command_revision == expected.command_revision
                        and bool(actual.value) == blocked):
                    applied += 1
                    result = "已禁用" if blocked else "已恢复"
                elif status and status.state.value in {"failed", "timed_out"}:
                    failed += 1
                    result = f"失败：{status.detail or status.state.value}"
                elif not device.online:
                    offline += 1
                    result = "离线，等待上线"
                else:
                    pending += 1
                    result = "等待学生确认"
                details.append(f"{device.hostname}：{result}")
            if devices:
                result_text = f"{'已禁用' if blocked else '已恢复'} {applied}/{len(devices)}"
            else:
                result_text = "等待设备加入"
            paused = rule["configured"] and not enabled
            if paused:
                result_text = "已暂停 · " + result_text
            elif not blocked:
                result_text = "解除中 · " + result_text
            for count, label in ((failed, "失败"), (offline, "离线待下发"), (pending, "待确认")):
                if count:
                    result_text += f" · {count} 台{label}"
            is_path = "\\" in identity or "/" in identity
            data = {
                "global": rule["global"], "program": ntpath.basename(identity) if is_path else identity,
                "path": identity if is_path else "", "device_ids": [device.device_id for device in devices],
            }
            scope = "全班 · 含新设备" if rule["global"] else (
                devices[0].hostname if len(devices) == 1 else f"{len(devices)} 台设备"
            )
            row = self.blacklist_table.rowCount()
            self.blacklist_table.insertRow(row)
            for column, text in enumerate((identity, scope, result_text)):
                item = QTableWidgetItem(text)
                item.setData(Qt.UserRole, data)
                item.setToolTip("\n".join(details) if column == 2 else text)
                self.blacklist_table.setItem(row, column, item)
            actions = QWidget()
            action_layout = QHBoxLayout(actions)
            action_layout.setContentsMargins(4, 2, 4, 2)
            button = QPushButton("解除" if blocked or paused else "重试解除")
            button.setProperty("compact", True)
            button.clicked.connect(lambda _checked, value=data: self._remove_blacklist(value))
            action_layout.addWidget(button)
            self.blacklist_table.setCellWidget(row, 3, actions)
            self.blacklist_table.setRowHeight(row, 40)
        count = self.blacklist_table.rowCount()
        self.application_rule_count.setText(f"禁用规则 · {len(rules)} 项")
        self.application_empty.setText(
            "没有匹配的规则，请调整搜索内容。" if rules else
            "尚未添加禁用规则。在上方输入程序名，或从“进程管理”选择软件。"
        )
        self.application_rules_stack.setCurrentIndex(0 if count else 1)

    def _refresh_audit(self, operations) -> None:
        self.audit_table.setRowCount(0)
        for operation in reversed(tuple(operations)):
            row = self.audit_table.rowCount()
            self.audit_table.insertRow(row)
            success = sum(status.state.value == "applied" for status in operation.statuses)
            failed = sum(status.state.value in {"failed", "timed_out"} for status in operation.statuses)
            values = (
                datetime.fromtimestamp(operation.submitted_at).strftime("%Y-%m-%d %H:%M:%S"),
                operation.action.kind.value, str(len(operation.target_ids)),
                str(success), str(failed), operation.command_id,
            )
            for column, value in enumerate(values):
                self.audit_table.setItem(row, column, QTableWidgetItem(value))

    def _export_audit(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "导出操作记录", "课堂操作记录.csv", "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow([self.audit_table.horizontalHeaderItem(i).text() for i in range(self.audit_table.columnCount())])
            for row in range(self.audit_table.rowCount()):
                writer.writerow([self.audit_table.item(row, column).text() for column in range(self.audit_table.columnCount())])
        self.operation_status.setText(f"已导出：{path}")

    def _save_settings(self) -> None:
        theme = str(self.theme_combo.currentData())
        cidrs = [item.strip() for item in self.internal_cidrs.text().split(",") if item.strip()]
        self.config.set("ui.theme", theme)
        self.config.set("ui.seat_columns", self.seat_columns.value())
        self.config.set("ui.seat_rows", self.seat_rows.value())
        self.config.set("teacher.silence_message", self.silence_message.text().strip())
        self.config.set("teacher.internal_cidrs", cidrs)
        self.config.save()
        self.seat_map.rows = self.seat_rows.value()
        self.seat_map.set_columns(self.seat_columns.value())
        self.apply_theme(theme)
        self.operation_status.setText("设置已保存")

    def _resolve_scope_ids(self, scope) -> List[str]:
        if not self._snapshot:
            return []
        if isinstance(scope, DeviceIds):
            return list(scope.device_ids)
        if hasattr(scope, "name"):
            return [item.device_id for item in self._snapshot.devices if item.group == scope.name]
        return [item.device_id for item in self._snapshot.devices]

    @staticmethod
    def _format_size(value: int) -> str:
        size = float(value)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} GB"

    def closeEvent(self, event: QCloseEvent) -> None:
        self.poll_timer.stop()
        for viewer in tuple(self._viewers.values()):
            viewer.close()
        try:
            self.runtime.close()
        except Exception as exc:
            logger.warning(f"Teacher runtime close failed: {exc}")
        event.accept()


__all__ = ["TeacherMainWindow"]
