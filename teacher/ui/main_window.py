"""v2 teacher console: seat-first classroom operations for 20-60 PCs."""

from __future__ import annotations

import csv
import json
import os
import ntpath
from datetime import datetime
from typing import Dict, List, Optional

from PyQt5.QtCore import QObject, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QCloseEvent
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
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
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from common.config import get_config
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
    FullScreenViewer,
    OperationDrawer,
    PatrolCard,
    SeatMapWidget,
)

logger = get_logger("teacher_ui")


class UiBridge(QObject):
    transfer_progress = pyqtSignal(str, int, int)
    transfer_complete = pyqtSignal(str, bool, str)


class TeacherMainWindow(QMainWindow):
    NAV_ITEMS = (
        ("教室", "classroom"),
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
        self._active_page = "classroom"
        self.bridge = UiBridge()

        self.setWindowTitle("局域网机房控制系统 · 教师端")
        self.setMinimumSize(1000, 600)
        self.resize(1440, 860)
        self._build_ui()
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
        self.scope_status = QLabel("作用域：全班")
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
        self.scope_buttons["all"].setChecked(True)
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
        search.setPlaceholderText("搜索主机名或 IP")
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
        self.seat_map = SeatMapWidget(
            int(self.config.get("ui.seat_columns", 6)),
            int(self.config.get("ui.seat_rows", 10)),
        )
        self.seat_map.selection_changed.connect(self._selection_changed)
        self.seat_map.positions_changed.connect(self._save_seat_positions)
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
        row.addWidget(start)
        row.addWidget(stop)
        page, layout = self._page_shell("巡视", actions)
        self.patrol_scroll = QScrollArea()
        self.patrol_scroll.setWidgetResizable(True)
        self.patrol_canvas = QWidget()
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
        self.file_table = self._table(("名称", "类型", "大小", "进度", "状态"))
        layout.addWidget(self.file_table, 1)
        self.file_hint = QLabel("接收位置：桌面 / 课堂文件")
        self.file_hint.setProperty("muted", True)
        layout.addWidget(self.file_hint)
        self.file_queue: List[dict] = []
        return page

    def _build_policy_page(self) -> QWidget:
        device_bar = QWidget()
        row = QHBoxLayout(device_bar)
        row.setContentsMargins(0, 0, 0, 0)
        self.process_device = QComboBox()
        self.process_device.setMinimumWidth(180)
        refresh = QPushButton("刷新进程")
        refresh.clicked.connect(self._query_processes)
        terminate = QPushButton("结束进程")
        terminate.setProperty("danger", True)
        terminate.clicked.connect(self._terminate_process)
        add_policy = QPushButton("加入永久黑名单")
        add_policy.clicked.connect(self._blacklist_selected_process)
        remove_policy = QPushButton("解除黑名单")
        remove_policy.clicked.connect(self._remove_blacklist)
        row.addWidget(self.process_device)
        row.addWidget(refresh)
        row.addWidget(terminate)
        row.addWidget(add_policy)
        row.addWidget(remove_policy)
        page, layout = self._page_shell("应用策略", device_bar)
        self.process_table = self._table(("PID", "进程", "路径", "用户", "保护"))
        self.process_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.process_device.currentIndexChanged.connect(self._process_device_changed)
        layout.addWidget(self.process_table, 1)
        self.blacklist_table = self._table(("设备", "程序或路径", "状态"))
        self.blacklist_table.setMaximumHeight(180)
        layout.addWidget(self.blacklist_table)
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
        self.maintenance_table = self._table(("设备", "IP", "MAC", "系统", "版本", "状态"))
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
    def _table(headers) -> QTableWidget:
        table = QTableWidget(0, len(headers))
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
        return TargetScope.devices(self._selected_ids)

    def _submit(self, callback, success_text: str):
        try:
            receipt = callback(self._current_scope())
            self.operation_status.setText(f"{success_text} · {len(receipt.target_ids)} 台")
            self._last_revision = -1
            return receipt
        except Exception as exc:
            QMessageBox.warning(self, "无法执行", str(exc))
            return None

    def _start_broadcast(self) -> None:
        self._submit(self.runtime.start_broadcast, "广播已下发")

    def _stop_broadcast(self) -> None:
        self._submit(self.runtime.stop_broadcast, "广播已停止")

    def _start_silence(self) -> None:
        message = str(
            self.config.get("teacher.silence_message", "请保持安静，注意教师屏幕")
        ).strip()
        self._submit(
            lambda scope: self.runtime.set_silence(scope, True, message),
            "肃静已下发",
        )

    def _block_network(self) -> None:
        cidrs = [item.strip() for item in self.config.get("teacher.internal_cidrs", []) if item.strip()]
        self._submit(lambda scope: self.runtime.set_network_blocked(scope, True, cidrs), "外网限制已下发")

    def _restore_all(self) -> None:
        self._submit(self.runtime.restore_all, "临时控制已恢复")

    def _start_inspection(self) -> None:
        session = self._submit(
            lambda scope: self.runtime.open_inspection(scope, InspectionMode.THUMBNAIL),
            "巡视已开始",
        )
        if session is not None:
            self._patrol_target_ids.update(session.target_ids)

    def _stop_inspection(self) -> None:
        receipt = self._submit(self.runtime.close_inspection, "巡视已停止")
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
            self.operation_drawer.set_operations(snapshot.operations)
            self._refresh_audit(snapshot.operations)
            self._consume_process_result(snapshot)
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
        search = self.classroom_search.text().strip().casefold()
        devices = [
            item for item in self._snapshot.devices
            if not search or search in item.hostname.casefold()
            or search in str(item.metadata.get("ip", "")).casefold()
        ]
        signature = tuple(
            (item.device_id, item.hostname, item.online, item.group, str(item.metadata.get("ip", "")))
            for item in devices
        )
        if signature != self._device_signature:
            self._device_signature = signature
            positions = self.config.get("ui.seat_positions", {})
            self.seat_map.selected = set(self._selected_ids)
            self.seat_map.set_devices(devices, positions)
            self._refresh_patrol()
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
        self._scope_changed()

    def _save_seat_positions(self, positions: dict) -> None:
        self.config.set("ui.seat_positions", positions)
        self.config.save()

    def _set_selected_group(self) -> None:
        if not self._selected_ids:
            QMessageBox.warning(self, "无法分组", "请先选择学生设备")
            return
        group, accepted = QInputDialog.getText(self, "设置分组", "分组名称：")
        group = group.strip()
        if not accepted or not group:
            return
        try:
            changed = self.runtime.set_group(sorted(self._selected_ids), group)
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
            card.opened.connect(self._open_viewer)
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
        for path in paths:
            self._queue_path(path)

    def _add_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "添加文件夹")
        if path:
            self._queue_path(path)

    def _queue_path(self, path: str) -> None:
        if any(item["path"] == path for item in self.file_queue):
            return
        is_dir = os.path.isdir(path)
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
        self.process_table.setRowCount(0)

    def _consume_process_result(self, snapshot) -> None:
        if not self._process_command_id:
            return
        operation = snapshot.operation(self._process_command_id)
        if not operation or not operation.statuses:
            return
        status = operation.statuses[0]
        if status.state.value == "applied":
            processes = status.payload.get("processes", [])
            self.process_table.setRowCount(0)
            for process in processes:
                row = self.process_table.rowCount()
                self.process_table.insertRow(row)
                values = (
                    process.get("pid", ""), process.get("name", ""),
                    process.get("path", ""), process.get("username", ""),
                    process.get("protection_reason", "") if process.get("protected") else "",
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(str(value or ""))
                    item.setData(Qt.UserRole, process)
                    self.process_table.setItem(row, column, item)
            self._process_command_id = None
            self.operation_status.setText(f"已读取 {len(processes)} 个进程")
        elif status.state.value in {"failed", "timed_out"}:
            self._process_command_id = None
            self.operation_status.setText(f"进程读取失败：{status.detail}")

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
            device_id = str(self.process_device.currentData())
            if QMessageBox.question(self, "永久应用策略", f"永久禁止运行 {process.get('name')}？\n只有教师端可以解除。") != QMessageBox.Yes:
                return
            self.runtime.set_blacklist(
                DeviceIds([device_id]), str(process.get("name") or ""),
                str(process.get("path") or ""), True,
            )
        except Exception as exc:
            QMessageBox.warning(self, "无法添加策略", str(exc))

    def _remove_blacklist(self) -> None:
        row = self.blacklist_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "无法解除", "请先选择一条黑名单策略")
            return
        value = self.blacklist_table.item(row, 0).data(Qt.UserRole)
        try:
            self.runtime.set_blacklist(
                DeviceIds([value["device_id"]]), value["program"], value["path"], False
            )
        except Exception as exc:
            QMessageBox.warning(self, "无法解除", str(exc))

    def _power(self, kind: CommandKind) -> None:
        names = {CommandKind.POWER_OFF: "关机", CommandKind.REBOOT: "重启", CommandKind.LOGOFF: "注销"}
        try:
            scope = self._current_scope()
        except Exception as exc:
            QMessageBox.warning(self, "无法执行", str(exc))
            return
        if QMessageBox.question(self, names[kind], f"确定对当前作用域执行{names[kind]}？") != QMessageBox.Yes:
            return
        self.runtime.power(scope, kind)

    def _wake(self) -> None:
        try:
            results = self.runtime.wake(self._current_scope())
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
            count = self.runtime.server.push_update(path, version, target_ids)
            if count <= 0:
                raise ValueError("更新包签名无效，或当前作用域没有在线设备")
            self.operation_status.setText(f"更新通知已发送 · {count} 台")
        except Exception as exc:
            QMessageBox.warning(self, "无法部署更新", str(exc))

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
        self.blacklist_table.setRowCount(0)
        for device in devices:
            row = self.maintenance_table.rowCount()
            self.maintenance_table.insertRow(row)
            meta = device.metadata
            values = (
                device.hostname, meta.get("ip", ""), meta.get("mac", ""),
                meta.get("os", "Windows"), meta.get("version", ""),
                "在线" if device.online else "离线",
            )
            for column, value in enumerate(values):
                self.maintenance_table.setItem(row, column, QTableWidgetItem(str(value)))
            for state in device.expected:
                if not state.domain.startswith("blacklist:") or not state.value:
                    continue
                identity = state.domain.split(":", 1)[1]
                policy_row = self.blacklist_table.rowCount()
                self.blacklist_table.insertRow(policy_row)
                is_path = "\\" in identity or "/" in identity
                data = {
                    "device_id": device.device_id,
                    "program": ntpath.basename(identity) if is_path else identity,
                    "path": identity if is_path else "",
                }
                first = QTableWidgetItem(device.hostname)
                first.setData(Qt.UserRole, data)
                self.blacklist_table.setItem(policy_row, 0, first)
                self.blacklist_table.setItem(policy_row, 1, QTableWidgetItem(identity))
                self.blacklist_table.setItem(policy_row, 2, QTableWidgetItem("永久生效"))

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
