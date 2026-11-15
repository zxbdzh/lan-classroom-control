import struct
import socket
import uuid
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from common.v2.media import (
    DEFAULT_DATAGRAM_SIZE,
    MEDIA_HEADER_SIZE,
    LatestFrame,
    MediaFrame,
    MediaProtocolError,
    MjpegFragmenter,
    MjpegReassembler,
)
from common.v2.media_transport import (
    _TcpDownlinkWriter,
    _tcp_packet,
    TcpMediaHub,
    TcpMjpegDownlink,
    TcpMjpegUplink,
    device_session_id,
)
from student.core.v2.session_actions import DesktopSessionActions
from teacher.core.media_runtime import BroadcastPublisher


def jpeg(size=400):
    assert size >= 4
    return b"\xff\xd8" + bytes((index % 251 for index in range(size - 4))) + b"\xff\xd9"


def packets(frame_id=1, size=400, datagram_size=100, timestamp_ms=1000):
    session_id = uuid.UUID("6f304b4b-3d14-4fd0-97e8-97f786436e91")
    fragmenter = MjpegFragmenter(session_id, max_datagram_size=datagram_size)
    return session_id, fragmenter.fragment(
        jpeg(size), frame_id=frame_id, timestamp_ms=timestamp_ms
    )


class TestMjpegFragmentation:
    def test_reassembles_out_of_order_datagrams(self):
        session_id, datagrams = packets(size=500)
        receiver = MjpegReassembler(ttl_ms=500)

        completed = []
        for datagram in reversed(datagrams):
            frame = receiver.push(datagram, now_ms=10)
            if frame is not None:
                completed.append(frame)

        assert completed == [
            MediaFrame(
                session_id=session_id,
                frame_id=1,
                timestamp_ms=1000,
                jpeg=jpeg(500),
            )
        ]

    def test_fixed_header_keeps_datagrams_within_requested_size(self):
        _, datagrams = packets(size=2500, datagram_size=DEFAULT_DATAGRAM_SIZE)

        assert MEDIA_HEADER_SIZE == struct.calcsize("!4sB16sQHHQI")
        assert all(MEDIA_HEADER_SIZE < len(item) <= DEFAULT_DATAGRAM_SIZE for item in datagrams)

    def test_identical_duplicate_fragment_is_ignored(self):
        _, datagrams = packets(size=250)
        receiver = MjpegReassembler()

        assert receiver.push(datagrams[0], now_ms=1) is None
        assert receiver.push(datagrams[0], now_ms=2) is None
        result = None
        for datagram in datagrams[1:]:
            result = receiver.push(datagram, now_ms=3) or result

        assert result is not None
        assert result.jpeg == jpeg(250)

    def test_conflicting_duplicate_discards_the_frame(self):
        _, datagrams = packets(size=250)
        receiver = MjpegReassembler()
        altered = bytearray(datagrams[0])
        altered[-1] ^= 0x01

        receiver.push(datagrams[0], now_ms=1)
        with pytest.raises(MediaProtocolError, match="conflicting duplicate"):
            receiver.push(bytes(altered), now_ms=2)

        assert receiver.pending_count == 0

    def test_crc_failure_discards_complete_frame(self):
        _, datagrams = packets(size=250)
        receiver = MjpegReassembler()
        altered = list(datagrams)
        final = bytearray(altered[-1])
        final[-1] ^= 0x01
        altered[-1] = bytes(final)

        with pytest.raises(MediaProtocolError, match="CRC"):
            for datagram in altered:
                receiver.push(datagram, now_ms=1)

        assert receiver.pending_count == 0

    def test_incomplete_frame_expires_using_arrival_time(self):
        _, datagrams = packets(size=250)
        receiver = MjpegReassembler(ttl_ms=10)

        receiver.push(datagrams[0], now_ms=100)

        assert receiver.discard_expired(now_ms=109) == 0
        assert receiver.discard_expired(now_ms=110) == 1
        assert receiver.pending_count == 0

    def test_completed_frame_ignores_late_repeated_datagrams(self):
        _, datagrams = packets(size=250)
        receiver = MjpegReassembler(ttl_ms=100)

        results = [receiver.push(item, now_ms=1) for item in datagrams]
        repeated = [receiver.push(item, now_ms=2) for item in datagrams]

        assert len([item for item in results if item is not None]) == 1
        assert all(item is None for item in repeated)


