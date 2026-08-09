"""Launch the hidden student agent inside the active interactive session."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Optional

from common.logger import get_logger

logger = get_logger("student_session_launcher")

AGENT_EXE = "LanClassroom-Student-Agent.exe"


def installed_agent_path(service_executable: Optional[str] = None) -> Optional[str]:
    executable = os.path.abspath(service_executable or sys.executable)
    service_dir = os.path.dirname(executable)
    if os.path.basename(service_dir).casefold() != "service":
        return None
    return os.path.join(os.path.dirname(service_dir), "Agent", AGENT_EXE)


def launch_installed_agent(service_executable: Optional[str] = None) -> bool:
    if sys.platform != "win32":
        return False
    agent_path = installed_agent_path(service_executable)
    if not agent_path:
        logger.debug("Student service is not running from the installed Service layout")
        return False
    if not os.path.isfile(agent_path):
        logger.warning(f"Student agent executable not found: {agent_path}")
        return False
    token = None
    primary = None
    process_info = None
    environment = None
    win32profile = None
    try:
        import psutil
        import win32con
        import win32process
        import win32profile
        import win32security
        import win32ts

        session_id = _active_session_id(win32ts)
        if session_id is None:
            logger.info("No active user session is available for the student agent")
            return False
        if _agent_is_running(session_id, psutil, win32ts):
            return True

        token = win32ts.WTSQueryUserToken(session_id)
        primary = win32security.DuplicateTokenEx(
            token,
            win32con.MAXIMUM_ALLOWED,
            win32security.SecurityImpersonation,
            win32security.TokenPrimary,
        )
        environment = win32profile.CreateEnvironmentBlock(primary, False)
        startup = win32process.STARTUPINFO()
        startup.lpDesktop = r"winsta0\default"
        process_info = win32process.CreateProcessAsUser(
            primary,
            agent_path,
            subprocess.list2cmdline([agent_path]),
            None,
            None,
            False,
            win32con.CREATE_UNICODE_ENVIRONMENT | win32con.CREATE_NEW_PROCESS_GROUP,
            environment,
            os.path.dirname(agent_path),
            startup,
        )
        logger.info(f"Student agent launched in session {session_id}")
        return True
    except Exception as exc:
        logger.warning(f"Cannot launch student agent in active session: {exc}")
        return False
    finally:
        if process_info:
            for handle in process_info[:2]:
                try:
                    handle.Close()
                except Exception:
                    pass
        if environment is not None and win32profile is not None:
            destroy = getattr(win32profile, "DestroyEnvironmentBlock", None)
            if destroy is not None:
                try:
                    destroy(environment)
                except Exception:
                    pass
        for handle in (primary, token):
            if handle is not None:
                try:
                    handle.Close()
                except Exception:
                    pass


def _active_session_id(win32ts) -> Optional[int]:
    console_id = int(win32ts.WTSGetActiveConsoleSessionId())
    sessions = tuple(win32ts.WTSEnumerateSessions(None, 1, 0))
    active_ids = {
        int(item["SessionId"])
        for item in sessions
        if int(item.get("State", -1)) == int(win32ts.WTSActive)
    }
    if console_id != 0xFFFFFFFF and console_id in active_ids:
        return console_id
    return min(active_ids) if active_ids else None


def _agent_is_running(session_id: int, psutil, win32ts) -> bool:
    for process in psutil.process_iter(["pid", "name"]):
        try:
            info = process.info
            if str(info.get("name") or "").casefold() != AGENT_EXE.casefold():
                continue
            if int(win32ts.ProcessIdToSessionId(int(info["pid"]))) == int(session_id):
                return True
        except Exception:
            continue
    return False


__all__ = ["AGENT_EXE", "installed_agent_path", "launch_installed_agent"]
