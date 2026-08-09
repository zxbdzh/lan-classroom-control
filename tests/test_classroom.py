import dataclasses

import pytest

from common.v2 import (
    All,
    ClassroomAction,
    CommandKind,
    DeviceCommandState,
    DeviceIds,
    Group,
    InspectionMode,
    MessageKind,
)
from teacher.core.classroom import AckPhase, Classroom, InMemoryTransport, ManualClock
from teacher.core.runtime import TeacherRuntime
from teacher.core.state_store import (
    AUDIT_RETENTION_SECONDS,
    InMemoryStateStore,
    SCHEMA_VERSION,
    SQLiteStateStore,
)


@pytest.fixture
def rig():
    clock = ManualClock(1_000.0)
    transport = InMemoryTransport(clock)
    transport.connect("pc-01", "PC-01", "front")
    transport.connect("pc-02", "PC-02", "back")
    classroom = Classroom(transport, InMemoryStateStore(), clock)
    return clock, transport, classroom


def test_submit_resolves_and_freezes_group_targets(rig):
    _, transport, classroom = rig

    receipt = classroom.submit(
        ClassroomAction(CommandKind.SILENCE_START, {"message": "Focus"}),
        Group("front"),
    )
    transport.connect("pc-03", "PC-03", "front")

    assert receipt.target_ids == ("pc-01",)
    assert [item.device_id for item in transport.sent] == ["pc-01"]
    assert transport.sent[0].envelope.kind is MessageKind.COMMAND
    assert transport.sent[0].envelope.issued_at == 1_000_000
    assert transport.sent[0].envelope.lease_expires_at == 1_010_000
    assert receipt.status_for("pc-01").state is DeviceCommandState.SENT
    assert receipt.status_for("pc-01").attempts == 1
    with pytest.raises(dataclasses.FrozenInstanceError):
        receipt.revision = 99


def test_normal_actions_skip_offline_devices_but_keep_result(rig):
    _, transport, classroom = rig
    transport.disconnect("pc-02")

    receipt = classroom.submit(
        ClassroomAction(CommandKind.NETWORK_BLOCK),
        DeviceIds(["pc-01", "pc-02", "missing"]),
    )

    assert receipt.status_for("pc-01").state is DeviceCommandState.SENT
    assert receipt.status_for("pc-02").state is DeviceCommandState.OFFLINE
    assert receipt.status_for("missing").state is DeviceCommandState.OFFLINE
    assert [item.device_id for item in transport.sent] == ["pc-01"]


def test_retries_only_until_received_ack(rig):
    clock, transport, classroom = rig
    receipt = classroom.submit(ClassroomAction(CommandKind.POWER_OFF), DeviceIds(["pc-01"]))

    clock.advance(0.299)
    assert len(transport.sent) == 1
    clock.advance(0.001)
    assert len(transport.sent) == 2
    transport.ack(
        receipt.command_id,
        "pc-01",
        AckPhase.RECEIVED,
        revision=receipt.revision,
    )
    clock.advance(1.0)

    status = classroom.snapshot().operation(receipt.command_id).status_for("pc-01")
    assert len(transport.sent) == 2
    assert status.state is DeviceCommandState.RECEIVED
    assert status.received_at == pytest.approx(1_000.3)


def test_missing_received_ack_times_out_after_two_retries(rig):
    clock, transport, classroom = rig
    receipt = classroom.submit(ClassroomAction(CommandKind.REBOOT), All())

    clock.advance(1.5)

    operation = classroom.snapshot().operation(receipt.command_id)
    assert len(transport.sent_to("pc-01")) == 3
    assert len(transport.sent_to("pc-02")) == 3
    assert operation.status_for("pc-01").attempts == 3
    assert operation.status_for("pc-01").state is DeviceCommandState.TIMED_OUT


