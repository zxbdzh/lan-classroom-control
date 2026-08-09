"""Measure the real UDP media socket path used by classroom broadcast."""

from __future__ import annotations

import argparse
import json
import math
import socket
import statistics
import threading
import time
import uuid
from typing import Dict, Optional

from common.v2.media_transport import UdpMjpegReceiver, UdpMjpegSender


def run_probe(
    *,
    destination: str = "239.255.95.29",
    frames: int = 100,
    fps: float = 20.0,
    frame_size: int = 96 * 1024,
    receive_timeout: float = 2.0,
) -> Dict[str, float]:
    """Send a bounded MJPEG stream through real sockets and report latency."""
    if frames <= 0:
        raise ValueError("frames must be positive")
    if fps <= 0:
        raise ValueError("fps must be positive")
    if frame_size < 4:
        raise ValueError("frame_size must be at least four bytes")
    if receive_timeout <= 0:
        raise ValueError("receive_timeout must be positive")

    address = socket.gethostbyname(str(destination))
    multicast = _is_ipv4_multicast(address)
    port = _available_udp_port()
    session_id = uuid.uuid4()
    sent_at = {}
    latencies = []
    lock = threading.Lock()
    complete = threading.Event()

    def on_frame(frame, _address) -> None:
        received_at = time.perf_counter()
        with lock:
            started_at = sent_at.get(frame.frame_id)
            if started_at is None:
                return
            latencies.append((received_at - started_at) * 1000.0)
            if len(latencies) >= frames:
                complete.set()

    receiver = UdpMjpegReceiver(
        port,
        on_frame,
        multicast_group=address if multicast else None,
        session_id=session_id,
    )
    sender: Optional[UdpMjpegSender] = None
    jpeg = _synthetic_jpeg(frame_size)
    interval = 1.0 / float(fps)
    started = time.perf_counter()
    try:
        receiver.start()
        sender = UdpMjpegSender(address, port, session_id)
        time.sleep(0.1)
        next_frame_at = time.perf_counter()
        for frame_id in range(1, frames + 1):
            with lock:
                sent_at[frame_id] = time.perf_counter()
            sender.send_frame(jpeg)
            next_frame_at += interval
            while True:
                remaining = next_frame_at - time.perf_counter()
                if remaining <= 0:
                    break
                time.sleep(min(remaining, 0.002))
        complete.wait(float(receive_timeout))
    finally:
        if sender is not None:
            sender.close()
        receiver.stop()
    elapsed = time.perf_counter() - started

    if not latencies:
        raise RuntimeError("no media frames were received")
    ordered = sorted(latencies)
    received = len(ordered)
    return {
        "frames_sent": float(frames),
        "frames_received": float(received),
        "delivery_percent": round(received * 100.0 / frames, 3),
        "frame_size_bytes": float(frame_size),
        "target_fps": float(fps),
        "elapsed_seconds": round(elapsed, 3),
        "min_ms": round(ordered[0], 3),
        "median_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(_percentile(ordered, 0.95), 3),
        "max_ms": round(ordered[-1], 3),
    }


def _available_udp_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.bind(("", 0))
        return int(probe.getsockname()[1])
    finally:
        probe.close()


def _is_ipv4_multicast(address: str) -> bool:
    first_octet = int(str(address).split(".", 1)[0])
    return 224 <= first_octet <= 239


def _synthetic_jpeg(size: int) -> bytes:
    return b"\xff\xd8" + bytes(index % 251 for index in range(size - 4)) + b"\xff\xd9"


def _percentile(values, fraction: float) -> float:
    index = max(0, min(len(values) - 1, math.ceil(len(values) * fraction) - 1))
    return float(values[index])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", default="239.255.95.29")
    parser.add_argument("--frames", type=int, default=100)
    parser.add_argument("--fps", type=float, default=20.0)
    parser.add_argument("--frame-size", type=int, default=96 * 1024)
    parser.add_argument("--receive-timeout", type=float, default=2.0)
    parser.add_argument("--minimum-delivery", type=float, default=99.0)
    parser.add_argument("--maximum-p95-ms", type=float, default=100.0)
    args = parser.parse_args()
    result = run_probe(
        destination=args.destination,
        frames=args.frames,
        fps=args.fps,
        frame_size=args.frame_size,
        receive_timeout=args.receive_timeout,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result["delivery_percent"] < args.minimum_delivery:
        return 2
    if result["p95_ms"] > args.maximum_p95_ms:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
