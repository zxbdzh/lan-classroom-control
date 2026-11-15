"""所有注册表/平台调用均使用 fake；临时策略备份只写 pytest 的 tmp_path。"""

import json
import os
import threading
from types import SimpleNamespace

import pytest

from student.core import usb_control
from student.core.usb_control import POLICY_KEY, POLICY_VALUES, UsbStorageController


_REAL_DEFAULT_STATE_FILE = UsbStorageController._default_state_file


class FakeKey:
    def __init__(self, registry, path):
        self.registry = registry
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeRegistry:
    HKEY_LOCAL_MACHINE = "HKLM"
    REG_DWORD = 4
    KEY_QUERY_VALUE = 1
    KEY_SET_VALUE = 2
    KEY_WOW64_64KEY = 0x100

    def __init__(self):
        self.keys = {}
        self.calls = []
        self.fail_set = set()
        self.fail_delete = set()
        self.ignore_set = set()
        self.denied = False
        self.fail_flush = False
        self.fail_cleanup = False
        self.before_change = lambda: None

    def OpenKey(self, root, path, reserved=0, access=0):
        self.calls.append(("open", root, path, access))
        if self.denied:
            raise PermissionError("access denied")
        if path not in self.keys:
            raise FileNotFoundError(path)
        return FakeKey(self, path)

    def CreateKeyEx(self, root, path, reserved=0, access=0):
        self.calls.append(("create", root, path, access))
        self.before_change()
        if self.denied:
            raise PermissionError("access denied")
        self.keys.setdefault(path, {})
        return FakeKey(self, path)

    def QueryValueEx(self, key, name):
        self.calls.append(("query", key.path, name))
        if name not in self.keys[key.path]:
            raise FileNotFoundError(name)
        return self.keys[key.path][name]

    def SetValueEx(self, key, name, reserved, value_type, value):
        self.calls.append(("set", key.path, name, value, value_type))
        self.before_change()
        if name in self.fail_set:
            raise PermissionError("set denied: " + name)
        if name not in self.ignore_set:
            self.keys[key.path][name] = (value, value_type)

    def DeleteValue(self, key, name):
        self.calls.append(("delete", key.path, name))
        self.before_change()
        if name in self.fail_delete:
            raise PermissionError("delete denied: " + name)
        if name not in self.keys[key.path]:
            raise FileNotFoundError(name)
        del self.keys[key.path][name]

    def FlushKey(self, key):
        self.calls.append(("flush", key.path))
        if self.fail_flush:
            raise OSError("flush failed")

    def QueryInfoKey(self, key):
        children = sum(path.startswith(key.path + "\\") for path in self.keys)
        return children, len(self.keys[key.path]), 0

    def DeleteKeyEx(self, root, path, access=0, reserved=0):
        self.calls.append(("delete_key", root, path, access))
        if self.fail_cleanup:
            raise PermissionError("key delete denied")
        if path not in self.keys:
            raise FileNotFoundError(path)
        if self.keys[path]:
            raise OSError("key not empty")
        del self.keys[path]


class FakeKernel32:
    def __init__(self):
        self.calls = []
        self.wait_result = 0
        self.create_result = 42
        self.release_result = True
        self.active = False

    def CreateMutexW(self, security, owned, name):
        self.calls.append(("create_mutex", name))
        return self.create_result

    def WaitForSingleObject(self, handle, timeout):
        self.calls.append(("wait", handle, timeout))
        self.active = self.wait_result in (0, 0x80)
        return self.wait_result

    def ReleaseMutex(self, handle):
        self.calls.append(("release", handle))
        self.active = False
        return self.release_result

    def CloseHandle(self, handle):
        self.calls.append(("close", handle))
        return True


@pytest.fixture
def usb(monkeypatch, tmp_path):
    registry = FakeRegistry()
    kernel = FakeKernel32()
    monkeypatch.setattr(UsbStorageController, "_kernel32", staticmethod(lambda: kernel))
    # 替换模块级 winreg 与 sys，不污染 pytest 进程的平台判定，更不接触本机注册表。
    monkeypatch.setattr(usb_control, "winreg", registry)
    monkeypatch.setattr(usb_control, "sys", SimpleNamespace(platform="win32"))
    state_file = tmp_path / "LanClassroom" / "usb_control_state.json"
    controller = UsbStorageController(state_file=str(state_file))
    return SimpleNamespace(controller=controller, registry=registry, path=state_file, kernel=kernel)


