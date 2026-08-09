"""Socket adapters for the bounded v2 MJPEG datagram protocol."""

from __future__ import annotations

import socket
import struct
import threading
import time
import uuid
from typing import Callable, Optional, Tuple

from common.logger import get_logger
from common.v2.media import MediaFrame, MediaProtocolError, MjpegFragmenter, MjpegReassembler

logger = get_logger("v2_media_transport")
_TCP_FRAME_HEADER = struct.Struct("!16sQQI")


def device_session_id(session_id: str, device_id: str) -> uuid.UUID:
    """Create a stable per-device stream ID beneath a classroom session."""
    return uuid.uuid5(uuid.UUID(str(session_id)), str(device_id))


class UdpMjpegSender:
    def __init__(
        self,
        destination: str,
        port: int,
        session_id: uuid.UUID,
        *,
        multicast_ttl: int = 1,
        socket_factory: Callable[..., socket.socket] = socket.socket,
    ) -> None:
        self.destination = str(destination)
        self.port = int(port)
        self.session_id = uuid.UUID(str(session_id))
        self._socket = socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 * 1024 * 1024)
        except OSError:
            pass
        if self.destination.startswith("239."):
            self._socket.setsockopt(
                socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, int(multicast_ttl)
            )
        self._fragmenter = MjpegFragmenter(self.session_id)
        self._frame_id = 0
        self._lock = threading.Lock()
        self._closed = False

    def send_frame(self, jpeg: bytes) -> int:
        with self._lock:
            if self._closed:
                return 0
            self._frame_id += 1
            datagrams = self._fragmenter.fragment(jpeg, frame_id=self._frame_id)
            sent = 0
            for datagram in datagrams:
                self._socket.sendto(datagram, (self.destination, self.port))
                sent += 1
            return sent

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._socket.close()


class UdpMjpegReceiver:
    def __init__(
        self,
        port: int,
        on_frame: Callable[[MediaFrame, Tuple[str, int]], None],
        *,
        multicast_group: Optional[str] = None,
        interface: str = "0.0.0.0",
        session_id: Optional[uuid.UUID] = None,
        socket_factory: Callable[..., socket.socket] = socket.socket,
    ) -> None:
        self.port = int(port)
        self.multicast_group = multicast_group
        self.interface = interface
        self.session_id = uuid.UUID(str(session_id)) if session_id else None
        self._on_frame = on_frame
        self._socket_factory = socket_factory
        self._socket = None
        self._reassembler = MjpegReassembler(ttl_ms=500)
        self._stop_event = threading.Event()
        self._thread = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        receiver = self._socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        receiver.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            receiver.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        except OSError:
            pass
        receiver.bind(("", self.port))
        if self.multicast_group:
            membership = struct.pack(
                "=4s4s",
                socket.inet_aton(self.multicast_group),
                socket.inet_aton(self.interface),
            )
            receiver.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
        receiver.settimeout(0.25)
        self._socket = receiver
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._receive_loop,
            name="udp-mjpeg-receiver",
            daemon=True,
        )
        self._thread.start()

    def process_datagram(self, datagram: bytes, address: Tuple[str, int]) -> bool:
        try:
            frame = self._reassembler.push(datagram)
        except MediaProtocolError as exc:
            logger.debug(f"Discard invalid media datagram from {address}: {exc}")
            return False
        if frame is None or (self.session_id and frame.session_id != self.session_id):
            return False
        self._on_frame(frame, address)
        return True

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        receiver = self._socket
        self._socket = None
        if receiver is not None:
            try:
                receiver.close()
            except OSError:
                pass
        thread = self._thread
        self._thread = None
        if thread and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(timeout)))

    def _receive_loop(self) -> None:
        while not self._stop_event.is_set():
            receiver = self._socket
            if receiver is None:
                return
            try:
                datagram, address = receiver.recvfrom(65507)
                self.process_datagram(datagram, address)
            except socket.timeout:
                self._reassembler.discard_expired()
            except OSError:
                if not self._stop_event.is_set():
                    logger.warning("UDP media receiver stopped after a socket error")
                return


