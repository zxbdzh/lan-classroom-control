import io
import threading
import time
from typing import Optional, Tuple
from PIL import Image, ImageChops, ImageStat
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
                 use_diff: bool = True, block_size: int = 16,
                 max_width: int = 0, max_height: int = 0):
        self.monitor = monitor
        self.fps = fps
        self.quality = quality
        self.use_diff = use_diff
        self.block_size = block_size
        self.max_width = max_width
        self.max_height = max_height
        self._running = False
        self._stop_event = threading.Event()
        self.recovery_interval = 0.5
        self._thread = None
        self._last_frame: Optional[Image.Image] = None
        self._frame_lock = threading.Lock()
        self._on_frame = None
        self._sct = None
        self._frame_interval = 1.0 / fps
        self._current_frame: Optional[bytes] = None
        self._frame_size: Tuple[int, int] = (0, 0)

    def start(self, on_frame_callback=None):
        if self._thread and self._thread.is_alive():
            return
        self._on_frame = on_frame_callback
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        logger.info(f"Screen capturer started, monitor={self.monitor}, fps={self.fps}")

    def stop(self):
        self._running = False
        self._stop_event.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=3)
        if thread is None or not thread.is_alive():
            self._thread = None
        logger.info("Screen capturer stopped")

    def _capture_loop(self):
        if not MSS_AVAILABLE:
            logger.error("mss is not available, cannot capture screen")
            self._running = False
            return
        recovering = False
        try:
            while not self._stop_event.is_set():
                try:
                    # 登录、显示器变化和驱动重置会使 GDI 句柄失效；在采集线程重建整个上下文。
                    with mss.mss() as sct:
                        self._sct = sct
                        monitors = sct.monitors
                        monitor_index = self.monitor if 0 <= self.monitor < len(monitors) else 0
                        monitor_info = monitors[monitor_index]
                        self._frame_size = (monitor_info["width"], monitor_info["height"])
                        while not self._stop_event.is_set():
                            start_time = time.monotonic()
                            raw = sct.grab(monitor_info)
                            if not raw or not raw.rgb:
                                raise RuntimeError("Screen capture returned an empty image")
                            img = self._resize_frame(Image.frombytes("RGB", raw.size, raw.rgb))
                            jpeg_data = self._compress_frame(img)
                            with self._frame_lock:
                                self._current_frame = jpeg_data
                                self._last_frame = img
                            if recovering:
                                logger.info("Screen capture recovered")
                                recovering = False
                            if self._on_frame:
                                try:
                                    self._on_frame(jpeg_data, img.size)
                                except Exception as exc:
                                    logger.debug(f"Frame delivery error: {exc}")
                            elapsed = time.monotonic() - start_time
                            self._stop_event.wait(max(0.0, self._frame_interval - elapsed))
                except Exception as exc:
                    if not recovering:
                        logger.warning(f"Screen capture unavailable; retrying: {exc}")
                    recovering = True
                    # 必须退避并可被 stop 打断，避免空图/初始化失败时忙循环占满 CPU。
                    self._sct = None
                    self._stop_event.wait(self.recovery_interval)
        finally:
            self._sct = None
            self._running = False

    def _compress_frame(self, img: Image.Image) -> bytes:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=self.quality, optimize=False)
        return buf.getvalue()

    def _resize_frame(self, img: Image.Image) -> Image.Image:
        if self.max_width <= 0 and self.max_height <= 0:
            return img
        width, height = img.size
        target_width = self.max_width or width
        target_height = self.max_height or height
        scale = min(target_width / width, target_height / height, 1.0)
        if scale >= 1.0:
            return img
        new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
        return img.resize(new_size, Image.LANCZOS)

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
        diff = ImageChops.difference(img1.convert("RGB"), img2.convert("RGB"))
        changed_blocks = []
        for by in range(blocks_h):
            for bx in range(blocks_w):
                x0 = bx * self.block_size
                y0 = by * self.block_size
                x1 = min(x0 + self.block_size, w)
                y1 = min(y0 + self.block_size, h)
                block_diff = diff.crop((x0, y0, x1, y1))
                channel_means = ImageStat.Stat(block_diff).mean
                if sum(channel_means) / max(1, len(channel_means)) > self.threshold:
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