def policy_values(registry):
    return registry.keys.get(POLICY_KEY, {})


def assert_no_registry_mutation(registry):
    assert not [call for call in registry.calls
                if call[0] in {"create", "set", "delete", "delete_key", "flush"}]


def test_block_persists_and_fsyncs_baseline_before_any_registry_mutation(usb, monkeypatch):
    syncs = []
    real_fsync = os.fsync

    def fsync(fd):
        syncs.append(fd)
        return real_fsync(fd)

    def check_backup():
        assert syncs
        state = json.loads(usb.path.read_text(encoding="utf-8"))
        assert state["key_existed"] is False
        assert state["values"] == {name: {"exists": False} for name in POLICY_VALUES}

    monkeypatch.setattr(usb_control.os, "fsync", fsync)
    usb.registry.before_change = check_backup
    assert usb.controller.block_storage()
    assert policy_values(usb.registry) == {name: (1, 4) for name in POLICY_VALUES}
    assert usb.controller.is_blocked()
    assert usb.controller.last_error == ""
    assert all(call[2] == POLICY_KEY for call in usb.registry.calls
               if call[0] in {"create", "open", "delete_key"})
    assert all(call[3] & usb.registry.KEY_WOW64_64KEY for call in usb.registry.calls
               if call[0] in {"create", "open", "delete_key"})
    assert set(call[2] for call in usb.registry.calls if call[0] == "set") == set(POLICY_VALUES)


def test_restore_absent_values_removes_only_owned_disk_key(usb):
    other_key = r"SOFTWARE\Policies\Microsoft\Windows\RemovableStorageDevices\OtherClass"
    usb.registry.keys[other_key] = {"Unrelated": (7, 4)}
    assert usb.controller.block_storage()
    assert usb.controller.restore_storage()
    assert POLICY_KEY not in usb.registry.keys
    assert usb.registry.keys[other_key] == {"Unrelated": (7, 4)}
    assert not usb.path.exists()
    assert not usb.controller.is_blocked()


def test_restore_preserves_original_disabled_enabled_and_missing_values(usb):
    baseline = {"Deny_Read": (1, 4), "Deny_Write": (0, 4), "OtherPolicy": (9, 4)}
    usb.registry.keys[POLICY_KEY] = baseline.copy()
    assert usb.controller.block_storage()
    usb.registry.keys[POLICY_KEY]["ExternalValue"] = (8, 4)
    assert usb.controller.restore_storage()
    assert policy_values(usb.registry) == dict(baseline, ExternalValue=(8, 4))
    assert not usb.controller.is_blocked()


def test_preexisting_fully_blocked_policy_still_has_recoverable_ownership(usb):
    baseline = {name: (1, 4) for name in POLICY_VALUES}
    usb.registry.keys[POLICY_KEY] = baseline.copy()
    assert usb.controller.block_storage()
    assert usb.controller.is_blocked()
    assert usb.path.is_file()
    assert not [call for call in usb.registry.calls if call[0] == "set"]
    assert usb.controller.restore_storage()
    assert policy_values(usb.registry) == baseline
    assert not usb.path.exists()


def test_repeated_block_does_not_replace_original_backup_even_across_instances(usb):
    usb.registry.keys[POLICY_KEY] = {"Deny_Read": (0, 4)}
    assert usb.controller.block_storage()
    baseline_bytes = usb.path.read_bytes()
    assert usb.controller.block_storage()
    new_controller = UsbStorageController(state_file=str(usb.path))
    assert new_controller.is_blocked()
    assert new_controller.block_storage()
    assert usb.path.read_bytes() == baseline_bytes
    assert new_controller.restore_stale_policy()
    assert policy_values(usb.registry) == {"Deny_Read": (0, 4)}


