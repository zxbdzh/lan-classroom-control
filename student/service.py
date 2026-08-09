"""Windows service entrypoint for privileged student-side control."""

from __future__ import annotations

import sys
import threading

from common.logger import get_logger
from student.core.student_client import StudentClient
from student.core.v2.ipc import STUDENT_PIPE_NAME, WindowsNamedPipeListener
from student.core.v2.runtime import SessionAgentProxy
from student.core.v2.session_launcher import launch_installed_agent

logger = get_logger("student_service")

try:
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil
except ImportError:
    servicemanager = None
    win32event = None
    win32service = None
    win32serviceutil = None


if win32serviceutil is not None:
    class LanClassroomStudentService(win32serviceutil.ServiceFramework):
        _svc_name_ = "LanClassroomStudentV2"
        _svc_display_name_ = "LanClassroom Student Service"
        _svc_description_ = "LAN classroom control service"

        def __init__(self, args):
            super().__init__(args)
            self._stop_handle = win32event.CreateEvent(None, 0, 0, None)
            self._stop_event = threading.Event()
            self._listener = None
            self._client = None
            self._pipe_thread = None

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self._stop_event.set()
            win32event.SetEvent(self._stop_handle)
            if self._listener:
                self._listener.close()

        def SvcDoRun(self):
            servicemanager.LogInfoMsg("LanClassroom student service starting")
            self._client = StudentClient(service_mode=True)
            self._client.start()
            self._pipe_thread = threading.Thread(target=self._pipe_loop, daemon=True)
            self._pipe_thread.start()
            win32event.WaitForSingleObject(self._stop_handle, win32event.INFINITE)
            if self._client:
                self._client.stop()
            servicemanager.LogInfoMsg("LanClassroom student service stopped")

        def _pipe_loop(self):
            while not self._stop_event.is_set():
                channel = None
                try:
                    self._listener = WindowsNamedPipeListener(STUDENT_PIPE_NAME)
                    launch_installed_agent()
                    channel = self._listener.accept()
                    proxy = SessionAgentProxy(channel)
                    self._client.attach_session_agent(proxy)
                    while not self._stop_event.wait(3.0):
                        if not proxy.call("ping", timeout=2.0).success:
                            break
                except Exception as exc:
                    if not self._stop_event.is_set():
                        logger.warning(f"Session agent connection failed: {exc}")
                finally:
                    self._client.attach_session_agent(None)
                    if channel:
                        channel.close()
                    if self._listener:
                        self._listener.close()
                    self._listener = None


def main() -> None:
    if win32serviceutil is None:
        raise RuntimeError("pywin32 is required to run the Windows service")
    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(LanClassroomStudentService)
        servicemanager.StartServiceCtrlDispatcher()
        return
    sys.exit(win32serviceutil.HandleCommandLine(LanClassroomStudentService))


if __name__ == "__main__":
    main()
