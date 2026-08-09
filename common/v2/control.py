"""Strict JSON envelopes for the v2 control channel.

TCP framing is deliberately outside this module.  The interface here owns the
wire JSON shape and all validation needed before a message reaches the domain
layer.
"""

from __future__ import annotations

import copy
import json
import math
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Mapping, Optional


PROTOCOL_VERSION = 2
MAX_CONTROL_SIZE = 1024 * 1024
_MAX_IDENTIFIER_LENGTH = 128
_MAX_ACTION_LENGTH = 128
_MAX_ERROR_LENGTH = 2048
_MAX_UINT64 = (1 << 64) - 1
_MAX_REVISION = (1 << 63) - 1

_ENVELOPE_FIELDS = frozenset(
    {
        "version",
        "kind",
        "command_id",
        "device_id",
        "revision",
        "timestamps",
        "action",
        "stage",
        "error",
        "payload",
    }
)
_TIMESTAMP_FIELDS = frozenset({"issued_at", "lease_expires_at"})


class ControlProtocolError(ValueError):
    """Raised when a v2 control envelope violates the wire contract."""


class MessageKind(str, Enum):
    COMMAND = "command"
    ACK = "ack"


class AckStage(str, Enum):
    RECEIVED = "received"
    APPLIED = "applied"


# The orchestration layer originally used the word "phase".  Keep one enum so
# comparisons work across that seam without translating values.
AckPhase = AckStage