@pytest.mark.parametrize("method", ["restore_storage", "restore_stale_policy"])
def test_restore_without_own_backup_never_reads_or_changes_registry(usb, method):
    usb.registry.keys[POLICY_KEY] = {name: (1, 4) for name in POLICY_VALUES}
    assert getattr(usb.controller, method)()
    assert usb.registry.calls == []
    assert not usb.controller.is_blocked()


@pytest.mark.parametrize("platform", ["linux", "darwin"])
@pytest.mark.parametrize("method", ["block_storage", "restore_storage", "restore_stale_policy"])
def test_non_windows_explicitly_fails_without_touching_registry(usb, monkeypatch, platform, method):
    monkeypatch.setattr(usb_control, "sys", SimpleNamespace(platform=platform))
    if method == "block_storage":
        assert not getattr(usb.controller, method)()
        assert "Windows" in usb.controller.last_error
    else:
        assert getattr(usb.controller, method)()
        assert usb.controller.last_error == ""
    assert usb.registry.calls == []
    assert not usb.path.exists()


def test_registry_module_unavailable_is_explicit_failure(usb, monkeypatch):
    monkeypatch.setattr(usb_control, "winreg", None)
    assert not usb.controller.block_storage()
    assert "不支持" in usb.controller.last_error


def test_registry_read_permission_failure_does_not_save_guessed_baseline(usb):
    usb.registry.denied = True
    assert not usb.controller.block_storage()
    assert "access denied" in usb.controller.last_error
    assert not usb.path.exists()
    assert not usb.controller.is_blocked()
    assert_no_registry_mutation(usb.registry)


def test_backup_directory_failure_prevents_all_registry_mutations(usb, monkeypatch):
    def fail(*_args, **_kwargs):
        raise PermissionError("backup directory denied")

    monkeypatch.setattr(usb_control.os, "makedirs", fail)
    assert not usb.controller.block_storage()
    assert "backup directory denied" in usb.controller.last_error
    assert not usb.path.exists()
    assert_no_registry_mutation(usb.registry)


def test_fsync_failure_never_applies_and_retains_retryable_backup(usb, monkeypatch):
    real_fsync = os.fsync

    def fail(_fd):
        raise OSError("disk full")

    monkeypatch.setattr(usb_control.os, "fsync", fail)
    assert not usb.controller.block_storage()
    assert "disk full" in usb.controller.last_error
    assert usb.controller.is_blocked()
    assert usb.path.exists()
    assert_no_registry_mutation(usb.registry)
    monkeypatch.setattr(usb_control.os, "fsync", real_fsync)
    assert usb.controller.block_storage()
    assert usb.controller.restore_storage()


@pytest.mark.parametrize("name", POLICY_VALUES)
def test_partial_apply_failure_preserves_baseline_for_restart_recovery(usb, name):
    usb.registry.fail_set.add(name)
    assert not usb.controller.block_storage()
    assert "set denied" in usb.controller.last_error
    assert usb.controller.is_blocked()
    assert usb.path.exists()
    usb.registry.fail_set.clear()
    restarted = UsbStorageController(state_file=str(usb.path))
    assert restarted.restore_stale_policy()
    assert POLICY_KEY not in usb.registry.keys
    assert not restarted.is_blocked()


def test_apply_readback_mismatch_is_failure_with_backup_retained(usb):
    usb.registry.ignore_set.add("Deny_Write")
    assert not usb.controller.block_storage()
    assert "回读" in usb.controller.last_error
    assert usb.controller.is_blocked()
    assert usb.path.exists()
    assert usb.controller.restore_storage()


def test_apply_flush_failure_keeps_recovery_state(usb):
    usb.registry.fail_flush = True
    assert not usb.controller.block_storage()
    assert "flush failed" in usb.controller.last_error
    assert usb.controller.is_blocked()
    usb.registry.fail_flush = False
    assert usb.controller.restore_stale_policy()


@pytest.mark.parametrize("name", POLICY_VALUES)
def test_partial_restore_attempts_other_values_and_is_retryable(usb, name):
    assert usb.controller.block_storage()
    usb.registry.fail_delete.add(name)
    assert not usb.controller.restore_storage()
    assert usb.controller.is_blocked()
    assert usb.path.exists()
    assert policy_values(usb.registry) == {name: (1, 4)}
    assert "delete denied" in usb.controller.last_error
    usb.registry.fail_delete.clear()
    assert usb.controller.restore_storage()
    assert not usb.path.exists()
    assert not usb.controller.is_blocked()
    assert usb.controller.last_error == ""


