"""Deterministic 60-device command/media soak for release qualification."""

from __future__ import annotations

import argparse
import json
import random
import statistics
import time
import tracemalloc
import uuid

from common.v2.media import MjpegFragmenter, MjpegReassembler
from common.v2.model import ClassroomAction, CommandKind, TargetScope
from teacher.core.classroom import AckPhase, Classroom, InMemoryTransport, ManualClock
from teacher.core.state_store import InMemoryStateStore


def _jpeg(size: int = 48000) -> bytes:
    return b"\xff\xd8" + bytes(index % 251 for index in range(size - 4)) + b"\xff\xd9"


def run_simulation(clients: int, duration: float, cycles: int = 0) -> dict:
    clock = ManualClock(1000.0)
    transport = InMemoryTransport(clock)
    for index in range(clients):
        transport.connect(f"pc-{index + 1:02d}", f"PC-{index + 1:02d}", f"g{index // 15 + 1}")
    classroom = Classroom(transport, InMemoryStateStore(), clock)
    session = uuid.uuid4()
    fragmenter = MjpegFragmenter(session)
    receivers = [MjpegReassembler(ttl_ms=500) for _ in range(clients)]
    frame = _jpeg()
    latencies = []
    command_count = 0
    media_frames = 0
    tracemalloc.start()
    started = time.monotonic()
    cycle = 0
    actions = (
        CommandKind.SILENCE_START,
        CommandKind.SILENCE_STOP,
        CommandKind.NETWORK_BLOCK,
        CommandKind.NETWORK_RESTORE,
        CommandKind.INSPECTION_OPEN,
        CommandKind.INSPECTION_CLOSE,
    )
    while (cycles and cycle < cycles) or (not cycles and time.monotonic() - started < duration):
        kind = actions[cycle % len(actions)]
        receipt = classroom.submit(ClassroomAction(kind), TargetScope.all())
        for device_id in receipt.target_ids:
            transport.ack(receipt.command_id, device_id, AckPhase.RECEIVED, revision=receipt.revision)
            transport.ack(receipt.command_id, device_id, AckPhase.APPLIED, revision=receipt.revision)
        command_count += len(receipt.target_ids)
        packets = fragmenter.fragment(frame, frame_id=cycle + 1)
        for receiver in receivers:
            shuffled = list(packets)
            random.Random(cycle).shuffle(shuffled)
            before = time.perf_counter()
            completed = None
            for packet in shuffled:
                completed = receiver.push(packet) or completed
            latencies.append((time.perf_counter() - before) * 1000)
            if completed is None:
                raise RuntimeError("media frame did not reassemble")
            media_frames += 1
        clock.advance(0.1)
        cycle += 1
        if not cycles:
            time.sleep(0.1)
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    classroom.close()
    ordered = sorted(latencies)
    p95 = ordered[max(0, int(len(ordered) * 0.95) - 1)] if ordered else 0.0
    return {
        "clients": clients,
        "cycles": cycle,
        "commands_applied": command_count,
        "media_frames_reassembled": media_frames,
        "reassembly_p95_ms": round(p95, 3),
        "reassembly_mean_ms": round(statistics.mean(latencies), 3) if latencies else 0.0,
        "memory_current_mb": round(current / 1024 / 1024, 3),
        "memory_peak_mb": round(peak / 1024 / 1024, 3),
        "wall_seconds": round(time.monotonic() - started, 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clients", type=int, default=60)
    parser.add_argument("--duration", type=float, default=1800.0)
    parser.add_argument("--cycles", type=int, default=0)
    args = parser.parse_args()
    if args.clients <= 0 or args.duration < 0 or args.cycles < 0:
        raise SystemExit("invalid simulation arguments")
    print(json.dumps(run_simulation(args.clients, args.duration, args.cycles), sort_keys=True))


if __name__ == "__main__":
    main()
