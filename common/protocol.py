import json
import struct
import time
import uuid
from enum import Enum
from typing import Any, Dict, Optional


class MessageType(Enum):
    STUDENT_REGISTER = "student_register"
    STUDENT_HEARTBEAT = "student_heartbeat"
    TEACHER_DISCOVER = "teacher_discover"
    BLACK_SCREEN = "black_screen"
    BROADCAST_START = "broadcast_start"
    BROADCAST_STOP = "broadcast_stop"
    BROADCAST_FRAME = "broadcast_frame"
    NET_CONTROL = "net_control"
    FILE_SEND_START = "file_send_start"
    FILE_SEND_DATA = "file_send_data"
    FILE_SEND_END = "file_send_end"
    FILE_SEND_ACK = "file_send_ack"
    COMMAND_ACK = "command_ack"
    STUDENT_STATUS = "student_status"


PROTOCOL_VERSION = "1.0"

HEADER_FORMAT = "!II"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

MAX_MESSAGE_SIZE = 100 * 1024 * 1024


def build_message(msg_type: MessageType, params: Optional[Dict[str, Any]] = None,
                  msg_id: Optional[str] = None) -> Dict[str, Any]:
    if params is None:
        params = {}
    return {
        "version": PROTOCOL_VERSION,
        "type": msg_type.value,
        "id": msg_id or str(uuid.uuid4()),
        "timestamp": int(time.time()),
        "params": params
    }


def serialize_message(msg: Dict[str, Any]) -> bytes:
    body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    header = struct.pack(HEADER_FORMAT, len(body), 0)
    return header + body


def deserialize_message(data: bytes) -> Dict[str, Any]:
    if len(data) < HEADER_SIZE:
        raise ValueError(f"Data too short: {len(data)} bytes")
    body_len, _ = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])
    if body_len > MAX_MESSAGE_SIZE:
        raise ValueError(f"Message too large: {body_len} bytes")
    body = data[HEADER_SIZE:HEADER_SIZE + body_len]
    return json.loads(body.decode("utf-8"))


def parse_message_type(msg: Dict[str, Any]) -> MessageType:
    return MessageType(msg["type"])


def build_binary_header(data_type: int, data_len: int) -> bytes:
    return struct.pack("!II", data_type, data_len)


def parse_binary_header(data: bytes):
    if len(data) < 8:
        raise ValueError("Binary header too short")
    data_type, data_len = struct.unpack("!II", data[:8])
    return data_type, data_len


class BinaryDataType(Enum):
    SCREEN_FRAME = 1
    FILE_CHUNK = 2
