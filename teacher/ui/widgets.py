"""Purpose-built widgets for the classroom operator workflow."""

from __future__ import annotations

import os
import re
import time
from typing import Callable, Dict, Iterable, List, Optional

from PyQt5.QtCore import QEvent, QMimeData, QPoint, QSize, Qt, pyqtSignal
from PyQt5.QtGui import (
    QColor, QDrag, QDragEnterEvent, QDragMoveEvent, QDropEvent, QFont,
    QImage, QPainter, QPixmap,
)
from PyQt5.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)


def hostname_order(hostname: str):
    match = re.search(r"(\d+)\s*$", hostname or "")
    return (0, int(match.group(1))) if match else (1, (hostname or "").casefold())


class ToggleSwitch(QAbstractButton):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(44, 24)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        if not self.isEnabled():
            painter.setOpacity(0.4)
        painter.setBrush(self.palette().highlight() if self.isChecked() else QColor("#98A2B3"))
        painter.drawRoundedRect(1, 1, 42, 22, 11, 11)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(23 if self.isChecked() else 3, 3, 18, 18)
        if self.hasFocus():
            painter.setBrush(Qt.NoBrush)
            painter.setPen(self.palette().highlight().color())
            painter.drawRoundedRect(0, 0, 43, 23, 11, 11)


class FileDropTable(QTableWidget):
    paths_dropped = pyqtSignal(list)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setAcceptDrops(True)
        self.setDragEnabled(False)
        self.setDragDropMode(QAbstractItemView.DropOnly)
        self.setDefaultDropAction(Qt.CopyAction)
        self.setDropIndicatorShown(False)

    @staticmethod
    def _local_paths(mime: QMimeData) -> List[str]:
        if not mime.hasUrls():
            return []
        paths = []
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            path = url.toLocalFile()
            if path and (os.path.isfile(path) or os.path.isdir(path)):
                paths.append(path)
        return paths

    def _accept_file_drag(self, event: QDropEvent) -> None:
        if event.possibleActions() & Qt.CopyAction and self._local_paths(event.mimeData()):
            # 入队不是移动文件；即使拖放源建议移动，也不能让资源管理器删除原件。
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        self._accept_file_drag(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        # 不走表格默认的行插入判定，已有行和空白区域都允许添加文件。
        self._accept_file_drag(event)

    def dropEvent(self, event: QDropEvent) -> None:
        paths = self._local_paths(event.mimeData())
        if not paths or not event.possibleActions() & Qt.CopyAction:
            event.ignore()
            return
        self.paths_dropped.emit(paths)
        event.setDropAction(Qt.CopyAction)
        event.accept()


class DeviceNameWidget(QWidget):
    copy_requested = pyqtSignal(str)
    note_requested = pyqtSignal(str)
    remove_requested = pyqtSignal(str)

    def __init__(self, device, dark: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.device_id = device.device_id
        self._hostname = device.hostname
        self.device_menu = QMenu(self)
        self.device_menu.addAction("复制设备名").triggered.connect(
            lambda _checked: self.copy_requested.emit(self.device_id)
        )
        self.device_menu.addAction("编辑备注").triggered.connect(
            lambda _checked: self.note_requested.emit(self.device_id)
        )
        self.device_menu.addSeparator()
        self.remove_action = self.device_menu.addAction("移除设备")
        self.remove_action.triggered.connect(lambda _checked: self.remove_requested.emit(self.device_id))
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda point: self.device_menu.popup(self.mapToGlobal(point))
        )
        self.setToolTip("右键可复制设备名、编辑备注或移除设备")
        self.setMinimumWidth(0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.copy_button = QPushButton(device.hostname)
        self.copy_button.setMinimumWidth(0)
        self.copy_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.copy_button.setFocusPolicy(Qt.NoFocus)
        self.copy_button.setCursor(Qt.PointingHandCursor)
        self.copy_button.setToolTip(f"点击复制设备名：{device.hostname}")
        self.copy_button.setAccessibleName(f"复制设备名 {device.hostname}")
        self.copy_button.installEventFilter(self)
        self.copy_button.setStyleSheet(
            "min-height:22px; padding:0; border:0; text-align:left; font-weight:600;"
            + (" color:white; background:transparent;" if dark else "")
        )
        self.copy_button.clicked.connect(lambda: self.copy_requested.emit(self.device_id))
        self.note_button = QPushButton("备注")
        self.note_button.setFocusPolicy(Qt.NoFocus)
        self.note_button.setFixedWidth(36)
        self.note_button.setStyleSheet(
            "min-height:22px; padding:0 3px;"
            + (" color:#D5DBE5; background:#171C24; border:1px solid #475467;" if dark else "")
        )
        self.note_button.setToolTip("添加或编辑备注（仅教师端保存，留空可清除）")
        self.note_button.setAccessibleName(f"编辑设备备注 {device.hostname}")
        self.note_button.clicked.connect(lambda: self.note_requested.emit(self.device_id))
        row.addWidget(self.copy_button, 1)
        row.addWidget(self.note_button)
        layout.addLayout(row)
        self.note_label = QLabel(device.note)
        self.note_label.setTextFormat(Qt.PlainText)
        self.note_label.setMinimumWidth(0)
        self.note_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.note_label.setToolTip(device.note)
        if dark:
            self.note_label.setStyleSheet("color:#BAC4D2;")
        else:
            self.note_label.setProperty("muted", True)
        self.note_label.setVisible(bool(device.note))
        layout.addWidget(self.note_label)

    def eventFilter(self, watched, event) -> bool:
        if watched is self.copy_button and event.type() == QEvent.Resize:
            # 卡片变窄只省略显示，不改变复制值；保留首尾方便区分相似机名。
            self.copy_button.setText(self.copy_button.fontMetrics().elidedText(
                self._hostname, Qt.ElideMiddle, max(0, event.size().width() - 2),
            ))
        return super().eventFilter(watched, event)


class SeatTile(QFrame):
    clicked = pyqtSignal(str, bool)
    copy_requested = pyqtSignal(str)
    note_requested = pyqtSignal(str)
    remove_requested = pyqtSignal(str)

    def __init__(self, device, parent=None) -> None:
        super().__init__(parent)
        self.device = device
        self._press_pos = QPoint()
        self.setObjectName("SeatTile")
        self.setProperty("selected", False)
        self.setProperty("offline", not device.online)
        self.setMinimumSize(126, 76)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)
        top = QHBoxLayout()
        self.name = DeviceNameWidget(device)
        self.name.copy_requested.connect(self.copy_requested.emit)
        self.name.note_requested.connect(self.note_requested.emit)
        self.name.remove_requested.connect(self.remove_requested.emit)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda point: self.name.device_menu.popup(self.mapToGlobal(point))
        )
        self.dot = QLabel("●")
        self.dot.setStyleSheet(
            "color: #16836B;" if device.online else "color: #98A2B3;"
        )
        top.addWidget(self.name, 1)
        top.addWidget(self.dot)
        layout.addLayout(top)
        meta = device.metadata or {}
        self.detail = QLabel(str(meta.get("ip") or "未连接"))
        self.detail.setProperty("muted", True)
        layout.addWidget(self.detail)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", bool(selected))
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event) -> None:
        self._press_pos = event.pos()
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.device.device_id, bool(event.modifiers() & Qt.ControlModifier))
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if not (event.buttons() & Qt.LeftButton):
            return
        if (event.pos() - self._press_pos).manhattanLength() < 10:
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(self.device.device_id)
        drag.setMimeData(mime)
        drag.exec_(Qt.MoveAction)


