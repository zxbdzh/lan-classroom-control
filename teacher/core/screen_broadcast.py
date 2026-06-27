import base64
import threading
import time
from typing import List, Optional, Callable
from common.protocol import MessageType, build_message
from common.screen_capture import ScreenCapturer
from common.logger import get_logger

logger = get_logger("screen_broadcast")


class ScreenBroadcaster:
    def __init__(self, fps: int = 20, quality: int = 70, monitor: int = 0):
        self.fps = fps
        self.quality = quality
        self.monitor = monitor
        self._capturer: Optional[ScreenCapturer] = None
        self._running = False
        self._target_connections: List = []
        self._target_lock = threading.Lock()
        self._broadcast_thread = None
        self._frame_count = 0
        self._last_frame_time = 0
        self.on_frame: Optional[Callable] = None

    def start(self):
        if self._running:
            return
        self._capturer = ScreenCapturer(
            monitor=self.monitor,
            fps=self.fps,
            quality=self.quality,
            use_diff=False
        )
        self._capturer.start()
        self._running = True
        self._broadcast_thread = threading.Thread(target=self._broadcast_loop, daemon=True)
        self._broadcast_thread.start()
        logger.info("Screen broadcaster started")

    def stop(self):
        self._running = False
        if self._broadcast_thread:
            self._broadcast_thread.join(timeout=3)
            self._broadcast_thread = None
        if self._capturer:
            self._capturer.stop()
            self._capturer = None
        with self._target_lock:
            self._target_connections.clear()
        logger.info("Screen broadcaster stopped")

    def add_target(self, conn):
        with self._target_lock:
            if conn not in self._target_connections:
                self._target_connections.append(conn)

    def remove_target(self, conn):
        with self._target_lock:
            if conn in self._target_connections:
                self._target_connections.remove(conn)

    def set_targets(self, connections: List):
        with self._target_lock:
            self._target_connections = list(connections)

    def _broadcast_loop(self):
        frame_interval = 1.0 / self.fps
        while self._running:
            start_time = time.time()
            try:
                frame_data = self._capturer.get_current_frame()
                frame_size = self._capturer.get_frame_size()
                if frame_data and frame_size != (0, 0):
                    self._broadcast_frame(frame_data, frame_size)
                    self._frame_count += 1
                    if self.on_frame:
                        self.on_frame(frame_data, frame_size)
            except Exception as e:
                logger.debug(f"Broadcast frame error: {e}")
            elapsed = time.time() - start_time
            # 帧率自适应：目标多时自动降帧
            with self._target_lock:
                target_count = len(self._target_connections)
            effective_interval = frame_interval * (1.5 if target_count > 5 else 1.0)
            sleep_time = effective_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _broadcast_frame(self, frame_data: bytes, frame_size: tuple):
        if not frame_data or len(frame_data) < 100:
            return
        width, height = frame_size
        frame_b64 = base64.b64encode(frame_data).decode('ascii')
        msg = build_message(MessageType.BROADCAST_FRAME, {
            "width": width,
            "height": height,
            "frame_data": frame_b64
        })

        with self._target_lock:
            targets = list(self._target_connections)

        for conn in targets:
            try:
                if not conn.is_alive():
                    continue
                conn.send_message(msg)
            except Exception as e:
                logger.debug(f"Send frame to {conn.addr} failed: {e}")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def target_count(self) -> int:
        with self._target_lock:
            return len(self._target_connections)

    def get_fps(self) -> float:
        if not self._running:
            return 0
        now = time.time()
        if now - self._last_frame_time > 1:
            self._last_frame_time = now
            fps = self._frame_count
            self._frame_count = 0
            return fps
        return 0
