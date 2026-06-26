import io
import threading
import time
from typing import Optional, Tuple
from PIL import Image
from common.logger import get_logger

logger = get_logger("screen_capture")

try:
    import mss
    MSS_AVAILABLE = True
except ImportError:
    MSS_AVAILABLE = False
    logger.warning("mss not available, screen capture will use fallback")


class ScreenCapturer:
    def __init__(self, monitor: int = 0, fps: int = 20, quality: int = 70,
                 use_diff: bool = True, block_size: int = 16):
        self.monitor = monitor
        self.fps = fps
        self.quality = quality
        self.use_diff = use_diff
        self.block_size = block_size
        self._running = False
        self._thread = None
        self._last_frame: Optional[Image.Image] = None
        self._frame_lock = threading.Lock()
        self._on_frame = None
        self._sct = None
        self._frame_interval = 1.0 / fps
        self._current_frame: Optional[bytes] = None
        self._frame_size: Tuple[int, int] = (0, 0)

    def start(self, on_frame_callback=None):
        if self._running:
            return
        self._on_frame = on_frame_callback
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        logger.info(f"Screen capturer started, monitor={self.monitor}, fps={self.fps}")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        if self._sct:
            self._sct.close()
            self._sct = None
        logger.info("Screen capturer stopped")

    def _capture_loop(self):
        if not MSS_AVAILABLE:
            logger.error("mss is not available, cannot capture screen")
            return
        with mss.mss() as sct:
            self._sct = sct
            monitors = sct.monitors
            if self.monitor >= len(monitors):
                logger.warning(f"Monitor {self.monitor} not found, using monitor 0")
                self.monitor = 0
            monitor_info = monitors[self.monitor]
            self._frame_size = (monitor_info["width"], monitor_info["height"])
            last_time = 0
            while self._running:
                start_time = time.time()
                try:
                    raw = sct.grab(monitor_info)
                    img = Image.frombytes("RGB", raw.size, raw.rgb)
                    jpeg_data = self._compress_frame(img)
                    with self._frame_lock:
                        self._current_frame = jpeg_data
                        self._last_frame = img
                    if self._on_frame:
                        self._on_frame(jpeg_data, img.size)
                except Exception as e:
                    logger.debug(f"Capture error: {e}")
                elapsed = time.time() - start_time
                sleep_time = self._frame_interval - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)

    def _compress_frame(self, img: Image.Image) -> bytes:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=self.quality, optimize=True)
        return buf.getvalue()

    def get_current_frame(self) -> Optional[bytes]:
        with self._frame_lock:
            return self._current_frame

    def get_frame_size(self) -> Tuple[int, int]:
        return self._frame_size

    @staticmethod
    def get_monitors():
        if not MSS_AVAILABLE:
            return []
        with mss.mss() as sct:
            return sct.monitors


class FrameDiffCalculator:
    def __init__(self, block_size: int = 16, threshold: int = 30):
        self.block_size = block_size
        self.threshold = threshold

    def compute_changed_blocks(self, img1: Image.Image, img2: Image.Image):
        if img1.size != img2.size:
            return None
        w, h = img1.size
        blocks_w = (w + self.block_size - 1) // self.block_size
        blocks_h = (h + self.block_size - 1) // self.block_size
        import numpy as np
        arr1 = np.array(img1, dtype=np.int16)
        arr2 = np.array(img2, dtype=np.int16)
        diff = np.abs(arr1 - arr2)
        diff_mean = diff.mean(axis=2)
        changed_blocks = []
        for by in range(blocks_h):
            for bx in range(blocks_w):
                x0 = bx * self.block_size
                y0 = by * self.block_size
                x1 = min(x0 + self.block_size, w)
                y1 = min(y0 + self.block_size, h)
                block_diff = diff_mean[y0:y1, x0:x1]
                if block_diff.mean() > self.threshold:
                    changed_blocks.append((bx, by))
        return changed_blocks

    def extract_blocks(self, img: Image.Image, blocks):
        w, h = img.size
        block_data = []
        for bx, by in blocks:
            x0 = bx * self.block_size
            y0 = by * self.block_size
            x1 = min(x0 + self.block_size, w)
            y1 = min(y0 + self.block_size, h)
            block_img = img.crop((x0, y0, x1, y1))
            buf = io.BytesIO()
            block_img.save(buf, format="JPEG", quality=70)
            block_data.append((bx, by, buf.getvalue()))
        return block_data

    def apply_blocks(self, base_img: Image.Image, block_data) -> Image.Image:
        result = base_img.copy()
        for bx, by, jpeg_data in block_data:
            x0 = bx * self.block_size
            y0 = by * self.block_size
            buf = io.BytesIO(jpeg_data)
            block_img = Image.open(buf)
            result.paste(block_img, (x0, y0))
        return result
