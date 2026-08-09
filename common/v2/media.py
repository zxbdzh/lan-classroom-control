"""Low-latency MJPEG framing for the v2 UDP media channel."""

from __future__ import annotations

import struct
import threading
import time
import uuid
import zlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


MEDIA_MAGIC = b"LCCM"
MEDIA_VERSION = 2
MEDIA_HEADER_FORMAT = "!4sB16sQHHQI"
_HEADER = struct.Struct(MEDIA_HEADER_FORMAT)
MEDIA_HEADER_SIZE = _HEADER.size
DEFAULT_DATAGRAM_SIZE = 1200
MAX_UDP_DATAGRAM_SIZE = 65507
DEFAULT_MAX_FRAME_SIZE = 16 * 1024 * 1024
DEFAULT_MAX_FRAGMENTS = 4096
DEFAULT_MAX_INFLIGHT_FRAMES = 64
_MAX_UINT16 = (1 << 16) - 1
_MAX_UINT64 = (1 << 64) - 1


class MediaProtocolError(ValueError):
    """Raised for malformed media data or violated resource limits."""


@dataclass(frozen=True)
class MediaFrame:
    session_id: uuid.UUID
    frame_id: int
    timestamp_ms: int
    jpeg: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, uuid.UUID):
            raise MediaProtocolError("session_id must be a UUID")
        _require_uint64(self.frame_id, "frame_id")
        _require_uint64(self.timestamp_ms, "timestamp_ms")
        frame_bytes = _jpeg_bytes(self.jpeg)
        object.__setattr__(self, "jpeg", frame_bytes)


class MjpegFragmenter:
    """Split complete JPEG frames into independently routable UDP datagrams."""

    def __init__(
        self,
        session_id: uuid.UUID,
        *,
        max_datagram_size: int = DEFAULT_DATAGRAM_SIZE,
    ) -> None:
        self.session_id = _session_uuid(session_id)
        _require_int_range(
            max_datagram_size,
            "max_datagram_size",
            MEDIA_HEADER_SIZE + 1,
            MAX_UDP_DATAGRAM_SIZE,
        )
        self.max_datagram_size = max_datagram_size
        self._payload_size = max_datagram_size - MEDIA_HEADER_SIZE

    def fragment(
        self,
        jpeg: bytes,
        *,
        frame_id: int,
        timestamp_ms: Optional[int] = None,
    ) -> List[bytes]:
        frame = _jpeg_bytes(jpeg)
        _require_uint64(frame_id, "frame_id")
        timestamp = _wall_clock_ms() if timestamp_ms is None else timestamp_ms
        _require_uint64(timestamp, "timestamp_ms")

        fragment_count = (len(frame) + self._payload_size - 1) // self._payload_size
        if fragment_count > _MAX_UINT16:
            raise MediaProtocolError(
                "frame needs {} fragments; maximum is {}".format(
                    fragment_count, _MAX_UINT16
                )
            )
        checksum = zlib.crc32(frame) & 0xFFFFFFFF
        datagrams: List[bytes] = []
        for index in range(fragment_count):
            start = index * self._payload_size
            payload = frame[start : start + self._payload_size]
            header = _HEADER.pack(
                MEDIA_MAGIC,
                MEDIA_VERSION,
                self.session_id.bytes,
                frame_id,
                fragment_count,
                index,
                timestamp,
                checksum,
            )
            datagrams.append(header + payload)
        return datagrams


@dataclass
class _Assembly:
    fragment_count: int
    timestamp_ms: int
    frame_crc: int
    first_seen_ms: int
    fragments: Dict[int, bytes] = field(default_factory=dict)
    received_bytes: int = 0

    @property
    def metadata(self) -> Tuple[int, int, int]:
        return self.fragment_count, self.timestamp_ms, self.frame_crc


