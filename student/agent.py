"""Hidden active-session agent for desktop-only student actions."""

from __future__ import annotations

import sys
import threading
import time

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication

from common.logger import get_logger
from student.core.v2.ipc import STUDENT_PIPE_NAME, WindowsNamedPipeConnector
from student.core.v2.runtime import SessionAgentRuntime
from student.core.v2.session_actions import DesktopSessionActions

logger = get_logger("student_agent")
_AGENT_MUTEX = None


def acquire_agent_instance() -> bool:
    global _AGENT_MUTEX
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        )
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(
            None, False, r"Local\LanClassroomStudentAgentV2"
        )
        if not handle:
            return True
        _AGENT_MUTEX = handle
        return ctypes.get_last_error() != 183
    except Exception as exc:
        logger.warning(f"Cannot create student agent instance lock: {exc}")
        return True


class StudentAgentApplication:
    def __init__(self) -> None:
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
        self.app = QApplication(sys.argv)
        self.app.setApplicationName("LanClassroom Student Agent")
        self.app.setQuitOnLastWindowClosed(False)
        # The service injects the durable device ID into every desktop action.
        # The standard-user agent must not open the service-owned ProgramData DB.
        self.actions = DesktopSessionActions("")
        self._stop_event = threading.Event()
        self._runtime = None
        self._worker = threading.Thread(target=self._connect_loop, daemon=True)
        self.app.aboutToQuit.connect(self.stop)

    def run(self) -> int:
        self._worker.start()
        return self.app.exec_()

    def stop(self) -> None:
        self._stop_event.set()
        runtime = self._runtime
        self._runtime = None
        if runtime:
            runtime.stop()
        self.actions.close()

    def _connect_loop(self) -> None:
        connector = WindowsNamedPipeConnector()
        while not self._stop_event.is_set():
            try:
                channel = connector.connect(STUDENT_PIPE_NAME)
                runtime = SessionAgentRuntime(channel)
                for action in (
                    "ping", "broadcast.start", "broadcast.stop", "silence.start",
                    "silence.stop", "input.lock", "input.unlock", "inspection.open",
                    "inspection.close", "remote_control.start", "remote_control.stop",
                    "remote_control.input", "restore_all",
                ):
                    runtime.register_handler(
                        action,
                        lambda params, name=action: self.actions.handle(name, params),
                    )
                self._runtime = runtime
                runtime.start()
                while not self._stop_event.wait(0.5):
                    thread = runtime._thread
                    if thread is None or not thread.is_alive():
                        break
                runtime.stop()
                channel.close()
                self._runtime = None
            except Exception as exc:
                logger.debug(f"Session agent pipe unavailable: {exc}")
            self._stop_event.wait(2.0)


def main() -> None:
    if not acquire_agent_instance():
        return
    sys.exit(StudentAgentApplication().run())


if __name__ == "__main__":
    main()
