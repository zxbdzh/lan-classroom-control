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


@pytest.mark.parametrize("restore_kind", [
    CommandKind.USB_STORAGE_RESTORE, CommandKind.RESTORE_ALL,
])
def test_usb_policy_uses_scope_ack_and_restore_domain(rig, restore_kind):
    _, transport, classroom = rig
    blocked = classroom.submit(ClassroomAction(CommandKind.USB_STORAGE_BLOCK), Group("front"))
    assert blocked.target_ids == ("pc-01",)
    assert blocked.expires_at is not None
    transport.ack(blocked.command_id, "pc-01", AckPhase.APPLIED, blocked.revision)
    assert classroom.snapshot().device("pc-01").actual_for("usb_storage").value is True
    assert classroom.snapshot().device("pc-02").expected_for("usb_storage") is None

    restored = classroom.submit(ClassroomAction(restore_kind), Group("front"))
    transport.ack(restored.command_id, "pc-01", AckPhase.APPLIED, restored.revision)
    assert classroom.snapshot().device("pc-01").actual_for("usb_storage").value is False


def test_usb_policy_expires_on_disconnect_without_reapplying_on_reconnect(rig):
    _, transport, classroom = rig
    receipt = classroom.submit(ClassroomAction(CommandKind.USB_STORAGE_BLOCK), Group("front"))
    transport.ack(receipt.command_id, "pc-01", AckPhase.APPLIED, receipt.revision)
    transport.disconnect("pc-01")
    assert classroom.snapshot().operation(receipt.command_id).status_for("pc-01").state is (
        DeviceCommandState.EXPIRED
    )
    assert classroom.snapshot().device("pc-01").actual_for("usb_storage") is None
    sent_count = len(transport.sent)
    transport.connect("pc-01", "PC-01", "front")
    assert len(transport.sent) == sent_count


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


def test_global_blacklist_covers_all_devices_and_future_arrivals_across_restart(tmp_path):
    database = str(tmp_path / "global-applications.db")
    transport = InMemoryTransport()
    transport.connect("pc-01", "PC-01")
    transport.connect("pc-02", "PC-02")
    transport.disconnect("pc-02")
    runtime = TeacherRuntime(server=transport, database_path=database)
    try:
        receipt = runtime.set_global_blacklist("Chrome.EXE", True)
        assert receipt.target_ids == ("pc-01", "pc-02")
        assert receipt.status_for("pc-02").state is DeviceCommandState.QUEUED
        assert runtime.snapshot().global_blacklist == ("chrome.exe",)
        assert transport.sent_to("pc-01")[-1].envelope.payload["path"] == ""
        transport.connect("pc-03", "新设备")
        assert transport.sent_to("pc-03")[-1].envelope.payload["program"] == "chrome.exe"
        revision = runtime.snapshot().device("pc-03").expected_for("blacklist:chrome.exe").command_revision
        transport.connect("pc-03", "新设备")
        assert runtime.snapshot().device("pc-03").expected_for("blacklist:chrome.exe").command_revision == revision
        transport.connect("pc-02", "PC-02")
        assert transport.sent_to("pc-02")[-1].envelope.action == "blacklist.set"
    finally:
        runtime.close()
    transport = InMemoryTransport()
    transport.connect("pc-04", "重启前已连接的新设备")
    restarted = TeacherRuntime(server=transport, database_path=database)
    try:
        assert restarted.snapshot().global_blacklist == ("chrome.exe",)
        assert transport.sent_to("pc-04")[-1].envelope.action == "blacklist.set"
        clear = restarted.set_global_blacklist("chrome.exe", False)
        assert clear.target_ids == ("pc-01", "pc-02", "pc-03", "pc-04")
        assert restarted.snapshot().global_blacklist == ()
        transport.connect("pc-05", "解除后新设备")
        assert not transport.sent_to("pc-05")
        transport.connect("pc-01", "PC-01")
        assert transport.sent_to("pc-01")[-1].envelope.action == "blacklist.clear"
    finally:
        restarted.close()
    cleared = TeacherRuntime(server=InMemoryTransport(), database_path=database)
    try:
        assert cleared.snapshot().global_blacklist == ()
    finally:
        cleared.close()


def test_global_blacklist_can_be_created_before_any_device_connects(rig):
    clock, _, _ = rig
    transport = InMemoryTransport(clock)
    classroom = Classroom(transport, InMemoryStateStore(), clock)
    receipt = classroom.set_global_blacklist("game.exe", True)
    assert receipt.target_ids == ()
    transport.connect("new", "NEW")
    assert classroom.snapshot().device("new").expected_for("blacklist:game.exe").value is True