def test_restore_set_failure_does_not_force_original_disabled_policy_open(usb):
    baseline = {"Deny_Read": (1, 4), "Deny_Write": (0, 4)}
    usb.registry.keys[POLICY_KEY] = baseline.copy()
    assert usb.controller.block_storage()
    usb.registry.fail_set.add("Deny_Write")
    assert not usb.controller.restore_storage()
    assert usb.controller.is_blocked()
    assert policy_values(usb.registry)["Deny_Read"] == (1, 4)
    usb.registry.fail_set.clear()
    assert usb.controller.restore_storage()
    assert policy_values(usb.registry) == baseline


def test_restore_readback_mismatch_retains_backup_for_retry(usb):
    usb.registry.keys[POLICY_KEY] = {"Deny_Read": (0, 4)}
    assert usb.controller.block_storage()
    usb.registry.ignore_set.add("Deny_Read")
    assert not usb.controller.restore_storage()
    assert "回读" in usb.controller.last_error
    assert usb.path.exists()
    usb.registry.ignore_set.clear()
    assert usb.controller.restore_storage()


def test_restore_flush_failure_retains_backup(usb):
    assert usb.controller.block_storage()
    usb.registry.fail_flush = True
    assert not usb.controller.restore_storage()
    assert usb.path.exists()
    usb.registry.fail_flush = False
    assert usb.controller.restore_storage()


def test_backup_remove_failure_remains_owned_and_retryable(usb, monkeypatch):
    assert usb.controller.block_storage()
    original_remove = os.remove

    def fail(path):
        if os.fspath(path) == str(usb.path):
            raise PermissionError("backup remove denied")
        return original_remove(path)

    monkeypatch.setattr(usb_control.os, "remove", fail)
    assert not usb.controller.restore_storage()
    assert usb.path.exists()
    assert usb.controller.is_blocked()
    monkeypatch.setattr(usb_control.os, "remove", original_remove)
    assert usb.controller.restore_stale_policy()
    assert not usb.path.exists()


def test_empty_key_cleanup_failure_retains_backup(usb):
    assert usb.controller.block_storage()
    usb.registry.fail_cleanup = True
    assert not usb.controller.restore_storage()
    assert usb.controller.is_blocked()
    usb.registry.fail_cleanup = False
    assert usb.controller.restore_stale_policy()


def test_external_values_in_created_key_are_not_removed(usb):
    assert usb.controller.block_storage()
    usb.registry.keys[POLICY_KEY]["AnotherProduct"] = (4, 4)
    assert usb.controller.restore_storage()
    assert policy_values(usb.registry) == {"AnotherProduct": (4, 4)}


def test_preexisting_empty_key_is_not_removed(usb):
    usb.registry.keys[POLICY_KEY] = {}
    assert usb.controller.block_storage()
    assert usb.controller.restore_storage()
    assert POLICY_KEY in usb.registry.keys
    assert not policy_values(usb.registry)


@pytest.mark.parametrize("payload", ["{broken", "{}", "[]", "null"])
@pytest.mark.parametrize("method", ["block_storage", "restore_storage"])
def test_corrupt_or_foreign_state_fails_closed_without_registry_access(usb, payload, method):
    usb.path.parent.mkdir(parents=True)
    usb.path.write_text(payload, encoding="utf-8")
    assert not getattr(usb.controller, method)()
    assert usb.controller.is_blocked()
    assert usb.path.read_text(encoding="utf-8") == payload
    assert usb.registry.calls == []