def test_applied_ack_updates_actual_state(rig):
    _, transport, classroom = rig
    receipt = classroom.submit(
        ClassroomAction(CommandKind.NETWORK_BLOCK), DeviceIds(["pc-01"])
    )

    transport.ack(receipt.command_id, "pc-01", AckPhase.RECEIVED, receipt.revision)
    transport.ack(receipt.command_id, "pc-01", AckPhase.APPLIED, receipt.revision)

    snapshot = classroom.snapshot()
    status = snapshot.operation(receipt.command_id).status_for("pc-01")
    assert status.state is DeviceCommandState.APPLIED
    assert snapshot.device("pc-01").actual_for("network").value is True

    transport.ack(
        receipt.command_id,
        "pc-01",
        AckPhase.APPLIED,
        receipt.revision,
        success=False,
        detail="late duplicate",
    )
    assert classroom.snapshot().operation(receipt.command_id).status_for("pc-01").state is (
        DeviceCommandState.APPLIED
    )


def test_new_revision_supersedes_pending_and_ignores_late_ack(rig):
    _, transport, classroom = rig
    first = classroom.submit(
        ClassroomAction(CommandKind.SILENCE_START), DeviceIds(["pc-01"])
    )
    second = classroom.submit(
        ClassroomAction(CommandKind.SILENCE_STOP), DeviceIds(["pc-01"])
    )

    transport.ack(first.command_id, "pc-01", AckPhase.APPLIED, first.revision)
    snapshot = classroom.snapshot()

    assert snapshot.operation(first.command_id).status_for("pc-01").state is (
        DeviceCommandState.SUPERSEDED
    )
    assert snapshot.operation(second.command_id).status_for("pc-01").state is (
        DeviceCommandState.SENT
    )
    assert snapshot.device("pc-01").actual_for("silence") is None
    assert snapshot.device("pc-01").expected_for("silence").value is False


def test_temporary_control_stays_active_while_device_is_connected(rig):
    clock, transport, classroom = rig
    receipt = classroom.submit(
        ClassroomAction(CommandKind.BROADCAST_START), DeviceIds(["pc-01"])
    )
    transport.ack(receipt.command_id, "pc-01", AckPhase.APPLIED, receipt.revision)

    clock.advance(60)

    snapshot = classroom.snapshot()
    assert snapshot.operation(receipt.command_id).status_for("pc-01").state is (
        DeviceCommandState.APPLIED
    )
    assert snapshot.device("pc-01").actual_for("broadcast").value is True


def test_disconnect_expires_temporary_control_but_keeps_permanent_policy(rig):
    _, transport, classroom = rig
    temporary = classroom.submit(
        ClassroomAction(CommandKind.BROADCAST_START), DeviceIds(["pc-01"])
    )
    permanent = classroom.submit(
        ClassroomAction(CommandKind.BLACKLIST_SET, {"program": "game.exe"}),
        DeviceIds(["pc-01"]),
    )
    transport.ack(temporary.command_id, "pc-01", AckPhase.APPLIED, temporary.revision)
    transport.ack(permanent.command_id, "pc-01", AckPhase.APPLIED, permanent.revision)

    transport.disconnect("pc-01")

    snapshot = classroom.snapshot()
    assert snapshot.operation(temporary.command_id).status_for("pc-01").state is (
        DeviceCommandState.EXPIRED
    )
    assert snapshot.device("pc-01").expected_for("broadcast") is None
    assert snapshot.device("pc-01").actual_for("broadcast") is None
    assert snapshot.device("pc-01").expected_for("blacklist:game.exe").permanent is True


def test_permanent_blacklist_queues_offline_and_syncs_on_reconnect(rig):
    _, transport, classroom = rig
    transport.disconnect("pc-02")
    receipt = classroom.submit(
        ClassroomAction(CommandKind.BLACKLIST_SET, {"program": "game.exe"}),
        DeviceIds(["pc-02"]),
    )

    assert receipt.status_for("pc-02").state is DeviceCommandState.QUEUED
    assert receipt.action.permanent is True
    assert not transport.sent_to("pc-02")
    expected = classroom.snapshot().device("pc-02").expected_for("blacklist:game.exe")
    assert expected.permanent is True

    transport.connect("pc-02", "PC-02", "back")

    status = classroom.snapshot().operation(receipt.command_id).status_for("pc-02")
    assert status.state is DeviceCommandState.SENT
    assert len(transport.sent_to("pc-02")) == 1