def _recv_exact(connection: socket.socket, size: int) -> bytes:
    chunks = []
    remaining = int(size)
    while remaining:
        chunk = connection.recv(remaining)
        if not chunk:
            raise OSError("TCP media connection closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _tcp_packet(session_id: uuid.UUID, frame_id: int, jpeg: bytes) -> bytes:
    timestamp_ms = int(time.time() * 1000)
    frame = MediaFrame(uuid.UUID(str(session_id)), int(frame_id), timestamp_ms, jpeg)
    return _TCP_FRAME_HEADER.pack(
        frame.session_id.bytes, frame.frame_id, frame.timestamp_ms, len(frame.jpeg)
    ) + frame.jpeg


class _TcpDownlinkWriter:
    """A hub endpoint whose pending slot always contains only the latest frame."""

    def __init__(self, connection: socket.socket, on_closed: Callable[[object], None]):
        self._connection = connection
        self._on_closed = on_closed
        self._condition = threading.Condition()
        self._pending = None
        self._closed = False
        self._thread = threading.Thread(
            target=self._run,
            name="tcp-mjpeg-downlink-writer",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def offer(self, packet: bytes) -> bool:
        with self._condition:
            if self._closed:
                return False
            self._pending = packet
            self._condition.notify_all()
            return True

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._pending = None
            self._condition.notify_all()
        try:
            self._connection.close()
        except OSError:
            pass
        if self._thread is not threading.current_thread():
            self._thread.join(timeout=2)

    def _run(self) -> None:
        try:
            self._connection.settimeout(2.0)
            while True:
                with self._condition:
                    while self._pending is None and not self._closed:
                        self._condition.wait(timeout=0.5)
                    if self._closed:
                        return
                    packet = self._pending
                    self._pending = None
                self._connection.sendall(packet)
        except OSError:
            return
        finally:
            with self._condition:
                self._closed = True
                self._pending = None
                self._condition.notify_all()
            try:
                self._connection.close()
            except OSError:
                pass
            self._on_closed(self)


class TcpMediaHub:
    """Teacher-side 9531 hub for broadcast downlink and inspection fallback."""

    def __init__(self, port: int, on_uplink_frame: Callable[[MediaFrame], None]) -> None:
        self.port = int(port)
        self._on_uplink_frame = on_uplink_frame
        self._listener = None
        self._downlinks = []
        self._uplinks = []
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread = None
        self._frame_id = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("", self.port))
        self.port = int(listener.getsockname()[1])
        listener.listen(64)
        listener.settimeout(0.25)
        self._listener = listener
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def broadcast(self, session_id: uuid.UUID, jpeg: bytes) -> int:
        with self._lock:
            self._frame_id += 1
            packet = _tcp_packet(session_id, self._frame_id, jpeg)
            connections = tuple(self._downlinks)
        sent = 0
        failed = []
        for connection in connections:
            if connection.offer(packet):
                sent += 1
            else:
                failed.append(connection)
        if failed:
            with self._lock:
                for connection in failed:
                    if connection in self._downlinks:
                        self._downlinks.remove(connection)
            for connection in failed:
                connection.close()
        return sent

    def stop(self) -> None:
        self._stop_event.set()
        listener = self._listener
        self._listener = None
        if listener:
            try:
                listener.close()
            except OSError:
                pass
        with self._lock:
            connections = tuple(self._downlinks) + tuple(self._uplinks)
            self._downlinks.clear()
            self._uplinks.clear()
        for connection in connections:
            if isinstance(connection, _TcpDownlinkWriter):
                connection.close()
            else:
                try:
                    connection.close()
                except OSError:
                    pass
        thread = self._thread
        self._thread = None
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2)

    def _accept_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                listener = self._listener
                if listener is None:
                    return
                connection, _address = listener.accept()
                connection.settimeout(5.0)
                role = _recv_exact(connection, 1)
                connection.settimeout(None)
                if role == b"D":
                    downlink = _TcpDownlinkWriter(connection, self._remove_downlink)
                    with self._lock:
                        self._downlinks.append(downlink)
                    downlink.start()
                elif role == b"U":
                    with self._lock:
                        self._uplinks.append(connection)
                    threading.Thread(
                        target=self._uplink_loop, args=(connection,), daemon=True
                    ).start()
                else:
                    connection.close()
            except socket.timeout:
                continue
            except OSError:
                if not self._stop_event.is_set():
                    logger.warning("TCP media hub accept failed")
                return

    def _remove_downlink(self, downlink: _TcpDownlinkWriter) -> None:
        with self._lock:
            if downlink in self._downlinks:
                self._downlinks.remove(downlink)

    def _uplink_loop(self, connection: socket.socket) -> None:
        try:
            while not self._stop_event.is_set():
                header = _recv_exact(connection, _TCP_FRAME_HEADER.size)
                session, frame_id, timestamp_ms, size = _TCP_FRAME_HEADER.unpack(header)
                if size <= 0 or size > 16 * 1024 * 1024:
                    raise OSError("invalid TCP media frame size")
                jpeg = _recv_exact(connection, size)
                self._on_uplink_frame(
                    MediaFrame(uuid.UUID(bytes=session), frame_id, timestamp_ms, jpeg)
                )
        except (OSError, MediaProtocolError):
            pass
        finally:
            with self._lock:
                if connection in self._uplinks:
                    self._uplinks.remove(connection)
            try:
                connection.close()
            except OSError:
                pass


