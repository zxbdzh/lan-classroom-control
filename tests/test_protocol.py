import pytest
import json
import time
from common.protocol import (
    MessageType, build_message, serialize_message, deserialize_message,
    parse_message_type, build_binary_header, parse_binary_header,
    BinaryDataType, PROTOCOL_VERSION, HEADER_SIZE, MAX_MESSAGE_SIZE
)


class TestMessageBuild:
    def test_build_message_basic(self):
        msg = build_message(MessageType.BLACK_SCREEN, {"enable": True})
        assert msg["version"] == PROTOCOL_VERSION
        assert msg["type"] == MessageType.BLACK_SCREEN.value
        assert "id" in msg
        assert "timestamp" in msg
        assert msg["params"]["enable"] is True

    def test_build_message_empty_params(self):
        msg = build_message(MessageType.STUDENT_HEARTBEAT)
        assert msg["params"] == {}

    def test_build_message_none_params(self):
        msg = build_message(MessageType.STUDENT_HEARTBEAT, None)
        assert msg["params"] == {}

    def test_build_message_custom_id(self):
        msg_id = "custom-id-123"
        msg = build_message(MessageType.BLACK_SCREEN, {}, msg_id=msg_id)
        assert msg["id"] == msg_id

    def test_build_message_chinese_params(self):
        msg = build_message(MessageType.BLACK_SCREEN, {"message": "测试中文 你好 世界"})
        assert msg["params"]["message"] == "测试中文 你好 世界"


class TestMessageSerialize:
    def test_serialize_deserialize_normal(self):
        original = build_message(MessageType.BLACK_SCREEN, {"enable": True, "count": 42})
        data = serialize_message(original)
        assert isinstance(data, bytes)
        assert len(data) >= HEADER_SIZE

        restored = deserialize_message(data)
        assert restored["type"] == original["type"]
        assert restored["params"]["enable"] == original["params"]["enable"]
        assert restored["params"]["count"] == original["params"]["count"]
        assert restored["id"] == original["id"]

    def test_serialize_deserialize_empty_params(self):
        original = build_message(MessageType.STUDENT_HEARTBEAT)
        data = serialize_message(original)
        restored = deserialize_message(data)
        assert restored["params"] == {}

    def test_serialize_deserialize_chinese(self):
        text = "测试中文内容 你好世界 🎉"
        original = build_message(MessageType.BLACK_SCREEN, {"msg": text})
        data = serialize_message(original)
        restored = deserialize_message(data)
        assert restored["params"]["msg"] == text

    def test_serialize_deserialize_large_params(self):
        large_data = "x" * 10000
        original = build_message(MessageType.BLACK_SCREEN, {"data": large_data})
        data = serialize_message(original)
        restored = deserialize_message(data)
        assert restored["params"]["data"] == large_data
        assert len(restored["params"]["data"]) == 10000

    def test_deserialize_too_short(self):
        with pytest.raises(ValueError):
            deserialize_message(b"short")

    def test_parse_message_type(self):
        msg = build_message(MessageType.BROADCAST_START, {})
        assert parse_message_type(msg) == MessageType.BROADCAST_START

    def test_parse_message_type_all_types(self):
        for msg_type in MessageType:
            msg = build_message(msg_type, {})
            assert parse_message_type(msg) == msg_type


class TestBinaryHeader:
    def test_build_parse_binary_header(self):
        data_type = 1
        data_len = 65536
        header = build_binary_header(data_type, data_len)
        assert len(header) == 8
        parsed_type, parsed_len = parse_binary_header(header)
        assert parsed_type == data_type
        assert parsed_len == data_len

    def test_binary_header_too_short(self):
        with pytest.raises(ValueError):
            parse_binary_header(b"short")

    def test_binary_data_type_enum(self):
        assert BinaryDataType.SCREEN_FRAME.value == 1
        assert BinaryDataType.FILE_CHUNK.value == 2
