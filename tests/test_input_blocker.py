import pytest
import sys
import ctypes
from types import SimpleNamespace
from unittest.mock import call, patch, MagicMock
from student.core.input_blocker import InputBlocker


class TestInputBlocker:
    def test_initial_state(self):
        blocker = InputBlocker()
        assert blocker.is_blocked() is False

    def test_block_unblock_pynput(self):
        with patch('sys.platform', 'linux'):
            blocker = InputBlocker()
            with patch.object(blocker, '_block_pynput') as mock_block, \
                 patch.object(blocker, '_unblock_pynput') as mock_unblock:
                blocker.block()
                assert blocker.is_blocked() is True
                mock_block.assert_called_once()
                mock_unblock.assert_not_called()

                blocker.unblock()
                assert blocker.is_blocked() is False
                mock_unblock.assert_called_once()

    def test_block_idempotent(self):
        with patch('sys.platform', 'linux'):
            blocker = InputBlocker()
            with patch.object(blocker, '_block_pynput') as mock_block:
                blocker.block()
                blocker.block()
                blocker.block()
                assert blocker.is_blocked() is True
                assert mock_block.call_count == 1

    def test_unblock_idempotent(self):
        with patch('sys.platform', 'linux'):
            blocker = InputBlocker()
            with patch.object(blocker, '_block_pynput') as mock_block, \
                 patch.object(blocker, '_unblock_pynput') as mock_unblock:
                blocker.unblock()
                blocker.unblock()
                assert blocker.is_blocked() is False
                mock_block.assert_not_called()
                mock_unblock.assert_not_called()

    def test_windows_blockinput(self):
        if sys.platform != 'win32':
            pytest.skip("Windows only test")
        blocker = InputBlocker()
        import threading
        calls = []
        with patch.object(ctypes.windll.user32, "BlockInput", side_effect=lambda enabled: (
            calls.append((enabled, threading.get_ident())) or True
        )):
            assert blocker.block()
            assert blocker.is_blocked()
            assert blocker.unblock()
        assert blocker.is_blocked() is False
        assert [enabled for enabled, _ in calls] == [True, False]
        assert calls[0][1] == calls[1][1]
        assert calls[0][1] != threading.get_ident()

    def test_windows_fallback_listeners_are_always_stopped(self):
        blocker = InputBlocker()
        user32 = MagicMock()
        user32.BlockInput.side_effect = [False, True]
        with patch('sys.platform', 'win32'), patch.object(
            ctypes,
            'windll',
            SimpleNamespace(user32=user32),
            create=True,
        ), patch.object(
            blocker, '_block_pynput'
        ) as fallback, patch.object(
            blocker, '_unblock_pynput'
        ) as cleanup:
            blocker.block()
            blocker.unblock()

        fallback.assert_called_once_with()
        cleanup.assert_called_once_with()
        assert user32.BlockInput.call_args_list == [call(True)]

    def test_failed_fallback_does_not_claim_locked(self):
        blocker = InputBlocker()
        with patch.object(blocker, "_block_windows", return_value=False), patch.object(
            blocker, "_block_pynput", return_value=False
        ):
            assert not blocker.block()
            assert not blocker.is_blocked()
