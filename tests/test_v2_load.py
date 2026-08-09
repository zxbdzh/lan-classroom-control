from scripts.v2_soak import run_simulation


def test_sixty_device_command_and_media_simulation():
    result = run_simulation(clients=60, duration=0, cycles=3)
    assert result["commands_applied"] == 180
    assert result["media_frames_reassembled"] == 180
    assert result["reassembly_p95_ms"] < 50
    assert result["memory_peak_mb"] < 128
