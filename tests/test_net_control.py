import pytest
import sys
from unittest.mock import patch, MagicMock
from student.core.net_control import NetController, RULE_NAME_PREFIX


class TestNetController:
    def test_initial_state(self):
        controller = NetController()
        assert controller.is_blocked() is False

    def test_block_unblock_windows(self):
        with patch('sys.platform', 'win32'):
            controller = NetController()
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                result = controller.block_internet()
                assert result is True
                assert controller.is_blocked() is True
                assert mock_run.call_count >= 1

                mock_run.reset_mock()

                result = controller.unblock_internet()
                assert result is True
                assert controller.is_blocked() is False

    def test_block_with_whitelist(self):
        with patch('sys.platform', 'win32'):
            controller = NetController()
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                whitelist = ["192.168.1.1", "10.0.0.1"]
                result = controller.block_internet(whitelist)
                assert result is True
                assert controller.is_blocked() is True
                assert controller._whitelist_ips == whitelist

    def test_block_idempotent(self):
        with patch('sys.platform', 'win32'):
            controller = NetController()
            with patch('subprocess.run') as mock_run:
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
                assert mock_run.call_count == 0

    def test_unblock_cleans_rules(self):
        with patch('sys.platform', 'win32'):
            controller = NetController()
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
            with patch('subprocess.run') as mock_run:
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