class TcpMjpegDownlink:
    def __init__(
        self,
        host: str,
        port: int,
        session_id: uuid.UUID,
        on_frame: Callable[[MediaFrame], None],
        *,
        connect_timeout: float = 3.0,
        reconnect_interval: float = 0.5,
        connection_factory: Callable[..., socket.socket] = socket.create_connection,
    ) -> None:
        self.host, self.port = str(host), int(port)
        self.session_id = uuid.UUID(str(session_id))
        self._on_frame = on_frame
        self.connect_timeout = float(connect_timeout)
        self.reconnect_interval = float(reconnect_interval)
        self._connection_factory = connection_factory
        self._socket = None
        self._stop_event = threading.Event()
        self._thread = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="tcp-mjpeg-downlink",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        connection = self._socket
        self._socket = None
        if connection:
            try:
                connection.close()
            except OSError:
                pass
        thread = self._thread
        self._thread = None
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            connection = None
            try:
                connection = self._connection_factory(
                    (self.host, self.port), timeout=self.connect_timeout
                )
                connection.sendall(b"D")
                connection.settimeout(None)
                self._socket = connection
                while not self._stop_event.is_set():
                    header = _recv_exact(connection, _TCP_FRAME_HEADER.size)
                    session, frame_id, timestamp_ms, size = _TCP_FRAME_HEADER.unpack(header)
                    if size <= 0 or size > 16 * 1024 * 1024:
                        raise OSError("invalid TCP media frame size")
                    jpeg = _recv_exact(connection, size)
                    frame = MediaFrame(uuid.UUID(bytes=session), frame_id, timestamp_ms, jpeg)
                    if frame.session_id == self.session_id:
                        self._on_frame(frame)
            except (OSError, MediaProtocolError):
                pass
            finally:
                if self._socket is connection:
                    self._socket = None
                if connection is not None:
                    try:
                        connection.close()
                    except OSError:
                        pass
            self._stop_event.wait(self.reconnect_interval)


class TcpMjpegUplink:
    """Student-side latest-frame TCP uplink with bounded reconnect buffering."""

    def __init__(
        self,
        host: str,
        port: int,
        session_id: uuid.UUID,
        *,
        connect_timeout: float = 3.0,
        reconnect_interval: float = 0.5,
        connection_factory: Callable[..., socket.socket] = socket.create_connection,
    ) -> None:
        self.host, self.port = str(host), int(port)
        self.session_id = uuid.UUID(str(session_id))
        self.connect_timeout = float(connect_timeout)
        self.reconnect_interval = float(reconnect_interval)
        self._connection_factory = connection_factory
        self._socket = None
        self._condition = threading.Condition()
        self._pending = None
        self._frame_id = 0
        self._stop_event = threading.Event()
        self._thread = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="tcp-mjpeg-uplink",
            daemon=True,
        )
        self._thread.start()

    def send_frame(self, jpeg: bytes) -> bool:
        with self._condition:
            if self._stop_event.is_set():
                return False
            self._frame_id += 1
            self._pending = _tcp_packet(self.session_id, self._frame_id, jpeg)
            self._condition.notify_all()
        return True

    def stop(self) -> None:
        self._stop_event.set()
        with self._condition:
            self._pending = None
            self._condition.notify_all()
        connection = self._socket
        self._socket = None
        if connection:
            try:
                connection.close()
            except OSError:
                pass
        thread = self._thread
        self._thread = None
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2)

    def _connect(self) -> socket.socket:
        connection = self._connection_factory(
            (self.host, self.port), timeout=self.connect_timeout
        )
        connection.sendall(b"U")
        connection.settimeout(2.0)
        self._socket = connection
        return connection

    def _loop(self) -> None:
        connection = None
        while not self._stop_event.is_set():
            if connection is None:
                try:
                    connection = self._connect()
                except OSError:
                    self._stop_event.wait(self.reconnect_interval)
                    continue
            with self._condition:
                while self._pending is None and not self._stop_event.is_set():
                    self._condition.wait(timeout=0.5)
                packet = self._pending
                self._pending = None
            if packet is None:
                continue
            try:
                connection.sendall(packet)
            except OSError:
                with self._condition:
                    if self._pending is None:
                        self._pending = packet
                try:
                    connection.close()
                except OSError:
                    pass
                connection = None
                self._socket = None
        if connection:
            try:
                connection.close()
            except OSError:
                pass


__all__ = [
    "TcpMediaHub", "TcpMjpegDownlink", "TcpMjpegUplink", "UdpMjpegReceiver",
    "UdpMjpegSender", "device_session_id",
]