def test_application_control_master_switch_preserves_rules_and_survives_restart(tmp_path):
    database = str(tmp_path / "application-switch.db")
    transport = InMemoryTransport()
    transport.connect("pc-01", "PC-01")
    transport.connect("pc-02", "PC-02")
    runtime = TeacherRuntime(server=transport, database_path=database)
    try:
        initial = runtime.set_global_blacklist("game.exe")
        runtime.set_blacklist(DeviceIds(["pc-01"]), "editor.exe", r"C:\Tools\editor.exe")
        transport.disconnect("pc-02")
        paused = runtime.set_application_control_enabled(False)
        assert all(receipt.action.kind is CommandKind.BLACKLIST_CLEAR for receipt in paused)
        snapshot = runtime.snapshot()
        assert snapshot.application_control_enabled is False
        assert snapshot.global_blacklist == ("game.exe",)
        assert snapshot.local_blacklist_rules[r"c:\tools\editor.exe"] == ("pc-01",)
        assert snapshot.device("pc-02").expected_for("blacklist:game.exe").value is False
        assert snapshot.device("pc-01").expected_for(r"blacklist:c:\tools\editor.exe").value is False
        transport.ack(initial.command_id, "pc-01", AckPhase.APPLIED, initial.revision)
        assert runtime.snapshot().device("pc-01").expected_for("blacklist:game.exe").value is False
        actual = runtime.snapshot().device("pc-01").actual_for("blacklist:game.exe")
        assert actual is None or not actual.value
        count = len(transport.sent)
        assert runtime.set_application_control_enabled(False) == ()
        assert len(transport.sent) == count
    finally:
        runtime.close()
    transport = InMemoryTransport()
    transport.connect("pc-03", "暂停期间新接入")
    restarted = TeacherRuntime(server=transport, database_path=database)
    try:
        assert restarted.snapshot().application_control_enabled is False
        assert all(item.envelope.action == "blacklist.clear" for item in transport.sent_to("pc-03"))
        transport.connect("pc-02", "PC-02")
        assert transport.sent_to("pc-02")[-1].envelope.action == "blacklist.clear"
        resumed = restarted.set_application_control_enabled(True)
        assert all(receipt.action.kind is CommandKind.BLACKLIST_SET for receipt in resumed)
        snapshot = restarted.snapshot()
        assert snapshot.application_control_enabled is True
        assert snapshot.device("pc-03").expected_for("blacklist:game.exe").value is True
        assert snapshot.device("pc-01").expected_for(r"blacklist:c:\tools\editor.exe").value is True
        assert snapshot.device("pc-03").expected_for(r"blacklist:c:\tools\editor.exe") is None
        transport.connect("pc-04", "重新开启后接入")
        assert transport.sent_to("pc-04")[-1].envelope.action == "blacklist.set"
    finally:
        restarted.close()


def test_application_control_switch_preserves_legacy_rules_and_edits_while_paused(rig):
    _, transport, classroom = rig
    classroom.submit(ClassroomAction(
        CommandKind.BLACKLIST_SET, {"program": "legacy.exe", "path": r"C:\Legacy\legacy.exe"},
    ), DeviceIds(["pc-01"]))
    classroom.set_application_control_enabled(False)
    classroom.set_global_blacklist("game.exe")
    classroom.set_blacklist(DeviceIds(["pc-02"]), "editor.exe", enabled=True)
    assert classroom.snapshot().device("pc-02").expected_for("blacklist:editor.exe").value is False
    classroom.set_global_blacklist("game.exe", False)
    classroom.set_blacklist(DeviceIds(["pc-02"]), "editor.exe", enabled=False)
    classroom.set_application_control_enabled(True)
    assert classroom.snapshot().global_blacklist == ()
    assert classroom.snapshot().device("pc-01").expected_for(r"blacklist:c:\legacy\legacy.exe").value is True
    assert classroom.snapshot().device("pc-02").expected_for("blacklist:editor.exe").value is False
    transport.connect("pc-03", "新设备")
    assert not transport.sent_to("pc-03")


def test_application_control_can_be_paused_without_any_rules_and_stays_paused(tmp_path):
    database = str(tmp_path / "empty-application-switch.db")
    runtime = TeacherRuntime(server=InMemoryTransport(), database_path=database)
    try:
        assert runtime.set_application_control_enabled(False) == ()
    finally:
        runtime.close()
    transport = InMemoryTransport()
    transport.connect("new", "NEW")
    restarted = TeacherRuntime(server=transport, database_path=database)
    try:
        assert restarted.snapshot().application_control_enabled is False
        receipt = restarted.set_global_blacklist("game.exe")
        assert receipt.action.kind is CommandKind.BLACKLIST_CLEAR
        assert restarted.snapshot().global_blacklist == ("game.exe",)
        assert not restarted.snapshot().device("new").expected_for("blacklist:game.exe").value
    finally:
        restarted.close()


