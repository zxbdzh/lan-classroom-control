"""Service-owned UltraVNC server; desktop input is owned by UltraVNC."""

import configparser
import os
import socket
import threading
import time

import psutil

from common.config import get_config
from common.ultravnc import ULTRAVNC_PORT, ULTRAVNC_SERVICE, find_binary


class UltraVNCServer:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.port = int(get_config().get("student.ultravnc_port", ULTRAVNC_PORT))
        self.active = False

    def start(self) -> dict:
        with self._lock:
            import win32service
            import win32serviceutil
            executable = find_binary(
                "winvnc.exe", str(get_config().get("student.ultravnc_server", ""))
            )
            manager = win32service.OpenSCManager(
                None, None, win32service.SC_MANAGER_ALL_ACCESS
            )
            service = None
            try:
                try:
                    service = win32service.OpenService(
                        manager, ULTRAVNC_SERVICE, win32service.SERVICE_ALL_ACCESS
                    )
                except Exception as exc:
                    if getattr(exc, "winerror", None) != 1060:
                        raise
                    service = win32service.CreateService(
                        manager, ULTRAVNC_SERVICE, "LanClassroom UltraVNC",
                        win32service.SERVICE_ALL_ACCESS,
                        win32service.SERVICE_WIN32_OWN_PROCESS,
                        win32service.SERVICE_DEMAND_START,
                        win32service.SERVICE_ERROR_NORMAL,
                        f'"{executable}" -service', None, 0, None, None, None,
                    )
                image = str(win32service.QueryServiceConfig(service)[3])
                if image.casefold() != f'"{executable}" -service'.casefold():
                    raise RuntimeError("课堂 UltraVNC 服务路径与当前安装包不一致，请修复安装")
                if win32service.QueryServiceStatus(service)[1] != win32service.SERVICE_STOPPED:
                    self.stop()
                self._write_configuration()
                self._firewall(True, executable)
                win32service.StartService(service, None)
                win32serviceutil.WaitForServiceStatus(
                    ULTRAVNC_SERVICE, win32service.SERVICE_RUNNING, 5
                )
                deadline = time.monotonic() + 6.0
                while time.monotonic() < deadline:
                    try:
                        with socket.create_connection(("127.0.0.1", self.port), 0.4) as conn:
                            if conn.recv(12).startswith(b"RFB "):
                                self.active = True
                                return {"backend": "ultravnc", "port": self.port, "ready": True}
                    except OSError:
                        pass
                    time.sleep(0.1)
                raise RuntimeError("UltraVNC 服务已启动，但未收到 RFB 就绪响应")
            except Exception:
                self.stop()
                raise
            finally:
                if service:
                    win32service.CloseServiceHandle(service)
                win32service.CloseServiceHandle(manager)

    def stop(self) -> bool:
        with self._lock:
            import win32service
            manager = None
            service = None
            try:
                manager = win32service.OpenSCManager(
                    None, None, win32service.SC_MANAGER_CONNECT
                )
                service = win32service.OpenService(
                    manager, ULTRAVNC_SERVICE,
                    win32service.SERVICE_QUERY_CONFIG
                    | win32service.SERVICE_QUERY_STATUS | win32service.SERVICE_STOP,
                )
                status = win32service.QueryServiceStatusEx(service)
                if status["CurrentState"] != win32service.SERVICE_STOPPED:
                    executable = find_binary(
                        "winvnc.exe", str(get_config().get("student.ultravnc_server", ""))
                    )
                    image = str(win32service.QueryServiceConfig(service)[3])
                    if image.casefold() != f'"{executable}" -service'.casefold():
                        raise RuntimeError("课堂 UltraVNC 服务路径与当前安装包不一致，请修复安装")
                    processes = self._service_processes(status["ProcessId"], executable)
                    if status["CurrentState"] != win32service.SERVICE_STOP_PENDING:
                        try:
                            win32service.ControlService(service, win32service.SERVICE_CONTROL_STOP)
                        except Exception as exc:
                            if getattr(exc, "winerror", None) != 1062:
                                raise
                    # 上游服务退出实测需 7–11 秒；先正常停止，再仅收尾已核实归属的进程。
                    if not self._wait_stopped(service, 0.6):
                        for process in reversed(processes):
                            try:
                                process.terminate()
                            except psutil.NoSuchProcess:
                                pass
                    _, alive = psutil.wait_procs(processes, timeout=0.6)
                    if alive or not self._wait_stopped(service, 0.6):
                        raise RuntimeError("课堂 UltraVNC 尚未完全退出，稍后自动重试")
            except Exception as exc:
                if getattr(exc, "winerror", None) != 1060:
                    raise
            finally:
                if service is not None:
                    win32service.CloseServiceHandle(service)
                if manager is not None:
                    win32service.CloseServiceHandle(manager)
                self._firewall(False)
            self.active = False
            return True

    @staticmethod
    def _service_processes(process_id: int, executable: str) -> list:
        if not process_id:
            raise RuntimeError("课堂 UltraVNC 服务没有可核实的进程")
        expected = os.path.normcase(os.path.realpath(executable))
        try:
            root = psutil.Process(process_id)
            if os.path.normcase(os.path.realpath(root.exe())) != expected:
                raise RuntimeError("课堂 UltraVNC 服务进程路径不匹配")
            if "-service" not in root.cmdline()[1:]:
                raise RuntimeError("课堂 UltraVNC 服务进程参数不匹配")
            # psutil 保留创建时间，避免 PID 重用误伤；只处理本服务树中同路径的 VNC。
            return [root] + [
                child for child in root.children(recursive=True)
                if os.path.normcase(os.path.realpath(child.exe())) == expected
            ]
        except psutil.NoSuchProcess:
            return []

    @staticmethod
    def _wait_stopped(service, timeout_seconds: float) -> bool:
        import win32service
        deadline = time.monotonic() + timeout_seconds
        while True:
            if win32service.QueryServiceStatusEx(service)["CurrentState"] == win32service.SERVICE_STOPPED:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.02)

    def _write_configuration(self) -> None:
        directory = os.path.join(
            os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "UltraVNC"
        )
        os.makedirs(directory, exist_ok=True)
        config = configparser.ConfigParser()
        config.optionxform = str
        config["admin"] = {
            "AuthRequired": "0", "SocketConnect": "1", "AutoPortSelect": "0",
            "PortNumber": str(self.port), "HTTPConnect": "0",
            "InputsEnabled": "1", "LocalInputsDisabled": "0",
            "FileTransferEnabled": "0", "QuerySetting": "2",
            "QueryAccept": "2", "QueryIfNoLogon": "0",
            "LockSetting": "0", "RemoveWallpaper": "1",
            "AllowProperties": "0", "AllowEditClients": "0", "AllowShutdown": "0",
            "DisableTrayIcon": "1", "BlankMonitorEnabled": "0",
        }
        config["poll"] = {
            "TurboMode": "1", "EnableDriver": "0", "EnableHook": "1",
            "EnableVirtual": "0", "PollFullScreen": "1",
        }
        # 定制服务名对应同名 ini；不改动用户另行安装的 uvnc_service 配置。
        path = os.path.join(directory, ULTRAVNC_SERVICE + ".ini")
        with open(path + ".tmp", "w", encoding="ascii") as handle:
            config.write(handle)
        os.replace(path + ".tmp", path)

    @staticmethod
    def _firewall(enabled: bool, executable: str = "") -> None:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        try:
            policy = win32com.client.Dispatch("HNetCfg.FwPolicy2")
            name = "LanClassroomUltraVNC"
            for rule in list(policy.Rules):
                if str(rule.Name) == name:
                    policy.Rules.Remove(name)
            if enabled:
                rule = win32com.client.Dispatch("HNetCfg.FWRule")
                rule.Name = name
                rule.ApplicationName = executable
                rule.Protocol = 6
                rule.LocalPorts = str(get_config().get("student.ultravnc_port", ULTRAVNC_PORT))
                rule.Direction = 1
                rule.Action = 1
                rule.Profiles = 0x7FFFFFFF
                rule.Enabled = True
                policy.Rules.Add(rule)
        finally:
            pythoncom.CoUninitialize()
