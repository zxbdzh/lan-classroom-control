import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PIL import Image

from common.screen_capture import FrameDiffCalculator, ScreenCapturer


def test_frame_diff_reports_only_changed_blocks():
    before = Image.new("RGB", (32, 16), "black")
    after = before.copy()
    after.paste("white", (16, 0, 32, 16))

    changed = FrameDiffCalculator(block_size=16, threshold=30).compute_changed_blocks(
        before, after
    )

    assert changed == [(1, 0)]


def test_frame_diff_ignores_identical_images():
    image = Image.new("RGB", (17, 17), "#334455")

    assert FrameDiffCalculator().compute_changed_blocks(image, image.copy()) == []


def test_frame_diff_rejects_different_image_sizes():
    calculator = FrameDiffCalculator()

    assert calculator.compute_changed_blocks(
        Image.new("RGB", (10, 10)), Image.new("RGB", (11, 10))
    ) is None


@pytest.mark.parametrize("failure_stage", ["open", "grab"])
def test_capture_recovers_after_display_becomes_available(monkeypatch, failure_stage):
    delivered = threading.Event()
    raw = SimpleNamespace(size=(8, 8), rgb=b"\x80\x40\x20" * 64)

    def capture_context(broken=False):
        context = MagicMock()
        context.__enter__.return_value = context
        context.monitors = [{"width": 8, "height": 8}]
        if broken:
            context.grab.side_effect = OSError("display unavailable")
        else:
            context.grab.return_value = raw
        return context

    unavailable = OSError("display not ready") if failure_stage == "open" else capture_context(True)
    factory = MagicMock(side_effect=[unavailable, capture_context()])
    monkeypatch.setattr("common.screen_capture.MSS_AVAILABLE", True)
    monkeypatch.setattr("common.screen_capture.mss.mss", factory)
    capturer = ScreenCapturer(fps=20, use_diff=False)
    capturer.recovery_interval = 0.02
    capturer.start(lambda _jpeg, _size: delivered.set())
    try:
        assert delivered.wait(1.0), "显示设备短暂不可用后应自动恢复出帧"
        assert factory.call_count == 2
    finally:
        capturer.stop()