@pytest.mark.parametrize("program", [
    "", "*.exe", "https://example.test/app.exe", "游戏", "foo.exe --flag",
    "svchost.exe", "LanClassroom-Student.exe",
])
def test_global_blacklist_rejects_invalid_program_without_creating_policy(rig, program):
    _, transport, classroom = rig
    with pytest.raises(ValueError):
        classroom.set_global_blacklist(program, True)
    assert classroom.snapshot().global_blacklist == ()
    assert not transport.sent


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


def test_device_note_persists_across_reconnect_restart_and_clear(tmp_path):
    database = str(tmp_path / "device-notes.db")
    transport = InMemoryTransport()
    transport.connect("pc-01", "同名设备", metadata={"ip": "192.168.1.10"})
    transport.connect("pc-02", "同名设备")
    runtime = TeacherRuntime(server=transport, database_path=database)
    try:
        revision = runtime.snapshot().revision
        assert runtime.set_device_note("pc-01", "  第一排 · 张同学  ") is True
        assert runtime.snapshot().revision > revision
        assert runtime.snapshot().device("pc-01").note == "第一排 · 张同学"
        assert runtime.snapshot().device("pc-02").note == ""
        revision = runtime.snapshot().revision
        assert runtime.set_device_note("pc-01", "第一排 · 张同学") is False
        assert runtime.snapshot().revision == revision
        transport.disconnect("pc-01")
        transport.connect("pc-01", "新主机名", metadata={"ip": "192.168.1.20", "note": "学生自报"})
        device = runtime.snapshot().device("pc-01")
        assert device.note == "第一排 · 张同学"
        assert device.hostname == "新主机名"
        assert device.metadata["ip"] == "192.168.1.20"
        assert not transport.sent
    finally:
        runtime.close()

    restarted = TeacherRuntime(server=InMemoryTransport(), database_path=database)
    try:
        assert restarted.snapshot().device("pc-01").note == "第一排 · 张同学"
        assert restarted.set_device_note("pc-01", "   ") is True
        with pytest.raises(KeyError):
            restarted.set_device_note("missing", "备注")
    finally:
        restarted.close()
    cleared = TeacherRuntime(server=InMemoryTransport(), database_path=database)
    try:
        assert cleared.snapshot().device("pc-01").note == ""
    finally:
        cleared.close()


@pytest.mark.parametrize("conflicting", [False, True])
def test_duplicate_cleanup_preserves_teacher_notes(conflicting):
    transport = InMemoryTransport()
    metadata = {"mac": "02:12:34:56:78:90"}
    transport.connect("stable-id", "PC-01", metadata=metadata)
    transport.connect("duplicate-id", "PC-01", metadata=metadata)
    runtime = TeacherRuntime(server=transport, database_path=":memory:")
    try:
        runtime.set_device_note("duplicate-id", "后排设备")
        if conflicting:
            runtime.set_device_note("stable-id", "前排设备")
        transport.disconnect("duplicate-id")
        runtime.classroom.consolidate_duplicate_devices("stable-id")
        snapshot = runtime.snapshot()
        if conflicting:
            assert len(snapshot.devices) == 2
            assert snapshot.device("stable-id").note == "前排设备"
            assert snapshot.device("duplicate-id").note == "后排设备"
        else:
            assert len(snapshot.devices) == 1
            assert snapshot.device("stable-id").note == "后排设备"
            runtime.set_device_note("duplicate-id", "更新备注")
            assert runtime.snapshot().device("stable-id").note == "更新备注"
    finally:
        runtime.close()


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


def test_broadcast_stops_only_selected_target():
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

    assert not transport.sent_to("pc-02")
    assert len(runtime.broadcast_media.started) == 1
    transport.clear_sent()
    runtime.stop_broadcast(DeviceIds(["pc-02"]))
    assert [item.device_id for item in transport.sent] == ["pc-02"]
    assert runtime.broadcast_media.stop_count == 0
    runtime.stop_broadcast(DeviceIds(["pc-01"]))
    assert runtime.broadcast_media.stop_count == 1
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


def test_reconnect_consolidates_confirmed_duplicate_without_losing_group_or_audit(tmp_path):
    database = str(tmp_path / "identity.db")
    transport = InMemoryTransport()
    transport.connect("stable-id", "PC-01", "第一组", metadata={"mac": "02:10:20:30:40:ab"})
    transport.connect("duplicate-id", "PC-01", metadata={"mac": "02-10-20-30-40-AB"})
    runtime = TeacherRuntime(server=transport, database_path=database)
    try:
        old = runtime.set_silence(DeviceIds(["duplicate-id"]), True)
        transport.disconnect("stable-id")
        transport.disconnect("duplicate-id")
        assert runtime._resolve_registered_identity("stable-id", "0210203040ab") == (
            "stable-id", "第一组"
        )
        snapshot = runtime.snapshot()
        assert [device.device_id for device in snapshot.devices] == ["stable-id"]
        assert snapshot.operation(old.command_id) is not None
        assert runtime.set_silence(All(), False).target_ids == ("stable-id",)
    finally:
        runtime.close()
    restarted = TeacherRuntime(server=InMemoryTransport(), database_path=database)
    try:
        assert [device.device_id for device in restarted.snapshot().devices] == ["stable-id"]
        assert restarted.snapshot().operation(old.command_id) is not None
    finally:
        restarted.close()


