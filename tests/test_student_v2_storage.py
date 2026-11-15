import json
import os
import uuid

from student.core.v2.blacklist import BlacklistEnforcer, BlacklistMatcher
from student.core.v2.processes import (
    ProcessInfo,
    ProcessTerminationResult,
    ProcessTerminationStatus,
)
from student.core.v2.storage import (
    StudentStateStore,
    default_state_database_path,
    find_legacy_device_id,
)


def test_legacy_device_identity_uses_newest_valid_user_config(tmp_path):
    older = tmp_path / "older.json"
    newer = tmp_path / "newer.json"
    invalid = tmp_path / "invalid.json"
    older_id = str(uuid.uuid4())
    newer_id = str(uuid.uuid4())
    older.write_text(
        json.dumps({"student": {"student_id": older_id}}), encoding="utf-8"
    )
    newer.write_text(
        json.dumps({"student": {"student_id": newer_id}}), encoding="utf-8"
    )
    invalid.write_text("not json", encoding="utf-8")
    os.utime(str(older), (10, 10))
    os.utime(str(newer), (20, 20))

    assert find_legacy_device_id([str(older), str(invalid), str(newer)]) == newer_id


def test_default_windows_state_path_uses_program_data(monkeypatch, tmp_path):
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))

    assert default_state_database_path() == str(
        tmp_path / "LanClassroom" / "student-v2.db"
    )


def test_device_identity_is_stable_in_memory():
    store = StudentStateStore(":memory:")

    first = store.get_or_create_device_id()
    second = store.get_or_create_device_id()

    assert first == second
    assert str(uuid.UUID(first)) == first
    store.close()


def test_device_identity_and_blacklist_survive_reopen(tmp_path):
    database = str(tmp_path / "student-v2.db")
    first_store = StudentStateStore(database, clock=lambda: 123.0)
    device_id = first_store.get_or_create_device_id()
    rule = first_store.add_blacklist_rule("Game.EXE")
    first_store.close()

    second_store = StudentStateStore(database)

    assert second_store.get_or_create_device_id() == device_id
    assert second_store.list_blacklist_rules() == [rule]
    second_store.close()


def test_blacklist_deduplicates_case_and_normalized_windows_path():
    store = StudentStateStore(":memory:", clock=lambda: 7.5)

    first = store.add_blacklist_rule(
        "C:/Games/Demo/Game.exe",
        "C:/Games/Demo/../Demo/Game.exe",
    )
    duplicate = store.add_blacklist_rule(
        "game.EXE",
        r"c:\games\demo\game.exe",
    )

    assert duplicate.rule_id == first.rule_id
    assert len(store.list_blacklist_rules()) == 1
    assert first.executable_name == "Game.exe"
    assert first.created_at == 7.5


def test_blacklist_remove_is_explicit_and_idempotent():
    store = StudentStateStore(":memory:")
    rule = store.add_blacklist_rule("chat.exe")

    assert store.remove_blacklist_rule(rule.rule_id) is True
    assert store.remove_blacklist_rule(rule.rule_id) is False
    assert store.list_blacklist_rules() == []


def test_blacklist_matcher_supports_name_and_optional_full_path():
    store = StudentStateStore(":memory:")
    name_rule = store.add_blacklist_rule("game.exe")
    path_rule = store.add_blacklist_rule("chat.exe", r"C:\Apps\Chat.exe")
    game = ProcessInfo(10, "GAME.EXE", r"D:\Game.exe", 1.0, "student")
    chat = ProcessInfo(11, "chat.exe", r"c:/apps/chat.exe", 2.0, "student")
    other_chat = ProcessInfo(12, "chat.exe", r"D:\Chat.exe", 3.0, "student")

    assert BlacklistMatcher.matches(game, name_rule)
    assert BlacklistMatcher.matches(chat, path_rule)
    assert not BlacklistMatcher.matches(other_chat, path_rule)


class _RecordingProcessController:
    def __init__(self, processes):
        self.processes = processes
        self.terminated = []

    def list_processes(self):
        return list(self.processes)

    def terminate_process(self, pid, expected_creation_time, timeout=3.0, force=False):
        self.terminated.append((pid, expected_creation_time))
        return ProcessTerminationResult(
            pid,
            ProcessTerminationStatus.TERMINATED,
            "terminated",
        )


def test_blacklist_enforcer_terminates_only_matching_instances():
    store = StudentStateStore(":memory:")
    store.add_blacklist_rule("game.exe")
    controller = _RecordingProcessController([
        ProcessInfo(21, "game.exe", r"C:\game.exe", 10.0, "student"),
        ProcessInfo(22, "notes.exe", r"C:\notes.exe", 11.0, "student"),
    ])
    enforcer = BlacklistEnforcer(store, controller)

    results = enforcer.enforce_once()

    assert controller.terminated == [(21, 10.0)]
    assert len(results) == 1
    assert results[0].termination.success
