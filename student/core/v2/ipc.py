import json
import queue
import struct
import sys
import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Tuple

STUDENT_PIPE_NAME = "LanClassroomStudentV2"


class ChannelClosedError(RuntimeError):
    pass


class MessageChannel(ABC):
    @abstractmethod
    def send(self, message: Dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    def receive(self, timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError


_CLOSE_SENTINEL = object()
_PIPE_HEADER = struct.Struct("!I")
_MAX_PIPE_MESSAGE = 1024 * 1024


class _MemoryMessageChannel(MessageChannel):
    def __init__(self, incoming: queue.Queue, outgoing: queue.Queue):
        self._incoming = incoming
        self._outgoing = outgoing
        self._closed = False
        self._lock = threading.Lock()

    def send(self, message: Dict[str, Any]) -> None:
        if not isinstance(message, dict):
            raise TypeError("message must be a dictionary")
        with self._lock:
            if self._closed:
                raise ChannelClosedError("channel is closed")
            encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
            self._outgoing.put(json.loads(encoded))

    def receive(self, timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
        with self._lock:
            if self._closed:
                raise ChannelClosedError("channel is closed")
        try:
            item = self._incoming.get(timeout=timeout)
        except queue.Empty:
            return None
        if item is _CLOSE_SENTINEL:
            with self._lock:
                self._closed = True
            raise ChannelClosedError("peer closed the channel")
        return item

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._outgoing.put(_CLOSE_SENTINEL)


def create_memory_channel_pair() -> Tuple[MessageChannel, MessageChannel]:
    left_incoming = queue.Queue()
    right_incoming = queue.Queue()
    return (
        _MemoryMessageChannel(left_incoming, right_incoming),
        _MemoryMessageChannel(right_incoming, left_incoming),
    )


class _ConnectionMessageChannel(MessageChannel):
    def __init__(self, connection: Any):
        self._connection = connection
        self._closed = False
        self._lock = threading.Lock()

    def send(self, message: Dict[str, Any]) -> None:
        if not isinstance(message, dict):
            raise TypeError("message must be a dictionary")
        encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        with self._lock:
            if self._closed:
                raise ChannelClosedError("channel is closed")
            try:
                self._connection.send_bytes(encoded)
            except (EOFError, OSError) as exc:
                raise ChannelClosedError(str(exc))

    def receive(self, timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
        with self._lock:
            if self._closed:
                raise ChannelClosedError("channel is closed")
        try:
            if timeout is not None and not self._connection.poll(max(0.0, timeout)):
                return None
            data = self._connection.recv_bytes()
            message = json.loads(data.decode("utf-8"))
            if not isinstance(message, dict):
                raise ValueError("pipe message must be a JSON object")
            return message
        except (EOFError, OSError) as exc:
            raise ChannelClosedError(str(exc))

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._connection.close()


class _Win32PipeMessageChannel(MessageChannel):
    def __init__(self, handle: Any) -> None:
        self._handle = handle
        self._closed = False
        self._send_lock = threading.Lock()
        self._state_lock = threading.Lock()

    def send(self, message: Dict[str, Any]) -> None:
        if not isinstance(message, dict):
            raise TypeError("message must be a dictionary")
        encoded = json.dumps(
            message, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        if len(encoded) > _MAX_PIPE_MESSAGE:
            raise ValueError("pipe message is too large")
        with self._send_lock:
            self._ensure_open()
            try:
                import win32file
                win32file.WriteFile(self._handle, _PIPE_HEADER.pack(len(encoded)) + encoded)
            except Exception as exc:
                raise ChannelClosedError(str(exc))

    def receive(self, timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
        self._ensure_open()
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        try:
            import win32file
            import win32pipe
            while True:
                _peeked, available, _remaining = win32pipe.PeekNamedPipe(
                    self._handle, 0
                )
                if available >= _PIPE_HEADER.size:
                    break
                if deadline is not None and time.monotonic() >= deadline:
                    return None
                time.sleep(0.01)
            header = self._read_exact(win32file, _PIPE_HEADER.size)
            size = _PIPE_HEADER.unpack(header)[0]
            if size <= 0 or size > _MAX_PIPE_MESSAGE:
                raise ValueError("invalid pipe message size")
            data = self._read_exact(win32file, size)
            message = json.loads(data.decode("utf-8"))
            if not isinstance(message, dict):
                raise ValueError("pipe message must be a JSON object")
            return message
        except ChannelClosedError:
            raise
        except Exception as exc:
            raise ChannelClosedError(str(exc))

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            handle = self._handle
            self._handle = None
        try:
            handle.Close()
        except Exception:
            pass

    def _ensure_open(self) -> None:
        with self._state_lock:
            if self._closed or self._handle is None:
                raise ChannelClosedError("channel is closed")

    def _read_exact(self, win32file: Any, size: int) -> bytes:
        chunks = []
        remaining = int(size)
        while remaining:
            _status, chunk = win32file.ReadFile(self._handle, remaining)
            if not chunk:
                raise ChannelClosedError("pipe peer closed the channel")
            chunks.append(bytes(chunk))
            remaining -= len(chunk)
        return b"".join(chunks)


def _normalize_pipe_name(pipe_name: str) -> str:
    name = (pipe_name or "").strip()
    if not name:
        raise ValueError("pipe_name must not be empty")
    prefix = "\\\\.\\pipe\\"
    if name.casefold().startswith(prefix.casefold()):
        return name
    name = name.strip("\\/")
    if "\\" in name or "/" in name:
        raise ValueError("pipe_name must be a single name")
    return prefix + name


class WindowsNamedPipeListener:
    """Local pipe listener accessible to the active standard-user session."""

    def __init__(self, pipe_name: str, listener_factory: Any = None) -> None:
        if sys.platform != "win32" and listener_factory is None:
            raise OSError("Windows named pipes are only available on Windows")
        self.address = _normalize_pipe_name(pipe_name)
        self._listener = None
        self._pending_handle = None
        self._closed = False
        if listener_factory is not None:
            self._listener = listener_factory(
                self.address,
                family="AF_PIPE",
                authkey=None,
            )
        elif sys.platform != "win32":
            from multiprocessing.connection import Listener
            self._listener = Listener(self.address, family="AF_PIPE", authkey=None)

    def accept(self) -> MessageChannel:
        if self._listener is not None:
            return _ConnectionMessageChannel(self._listener.accept())
        if self._closed:
            raise ChannelClosedError("listener is closed")
        import pywintypes
        import win32pipe
        handle = win32pipe.CreateNamedPipe(
            self.address,
            win32pipe.PIPE_ACCESS_DUPLEX,
            win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT,
            1,
            _MAX_PIPE_MESSAGE + _PIPE_HEADER.size,
            _MAX_PIPE_MESSAGE + _PIPE_HEADER.size,
            0,
            _pipe_security_attributes(),
        )
        self._pending_handle = handle
        try:
            try:
                win32pipe.ConnectNamedPipe(handle, None)
            except pywintypes.error as exc:
                if getattr(exc, "winerror", exc.args[0] if exc.args else None) != 535:
                    raise
            if self._closed:
                raise ChannelClosedError("listener is closed")
            self._pending_handle = None
            return _Win32PipeMessageChannel(handle)
        except Exception:
            self._pending_handle = None
            try:
                handle.Close()
            except Exception:
                pass
            raise

    def close(self) -> None:
        self._closed = True
        if self._listener is not None:
            self._listener.close()
        handle = self._pending_handle
        self._pending_handle = None
        if handle is not None:
            try:
                handle.Close()
            except Exception:
                pass


class WindowsNamedPipeConnector:
    def __init__(self, connector: Any = None) -> None:
        if sys.platform != "win32" and connector is None:
            raise OSError("Windows named pipes are only available on Windows")
        self._connector = connector

    def connect(self, pipe_name: str) -> MessageChannel:
        address = _normalize_pipe_name(pipe_name)
        if self._connector is not None:
            connection = self._connector(
                address,
                family="AF_PIPE",
                authkey=None,
            )
            return _ConnectionMessageChannel(connection)
        import pywintypes
        import win32con
        import win32file
        import win32pipe
        deadline = time.monotonic() + 5.0
        while True:
            try:
                handle = win32file.CreateFile(
                    address,
                    win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                    0,
                    None,
                    win32con.OPEN_EXISTING,
                    0,
                    None,
                )
                return _Win32PipeMessageChannel(handle)
            except pywintypes.error as exc:
                code = getattr(exc, "winerror", exc.args[0] if exc.args else None)
                if code not in {2, 231} or time.monotonic() >= deadline:
                    raise
                remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
                try:
                    win32pipe.WaitNamedPipe(address, min(remaining_ms, 500))
                except pywintypes.error:
                    time.sleep(0.05)


def _pipe_security_attributes() -> Any:
    import pywintypes
    import win32security
    descriptor = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        "D:(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGW;;;AU)",
        win32security.SDDL_REVISION_1,
    )
    attributes = pywintypes.SECURITY_ATTRIBUTES()
    attributes.SECURITY_DESCRIPTOR = descriptor
    return attributes