def test_wol_is_sent_to_offline_device(rig):
    _, transport, classroom = rig
    transport.disconnect("pc-02")

    receipt = classroom.submit(
        ClassroomAction(CommandKind.WAKE_ON_LAN, {"mac": "00:11:22:33:44:55"}),
        DeviceIds(["pc-02"]),
    )

    assert receipt.status_for("pc-02").state is DeviceCommandState.SENT
    assert len(transport.sent_to("pc-02")) == 1


def test_snapshot_after_revision_returns_only_changes(rig):
    _, _, classroom = rig
    first = classroom.snapshot()

    assert classroom.snapshot(after_revision=first.revision) is None
    classroom.submit(ClassroomAction(CommandKind.LOGOFF), DeviceIds(["pc-01"]))
    changed = classroom.snapshot(after_revision=first.revision)
    assert changed.revision > first.revision


def test_open_inspection_returns_session_and_uses_same_submission_path(rig):
    _, _, classroom = rig

    session = classroom.open_inspection(DeviceIds(["pc-01"]), InspectionMode.FOCUS)
    operation = classroom.snapshot().operation(session.command_id)

    assert session.mode is InspectionMode.FOCUS
    assert session.target_ids == ("pc-01",)
    assert session.expires_at - session.opened_at == 10
    assert operation.action.kind is CommandKind.INSPECTION_OPEN
    assert operation.action.payload["mode"] == "focus"


def test_remote_input_bypasses_audit_and_retry_state():
    transport = InMemoryTransport()
    transport.connect("pc-01", "PC-01")
    runtime = TeacherRuntime(server=transport, database_path=":memory:")

    assert runtime.send_remote_input(
        "pc-01", {"type": "mouse_move", "x": 0.25, "y": 0.75}
    ) is True

    sent = transport.sent_to("pc-01")
    assert len(sent) == 1
    assert sent[0].envelope.action == CommandKind.REMOTE_INPUT.value
    assert sent[0].envelope.payload["x"] == 0.25
    assert runtime.snapshot().operations == ()
    runtime.close()


def test_runtime_set_group_updates_snapshot_and_persists_identity(tmp_path):
    database = str(tmp_path / "classroom.db")
    transport = InMemoryTransport()
    transport.connect(
        "pc-01",
        "PC-01",
        metadata={"mac": "00:11:22:33:44:55"},
    )
    runtime = TeacherRuntime(server=transport, database_path=database)

    transport.set_student_group = lambda student_ids, group: _set_transport_group(
        transport, student_ids, group
    )
    assert runtime.set_group(["pc-01"], "第一组") == 1
    assert runtime.snapshot().device("pc-01").group == "第一组"
    runtime.close()

    restarted_transport = InMemoryTransport()
    restarted = TeacherRuntime(server=restarted_transport, database_path=database)
    assert restarted._resolve_registered_identity(
        "new-id", "00:11:22:33:44:55"
    ) == ("pc-01", "第一组")
    assert restarted._resolve_registered_identity("pc-01", "") == (
        "pc-01",
        "第一组",
    )
    restarted.close()


def _set_transport_group(transport, student_ids, group):
    changed = 0
    for student_id in student_ids:
        device = next(
            (item for item in transport.devices() if item.device_id == student_id),
            None,
        )
        if device is None:
            continue
        transport._devices[student_id] = dataclasses.replace(device, group=group)
        transport._device_handler(transport._devices[student_id])
        changed += 1
    return changed