class EmptySeat(QFrame):
    def __init__(self, index: int, parent=None) -> None:
        super().__init__(parent)
        self.index = index
        self.setObjectName("EmptySeat")
        self.setMinimumSize(126, 76)


class SeatMapWidget(QScrollArea):
    selection_changed = pyqtSignal(tuple)
    positions_changed = pyqtSignal(dict)
    copy_requested = pyqtSignal(str)
    note_requested = pyqtSignal(str)
    remove_requested = pyqtSignal(str)

    def __init__(self, columns: int = 6, rows: int = 10, parent=None) -> None:
        super().__init__(parent)
        self.columns = max(2, int(columns))
        self.rows = max(1, int(rows))
        self.positions: Dict[str, int] = {}
        self.selected = set()
        self.devices = []
        self.setWidgetResizable(True)
        self.setAcceptDrops(True)
        self.canvas = QWidget()
        self.grid = QGridLayout(self.canvas)
        self.grid.setContentsMargins(18, 14, 18, 24)
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(12)
        self.setWidget(self.canvas)

    def set_devices(self, devices: Iterable, positions: Optional[dict] = None) -> None:
        self.devices = list(devices)
        if positions is not None:
            self.positions = {str(k): int(v) for k, v in positions.items()}
        assigned = set(self.positions.values())
        next_position = 0
        for device in sorted(self.devices, key=lambda item: hostname_order(item.hostname)):
            if device.device_id in self.positions:
                continue
            while next_position in assigned:
                next_position += 1
            self.positions[device.device_id] = next_position
            assigned.add(next_position)
        self._render()

    def set_columns(self, columns: int) -> None:
        self.columns = max(2, int(columns))
        self._render()

    def _render(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().hide()
                # 右键菜单可能仍在发送信号，保留父对象所有权到事件循环延迟销毁。
                item.widget().deleteLater()
        lectern = QLabel("讲台  ·  教师端")
        lectern.setObjectName("Lectern")
        lectern.setAlignment(Qt.AlignCenter)
        self.grid.addWidget(lectern, 0, 0, 1, self.columns)
        by_position = {self.positions.get(item.device_id, -1): item for item in self.devices}
        capacity = max(self.rows * self.columns, max(by_position.keys(), default=-1) + 1)
        for index in range(capacity):
            row, column = divmod(index, self.columns)
            device = by_position.get(index)
            if device is None:
                widget = EmptySeat(index)
            else:
                widget = SeatTile(device)
                widget.set_selected(device.device_id in self.selected)
                widget.clicked.connect(self._select)
                widget.copy_requested.connect(self.copy_requested.emit)
                widget.note_requested.connect(self.note_requested.emit)
                widget.remove_requested.connect(self.remove_requested.emit)
            self.grid.addWidget(widget, row + 1, column)
        row_count = (capacity + self.columns - 1) // self.columns
        self.grid.setRowStretch(row_count + 1, 1)
        minimum_height = (
            14 + 40 + 12 + row_count * 76 + max(0, row_count - 1) * 12 + 24
        )
        self.canvas.setMinimumHeight(minimum_height)

    def _select(self, device_id: str, additive: bool) -> None:
        if not additive:
            self.selected.clear()
        if additive and device_id in self.selected:
            self.selected.remove(device_id)
        else:
            self.selected.add(device_id)
        # 只更新选中样式，保留按住鼠标的控件，才能继续接收拖动事件。
        for tile in self.canvas.findChildren(SeatTile):
            tile.set_selected(tile.device.device_id in self.selected)
        self.selection_changed.emit(tuple(sorted(self.selected)))

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        device_id = event.mimeData().text()
        point = self.canvas.mapFrom(self.viewport(), event.pos())
        target = self.canvas.childAt(point)
        while target and not isinstance(target, (SeatTile, EmptySeat)):
            target = target.parentWidget()
        if target is None or device_id not in self.positions:
            return
        target_index = (
            target.index if isinstance(target, EmptySeat) else self.positions[target.device.device_id]
        )
        source_index = self.positions[device_id]
        occupant = next((key for key, value in self.positions.items() if value == target_index), None)
        self.positions[device_id] = target_index
        if occupant and occupant != device_id:
            self.positions[occupant] = source_index
        self._render()
        self.positions_changed.emit(dict(self.positions))
        event.acceptProposedAction()


class PatrolCard(QFrame):
    opened = pyqtSignal(str)
    copy_requested = pyqtSignal(str)
    note_requested = pyqtSignal(str)
    remove_requested = pyqtSignal(str)

    def __init__(self, device, parent=None) -> None:
        super().__init__(parent)
        self.device = device
        self.setObjectName("PatrolCard")
        self.setMinimumSize(220, 150)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.image = QLabel("等待画面" if device.online else "设备离线")
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setStyleSheet("color:#98A2B3; background:#0D1117;")
        self.image.setMinimumHeight(124)
        footer = QFrame()
        footer.setObjectName("PatrolFooter")
        footer.setStyleSheet("QFrame#PatrolFooter { background:#171C24; }")
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(9, 7, 9, 7)
        self.name = DeviceNameWidget(device, dark=True)
        self.name.copy_requested.connect(self.copy_requested.emit)
        self.name.note_requested.connect(self.note_requested.emit)
        self.name.remove_requested.connect(self.remove_requested.emit)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda point: self.name.device_menu.popup(self.mapToGlobal(point))
        )
        footer_layout.addWidget(self.name)
        layout.addWidget(self.image, 1)
        layout.addWidget(footer)

    def update_frame(self, jpeg: bytes) -> None:
        image = QImage.fromData(jpeg)
        if image.isNull():
            return
        pixmap = QPixmap.fromImage(image).scaled(
            self.image.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.image.setPixmap(pixmap)

    def mouseDoubleClickEvent(self, event) -> None:
        self.opened.emit(self.device.device_id)
        super().mouseDoubleClickEvent(event)


class FullScreenViewer(QDialog):
    remote_input = pyqtSignal(str, dict)
    remote_toggled = pyqtSignal(str, bool)

    def __init__(self, device_id: str, hostname: str, parent=None) -> None:
        super().__init__(parent)
        self.device_id = device_id
        self.remote_enabled = False
        self._last_mouse_sent = 0.0
        self.setWindowTitle(hostname)
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setStyleSheet("background:#090C10; color:white;")
        self.setMouseTracking(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(14, 10, 14, 10)
        title = QLabel(hostname)
        title.setStyleSheet("color:white; font-size:15px; font-weight:600;")
        self.remote_button = QPushButton("远程控制")
        self.remote_button.setCheckable(True)
        self.remote_button.toggled.connect(self._toggle_remote)
        close = QPushButton("关闭")
        close.clicked.connect(self.close)
        toolbar.addWidget(title)
        toolbar.addStretch()
        toolbar.addWidget(self.remote_button)
        toolbar.addWidget(close)
        self.image = QLabel("等待学生画面")
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setMouseTracking(True)
        self.image.installEventFilter(self)
        layout.addLayout(toolbar)
        layout.addWidget(self.image, 1)

    def update_frame(self, jpeg: bytes) -> None:
        image = QImage.fromData(jpeg)
        if not image.isNull():
            self.image.setPixmap(QPixmap.fromImage(image).scaled(
                self.image.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            ))

    def _toggle_remote(self, enabled: bool) -> None:
        self.remote_enabled = enabled
        self.remote_toggled.emit(self.device_id, enabled)

    def eventFilter(self, watched, event):
        if watched is self.image and self.remote_enabled:
            if event.type() == QEvent.MouseMove:
                now = time.monotonic()
                if now - self._last_mouse_sent < 1.0 / 30.0:
                    return True
                position = self._normalized_image_position(event.x(), event.y())
                if position is None:
                    return True
                self._last_mouse_sent = now
                self.remote_input.emit(self.device_id, {
                    "type": "mouse_move", "x": position[0], "y": position[1]
                })
                return True
            if event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
                names = {Qt.LeftButton: "left", Qt.RightButton: "right", Qt.MiddleButton: "middle"}
                button = names.get(event.button())
                position = self._normalized_image_position(event.x(), event.y())
                if button and position is not None:
                    self.remote_input.emit(self.device_id, {
                        "type": "mouse_button", "button": button,
                        "pressed": event.type() == QEvent.MouseButtonPress,
                        "x": position[0], "y": position[1],
                    })
                    return True
            if event.type() == QEvent.Wheel:
                self.remote_input.emit(self.device_id, {
                    "type": "mouse_wheel", "delta": event.angleDelta().y()
                })
                return True
        return super().eventFilter(watched, event)

    def _normalized_image_position(self, x: int, y: int):
        pixmap = self.image.pixmap()
        if pixmap is None or pixmap.isNull():
            return None
        width, height = pixmap.width(), pixmap.height()
        left = (self.image.width() - width) / 2.0
        top = (self.image.height() - height) / 2.0
        if x < left or y < top or x > left + width or y > top + height:
            return None
        return ((x - left) / max(1, width), (y - top) / max(1, height))

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape and not self.remote_enabled:
            self.close()
            return
        if self.remote_enabled and not event.isAutoRepeat():
            self.remote_input.emit(self.device_id, {
                "type": "key", "virtual_key": event.nativeVirtualKey(),
                "text": event.text(), "pressed": True,
            })
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if self.remote_enabled and not event.isAutoRepeat():
            self.remote_input.emit(self.device_id, {
                "type": "key", "virtual_key": event.nativeVirtualKey(),
                "text": event.text(), "pressed": False,
            })
            return
        super().keyReleaseEvent(event)


class OperationDrawer(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("OperationDrawer")
        self.setFixedWidth(300)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 16, 14, 16)
        header = QLabel("最近操作")
        header.setStyleSheet("font-weight:600; font-size:15px;")
        self.list = QListWidget()
        layout.addWidget(header)
        layout.addWidget(self.list, 1)

    def set_operations(self, operations) -> None:
        self.list.clear()
        names = {
            "broadcast.start": "开始广播", "broadcast.stop": "停止广播",
            "silence.start": "黑屏肃静", "silence.stop": "解除肃静",
            "network.block": "禁止外网", "network.restore": "恢复网络",
            "usb_storage.block": "限制 USB 存储", "usb_storage.restore": "恢复 USB 存储",
            "classroom.restore_all": "全部恢复", "process.terminate": "结束进程",
            "blacklist.set": "添加应用策略", "blacklist.clear": "解除应用策略",
            "power.off": "关机", "power.reboot": "重启", "power.logoff": "注销",
        }
        for operation in reversed(tuple(operations)[-12:]):
            states = [status.state.value for status in operation.statuses]
            success = sum(1 for state in states if state == "applied")
            failed = sum(1 for state in states if state in {"failed", "timed_out"})
            label = names.get(operation.action.kind.value, operation.action.kind.value)
            item = QListWidgetItem(f"{label}\n{success}/{len(states)} 已执行" + (f" · {failed} 失败" if failed else ""))
            item.setData(Qt.UserRole, operation.command_id)
            details = [
                f"{status.device_id}：{status.detail}"
                for status in operation.statuses if status.detail
            ]
            item.setToolTip("\n".join(details))
            self.list.addItem(item)


__all__ = [
    "DeviceNameWidget", "FileDropTable", "FullScreenViewer", "OperationDrawer",
    "PatrolCard", "SeatMapWidget", "ToggleSwitch", "hostname_order"
]
