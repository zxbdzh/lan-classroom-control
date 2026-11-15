"""Teacher-side production media services for broadcast and inspection."""

from __future__ import annotations

import threading
import uuid
from typing import Callable, Dict, Iterable, Optional

from common.screen_capture import ScreenCapturer
from common.logger import get_logger
from common.v2.media import LatestFrame, MediaFrame
from common.v2.media_transport import (
    TcpMediaHub, UdpMjpegReceiver, UdpMjpegSender, device_session_id,
)

logger = get_logger("teacher_media")


class BroadcastPublisher:
    def __init__(
        self,
        group: str,
        port: int,
        fps: int,
        quality: int,
        tcp_hub=None,
        max_width: int = 1920,
        max_height: int = 1080,
    ) -> None:
        self.group = group
        self.port = int(port)
        self.fps = int(fps)
        self.quality = int(quality)
        self.max_width = int(max_width)
        self.max_height = int(max_height)
        self._sender = None
        self._capturer = None
        self._lock = threading.RLock()
        self._tcp_hub = tcp_hub
        self._session_id = None
        self._udp_error_reported = False

    def start(self, session_id: str, monitor: int = 0) -> None:
        self.stop()
        with self._lock:
            self._session_id = uuid.UUID(str(session_id))
            self._udp_error_reported = False
            self._sender = UdpMjpegSender(
                self.group, self.port, self._session_id
            )
            self._capturer = ScreenCapturer(
                monitor=int(monitor),
                fps=self.fps,
                quality=self.quality,
                use_diff=False,
                max_width=self.max_width,
                max_height=self.max_height,
            )
            self._capturer.start(self._publish)

    def stop(self) -> None:
        with self._lock:
            capturer, sender = self._capturer, self._sender
            self._capturer = None
            self._sender = None
            self._session_id = None
        if capturer is not None:
            capturer.stop()
        if sender is not None:
            sender.close()

    def _publish(self, jpeg: bytes, _size: tuple) -> None:
        with self._lock:
            sender = self._sender
            session_id = self._session_id
        # 先发布独立 TCP 备用画面，组播路由失效不能连带阻断备用通道。
        if self._tcp_hub is not None and session_id is not None:
            self._tcp_hub.broadcast(session_id, jpeg)
        if sender is not None:
            try:
                sender.send_frame(jpeg)
                if self._udp_error_reported:
                    logger.info("Broadcast multicast recovered")
                self._udp_error_reported = False
            except OSError as exc:
                if not self._udp_error_reported:
                    logger.warning(f"Broadcast multicast unavailable; TCP remains active: {exc}")
                self._udp_error_reported = True


class InspectionReceiver:
    def __init__(self, port: int) -> None:
        self.port = int(port)
        self._stream_devices: Dict[uuid.UUID, str] = {}
        self._latest: Dict[str, LatestFrame] = {}
        self._callbacks = []
        self._lock = threading.RLock()
        self._receiver = UdpMjpegReceiver(self.port, self._on_frame)

    def start(self) -> None:
        self._receiver.start()

    def stop(self) -> None:
        self._receiver.stop()
        with self._lock:
            self._stream_devices.clear()
            self._latest.clear()

    def register(self, session_id: str, device_ids: Iterable[str]) -> None:
        with self._lock:
            for device_id in device_ids:
                stream_id = device_session_id(session_id, device_id)
                self._stream_devices[stream_id] = device_id
                self._latest.setdefault(device_id, LatestFrame())

    def unregister(self, session_id: str, device_ids: Iterable[str]) -> None:
        with self._lock:
            removed_devices = set()
            for device_id in device_ids:
                stream_id = device_session_id(session_id, device_id)
                if self._stream_devices.pop(stream_id, None) is not None:
                    removed_devices.add(device_id)
            active_devices = set(self._stream_devices.values())
            for device_id in removed_devices - active_devices:
                self._latest.pop(device_id, None)

    def subscribe(self, callback: Callable[[str, MediaFrame], None]) -> None:
        with self._lock:
            if callback not in self._callbacks:
                self._callbacks.append(callback)

    def take(self, device_id: str) -> Optional[MediaFrame]:
        with self._lock:
            slot = self._latest.get(device_id)
        return slot.take() if slot else None

    def _on_frame(self, frame: MediaFrame, _address: tuple) -> None:
        with self._lock:
            device_id = self._stream_devices.get(frame.session_id)
            if not device_id:
                return
            slot = self._latest.setdefault(device_id, LatestFrame())
            callbacks = tuple(self._callbacks)
        if not slot.publish(frame):
            return
        for callback in callbacks:
            callback(device_id, frame)

    def publish_tcp_frame(self, frame: MediaFrame) -> None:
        self._on_frame(frame, ("tcp", 0))


__all__ = ["BroadcastPublisher", "InspectionReceiver"]