class MjpegReassembler:
    """Reassemble unordered UDP datagrams with bounded memory and lifetime."""

    def __init__(
        self,
        *,
        ttl_ms: int = 500,
        max_frame_size: int = DEFAULT_MAX_FRAME_SIZE,
        max_fragments: int = DEFAULT_MAX_FRAGMENTS,
        max_inflight_frames: int = DEFAULT_MAX_INFLIGHT_FRAMES,
    ) -> None:
        _require_int_range(ttl_ms, "ttl_ms", 1, _MAX_UINT64)
        _require_int_range(max_frame_size, "max_frame_size", 4, _MAX_UINT64)
        _require_int_range(max_fragments, "max_fragments", 1, _MAX_UINT16)
        _require_int_range(
            max_inflight_frames,
            "max_inflight_frames",
            1,
            _MAX_UINT16,
        )
        self.ttl_ms = ttl_ms
        self.max_frame_size = max_frame_size
        self.max_fragments = max_fragments
        self.max_inflight_frames = max_inflight_frames
        self._assemblies: Dict[Tuple[uuid.UUID, int], _Assembly] = {}
        self._completed: Dict[Tuple[uuid.UUID, int], int] = {}
        self._lock = threading.Lock()

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._assemblies)

    def push(
        self, datagram: bytes, *, now_ms: Optional[int] = None
    ) -> Optional[MediaFrame]:
        now = _monotonic_ms() if now_ms is None else now_ms
        _require_uint64(now, "now_ms")
        packet = self._parse_datagram(datagram)
        session_id, frame_id, count, index, timestamp, frame_crc, payload = packet
        key = (session_id, frame_id)

        with self._lock:
            self._discard_expired_locked(now)
            if key in self._completed:
                return None

            assembly = self._assemblies.get(key)
            if assembly is None:
                self._make_room_locked()
                assembly = _Assembly(
                    fragment_count=count,
                    timestamp_ms=timestamp,
                    frame_crc=frame_crc,
                    first_seen_ms=now,
                )
                self._assemblies[key] = assembly
            elif assembly.metadata != (count, timestamp, frame_crc):
                del self._assemblies[key]
                raise MediaProtocolError("fragment metadata changed within a frame")

            previous = assembly.fragments.get(index)
            if previous is not None:
                if previous == payload:
                    return None
                del self._assemblies[key]
                raise MediaProtocolError("conflicting duplicate fragment")

            assembly.fragments[index] = payload
            assembly.received_bytes += len(payload)
            if assembly.received_bytes > self.max_frame_size:
                del self._assemblies[key]
                raise MediaProtocolError("reassembled frame is too large")
            if len(assembly.fragments) != assembly.fragment_count:
                return None

            frame_bytes = b"".join(
                assembly.fragments[position]
                for position in range(assembly.fragment_count)
            )
            del self._assemblies[key]
            actual_crc = zlib.crc32(frame_bytes) & 0xFFFFFFFF
            if actual_crc != assembly.frame_crc:
                raise MediaProtocolError(
                    "frame CRC mismatch: expected {:08x}, got {:08x}".format(
                        assembly.frame_crc, actual_crc
                    )
                )
            frame_bytes = _jpeg_bytes(frame_bytes)
            self._completed[key] = now + self.ttl_ms

        return MediaFrame(
            session_id=session_id,
            frame_id=frame_id,
            timestamp_ms=timestamp,
            jpeg=frame_bytes,
        )

    def discard_expired(self, *, now_ms: Optional[int] = None) -> int:
        now = _monotonic_ms() if now_ms is None else now_ms
        _require_uint64(now, "now_ms")
        with self._lock:
            return self._discard_expired_locked(now)

    expire = discard_expired

    def _parse_datagram(
        self, datagram: bytes
    ) -> Tuple[uuid.UUID, int, int, int, int, int, bytes]:
        if not isinstance(datagram, (bytes, bytearray, memoryview)):
            raise MediaProtocolError("datagram must be bytes")
        packet = bytes(datagram)
        if len(packet) <= MEDIA_HEADER_SIZE:
            raise MediaProtocolError("datagram is truncated or has no payload")
        if len(packet) > MAX_UDP_DATAGRAM_SIZE:
            raise MediaProtocolError("datagram exceeds the UDP payload limit")
        try:
            magic, version, session, frame_id, count, index, timestamp, crc = (
                _HEADER.unpack_from(packet)
            )
        except struct.error as exc:
            raise MediaProtocolError("invalid media header") from exc
        if magic != MEDIA_MAGIC:
            raise MediaProtocolError("invalid media magic")
        if version != MEDIA_VERSION:
            raise MediaProtocolError(
                "unsupported media version: {}".format(version)
            )
        if count == 0 or count > self.max_fragments:
            raise MediaProtocolError("invalid fragment count: {}".format(count))
        if index >= count:
            raise MediaProtocolError(
                "fragment index {} is outside count {}".format(index, count)
            )
        return (
            uuid.UUID(bytes=session),
            frame_id,
            count,
            index,
            timestamp,
            crc,
            packet[MEDIA_HEADER_SIZE:],
        )

    def _discard_expired_locked(self, now_ms: int) -> int:
        expired = [
            key
            for key, assembly in self._assemblies.items()
            if now_ms - assembly.first_seen_ms >= self.ttl_ms
        ]
        for key in expired:
            del self._assemblies[key]

        completed_expired = [
            key for key, deadline in self._completed.items() if now_ms >= deadline
        ]
        for key in completed_expired:
            del self._completed[key]
        return len(expired)

    def _make_room_locked(self) -> None:
        if len(self._assemblies) < self.max_inflight_frames:
            return
        oldest_key = min(
            self._assemblies,
            key=lambda key: self._assemblies[key].first_seen_ms,
        )
        del self._assemblies[oldest_key]


