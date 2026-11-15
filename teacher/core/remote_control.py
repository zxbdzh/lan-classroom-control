"""Independent viewer lifecycle, gated by the student's APPLIED acknowledgement."""

import subprocess
import time
from typing import Callable

from common.config import get_config
from common.ultravnc import ULTRAVNC_PORT, find_binary
from common.v2.model import DeviceCommandState


class RemoteControlWindows:
    def __init__(self, stopped: Callable[[str], object]) -> None:
        self.sessions = {}
        self._stopped = stopped
        self.messages = []

    def prepare(self) -> str:
        return find_binary(
            "vncviewer.exe", str(get_config().get("teacher.ultravnc_viewer", ""))
        )

    def add(self, device_id: str, host: str, command_id: str, executable: str) -> None:
        self.sessions[device_id] = {
            "host": host, "command_id": command_id, "executable": executable,
            "deadline": time.monotonic() + 20.0, "process": None,
        }

    def poll(self, snapshot) -> None:
        for device_id, session in tuple(self.sessions.items()):
            try:
                device = snapshot.device(device_id)
                if device is None or not device.online:
                    raise RuntimeError("设备已离线")
                process = session["process"]
                if process is not None:
                    if process.poll() is not None:
                        self.stop(device_id)
                    continue
                operation = next(
                    (item for item in snapshot.operations
                     if item.command_id == session["command_id"]), None
                )
                status = next(
                    (item for item in operation.statuses if item.device_id == device_id),
                    None,
                ) if operation else None
                if status and status.state == DeviceCommandState.APPLIED:
                    if status.payload.get("backend") != "ultravnc":
                        raise RuntimeError("学生端不支持 UltraVNC，请更新学生端")
                    port = int(status.payload.get("port", ULTRAVNC_PORT))
                    args = [
                        session["executable"], f'{session["host"]}::{port}',
                        "-autoscaling", "-quickoption", "3", "-nostatus",
                        "-nohotkeys", "-disablesponsor",
                    ]
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startupinfo.wShowWindow = 4  # 独立窗口显示但不主动抢占教师当前焦点。
                    session["process"] = subprocess.Popen(
                        args, startupinfo=startupinfo, close_fds=True
                    )
                    self.messages.append(f"{device.hostname}：已打开 UltraVNC 窗口，连接结果见窗口")
                elif status and status.state in {
                    DeviceCommandState.FAILED, DeviceCommandState.OFFLINE,
                    DeviceCommandState.EXPIRED, DeviceCommandState.SUPERSEDED,
                }:
                    raise RuntimeError(status.detail or status.state.value)
                elif time.monotonic() >= session["deadline"]:
                    raise RuntimeError("等待学生端远控就绪超时")
            except Exception as exc:
                self.messages.append(f"{device_id}：远控结束：{exc}")
                self.stop(device_id)

    def stop(self, device_id: str) -> None:
        session = self.sessions.pop(device_id, None)
        if session:
            process = session["process"]
            try:
                if process and process.poll() is None:
                    process.terminate()
            finally:
                self._stopped(device_id)

    def close(self) -> None:
        for device_id in tuple(self.sessions):
            self.stop(device_id)