@pytest.mark.parametrize("mutation", [
    lambda state: state.update(owner="OtherProduct"),
    lambda state: state.update(version=2),
    lambda state: state.update(registry_view=32),
    lambda state: state.update(key_existed="False"),
    lambda state: state["values"].pop("Deny_Read"),
    lambda state: state["values"].update(ArbitraryRegistryValue={"exists": False}),
    lambda state: state["values"].update(Deny_Read={"exists": True, "type": 4, "value": -1}),
    lambda state: state["values"].update(Deny_Read={"exists": True, "type": 4, "value": True}),
    lambda state: state["values"].update(Deny_Read={"exists": True, "type": 1, "value": 0}),
])
def test_invalid_state_schema_cannot_select_unowned_registry_values(usb, mutation):
    assert usb.controller.block_storage()
    state = json.loads(usb.path.read_text(encoding="utf-8"))
    mutation(state)
    usb.path.write_text(json.dumps(state), encoding="utf-8")
    usb.registry.calls.clear()
    assert not usb.controller.restore_storage()
    assert usb.controller.is_blocked()
    assert usb.path.exists()
    assert usb.registry.calls == []


def test_unexpected_registry_type_is_not_overwritten(usb):
    usb.registry.keys[POLICY_KEY] = {"Deny_Read": ("administrator custom value", 1)}
    assert not usb.controller.block_storage()
    assert "REG_DWORD" in usb.controller.last_error
    assert not usb.path.exists()
    assert_no_registry_mutation(usb.registry)


def test_missing_baseline_after_successful_block_is_error_not_guess(usb):
    assert usb.controller.block_storage()
    usb.path.unlink()
    usb.registry.calls.clear()
    assert not usb.controller.restore_storage()
    assert "备份丢失" in usb.controller.last_error
    assert usb.controller.is_blocked()
    assert_no_registry_mutation(usb.registry)


def test_default_state_path_is_program_data_and_supports_injection(usb, monkeypatch, tmp_path):
    monkeypatch.setenv("ProgramData", str(tmp_path / "ProgramData"))
    # 其他模块的隔离 fixture 可以替换该函数，此处显式检验真实默认路径。
    controller = UsbStorageController(state_file=str(tmp_path / "chosen.json"))
    assert controller._state_file == str(tmp_path / "chosen.json")
    assert "LanClassroom" in _REAL_DEFAULT_STATE_FILE()
    assert _REAL_DEFAULT_STATE_FILE().startswith(str(tmp_path / "ProgramData"))


def test_apply_and_restore_are_serialized_by_reentrant_lock(usb):
    entered = threading.Event()
    result = []

    def apply():
        entered.set()
        result.append(usb.controller.block_storage())

    # 同线程重复进入必须可用，另一线程的 apply 则必须等 restore/apply 临界区释放。
    with usb.controller._lock:
        with usb.controller._lock:
            worker = threading.Thread(target=apply)
            worker.start()
            assert entered.wait(2)
            assert not usb.path.exists()
    worker.join(3)
    assert not worker.is_alive()
    assert result == [True]
    assert usb.controller.restore_stale_policy()


def test_block_does_not_recapture_restricted_values_after_backup_disappears(usb):
    assert usb.controller.block_storage()
    usb.path.unlink()
    usb.registry.calls.clear()
    assert not usb.controller.block_storage()
    assert "备份丢失" in usb.controller.last_error
    assert_no_registry_mutation(usb.registry)
    assert not usb.path.exists()
    assert usb.controller.is_blocked()


def test_non_windows_restore_with_existing_backup_fails_without_losing_state(usb, monkeypatch):
    assert usb.controller.block_storage()
    baseline = usb.path.read_bytes()
    usb.registry.calls.clear()
    monkeypatch.setattr(usb_control, "sys", SimpleNamespace(platform="linux"))
    assert not usb.controller.restore_stale_policy()
    assert "Windows" in usb.controller.last_error
    assert usb.path.read_bytes() == baseline
    assert usb.controller.is_blocked()
    assert usb.registry.calls == []


def test_policy_notice_does_not_claim_immediate_device_block(usb):
    assert "重启" in usb.controller.POLICY_NOTICE
    assert "不保证即时" in usb.controller.POLICY_NOTICE


@pytest.mark.parametrize("wait_result", [0x102, 0xFFFFFFFF])
def test_mutex_wait_failure_never_touches_registry_or_backup(usb, wait_result):
    usb.kernel.wait_result = wait_result
    assert not usb.controller.block_storage()
    assert "互斥锁" in usb.controller.last_error
    assert usb.registry.calls == []
    assert not usb.path.exists()
    assert ("close", 42) in usb.kernel.calls
    assert not [call for call in usb.kernel.calls if call[0] == "release"]