class TestMediaValidation:
    @pytest.mark.parametrize(
        "value",
        [b"", b"not-a-jpeg", b"\xff\xd8missing-end", b"missing-start\xff\xd9"],
    )
    def test_fragmenter_rejects_non_jpeg_frames(self, value):
        fragmenter = MjpegFragmenter(uuid.uuid4())

        with pytest.raises(MediaProtocolError):
            fragmenter.fragment(value, frame_id=1, timestamp_ms=1)

    @pytest.mark.parametrize("frame_id", [-1, True, 2**64])
    def test_fragmenter_rejects_invalid_frame_ids(self, frame_id):
        fragmenter = MjpegFragmenter(uuid.uuid4())

        with pytest.raises(MediaProtocolError):
            fragmenter.fragment(jpeg(), frame_id=frame_id, timestamp_ms=1)

    def test_fragmenter_rejects_datagram_size_without_payload_room(self):
        with pytest.raises(MediaProtocolError):
            MjpegFragmenter(uuid.uuid4(), max_datagram_size=MEDIA_HEADER_SIZE)

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda packet: packet.__setitem__(slice(0, 4), b"FAIL"),
            lambda packet: packet.__setitem__(4, 99),
            lambda packet: struct.pack_into("!H", packet, 29, 0),
            lambda packet: struct.pack_into("!H", packet, 31, 500),
        ],
    )
    def test_reassembler_rejects_invalid_headers(self, mutate):
        _, datagrams = packets(size=100)
        malformed = bytearray(datagrams[0])
        mutate(malformed)

        with pytest.raises(MediaProtocolError):
            MjpegReassembler().push(bytes(malformed), now_ms=1)

    def test_reassembler_rejects_truncated_datagram(self):
        with pytest.raises(MediaProtocolError):
            MjpegReassembler().push(b"short", now_ms=1)

    def test_reassembler_rejects_metadata_changes_within_frame(self):
        _, datagrams = packets(size=250)
        receiver = MjpegReassembler()
        changed = bytearray(datagrams[1])
        struct.pack_into("!Q", changed, 33, 2000)

        receiver.push(datagrams[0], now_ms=1)
        with pytest.raises(MediaProtocolError, match="metadata"):
            receiver.push(bytes(changed), now_ms=2)

    def test_reassembler_enforces_maximum_frame_size(self):
        _, datagrams = packets(size=250)
        receiver = MjpegReassembler(max_frame_size=100)

        with pytest.raises(MediaProtocolError, match="too large"):
            for datagram in datagrams:
                receiver.push(datagram, now_ms=1)


class TestLatestFrame:
    def frame(self, frame_id, timestamp_ms=1000, session_id=None):
        return MediaFrame(
            session_id=session_id or uuid.UUID("6f304b4b-3d14-4fd0-97e8-97f786436e91"),
            frame_id=frame_id,
            timestamp_ms=timestamp_ms,
            jpeg=jpeg(20),
        )

    def test_new_frame_replaces_pending_frame_without_queueing(self):
        slot = LatestFrame()

        assert slot.publish(self.frame(1)) is True
        assert slot.publish(self.frame(2)) is True

        assert slot.take().frame_id == 2
        assert slot.take() is None

    def test_older_or_duplicate_frame_never_replaces_latest(self):
        slot = LatestFrame()

        slot.publish(self.frame(5))

        assert slot.publish(self.frame(5)) is False
        assert slot.publish(self.frame(4)) is False
        assert slot.take().frame_id == 5

    def test_watermark_remains_after_take(self):
        slot = LatestFrame()
        slot.publish(self.frame(5))
        slot.take()

        assert slot.publish(self.frame(4)) is False
        assert slot.take() is None

    def test_newer_session_timestamp_replaces_old_session(self):
        slot = LatestFrame()
        old_session = uuid.uuid4()
        new_session = uuid.uuid4()
        slot.publish(self.frame(100, timestamp_ms=1000, session_id=old_session))

        assert slot.publish(
            self.frame(1, timestamp_ms=1001, session_id=new_session)
        ) is True
        assert slot.publish(
            self.frame(101, timestamp_ms=999, session_id=old_session)
        ) is False
        assert slot.take().session_id == new_session