@dataclass(frozen=True)
class ControlEnvelope:
    """Validated v2 control data, independent of the TCP transport."""

    kind: MessageKind
    command_id: str
    device_id: Optional[str]
    revision: int
    issued_at: int
    lease_expires_at: Optional[int]
    action: Optional[str]
    stage: Optional[AckStage]
    error: Optional[str]
    payload: Dict[str, Any]
    version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        copied_payload = _copy_payload(self.payload)
        object.__setattr__(self, "payload", copied_payload)
        _validate_envelope(self)

    @classmethod
    def command(
        cls,
        action: str,
        payload: Optional[Mapping[str, Any]] = None,
        *,
        command_id: Optional[str] = None,
        device_id: Optional[str] = None,
        revision: int = 0,
        issued_at: Optional[int] = None,
        lease_expires_at: Optional[int] = None,
    ) -> "ControlEnvelope":
        command_payload = _payload_mapping(payload)
        return cls(
            kind=MessageKind.COMMAND,
            command_id=str(uuid.uuid4()) if command_id is None else command_id,
            device_id=device_id,
            revision=revision,
            issued_at=_now_ms() if issued_at is None else issued_at,
            lease_expires_at=lease_expires_at,
            action=action,
            stage=None,
            error=None,
            payload=command_payload,
        )

    @classmethod
    def ack(
        cls,
        *,
        command_id: str,
        device_id: str,
        revision: int,
        stage: AckStage,
        issued_at: Optional[int] = None,
        payload: Optional[Mapping[str, Any]] = None,
        error: Optional[str] = None,
    ) -> "ControlEnvelope":
        try:
            parsed_stage = stage if isinstance(stage, AckStage) else AckStage(stage)
        except (TypeError, ValueError) as exc:
            raise ControlProtocolError("stage must be 'received' or 'applied'") from exc

        ack_payload = _payload_mapping(payload)
        return cls(
            kind=MessageKind.ACK,
            command_id=command_id,
            device_id=device_id,
            revision=revision,
            issued_at=_now_ms() if issued_at is None else issued_at,
            lease_expires_at=None,
            action=None,
            stage=parsed_stage,
            error=error,
            payload=ack_payload,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Return a detached representation matching the exact wire shape."""

        return {
            "version": self.version,
            "kind": self.kind.value,
            "command_id": self.command_id,
            "device_id": self.device_id,
            "revision": self.revision,
            "timestamps": {
                "issued_at": self.issued_at,
                "lease_expires_at": self.lease_expires_at,
            },
            "action": self.action,
            "stage": None if self.stage is None else self.stage.value,
            "error": self.error,
            "payload": copy.deepcopy(self.payload),
        }


def serialize_control(envelope: ControlEnvelope) -> bytes:
    """Serialize a validated envelope to canonical UTF-8 JSON bytes."""

    if not isinstance(envelope, ControlEnvelope):
        raise ControlProtocolError("envelope must be a ControlEnvelope")
    _validate_envelope(envelope)
    try:
        encoded = json.dumps(
            envelope.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ControlProtocolError("payload is not valid JSON") from exc
    if len(encoded) > MAX_CONTROL_SIZE:
        raise ControlProtocolError(
            "control envelope too large: {} bytes".format(len(encoded))
        )
    return encoded


def deserialize_control(data: bytes) -> ControlEnvelope:
    """Parse UTF-8 JSON and validate the complete v2 envelope contract."""

    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ControlProtocolError("control data must be bytes")
    raw_bytes = bytes(data)
    if not raw_bytes:
        raise ControlProtocolError("control data is empty")
    if len(raw_bytes) > MAX_CONTROL_SIZE:
        raise ControlProtocolError(
            "control envelope too large: {} bytes".format(len(raw_bytes))
        )

    try:
        decoded = raw_bytes.decode("utf-8")
        raw = json.loads(
            decoded,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except ControlProtocolError:
        raise
    except (UnicodeDecodeError, ValueError) as exc:
        raise ControlProtocolError("invalid control JSON") from exc

    if not isinstance(raw, dict):
        raise ControlProtocolError("control envelope must be a JSON object")
    _require_exact_fields(raw, _ENVELOPE_FIELDS, "control envelope")

    timestamps = raw["timestamps"]
    if not isinstance(timestamps, dict):
        raise ControlProtocolError("timestamps must be a JSON object")
    _require_exact_fields(timestamps, _TIMESTAMP_FIELDS, "timestamps")

    kind = _parse_enum(MessageKind, raw["kind"], "kind")
    stage = (
        None
        if raw["stage"] is None
        else _parse_enum(AckStage, raw["stage"], "stage")
    )
    payload = raw["payload"]
    if not isinstance(payload, dict):
        raise ControlProtocolError("payload must be a JSON object")

    return ControlEnvelope(
        version=raw["version"],
        kind=kind,
        command_id=raw["command_id"],
        device_id=raw["device_id"],
        revision=raw["revision"],
        issued_at=timestamps["issued_at"],
        lease_expires_at=timestamps["lease_expires_at"],
        action=raw["action"],
        stage=stage,
        error=raw["error"],
        payload=payload,
    )


# Short aliases are useful at transport call sites while preserving one
# implementation and one wire contract.
encode_control = serialize_control
decode_control = deserialize_control


def _validate_envelope(envelope: ControlEnvelope) -> None:
    _require_int(envelope.version, "version", 0, _MAX_REVISION)
    if envelope.version != PROTOCOL_VERSION:
        raise ControlProtocolError(
            "unsupported protocol version: {!r}".format(envelope.version)
        )
    if not isinstance(envelope.kind, MessageKind):
        raise ControlProtocolError("kind must be a MessageKind")

    _require_text(envelope.command_id, "command_id", _MAX_IDENTIFIER_LENGTH)
    _require_optional_text(
        envelope.device_id, "device_id", _MAX_IDENTIFIER_LENGTH
    )
    _require_int(envelope.revision, "revision", 0, _MAX_REVISION)
    _require_int(envelope.issued_at, "issued_at", 0, _MAX_UINT64)
    if envelope.lease_expires_at is not None:
        _require_int(
            envelope.lease_expires_at,
            "lease_expires_at",
            0,
            _MAX_UINT64,
        )
        if envelope.lease_expires_at <= envelope.issued_at:
            raise ControlProtocolError("lease_expires_at must be after issued_at")

    if not isinstance(envelope.payload, dict):
        raise ControlProtocolError("payload must be a JSON object")
    _validate_json_value(envelope.payload, "payload")

    if envelope.kind is MessageKind.COMMAND:
        _require_text(envelope.action, "action", _MAX_ACTION_LENGTH)
        if envelope.stage is not None:
            raise ControlProtocolError("command stage must be null")
        if envelope.error is not None:
            raise ControlProtocolError("command error must be null")
        return

    if envelope.device_id is None:
        raise ControlProtocolError("ack device_id is required")
    if envelope.action is not None:
        raise ControlProtocolError("ack action must be null")
    if not isinstance(envelope.stage, AckStage):
        raise ControlProtocolError("ack stage is required")
    if envelope.lease_expires_at is not None:
        raise ControlProtocolError("ack lease_expires_at must be null")
    if envelope.error is not None:
        _require_text(envelope.error, "error", _MAX_ERROR_LENGTH)


def _copy_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ControlProtocolError("payload must be a JSON object")
    copied = copy.deepcopy(dict(payload))
    _validate_json_value(copied, "payload")
    return copied


def _payload_mapping(
    payload: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise ControlProtocolError("payload must be a JSON object")
    return dict(payload)


def _validate_json_value(value: Any, path: str) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ControlProtocolError("{} contains a non-finite number".format(path))
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, "{}[{}]".format(path, index))
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ControlProtocolError("{} keys must be strings".format(path))
            _validate_json_value(item, "{}.{}".format(path, key))
        return
    raise ControlProtocolError(
        "{} contains unsupported value type {}".format(path, type(value).__name__)
    )


def _require_exact_fields(
    value: Mapping[str, Any], expected: frozenset, label: str
) -> None:
    actual = set(value)
    missing = expected - actual
    unknown = actual - expected
    if missing:
        raise ControlProtocolError(
            "{} missing fields: {}".format(label, ", ".join(sorted(missing)))
        )
    if unknown:
        raise ControlProtocolError(
            "{} has unknown fields: {}".format(label, ", ".join(sorted(unknown)))
        )


def _require_int(value: Any, label: str, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ControlProtocolError("{} must be an integer".format(label))
    if value < minimum or value > maximum:
        raise ControlProtocolError(
            "{} must be between {} and {}".format(label, minimum, maximum)
        )


def _require_text(value: Any, label: str, maximum_length: int) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ControlProtocolError("{} must be a non-empty string".format(label))
    if len(value) > maximum_length:
        raise ControlProtocolError(
            "{} exceeds {} characters".format(label, maximum_length)
        )


def _require_optional_text(
    value: Any, label: str, maximum_length: int
) -> None:
    if value is not None:
        _require_text(value, label, maximum_length)


def _parse_enum(enum_type: Any, value: Any, label: str) -> Any:
    if not isinstance(value, str):
        raise ControlProtocolError("{} must be a string".format(label))
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ControlProtocolError("unsupported {}: {!r}".format(label, value)) from exc


def _object_without_duplicate_keys(pairs: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ControlProtocolError("duplicate JSON key: {}".format(key))
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ControlProtocolError("invalid JSON number: {}".format(value))


def _now_ms() -> int:
    return int(time.time() * 1000)


__all__ = [
    "AckPhase",
    "AckStage",
    "ControlEnvelope",
    "ControlProtocolError",
    "MAX_CONTROL_SIZE",
    "MessageKind",
    "PROTOCOL_VERSION",
    "decode_control",
    "deserialize_control",
    "encode_control",
    "serialize_control",
]
