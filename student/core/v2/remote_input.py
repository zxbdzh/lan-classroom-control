"""Allow-listed Windows input injection for an active remote-control lease."""

from __future__ import annotations

import ctypes
import sys
from typing import Mapping


class RemoteInputController:
    def apply(self, payload: Mapping[str, object]) -> dict:
        if sys.platform != "win32":
            raise RuntimeError("remote input is only supported on Windows")
        event_type = str(payload.get("type") or "")
        if event_type == "mouse_move":
            return self._mouse_move(payload)
        if event_type == "mouse_button":
            return self._mouse_button(payload)
        if event_type == "mouse_wheel":
            delta = max(-1200, min(1200, int(payload.get("delta", 0))))
            ctypes.windll.user32.mouse_event(0x0800, 0, 0, delta, 0)
            return {"applied": True}
        if event_type == "key":
            return self._key(payload)
        raise ValueError("unsupported remote input event")

    @staticmethod
    def _mouse_move(payload: Mapping[str, object]) -> dict:
        x = max(0.0, min(1.0, float(payload.get("x", 0))))
        y = max(0.0, min(1.0, float(payload.get("y", 0))))
        user32 = ctypes.windll.user32
        width = max(1, int(user32.GetSystemMetrics(0)))
        height = max(1, int(user32.GetSystemMetrics(1)))
        user32.SetCursorPos(int(x * (width - 1)), int(y * (height - 1)))
        return {"applied": True}

    @staticmethod
    def _mouse_button(payload: Mapping[str, object]) -> dict:
        button = str(payload.get("button") or "left").casefold()
        pressed = bool(payload.get("pressed", True))
        flags = {
            ("left", True): 0x0002,
            ("left", False): 0x0004,
            ("right", True): 0x0008,
            ("right", False): 0x0010,
            ("middle", True): 0x0020,
            ("middle", False): 0x0040,
        }
        flag = flags.get((button, pressed))
        if flag is None:
            raise ValueError("unsupported mouse button")
        if "x" in payload and "y" in payload:
            RemoteInputController._mouse_move(payload)
        ctypes.windll.user32.mouse_event(flag, 0, 0, 0, 0)
        return {"applied": True}

    @staticmethod
    def _key(payload: Mapping[str, object]) -> dict:
        text = str(payload.get("text") or "")[:1]
        virtual_key = int(payload.get("virtual_key", 0))
        if text:
            mapped = int(ctypes.windll.user32.VkKeyScanW(ord(text)))
            virtual_key = mapped & 0xFF
        if virtual_key <= 0 or virtual_key > 255:
            raise ValueError("invalid virtual key")
        pressed = bool(payload.get("pressed", True))
        ctypes.windll.user32.keybd_event(
            virtual_key, 0, 0 if pressed else 0x0002, 0
        )
        return {"applied": True}


__all__ = ["RemoteInputController"]