def test_device_session_ids_are_stable_and_device_specific():
    session = str(uuid.uuid4())
    assert device_session_id(session, "pc-01") == device_session_id(session, "pc-01")
    assert device_session_id(session, "pc-01") != device_session_id(session, "pc-02")


def test_tcp_media_fallback_uses_separate_stream_socket():
    session = uuid.uuid4()
    received = []
    event = threading.Event()
    hub = TcpMediaHub(0, lambda _frame: None)
    hub.start()
    downlink = TcpMjpegDownlink(
        "127.0.0.1", hub.port, session,
        lambda frame: (received.append(frame), event.set()),
    )
    downlink.start()
    deadline = time.time() + 1
    while hub.broadcast(session, jpeg(200)) == 0 and time.time() < deadline:
        time.sleep(0.01)
    assert event.wait(1)
    assert received[-1].jpeg == jpeg(200)
    downlink.stop()
    hub.stop()


def test_tcp_inspection_uplink_delivers_latest_frame_to_teacher_hub():
    session = uuid.uuid4()
    received = []
    event = threading.Event()
    hub = TcpMediaHub(0, lambda frame: (received.append(frame), event.set()))
    hub.start()
    uplink = TcpMjpegUplink(
        "127.0.0.1",
        hub.port,
        session,
        reconnect_interval=0.01,
    )
    uplink.start()

    assert uplink.send_frame(jpeg(240)) is True
    assert event.wait(2)
    assert received[-1].session_id == session
    assert received[-1].jpeg == jpeg(240)

    uplink.stop()
    hub.stop()


def test_tcp_media_hub_stop_closes_active_uplinks():
    hub = TcpMediaHub(0, lambda _frame: None)
    hub.start()
    uplink = TcpMjpegUplink(
        "127.0.0.1", hub.port, uuid.uuid4(), reconnect_interval=0.01
    )
    uplink.start()
    deadline = time.time() + 1
    while not hub._uplinks and time.time() < deadline:
        time.sleep(0.01)

    assert hub._uplinks
    hub.stop()
    assert hub._uplinks == []
    uplink.stop()


def test_tcp_downlink_writer_coalesces_slow_client_frames():
    started = threading.Event()
    release = threading.Event()

    class SlowConnection:
        def __init__(self):
            self.sent = []

        def settimeout(self, _value):
            return None

        def sendall(self, packet):
            self.sent.append(packet)
            if len(self.sent) == 1:
                started.set()
                release.wait(1)

        def close(self):
            release.set()

    connection = SlowConnection()
    writer = _TcpDownlinkWriter(connection, lambda _writer: None)
    writer.start()
    assert writer.offer(b"first")
    assert started.wait(1)
    for index in range(100):
        assert writer.offer("frame-{}".format(index).encode("ascii"))
    release.set()
    deadline = time.time() + 1
    while len(connection.sent) < 2 and time.time() < deadline:
        time.sleep(0.01)
    writer.close()

    assert connection.sent == [b"first", b"frame-99"]


