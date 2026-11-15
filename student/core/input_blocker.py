import sys
import threading
import time
from common.logger import get_logger

logger = get_logger("input_blocker")


class InputBlocker:
    def __init__(self):
        self._blocked = False
        self._keyboard_listener = None
        self._mouse_listener = None
        self._lock = threading.RLock()
        self._release = threading.Event()
        self._worker = None
        self._backend = None
        self._expires_at = 0.0

    def block(self) -> bool:
        with self._lock:
            self.renew()
            if self._blocked:
                return True
            if sys.platform == "win32" and self._block_windows():
                self._backend = "windows"
                self._blocked = True
            else:
                self._blocked = bool(self._block_pynput())
                self._backend = "pynput" if self._blocked else None
            if self._blocked:
                logger.info("Input blocked")
            else:
                logger.error("Input blocking failed")
            return self._blocked

    def unblock(self) -> bool:
        with self._lock:
            if not self._blocked:
                return True
            if self._backend == "windows":
                self._release.set()
                if self._worker:
                    self._worker.join(timeout=2.0)
                    if self._worker.is_alive():
                        return False
            self._unblock_pynput()
            self._blocked = False
            self._backend = None
            logger.info("Input unblocked")
            return True

    def is_blocked(self) -> bool:
        with self._lock:
            return self._blocked

    def renew(self) -> None:
        self._expires_at = time.monotonic() + 15.0

    def _block_windows(self) -> bool:
        ready = threading.Event()
        outcome = {"success": False}
        self._release.clear()

        def hold_input() -> None:
            try:
                import ctypes
                user32 = ctypes.windll.user32
                outcome["success"] = bool(user32.BlockInput(True))
                ready.set()
                if outcome["success"]:
                    try:
                        # 即使 Qt 主线程卡死，锁定线程仍会独立到期释放本机键鼠。
                        while not self._release.wait(0.25):
                            if time.monotonic() >= self._expires_at:
                                self._blocked = False
                                break
                    finally:
                        # Windows 只允许锁定线程解除 BlockInput；线程退出也会解除锁定。
                        user32.BlockInput(False)
            except Exception as exc:
                logger.warning(f"Windows BlockInput failed: {exc}")
            finally:
                ready.set()

        self._worker = threading.Thread(
            target=hold_input, name="student-input-lock", daemon=True
        )
        self._worker.start()
        if not ready.wait(2.0):
            self._release.set()
            return False
        return outcome["success"]

    def _block_pynput(self) -> bool:
        try:
            from pynput import keyboard, mouse

            # pynput 回调返回 False 会停止监听；suppress 本身负责持续拦截。
            self._keyboard_listener = keyboard.Listener(suppress=True)
            self._mouse_listener = mouse.Listener(suppress=True)
            self._keyboard_listener.start()
            self._mouse_listener.start()
            self._keyboard_listener.wait()
            self._mouse_listener.wait()
            if self._keyboard_listener.is_alive() and self._mouse_listener.is_alive():
                return True
        except ImportError:
            logger.warning("pynput not available, input blocking not functional")
        except Exception as e:
            logger.warning(f"pynput block failed: {e}")
        self._unblock_pynput()
        return False

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
