import pytest

from scripts.v2_network_probe import _percentile, run_probe


def test_udp_probe_uses_real_loopback_sockets():
    result = run_probe(
        destination="127.0.0.1",
        frames=8,
        fps=40,
        frame_size=8 * 1024,
        receive_timeout=1.0,
    )

    assert result["frames_sent"] == 8
    assert result["frames_received"] == 8
    assert result["delivery_percent"] == 100.0
    assert 0 <= result["median_ms"] <= result["max_ms"]


def test_udp_probe_validates_arguments():
    with pytest.raises(ValueError, match="frames"):
        run_probe(frames=0)
    with pytest.raises(ValueError, match="fps"):
        run_probe(fps=0)
    with pytest.raises(ValueError, match="frame_size"):
        run_probe(frame_size=3)


def test_probe_percentile_uses_nearest_rank():
    assert _percentile([1.0, 2.0, 3.0, 4.0], 0.95) == 4.0