@pytest.mark.parametrize("protected", ["group", "policy"])
def test_duplicate_cleanup_preserves_conflicting_group_or_permanent_policy(protected):
    transport = InMemoryTransport()
    transport.connect("stable-id", "PC-01", metadata={"mac": "02:10:20:30:40:ab"})
    transport.connect("duplicate-id", "PC-01", "第二组" if protected == "group" else "default",
                      metadata={"mac": "02-10-20-30-40-AB"})
    runtime = TeacherRuntime(server=transport, database_path=":memory:")
    try:
        if protected == "policy":
            runtime.set_blacklist(DeviceIds(["duplicate-id"]), "game.exe")
        transport.disconnect("stable-id")
        transport.disconnect("duplicate-id")
        assert runtime._resolve_registered_identity("stable-id", "0210203040ab")[0] == "stable-id"
        assert len(runtime.snapshot().devices) == 2
    finally:
        runtime.close()


def test_archived_identity_reconnect_maps_to_canonical_after_mac_change():
    transport = InMemoryTransport()
    for device_id in ("stable-id", "duplicate-id"):
        transport.connect(device_id, "PC-01", metadata={
            "mac": "02:10:20:30:40:01", "machine_id": "hardware-01",
        })
    runtime = TeacherRuntime(server=transport, database_path=":memory:")
    try:
        transport.disconnect("duplicate-id")
        runtime.classroom.consolidate_duplicate_devices("stable-id")
        assert runtime._resolve_registered_identity(
            "duplicate-id", "02:10:20:30:40:02", "hardware-01"
        )[0] == "stable-id"
        transport.disconnect("duplicate-id")
        assert len(runtime.snapshot().devices) == 1
    finally:
        runtime.close()


def test_archived_id_reused_by_different_hardware_is_not_hidden():
    transport = InMemoryTransport()
    for device_id in ("stable-id", "duplicate-id"):
        transport.connect(device_id, "PC-01", metadata={
            "mac": "02:10:20:30:40:01", "machine_id": "hardware-01",
        })
    runtime = TeacherRuntime(server=transport, database_path=":memory:")
    try:
        transport.disconnect("duplicate-id")
        runtime.classroom.consolidate_duplicate_devices("stable-id")
        resolved, _group = runtime._resolve_registered_identity(
            "duplicate-id", "02:10:20:30:40:02", "hardware-02"
        )
        transport.connect(resolved, "PC-02", metadata={
            "mac": "02:10:20:30:40:02", "machine_id": "hardware-02",
        })
        assert len(runtime.snapshot().devices) == 2
    finally:
        runtime.close()


def test_forget_offline_seat_preserves_audit_and_allows_reconnect(tmp_path):
    transport = InMemoryTransport()
    transport.connect("a", "PC")
    db = str(tmp_path / "forget.db")
    runtime = TeacherRuntime(server=transport, database_path=db)
    receipt = runtime.set_silence(All(), False)
    with pytest.raises(ValueError):
        runtime.classroom.forget_offline_device("a")
    transport.disconnect("a")
    runtime.classroom.forget_offline_device("a")
    runtime.close()
    transport = InMemoryTransport()
    runtime = TeacherRuntime(server=transport, database_path=db)
    assert not runtime.snapshot().devices
    assert runtime.snapshot().operation(receipt.command_id)
    transport.connect("a", "PC")
    assert runtime.snapshot().device("a").online
    runtime.close()


def test_startup_merges_hardware_match_but_keeps_name_and_ip_collision(tmp_path):
    db = str(tmp_path / "history.db")
    transport = InMemoryTransport()
    for key, mac, hw in (("a", "001122334401", "board-1"), ("b", "001122334402", "board-1"),
                         ("c", "001122334403", "")):
        transport.connect(key, "PC", metadata={"mac": mac, "machine_id": hw, "ip": "192.0.2.4"})
    runtime = TeacherRuntime(server=transport, database_path=db)
    runtime.close()
    runtime = TeacherRuntime(server=InMemoryTransport(), database_path=db)
    assert len(runtime.snapshot().devices) == 2
    assert runtime.snapshot().device("c")
    runtime.close()
