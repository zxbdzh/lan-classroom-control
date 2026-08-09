import json

import pytest

from common.v2.control import (
    AckPhase,
    AckStage,
    ControlEnvelope,
    ControlProtocolError,
    MessageKind,
    PROTOCOL_VERSION,
    deserialize_control,
    serialize_control,
)


def command(**overrides):
    values = {
        "action": "black_screen.set",
        "command_id": "cmd-001",
        "device_id": None,
        "revision": 7,
        "issued_at": 1_720_000_000_000,
        "lease_expires_at": 1_720_000_010_000,
        "payload": {"enabled": True, "message": "请保持安静"},
    }
    values.update(overrides)
    return ControlEnvelope.command(**values)


class TestControlEnvelope:
    def test_command_round_trip_uses_fixed_v2_shape(self):
        original = command()

        encoded = serialize_control(original)
        raw = json.loads(encoded.decode("utf-8"))
        restored = deserialize_control(encoded)

        assert set(raw) == {
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
        assert raw["timestamps"] == {
            "issued_at": 1_720_000_000_000,
            "lease_expires_at": 1_720_000_010_000,
        }
        assert restored == original
        assert restored.version == PROTOCOL_VERSION
        assert restored.kind is MessageKind.COMMAND
        assert restored.payload["message"] == "请保持安静"

    @pytest.mark.parametrize("stage", [AckStage.RECEIVED, AckStage.APPLIED])
    def test_ack_round_trip_supports_both_lifecycle_stages(self, stage):
        ack = ControlEnvelope.ack(
            command_id="cmd-001",
            device_id="student-12",
            revision=7,
            stage=stage,
            issued_at=1_720_000_000_250,
            payload={"status": "ok"},
        )

        restored = deserialize_control(serialize_control(ack))

        assert restored.kind is MessageKind.ACK
        assert restored.stage is stage
        assert restored.device_id == "student-12"
        assert restored.action is None
        assert restored.lease_expires_at is None
        assert restored.error is None

    def test_ack_can_report_an_application_error(self):
        ack = ControlEnvelope.ack(
            command_id="cmd-002",
            device_id="student-13",
            revision=8,
            stage=AckStage.APPLIED,
            issued_at=1_720_000_000_300,
            error="firewall rule rejected",
            payload={"code": "ACCESS_DENIED"},
        )

        restored = deserialize_control(serialize_control(ack))

        assert restored.error == "firewall rule rejected"
        assert restored.payload == {"code": "ACCESS_DENIED"}

    def test_ack_phase_is_compatibility_alias(self):
        assert AckPhase is AckStage

    def test_default_command_values_are_generated(self):
        envelope = ControlEnvelope.command("broadcast.start", {"fps": 15})

        assert envelope.command_id
        assert envelope.revision == 0
        assert envelope.issued_at > 0
        assert envelope.payload == {"fps": 15}

    def test_constructor_copies_payload_before_exposing_it(self):
        payload = {"targets": ["one"]}
        envelope = command(payload=payload)

        payload["targets"].append("two")

        assert envelope.payload == {"targets": ["one"]}


class TestControlValidation:
    @pytest.mark.parametrize(
        "changes",
        [
            {"action": ""},
            {"command_id": ""},
            {"revision": -1},
            {"revision": True},
            {"issued_at": -1},
            {"lease_expires_at": 1_719_999_999_999},
            {"payload": []},
            {"payload": {1: "non-string-key"}},
            {"payload": {"bad": float("nan")}},
            {"device_id": 42},
        ],
    )
    def test_invalid_commands_are_rejected(self, changes):
        with pytest.raises(ControlProtocolError):
            command(**changes)

    @pytest.mark.parametrize(
        "changes",
        [
            {"command_id": ""},
            {"device_id": ""},
            {"revision": -1},
            {"stage": "finished"},
            {"error": ""},
            {"error": 403},
        ],
    )
    def test_invalid_acknowledgements_are_rejected(self, changes):
        values = {
            "command_id": "cmd-001",
            "device_id": "student-01",
            "revision": 1,
            "stage": AckStage.RECEIVED,
            "issued_at": 100,
        }
        values.update(changes)

        with pytest.raises(ControlProtocolError):
            ControlEnvelope.ack(**values)

    def test_serialize_revalidates_mutated_nested_payload(self):
        envelope = command()
        envelope.payload["bad"] = object()

        with pytest.raises(ControlProtocolError):
            serialize_control(envelope)

    @pytest.mark.parametrize(
        "mutation",
        [
            lambda value: value.pop("payload"),
            lambda value: value.update({"unknown": True}),
            lambda value: value.update({"version": 1}),
            lambda value: value.update({"kind": "request"}),
            lambda value: value.update({"revision": True}),
            lambda value: value.update({"payload": []}),
            lambda value: value.update({"timestamps": {"issued_at": 1}}),
        ],
    )
    def test_deserialize_rejects_malformed_envelopes(self, mutation):
        raw = command().to_dict()
        mutation(raw)

        with pytest.raises(ControlProtocolError):
            deserialize_control(json.dumps(raw).encode("utf-8"))

    def test_deserialize_rejects_duplicate_json_keys(self):
        encoded = serialize_control(command())
        text = encoded.decode("utf-8").replace(
            '"version":2', '"version":2,"version":2', 1
        )

        with pytest.raises(ControlProtocolError, match="duplicate"):
            deserialize_control(text.encode("utf-8"))

    @pytest.mark.parametrize(
        "data",
        [
            b"",
            b"[]",
            b"{not-json}",
            b'{} trailing',
            b'\xff',
            "not-bytes",
        ],
    )
    def test_deserialize_rejects_invalid_wire_data(self, data):
        with pytest.raises(ControlProtocolError):
            deserialize_control(data)

    def test_deserialize_enforces_size_limit(self):
        with pytest.raises(ControlProtocolError, match="too large"):
            deserialize_control(b" " * (1024 * 1024 + 1))
