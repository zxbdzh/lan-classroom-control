"""Desktop-session actions executed by the hidden student agent."""

from __future__ import annotations

import threading
import time
import uuid
from typing import Dict, Mapping

from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot

from common.config import get_config
from common.screen_capture import ScreenCapturer
from common.v2.media_transport import (
    TcpMjpegDownlink, TcpMjpegUplink, UdpMjpegReceiver, UdpMjpegSender,
    device_session_id,
)
from student.core.input_blocker import InputBlocker
from student.core.v2.remote_input import RemoteInputController
from student.ui.overlay import OverlayWindow


class DesktopSessionActions(QObject):
    requested = pyqtSignal(object)

    def __init__(self, device_id: str) -> None:
        super().__init__()
        self.device_id = device_id
        self.config = get_config()
        self.overlay = OverlayWindow()
        self.input_blocker = InputBlocker()
        self.remote_input = RemoteInputController()
        self._input_reasons = set()
        self._broadcasting = False
        self._silence = False
        self._broadcast_receiver = None
        self._broadcast_fallback = None
        self._broadcast_timer = None
        self._last_udp_frame = 0.0
        self._broadcast_generation = 0
        self._broadcast_lock = threading.RLock()
        self._inspection_sender = None
        self._inspection_tcp_sender = None
        self._capturer = None
        self.requested.connect(self._execute_requested)

    def handle(self, action: str, params: Mapping[str, object]) -> dict:
        request = {
            "action": str(action),
            "params": dict(params),
            "event": threading.Event(),
            "result": None,
            "error": None,
        }
        self.requested.emit(request)
        if not request["event"].wait(8.0):
            raise RuntimeError("desktop action timed out")
        if request["error"]:
            raise RuntimeError(str(request["error"]))
        return dict(request["result"] or {})

    @pyqtSlot(object)
    def _execute_requested(self, request: dict) -> None:
        try:
            action = request["action"]
            params = request["params"]
            handlers = {
                "ping": lambda _params: {"ready": True},
                "broadcast.start": self._broadcast_start,
                "broadcast.stop": self._broadcast_stop,
                "silence.start": self._silence_start,
                "silence.stop": self._silence_stop,
                "input.lock": lambda _params: self._input("input", True),
                "input.unlock": lambda _params: self._input("input", False),
                "inspection.open": self._inspection_open,
                "inspection.close": self._inspection_close,
                "remote_control.start": self._remote_start,
                "remote_control.stop": self._remote_stop,
                "remote_control.input": self.remote_input.apply,
                "restore_all": lambda _params: self.restore_all(),
            }
            handler = handlers.get(action)
            if handler is None:
                raise ValueError(f"unsupported desktop action: {action}")
            request["result"] = handler(params)
        except Exception as exc:
            request["error"] = str(exc)
        finally:
            request["event"].set()

    def _input(self, reason: str, enabled: bool) -> dict:
        was_locked = bool(self._input_reasons)
        if enabled:
            self._input_reasons.add(reason)
        else:
            self._input_reasons.discard(reason)
        is_locked = bool(self._input_reasons)
        if is_locked and not was_locked:
            self.input_blocker.block()
        elif was_locked and not is_locked:
            self.input_blocker.unblock()
        return {"input_locked": is_locked}

    def _broadcast_start(self, params: Mapping[str, object]) -> dict:
        self._broadcast_stop({})
        session_id = uuid.UUID(str(params.get("session_id")))
        group = str(params.get("multicast_group") or self.config.get("student.multicast_group"))
        port = int(params.get("port") or self.config.get("student.broadcast_port", 9529))
        receiver = UdpMjpegReceiver(
            port,
            self._broadcast_frame,
            multicast_group=group,
            session_id=session_id,
        )
        host = str(params.get("teacher_host") or "127.0.0.1")
        fallback_port = int(
            params.get("fallback_port") or self.config.get("student.bulk_port", 9531)
        )
        with self._broadcast_lock:
            self._broadcast_receiver = receiver
            self._broadcasting = True
            generation = self._broadcast_generation
        receiver.start()
        self._schedule_fallback_check(host, fallback_port, session_id, generation, 0.8)
        self.overlay.show_broadcast()
        if params.get("lock_input", True):
            self._input("broadcast", True)
        return {"broadcasting": True}

    def _broadcast_stop(self, _params: Mapping[str, object]) -> dict:
        with self._broadcast_lock:
            self._broadcast_generation += 1
            timer = self._broadcast_timer
            self._broadcast_timer = None
            receiver = self._broadcast_receiver
            self._broadcast_receiver = None
            fallback = self._broadcast_fallback
            self._broadcast_fallback = None
            self._last_udp_frame = 0.0
            self._broadcasting = False
        if timer:
            timer.cancel()
        if receiver:
            receiver.stop()
        if fallback:
            fallback.stop()
        self._input("broadcast", False)
        if not self._silence:
            self.overlay.hide_overlay()
        return {"broadcasting": False}

    def _broadcast_frame(self, frame, _address) -> None:
        with self._broadcast_lock:
            self._last_udp_frame = time.monotonic()
        self.overlay.update_frame(frame.jpeg, (0, 0))

    def _schedule_fallback_check(
        self,
        host: str,
        port: int,
        session_id: uuid.UUID,
        generation: int,
        delay: float = 0.25,
    ) -> None:
        timer = threading.Timer(
            delay,
            self._check_fallback,
            args=(host, port, session_id, generation),
        )
        timer.daemon = True
        with self._broadcast_lock:
            if generation != self._broadcast_generation or not self._broadcasting:
                return
            self._broadcast_timer = timer
        timer.start()

    def _check_fallback(
        self, host: str, port: int, session_id: uuid.UUID, generation: int
    ) -> None:
        with self._broadcast_lock:
            if generation != self._broadcast_generation or not self._broadcasting:
                return
            udp_stale = (
                not self._last_udp_frame
                or time.monotonic() - self._last_udp_frame >= 0.8
            )
            if udp_stale and self._broadcast_fallback is None:
                try:
                    fallback = TcpMjpegDownlink(
                        host, port, session_id, self._broadcast_fallback_frame
                    )
                    self._broadcast_fallback = fallback
                    fallback.start()
                except Exception:
                    self._broadcast_fallback = None
        self._schedule_fallback_check(host, port, session_id, generation)

    def _broadcast_fallback_frame(self, frame) -> None:
        with self._broadcast_lock:
            if not self._broadcasting:
                return
            if (
                self._last_udp_frame
                and time.monotonic() - self._last_udp_frame < 0.8
            ):
                return
        self.overlay.update_frame(frame.jpeg, (0, 0))

    def _silence_start(self, params: Mapping[str, object]) -> dict:
        self._silence = True
        self.overlay.show_black_screen(str(params.get("message") or ""))
        self._input("silence", True)
        return {"silence": True}

    def _silence_stop(self, _params: Mapping[str, object]) -> dict:
        self._silence = False
        self._input("silence", False)
        if self._broadcasting:
            self.overlay.show_broadcast()
        else:
            self.overlay.hide_overlay()
        return {"silence": False}

    def _inspection_open(self, params: Mapping[str, object]) -> dict:
        self._inspection_close({})
        base_session = str(params.get("session_id") or "")
        if not base_session:
            raise ValueError("inspection session_id is required")
        teacher_host = str(params.get("teacher_host") or "127.0.0.1")
        mode = str(params.get("mode") or "thumbnail")
        stream_id = device_session_id(
            base_session, str(params.get("device_id") or self.device_id)
        )
        self._inspection_sender = UdpMjpegSender(
            teacher_host,
            int(params.get("port") or self.config.get("student.inspection_port", 9530)),
            stream_id,
        )
        self._inspection_tcp_sender = TcpMjpegUplink(
            teacher_host,
            int(params.get("fallback_port") or self.config.get("student.bulk_port", 9531)),
            stream_id,
        )
        self._inspection_tcp_sender.start()
        focus = mode in {"focus", "remote_control"}
        self._capturer = ScreenCapturer(
            monitor=1,
            fps=15 if focus else 1,
            quality=60 if focus else 45,
            use_diff=False,
            max_width=1280 if focus else 640,
            max_height=720 if focus else 360,
        )
        self._capturer.start(self._send_inspection_frame)
        return {"inspection": True, "mode": mode, "tcp_fallback": True}

    def _send_inspection_frame(self, jpeg: bytes, _size: tuple) -> None:
        if self._inspection_sender:
            self._inspection_sender.send_frame(jpeg)
        if self._inspection_tcp_sender:
            self._inspection_tcp_sender.send_frame(jpeg)

    def _inspection_close(self, _params: Mapping[str, object]) -> dict:
        capturer = self._capturer
        sender = self._inspection_sender
        tcp_sender = self._inspection_tcp_sender
        self._capturer = None
        self._inspection_sender = None
        self._inspection_tcp_sender = None
        if capturer:
            capturer.stop()
        if sender:
            sender.close()
        if tcp_sender:
            tcp_sender.stop()
        return {"inspection": False}

    def _remote_start(self, params: Mapping[str, object]) -> dict:
        result = self._inspection_open({**dict(params), "mode": "remote_control"})
        self._input("remote_control", True)
        result["remote_control"] = True
        return result

    def _remote_stop(self, _params: Mapping[str, object]) -> dict:
        self._input("remote_control", False)
        self._inspection_close({})
        return {"remote_control": False}

    def restore_all(self) -> dict:
        self._broadcast_stop({})
        self._silence_stop({})
        self._remote_stop({})
        self._input("input", False)
        self._inspection_close({})
        return {"restored": True}

    def close(self) -> None:
        self.restore_all()


__all__ = ["DesktopSessionActions"]