def test_session_agent_starts_tcp_fallback_when_udp_becomes_stale(qtbot):
    actions = DesktopSessionActions("pc-01")
    qtbot.addWidget(actions.overlay)
    actions._broadcast_generation = 4
    actions._broadcasting = True
    actions._broadcast_fallback = None
    actions._last_udp_frame = 9.0
    actions._schedule_fallback_check = MagicMock()
    actions.overlay.update_frame = MagicMock()
    fallback = MagicMock()
    session_id = uuid.uuid4()

    with patch(
        "student.core.v2.session_actions.TcpMjpegDownlink",
        return_value=fallback,
    ) as downlink, patch(
        "student.core.v2.session_actions.time.monotonic",
        return_value=10.0,
    ):
        actions._check_fallback("192.168.1.2", 9531, session_id, 4)

    downlink.assert_called_once()
    fallback.start.assert_called_once_with()
    assert actions._broadcast_fallback is fallback
    actions._schedule_fallback_check.assert_called_once_with(
        "192.168.1.2", 9531, session_id, 4
    )

    frame = MediaFrame(session_id, 1, 1, jpeg(40))
    with patch(
        "student.core.v2.session_actions.time.monotonic",
        return_value=9.5,
    ):
        actions._broadcast_fallback_frame(frame)
    actions.overlay.update_frame.assert_not_called()

    with patch(
        "student.core.v2.session_actions.time.monotonic",
        return_value=10.0,
    ):
        actions._broadcast_fallback_frame(frame)
    actions.overlay.update_frame.assert_called_once_with(frame.jpeg, (0, 0))
    actions.close()


@pytest.mark.parametrize("partial_frame", [False, True])
def test_broadcast_downlink_reconnects_when_connected_peer_stops_delivering_frames(
    partial_frame,
):
    session_id = uuid.uuid4()
    delivered = threading.Event()
    release = threading.Event()
    connections = []
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(2)
    listener.settimeout(1.0)
    packet = _tcp_packet(session_id, 1, jpeg(240))

    def serve():
        try:
            stalled, _address = listener.accept()
            connections.append(stalled)
            stalled.recv(1)
            if partial_frame:
                stalled.sendall(packet[:-10])
            recovered, _address = listener.accept()
            connections.append(recovered)
            recovered.recv(1)
            recovered.sendall(packet)
            release.wait(2)
        except OSError:
            return

    server = threading.Thread(target=serve, daemon=True)
    downlink = TcpMjpegDownlink(
        "127.0.0.1", listener.getsockname()[1], session_id,
        lambda frame: delivered.set() if frame.jpeg == jpeg(240) else None,
        connect_timeout=0.05, reconnect_interval=0.01,
    )
    downlink.frame_timeout = 0.05
    server.start()
    downlink.start()
    try:
        assert delivered.wait(1.0), "连接仍在但无完整画面时，应自动重连而非一直黑屏"
    finally:
        release.set()
        listener.close()
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        downlink.stop()
        server.join(timeout=1)


def test_broadcast_udp_failure_does_not_interrupt_tcp_fallback():
    hub = MagicMock()
    publisher = BroadcastPublisher("239.255.95.29", 9529, 20, 70, hub)
    publisher._session_id = uuid.uuid4()
    publisher._sender = MagicMock()
    publisher._sender.send_frame.side_effect = OSError("multicast unavailable")
    frame = jpeg(240)

    publisher._publish(frame, (32, 16))

    hub.broadcast.assert_called_once_with(publisher._session_id, frame)


def test_tcp_media_hub_keeps_accepting_after_peer_disconnects_during_handshake():
    session_id = uuid.uuid4()
    delivered = threading.Event()
    hub = TcpMediaHub(0, lambda _frame: None)
    hub.start()
    interrupted = socket.create_connection(("127.0.0.1", hub.port), timeout=0.2)
    interrupted.close()
    time.sleep(0.05)
    downlink = TcpMjpegDownlink(
        "127.0.0.1", hub.port, session_id, lambda _frame: delivered.set(),
        connect_timeout=0.1, reconnect_interval=0.01,
    )
    downlink.start()
    try:
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and not delivered.is_set():
            hub.broadcast(session_id, jpeg(240))
            delivered.wait(0.02)
        assert delivered.is_set(), "一个学生握手断线不能让整个教师媒体监听永久退出"
    finally:
        downlink.stop()
        hub.stop()
