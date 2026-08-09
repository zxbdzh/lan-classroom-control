from PIL import Image

from common.screen_capture import FrameDiffCalculator


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