class LatestFrame:
    """A thread-safe single-slot buffer that never lets stale frames win."""

    def __init__(self) -> None:
        self._pending: Optional[MediaFrame] = None
        self._watermark: Optional[Tuple[uuid.UUID, int, int]] = None
        self._lock = threading.Lock()

    def publish(self, frame: MediaFrame) -> bool:
        if not isinstance(frame, MediaFrame):
            raise MediaProtocolError("frame must be a MediaFrame")
        with self._lock:
            if self._watermark is not None and not self._is_newer(frame):
                return False
            self._pending = frame
            self._watermark = (
                frame.session_id,
                frame.frame_id,
                frame.timestamp_ms,
            )
            return True

    def take(self) -> Optional[MediaFrame]:
        with self._lock:
            frame = self._pending
            self._pending = None
            return frame

    read = take

    def _is_newer(self, frame: MediaFrame) -> bool:
        watermark = self._watermark
        if watermark is None:
            return True
        session_id, frame_id, timestamp_ms = watermark
        if frame.session_id == session_id:
            return frame.frame_id > frame_id
        return frame.timestamp_ms >= timestamp_ms


def _jpeg_bytes(value: bytes) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise MediaProtocolError("JPEG frame must be bytes")
    frame = bytes(value)
    if (
        len(frame) < 4
        or not frame.startswith(b"\xff\xd8")
        or not frame.endswith(b"\xff\xd9")
    ):
        raise MediaProtocolError("frame is not a complete JPEG image")
    return frame


def _session_uuid(value: uuid.UUID) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        try:
            return uuid.UUID(value)
        except ValueError as exc:
            raise MediaProtocolError("invalid session UUID") from exc
    raise MediaProtocolError("session_id must be a UUID")


def _require_uint64(value: int, label: str) -> None:
    _require_int_range(value, label, 0, _MAX_UINT64)


def _require_int_range(value: int, label: str, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MediaProtocolError("{} must be an integer".format(label))
    if value < minimum or value > maximum:
        raise MediaProtocolError(
            "{} must be between {} and {}".format(label, minimum, maximum)
        )


def _wall_clock_ms() -> int:
    return int(time.time() * 1000)


def _monotonic_ms() -> int:
    return int(time.monotonic() * 1000)


__all__ = [
    "DEFAULT_DATAGRAM_SIZE",
    "DEFAULT_MAX_FRAGMENTS",
    "DEFAULT_MAX_FRAME_SIZE",
    "DEFAULT_MAX_INFLIGHT_FRAMES",
    "LatestFrame",
    "MAX_UDP_DATAGRAM_SIZE",
    "MEDIA_HEADER_FORMAT",
    "MEDIA_HEADER_SIZE",
    "MEDIA_MAGIC",
    "MEDIA_VERSION",
    "MediaFrame",
    "MediaProtocolError",
    "MjpegFragmenter",
    "MjpegReassembler",
]
