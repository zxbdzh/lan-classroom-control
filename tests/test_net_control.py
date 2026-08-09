import pytest
import sys
from unittest.mock import patch, MagicMock
from student.core.net_control import NetController, RULE_NAME_PREFIX


class TestNetController:
    def test_initial_state(self):
        controller = NetController()
        assert controller.is_blocked() is False

    def test_block_unblock_windows(self, tmp_path):
        with patch('sys.platform', 'win32'):
            controller = NetController()
            controller._state_file = str(tmp_path / "net_state.json")
            with patch.object(controller, '_check_admin', return_value=True), \
                 patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                result = controller.block_internet()
                assert result is True
                assert controller.is_blocked() is True
                assert mock_run.call_count >= 1

                mock_run.reset_mock()

                result = controller.unblock_internet()
                assert result is True
                assert controller.is_blocked() is False

    def test_block_with_whitelist(self, tmp_path):
        with patch('sys.platform', 'win32'):
            controller = NetController()
            controller._state_file = str(tmp_path / "net_state.json")
            with patch.object(controller, '_check_admin', return_value=True), \
                 patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                whitelist = ["192.168.1.1", "10.0.0.1"]
                result = controller.block_internet(whitelist)
                assert result is True
                assert controller.is_blocked() is True
                assert controller._whitelist_ips == whitelist
                commands = [" ".join(call.args[0]) for call in mock_run.call_args_list]
                assert any("remoteip=192.168.1.1" in cmd for cmd in commands)
                assert any("remoteip=192.168.0.0/16" in cmd and "action=allow" in cmd for cmd in commands)
                assert any(f"{RULE_NAME_PREFIX}_BlockExternal_" in cmd and "action=block" in cmd for cmd in commands)
                assert not any("firewallpolicy blockinbound,blockoutbound" in cmd for cmd in commands)

    def test_block_does_not_override_lan_allow_with_program_rules(self, tmp_path):
        chrome_path = tmp_path / "Google" / "Chrome" / "Application" / "chrome.exe"
        chrome_path.parent.mkdir(parents=True)
        chrome_path.write_text("")
        with patch('sys.platform', 'win32'):
            controller = NetController()
            controller._state_file = str(tmp_path / "net_state.json")
            with patch.object(controller, '_check_admin', return_value=True), \
                 patch.dict('os.environ', {"ProgramFiles": str(tmp_path), "ProgramFiles(x86)": "", "LocalAppData": ""}), \
                 patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

                assert controller.block_internet() is True

                commands = [" ".join(call.args[0]) for call in mock_run.call_args_list]
                assert not any(f"program={chrome_path}" in cmd for cmd in commands)
                assert any("remoteip=10.0.0.0/8" in cmd and "action=allow" in cmd for cmd in commands)

    def test_block_idempotent(self, tmp_path):
        with patch('sys.platform', 'win32'):
            controller = NetController()
            controller._state_file = str(tmp_path / "net_state.json")
            with patch.object(controller, '_check_admin', return_value=True), \
                 patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                controller.block_internet()
                first_count = mock_run.call_count
                controller.block_internet()
                assert mock_run.call_count == first_count

    def test_unblock_idempotent(self):
        with patch('sys.platform', 'win32'):
            controller = NetController()
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                controller.unblock_internet()
                assert mock_run.call_count >= 1

    def test_partial_windows_block_failure_cleans_created_rules(self):
        with patch('sys.platform', 'win32'):
            controller = NetController()
            with patch.object(controller, '_check_admin', return_value=True), \
                 patch.object(controller, '_cleanup_windows_rules', return_value=True) as cleanup, \
                 patch.object(controller, '_run_cmd', return_value=False):
                assert controller.block_internet() is False

        assert cleanup.call_count == 2
        assert controller.is_blocked() is False

    def test_unblock_cleans_rules(self, tmp_path):
        with patch('sys.platform', 'win32'):
            controller = NetController()
            controller._state_file = str(tmp_path / "net_state.json")
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout=f"规则名称: {RULE_NAME_PREFIX}_BlockHTTP\n",
                    stderr=""
                )
                controller._blocked = True
                result = controller.unblock_internet()
                assert result is True
                assert controller.is_blocked() is False

    def test_linux_block(self):
        with patch('sys.platform', 'linux'):
            controller = NetController()
            with patch('os.geteuid', return_value=0, create=True), \
                 patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                result = controller.block_internet()
                assert result is True
                assert controller.is_blocked() is True

    def test_linux_unblock(self):
        with patch('sys.platform', 'linux'):
            controller = NetController()
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                controller._blocked = True
                result = controller.unblock_internet()
                assert result is True
                assert controller.is_blocked() is False
