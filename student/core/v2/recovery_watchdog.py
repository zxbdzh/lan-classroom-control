"""A separate SYSTEM process restores temporary controls if the service dies."""

import json
import os
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager

from common.logger import get_logger
from student.core.net_control import NetController
from student.core.usb_control import UsbStorageController
from student.core.v2.ultravnc import UltraVNCServer

logger = get_logger("recovery_watchdog")


def state_path() -> str:
    return os.path.join(
        os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
        "LanClassroom", "recovery-owner.json",
    )


@contextmanager
def recovery_lock():
    import win32api
    import win32event
    handle = win32event.CreateMutex(None, False, r"Global\LanClassroomRecoveryV2")
    acquired = False
    try:
        result = win32event.WaitForSingleObject(handle, 5000)
        if result not in (0, 0x80):
            raise RuntimeError("recovery lock timed out")
        acquired = True
        yield
    finally:
        if acquired:
            win32event.ReleaseMutex(handle)
        win32api.CloseHandle(handle)


def read_state() -> dict:
    with open(state_path(), "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_state(state: dict) -> None:
    path = state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as handle:
        json.dump(state, handle)
    os.replace(path + ".tmp", path)


class RecoveryWatchdog:
    def __init__(self) -> None:
        self.owner = str(uuid.uuid4())

    def start(self) -> None:
        import psutil
        with recovery_lock():
            # 与旧守护进程串行清理，再发布新所有者，避免旧进程解除新课堂状态。
            if not NetController().restore_stale_rules():
                raise RuntimeError("无法清理上次课堂禁网状态")
            if not UsbStorageController().restore_stale_policy():
                raise RuntimeError("无法清理上次课堂 USB 存储限制")
            UltraVNCServer().stop()
            write_state({
                "owner": self.owner, "pid": os.getpid(),
                "created": psutil.Process().create_time(),
                "active": False, "deadline": time.time() + 15.0,
            })
        args = (
            [sys.executable, "--recovery-watchdog", self.owner]
            if getattr(sys, "frozen", False) else
            [sys.executable, "-m", "student.core.v2.recovery_watchdog", self.owner]
        )
        subprocess.Popen(
            args, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            close_fds=True,
        )

    def renew(self, active: bool) -> None:
        try:
            with recovery_lock():
                state = read_state()
                if state.get("owner") == self.owner:
                    state.update(active=bool(active), deadline=time.time() + 15.0)
                    write_state(state)
        except Exception as exc:
            logger.error(f"Cannot renew independent recovery watchdog: {exc}")

    def attach_agent(self, pid: int) -> None:
        import psutil
        from student.core.v2.session_launcher import installed_agent_path
        process = psutil.Process(pid)
        expected = installed_agent_path()
        if not expected or os.path.normcase(process.exe()) != os.path.normcase(expected):
            return
        with recovery_lock():
            state = read_state()
            if state.get("owner") == self.owner:
                state.update(agent_pid=pid, agent_created=process.create_time())
                write_state(state)

    def reset_agent(self) -> None:
        with recovery_lock():
            state = read_state()
            if state.get("owner") == self.owner:
                release_agent(state)
                state.pop("agent_pid", None)
                state.pop("agent_created", None)
                write_state(state)


def release_agent(state: dict) -> None:
    import psutil
    if not state.get("agent_pid"):
        return
    try:
        process = psutil.Process(int(state["agent_pid"]))
        if process.create_time() != float(state["agent_created"]):
            return
        # 仅终止已登记的本项目代理实例，清掉卡住的遮罩和进程内键鼠钩子。
        process.terminate()
        try:
            process.wait(timeout=1.0)
        except psutil.TimeoutExpired:
            process.kill()
            process.wait(timeout=1.0)
    except psutil.NoSuchProcess:
        pass


def watch(owner: str) -> None:
    import psutil
    while True:
        parent_dead = False
        try:
            with recovery_lock():
                state = read_state()
                if state.get("owner") != owner:
                    return
                try:
                    parent = psutil.Process(int(state["pid"]))
                    parent_dead = parent.create_time() != float(state["created"])
                except psutil.NoSuchProcess:
                    parent_dead = True
                expired = bool(state.get("active")) and time.time() >= state["deadline"]
                if parent_dead or expired:
                    # 不依赖学生服务自己的 finally；服务被强杀后规则仍能恢复。
                    network_ok = NetController().restore_stale_rules()
                    usb_ok = UsbStorageController().restore_stale_policy()
                    try:
                        remote_ok = UltraVNCServer().stop()
                    finally:
                        release_agent(state)
                    if network_ok and usb_ok and remote_ok:
                        state["active"] = False
                        write_state(state)
                        if parent_dead:
                            return
        except FileNotFoundError:
            return
        except Exception as exc:
            logger.warning(f"Recovery retry required: {exc}")
        time.sleep(0.5)


if __name__ == "__main__":
    watch(sys.argv[1])
