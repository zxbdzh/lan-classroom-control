import sys
import threading
from typing import Optional
from common.logger import get_logger

logger = get_logger("input_blocker")


class InputBlocker:
    def __init__(self):
        self._blocked = False
        self._keyboard_listener = None
        self._mouse_listener = None
        self._lock = threading.Lock()

    def block(self):
        with self._lock:
            if self._blocked:
                return
            self._blocked = True
        if sys.platform == "win32":
            self._block_windows()
        else:
            self._block_pynput()
        logger.info("Input blocked")

    def unblock(self):
        with self._lock:
            if not self._blocked:
                return
            self._blocked = False
        if sys.platform == "win32":
            self._unblock_windows()
        else:
            self._unblock_pynput()
        logger.info("Input unblocked")

    def is_blocked(self) -> bool:
        with self._lock:
            return self._blocked

    def _block_windows(self):
        try:
            import ctypes
            user32 = ctypes.windll.user32
            user32.BlockInput(True)
        except Exception as e:
            logger.warning(f"Windows BlockInput failed: {e}, falling back to pynput")
            self._block_pynput()

    def _unblock_windows(self):
        try:
            import ctypes
            user32 = ctypes.windll.user32
            user32.BlockInput(False)
        except Exception as e:
            logger.warning(f"Windows UnblockInput failed: {e}")
            self._unblock_pynput()

    def _block_pynput(self):
        try:
            from pynput import keyboard, mouse

            def on_press(key):
                return False

            def on_release(key):
                return False

            def on_move(x, y):
                return False

            def on_click(x, y, button, pressed):
                return False

            def on_scroll(x, y, dx, dy):
                return False

            self._keyboard_listener = keyboard.Listener(
                on_press=on_press,
                on_release=on_release,
                suppress=True
            )
            self._mouse_listener = mouse.Listener(
                on_move=on_move,
                on_click=on_click,
                on_scroll=on_scroll,
                suppress=True
            )
            self._keyboard_listener.start()
            self._mouse_listener.start()
        except ImportError:
            logger.warning("pynput not available, input blocking not functional")
        except Exception as e:
            logger.warning(f"pynput block failed: {e}")

    def _unblock_pynput(self):
        if self._keyboard_listener:
            try:
                self._keyboard_listener.stop()
            except Exception:
                pass
            self._keyboard_listener = None
        if self._mouse_listener:
            try:
                self._mouse_listener.stop()
            except Exception:
                pass
            self._mouse_listener = None
