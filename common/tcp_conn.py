import socket
import struct
import threading
import time
from typing import Callable, Dict, Optional
from common.protocol import (
    HEADER_SIZE, HEADER_FORMAT, MAX_MESSAGE_SIZE,
    serialize_message, deserialize_message, MessageType
)
from common.logger import get_logger

logger = get_logger("tcp_conn")


class TCPConnection:
    def __init__(self, sock: socket.socket, addr: tuple):
        self.sock = sock
        self.addr = addr
        self._lock = threading.Lock()
        self._recv_buffer = b""
        self._running = False
        self._recv_thread = None
        self.on_message: Optional[Callable] = None
        self.on_disconnect: Optional[Callable] = None
        self.student_id: Optional[str] = None
        self.connected_time = time.time()

    def start(self):
        self._running = True
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._recv_thread.start()

    def stop(self):
        self._running = False
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass

    def _recv_loop(self):
        while self._running:
            try:
                data = self.sock.recv(65536)
                if not data:
                    break
                self._recv_buffer += data
                self._process_buffer()
            except socket.timeout:
                continue
            except Exception as e:
                logger.debug(f"Recv error from {self.addr}: {e}")
                break
        self._running = False
        if self.on_disconnect:
            self.on_disconnect(self)

    def _process_buffer(self):
        while len(self._recv_buffer) >= HEADER_SIZE:
            body_len, _ = struct.unpack(HEADER_FORMAT, self._recv_buffer[:HEADER_SIZE])
            if body_len > MAX_MESSAGE_SIZE:
                logger.warning(f"Message too large from {self.addr}: {body_len}")
                self._recv_buffer = b""
                return
            total_len = HEADER_SIZE + body_len
            if len(self._recv_buffer) < total_len:
                break
            msg_data = self._recv_buffer[:total_len]
            self._recv_buffer = self._recv_buffer[total_len:]
            try:
                msg = deserialize_message(msg_data)
                if self.on_message:
                    self.on_message(self, msg)
            except Exception as e:
                logger.warning(f"Parse message error from {self.addr}: {e}")

    def send_message(self, msg: dict) -> bool:
        try:
            data = serialize_message(msg)
            with self._lock:
                self.sock.sendall(data)
            return True
        except Exception as e:
            logger.debug(f"Send error to {self.addr}: {e}")
            return False

    def send_binary(self, data: bytes) -> bool:
        try:
            with self._lock:
                self.sock.sendall(data)
            return True
        except Exception as e:
            logger.debug(f"Send binary error to {self.addr}: {e}")
            return False

    def is_alive(self) -> bool:
        return self._running


class TCPServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 9528):
        self.host = host
        self.port = port
        self._sock: Optional[socket.socket] = None
        self._running = False
        self._accept_thread = None
        self._connections: Dict[str, TCPConnection] = {}
        self._connections_lock = threading.Lock()
        self.on_connect: Optional[Callable] = None
        self.on_message: Optional[Callable] = None
        self.on_disconnect: Optional[Callable] = None

    def start(self):
        if self._running:
            return
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self._sock.listen(128)
        self._sock.settimeout(1.0)
        self._running = True
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()
        logger.info(f"TCP server started on {self.host}:{self.port}")

    def stop(self):
        self._running = False
        if self._accept_thread:
            self._accept_thread.join(timeout=3)
        with self._connections_lock:
            for conn in self._connections.values():
                conn.stop()
            self._connections.clear()
        if self._sock:
            self._sock.close()
            self._sock = None
        logger.info("TCP server stopped")

    def _accept_loop(self):
        while self._running:
            try:
                sock, addr = self._sock.accept()
                sock.settimeout(None)
                conn = TCPConnection(sock, addr)
                conn.on_message = self._handle_message
                conn.on_disconnect = self._handle_disconnect
                conn.start()
                conn_key = f"{addr[0]}:{addr[1]}"
                with self._connections_lock:
                    self._connections[conn_key] = conn
                logger.info(f"New connection from {addr}")
                if self.on_connect:
                    self.on_connect(conn)
            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    logger.error(f"Accept error: {e}")

    def _handle_message(self, conn: TCPConnection, msg: dict):
        if self.on_message:
            self.on_message(conn, msg)

    def _handle_disconnect(self, conn: TCPConnection):
        conn_key = f"{conn.addr[0]}:{conn.addr[1]}"
        with self._connections_lock:
            if conn_key in self._connections:
                del self._connections[conn_key]
        logger.info(f"Connection closed: {conn.addr}")
        if self.on_disconnect:
            self.on_disconnect(conn)

    def get_connection(self, student_id: str) -> Optional[TCPConnection]:
        with self._connections_lock:
            for conn in self._connections.values():
                if conn.student_id == student_id:
                    return conn
        return None

    def get_all_connections(self):
        with self._connections_lock:
            return list(self._connections.values())

    def broadcast_message(self, msg: dict):
        conns = self.get_all_connections()
        for conn in conns:
            conn.send_message(msg)


class TCPClient:
    def __init__(self):
        self._sock: Optional[socket.socket] = None
        self._conn: Optional[TCPConnection] = None
        self._running = False
        self._reconnect_thread = None
        self.server_host: Optional[str] = None
        self.server_port: Optional[int] = None
        self.on_connect: Optional[Callable] = None
        self.on_message: Optional[Callable] = None
        self.on_disconnect: Optional[Callable] = None
        self.auto_reconnect = True
        self.reconnect_delay = 3.0
        self._lock = threading.Lock()

    def connect(self, host: str, port: int) -> bool:
        self.server_host = host
        self.server_port = port
        return self._do_connect()

    def _do_connect(self) -> bool:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect((self.server_host, self.server_port))
            sock.settimeout(None)
            self._conn = TCPConnection(sock, (self.server_host, self.server_port))
            self._conn.on_message = self._handle_message
            self._conn.on_disconnect = self._handle_disconnect
            self._conn.start()
            self._running = True
            logger.info(f"Connected to server {self.server_host}:{self.server_port}")
            if self.on_connect:
                self.on_connect()
            if self._reconnect_thread is None and self.auto_reconnect:
                self._reconnect_thread = threading.Thread(target=self._reconnect_loop, daemon=True)
                self._reconnect_thread.start()
            return True
        except Exception as e:
            logger.warning(f"Connect to {self.server_host}:{self.server_port} failed: {e}")
            return False

    def _handle_message(self, conn: TCPConnection, msg: dict):
        if self.on_message:
            self.on_message(msg)

    def _handle_disconnect(self, conn: TCPConnection):
        logger.info("Disconnected from server")
        if self.on_disconnect:
            self.on_disconnect()

    def _reconnect_loop(self):
        while self.auto_reconnect:
            time.sleep(self.reconnect_delay)
            if not self.is_connected():
                logger.info("Trying to reconnect...")
                self._do_connect()

    def send_message(self, msg: dict) -> bool:
        if self._conn and self._conn.is_alive():
            return self._conn.send_message(msg)
        return False

    def send_binary(self, data: bytes) -> bool:
        if self._conn and self._conn.is_alive():
            return self._conn.send_binary(data)
        return False

    def is_connected(self) -> bool:
        return self._conn is not None and self._conn.is_alive()

    def disconnect(self):
        self.auto_reconnect = False
        self._running = False
        if self._conn:
            self._conn.stop()
            self._conn = None