def test_mutex_creation_failure_is_reported_without_policy_mutation(usb):
    usb.kernel.create_result = 0
    assert not usb.controller.block_storage()
    assert "互斥锁" in usb.controller.last_error
    assert usb.registry.calls == []
    assert not usb.path.exists()


def test_abandoned_mutex_can_recover_saved_policy_after_previous_owner_dies(usb):
    assert usb.controller.block_storage()
    usb.kernel.wait_result = 0x80
    restarted = UsbStorageController(state_file=str(usb.path))
    assert restarted.restore_stale_policy()
    assert not usb.path.exists()
    assert POLICY_KEY not in usb.registry.keys


def test_mutex_spans_registry_writes_and_final_backup_removal(usb, monkeypatch):
    def check_lock():
        assert usb.kernel.active

    usb.registry.before_change = check_lock
    assert usb.controller.block_storage()
    assert not usb.kernel.active
    original_remove = os.remove

    def remove(path):
        assert usb.kernel.active
        return original_remove(path)

    monkeypatch.setattr(usb_control.os, "remove", remove)
    assert usb.controller.restore_storage()
    assert not usb.kernel.active
    assert usb.kernel.calls.count(("create_mutex", r"Global\LanClassroomUsbStoragePolicyV1")) == 2
    assert usb.kernel.calls.count(("release", 42)) == 2
    assert usb.kernel.calls.count(("close", 42)) == 2


def test_mutex_is_released_and_closed_after_partial_apply_failure(usb):
    usb.registry.fail_set.add("Deny_Write")
    assert not usb.controller.block_storage()
    assert usb.kernel.calls[-2:] == [("release", 42), ("close", 42)]
    assert not usb.kernel.active
    assert usb.path.exists()


def test_mutex_timeout_on_restore_preserves_recoverable_backup(usb):
    assert usb.controller.block_storage()
    baseline = usb.path.read_bytes()
    usb.registry.calls.clear()
    usb.kernel.wait_result = 0x102
    assert not usb.controller.restore_storage()
    assert usb.controller.is_blocked()
    assert usb.path.read_bytes() == baseline
    assert usb.registry.calls == []


def test_mutex_release_failure_is_not_reported_as_success(usb):
    usb.kernel.release_result = False
    assert not usb.controller.block_storage()
    assert usb.path.exists()
    assert usb.controller.is_blocked()
    assert "互斥锁" in usb.controller.last_error
    assert usb.kernel.calls[-1] == ("close", 42)


@pytest.mark.parametrize("method", ["restore_storage", "block_storage"])
@pytest.mark.parametrize("originally_blocked", [False, True])
def test_old_instance_reconciles_after_another_instance_restores(usb, method, originally_blocked):
    baseline = {name: (1, 4) for name in POLICY_VALUES} if originally_blocked else {"Deny_Read": (0, 4)}
    usb.registry.keys[POLICY_KEY] = baseline.copy()
    assert usb.controller.block_storage()
    assert usb.controller._baseline is not None
    watchdog = UsbStorageController(state_file=str(usb.path))
    assert watchdog.restore_stale_policy()
    assert not usb.path.exists()
    assert usb.controller._blocked
    usb.registry.calls.clear()
    assert getattr(usb.controller, method)()
    assert usb.controller.last_error == ""
    if method == "restore_storage":
        assert_no_registry_mutation(usb.registry)
        assert not usb.controller.is_blocked()
        assert usb.controller._baseline is None
        assert usb.controller.block_storage()
    assert usb.controller.is_blocked()
    assert usb.controller.restore_storage()
    assert policy_values(usb.registry) == baseline
    assert not usb.controller.is_blocked()


@pytest.mark.parametrize("method", ["restore_storage", "block_storage"])
def test_missing_backup_with_no_cached_baseline_never_clears_ownership(usb, method):
    usb.controller._blocked = True
    usb.registry.keys[POLICY_KEY] = {}
    assert usb.controller._baseline is None
    assert not getattr(usb.controller, method)()
    assert "无可核实基线" in usb.controller.last_error
    assert usb.controller.is_blocked()
    assert usb.registry.calls == []
    assert not usb.path.exists()