def test_sqlite_store_uses_migrations_wal_and_restores_state(tmp_path):
    path = str(tmp_path / "classroom.db")
    clock = ManualClock(50.0)
    transport = InMemoryTransport(clock)
    transport.connect("pc-01", "PC-01", "front")
    store = SQLiteStateStore(path)
    assert store.schema_version == SCHEMA_VERSION
    assert store.journal_mode == "wal"

    classroom = Classroom(transport, store, clock)
    receipt = classroom.submit(
        ClassroomAction(CommandKind.BLACKLIST_SET, {"program": "chat.exe"}), All()
    )
    transport.ack(receipt.command_id, "pc-01", AckPhase.APPLIED, receipt.revision)
    classroom.close()
    store.close()

    restarted_transport = InMemoryTransport(clock)
    restarted_transport.connect("pc-01", "PC-01", "front")
    restarted_store = SQLiteStateStore(path)
    assert restarted_store.load()["command_revision"] == receipt.revision
    restarted = Classroom(restarted_transport, restarted_store, clock)
    snapshot = restarted.snapshot()

    assert snapshot.operation(receipt.command_id).status_for("pc-01").state is (
        DeviceCommandState.APPLIED
    )
    assert snapshot.device("pc-01").expected_for("blacklist:chat.exe").permanent is True
    assert len(restarted_transport.sent_to("pc-01")) == 1
    restarted.close()
    restarted_store.close()


def test_invalid_scope_and_non_blacklist_permanent_action_are_rejected(rig):
    _, _, classroom = rig
    with pytest.raises(ValueError):
        ClassroomAction(CommandKind.POWER_OFF, permanent=True)
    with pytest.raises(TypeError):
        classroom.submit(ClassroomAction(CommandKind.POWER_OFF), object())


def test_operation_history_prunes_after_ninety_days_but_keeps_policy(rig):
    clock, transport, classroom = rig
    temporary = classroom.submit(
        ClassroomAction(CommandKind.SILENCE_START), DeviceIds(["pc-01"])
    )
    permanent = classroom.submit(
        ClassroomAction(CommandKind.BLACKLIST_SET, {"program": "game.exe"}),
        DeviceIds(["pc-01"]),
    )
    transport.ack(temporary.command_id, "pc-01", AckPhase.APPLIED, temporary.revision)
    transport.ack(permanent.command_id, "pc-01", AckPhase.APPLIED, permanent.revision)

    clock.advance(AUDIT_RETENTION_SECONDS + 1)
    classroom.submit(ClassroomAction(CommandKind.LOGOFF), DeviceIds(["pc-01"]))

    snapshot = classroom.snapshot()
    assert snapshot.operation(temporary.command_id) is None
    assert snapshot.operation(permanent.command_id) is not None
    assert snapshot.device("pc-01").expected_for("blacklist:game.exe").permanent


def test_runtime_stops_original_broadcast_targets_when_scope_changes():
    transport = InMemoryTransport()
    transport.connect("pc-01", "PC-01")
    transport.connect("pc-02", "PC-02")
    runtime = TeacherRuntime(server=transport, database_path=":memory:")

    class FakeBroadcast:
        group = "239.255.95.29"
        port = 9529

        def __init__(self):
            self.started = []
            self.stop_count = 0

        def start(self, session_id, monitor):
            self.started.append((session_id, monitor))

        def stop(self):
            self.stop_count += 1

    runtime.broadcast_media = FakeBroadcast()
    runtime.start_broadcast(All())
    transport.clear_sent()

    runtime.start_broadcast(DeviceIds(["pc-01"]))

    assert [item.envelope.action for item in transport.sent_to("pc-02")] == [
        CommandKind.BROADCAST_STOP.value
    ]
    transport.clear_sent()
    runtime.stop_broadcast(DeviceIds(["pc-02"]))
    assert [item.device_id for item in transport.sent] == ["pc-01"]
    assert transport.sent[0].envelope.action == CommandKind.BROADCAST_STOP.value
    runtime.close()


def test_runtime_replaces_and_unregisters_inspection_streams():
    transport = InMemoryTransport()
    transport.connect("pc-01", "PC-01")
    transport.connect("pc-02", "PC-02")
    runtime = TeacherRuntime(server=transport, database_path=":memory:")

    first = runtime.open_inspection(All(), InspectionMode.THUMBNAIL)
    assert len(runtime.inspection_media._stream_devices) == 2
    second = runtime.open_inspection(DeviceIds(["pc-01"]), InspectionMode.FOCUS)
    assert second.session_id != first.session_id
    assert len(runtime.inspection_media._stream_devices) == 2

    runtime.close_inspection(DeviceIds(["pc-01"]))
    assert len(runtime.inspection_media._stream_devices) == 1
    runtime.close()
