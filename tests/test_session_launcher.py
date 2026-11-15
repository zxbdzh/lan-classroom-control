import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from student.core.v2 import session_launcher


def test_installed_agent_path_resolves_sibling_agent_directory():
    service = os.path.join(
        "C:\\Program Files",
        "LanClassroom",
        "Service",
        "LanClassroom-Student-Service.exe",
    )

    assert session_launcher.installed_agent_path(service) == os.path.join(
        "C:\\Program Files",
        "LanClassroom",
        "Agent",
        session_launcher.AGENT_EXE,
    )
    assert session_launcher.installed_agent_path(
        os.path.join("C:\\dev", "student", "service.exe")
    ) is None


def test_active_session_prefers_active_console_then_lowest_active():
    win32ts = SimpleNamespace(
        WTSActive=0,
        WTSGetActiveConsoleSessionId=lambda: 4,
        WTSEnumerateSessions=lambda *_: (
            {"SessionId": 9, "State": 0},
            {"SessionId": 4, "State": 0},
        ),
    )
    assert session_launcher._active_session_id(win32ts) == 4

    win32ts.WTSGetActiveConsoleSessionId = lambda: 8
    assert session_launcher._active_session_id(win32ts) == 4


def test_launch_skips_agent_already_running_in_active_session(monkeypatch, tmp_path):
    agent_path = _installed_agent(tmp_path)
    modules = _win32_modules()
    process = SimpleNamespace(info={"pid": 42, "name": session_launcher.AGENT_EXE})
    modules["psutil"].process_iter.return_value = [process]
    modules["win32ts"].ProcessIdToSessionId.return_value = 4

    monkeypatch.setattr(session_launcher.sys, "platform", "win32")
    with patch.dict(sys.modules, modules):
        assert session_launcher.launch_installed_agent(str(agent_path)) is True

    modules["win32ts"].WTSQueryUserToken.assert_not_called()


def test_launch_uses_active_user_token_and_closes_all_handles(monkeypatch, tmp_path):
    service_path = _installed_agent(tmp_path)
    modules = _win32_modules()
    modules["psutil"].process_iter.return_value = []
    token = MagicMock()
    primary = MagicMock()
    process_handle = MagicMock()
    thread_handle = MagicMock()
    environment = object()
    modules["win32ts"].WTSQueryUserToken.return_value = token
    modules["win32security"].DuplicateTokenEx.return_value = primary
    modules["win32profile"].CreateEnvironmentBlock.return_value = environment
    modules["win32process"].CreateProcessAsUser.return_value = (
        process_handle,
        thread_handle,
        100,
        101,
    )

    monkeypatch.setattr(session_launcher.sys, "platform", "win32")
    with patch.dict(sys.modules, modules):
        assert session_launcher.launch_installed_agent(str(service_path)) is True

    agent_path = session_launcher.installed_agent_path(str(service_path))
    modules["win32security"].DuplicateTokenEx.assert_called_once_with(
        ExistingToken=token,
        ImpersonationLevel=modules["win32security"].SecurityImpersonation,
        DesiredAccess=modules["win32con"].MAXIMUM_ALLOWED,
        TokenType=modules["win32security"].TokenPrimary,
    )
    call = modules["win32process"].CreateProcessAsUser.call_args.args
    assert call[0] is primary
    assert call[1] == agent_path
    assert call[2] == os.path.basename(agent_path) or agent_path in call[2]
    assert call[7] is environment
    assert call[8] == os.path.dirname(agent_path)
    assert call[9].lpDesktop == r"winsta0\default"
    process_handle.Close.assert_called_once_with()
    thread_handle.Close.assert_called_once_with()
    primary.Close.assert_called_once_with()
    token.Close.assert_called_once_with()
    modules["win32profile"].DestroyEnvironmentBlock.assert_called_once_with(environment)


def test_launch_closes_tokens_when_process_creation_fails(monkeypatch, tmp_path):
    service_path = _installed_agent(tmp_path)
    modules = _win32_modules()
    modules["psutil"].process_iter.return_value = []
    token = MagicMock()
    primary = MagicMock()
    modules["win32ts"].WTSQueryUserToken.return_value = token
    modules["win32security"].DuplicateTokenEx.return_value = primary
    modules["win32process"].CreateProcessAsUser.side_effect = OSError("denied")

    monkeypatch.setattr(session_launcher.sys, "platform", "win32")
    with patch.dict(sys.modules, modules):
        assert session_launcher.launch_installed_agent(str(service_path)) is False

    primary.Close.assert_called_once_with()
    token.Close.assert_called_once_with()
    modules["win32profile"].DestroyEnvironmentBlock.assert_called_once()


def _installed_agent(tmp_path):
    service_dir = tmp_path / "Service"
    agent_dir = tmp_path / "Agent"
    service_dir.mkdir()
    agent_dir.mkdir()
    service_path = service_dir / "LanClassroom-Student-Service.exe"
    service_path.write_bytes(b"")
    (agent_dir / session_launcher.AGENT_EXE).write_bytes(b"")
    return service_path


def _win32_modules():
    win32con = SimpleNamespace(
        MAXIMUM_ALLOWED=1,
        CREATE_UNICODE_ENVIRONMENT=2,
        CREATE_NEW_PROCESS_GROUP=4,
    )
    win32process = SimpleNamespace(
        STARTUPINFO=lambda: SimpleNamespace(lpDesktop=None),
        CreateProcessAsUser=MagicMock(),
    )
    win32profile = SimpleNamespace(
        CreateEnvironmentBlock=MagicMock(return_value=object()),
        DestroyEnvironmentBlock=MagicMock(),
    )
    win32security = SimpleNamespace(
        SecurityImpersonation=2,
        TokenPrimary=1,
        DuplicateTokenEx=MagicMock(),
    )
    win32ts = SimpleNamespace(
        WTSActive=0,
        WTSGetActiveConsoleSessionId=MagicMock(return_value=4),
        WTSEnumerateSessions=MagicMock(
            return_value=({"SessionId": 4, "State": 0},)
        ),
        WTSQueryUserToken=MagicMock(),
        ProcessIdToSessionId=MagicMock(),
    )
    return {
        "psutil": SimpleNamespace(process_iter=MagicMock()),
        "win32con": win32con,
        "win32process": win32process,
        "win32profile": win32profile,
        "win32security": win32security,
        "win32ts": win32ts,
    }