@pytest.mark.parametrize("method", ["restore_storage", "block_storage"])
def test_reconciliation_read_failure_keeps_cached_baseline_and_ownership(usb, method):
    assert usb.controller.block_storage()
    baseline = usb.controller._baseline
    assert UsbStorageController(state_file=str(usb.path)).restore_stale_policy()
    usb.registry.denied = True
    usb.registry.calls.clear()
    assert not getattr(usb.controller, method)()
    assert "access denied" in usb.controller.last_error
    assert usb.controller._baseline == baseline
    assert usb.controller.is_blocked()
    assert_no_registry_mutation(usb.registry)
    usb.registry.denied = False
    assert usb.controller.restore_storage()
    assert not usb.controller.is_blocked()


@pytest.mark.parametrize("method", ["restore_storage", "block_storage"])
def test_reconciliation_requires_every_original_value_and_type_to_match(usb, method):
    usb.registry.keys[POLICY_KEY] = {"Deny_Read": (0, 4), "Deny_Write": (1, 4)}
    assert usb.controller.block_storage()
    cached = usb.controller._baseline
    assert UsbStorageController(state_file=str(usb.path)).restore_stale_policy()
    # 数值相同但类型不同，不能当作恢复完成。
    usb.registry.keys[POLICY_KEY]["Deny_Read"] = (0, 1)
    usb.registry.calls.clear()
    assert not getattr(usb.controller, method)()
    assert "不等于已知原值" in usb.controller.last_error
    assert usb.controller._baseline == cached
    assert usb.controller.is_blocked()
    assert_no_registry_mutation(usb.registry)


def test_restore_instance_caches_before_partial_failure_then_reconciles_external_success(usb):
    assert usb.controller.block_storage()
    restorer = UsbStorageController(state_file=str(usb.path))
    usb.registry.fail_delete.add("Deny_Write")
    assert not restorer.restore_storage()
    assert restorer._baseline is not None
    assert restorer.is_blocked()
    usb.registry.fail_delete.clear()
    assert UsbStorageController(state_file=str(usb.path)).restore_stale_policy()
    usb.registry.calls.clear()
    assert restorer.restore_storage()
    assert not restorer.is_blocked()
    assert_no_registry_mutation(usb.registry)


def test_restore_fsync_failure_has_no_trusted_cache_and_cannot_assume_external_recovery(usb, monkeypatch):
    assert usb.controller.block_storage()
    restorer = UsbStorageController(state_file=str(usb.path))
    original_fsync = os.fsync

    def fail_fsync(_fd):
        raise OSError("baseline fsync failed")

    usb.registry.calls.clear()
    monkeypatch.setattr(usb_control.os, "fsync", fail_fsync)
    assert not restorer.restore_storage()
    assert "baseline fsync failed" in restorer.last_error
    assert restorer._baseline is None
    assert restorer.is_blocked()
    assert_no_registry_mutation(usb.registry)
    monkeypatch.setattr(usb_control.os, "fsync", original_fsync)
    assert UsbStorageController(state_file=str(usb.path)).restore_stale_policy()
    usb.registry.calls.clear()
    assert not restorer.restore_storage()
    assert "无可核实基线" in restorer.last_error
    assert restorer.is_blocked()
    assert usb.registry.calls == []


def test_cache_is_validated_and_fsynced_before_first_restore_mutation(usb, monkeypatch):
    assert usb.controller.block_storage()
    restorer = UsbStorageController(state_file=str(usb.path))
    original_fsync = os.fsync
    synced = []

    def track_fsync(fd):
        original_fsync(fd)
        synced.append(fd)

    def before_restore_change():
        assert synced
        assert restorer._baseline == {name: {"exists": False} for name in POLICY_VALUES}

    monkeypatch.setattr(usb_control.os, "fsync", track_fsync)
    usb.registry.before_change = before_restore_change
    assert restorer.restore_storage()
    assert restorer._baseline is None
