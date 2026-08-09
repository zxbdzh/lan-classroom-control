"""Persistence adapters for the v2 classroom module.

The module deliberately exposes only ``load`` and ``save``.  SQLite table
layout, migrations, transactions, and audit retention remain behind that
small seam, while tests can use the in-memory adapter.
"""

from __future__ import annotations

import copy
import json
import os
import sqlite3
import threading
import time
from enum import Enum
from typing import Any, Dict, Mapping, Optional


SCHEMA_VERSION = 2
AUDIT_RETENTION_SECONDS = 90 * 24 * 60 * 60


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]
    return value


def _json_dumps(value: Any) -> str:
    return json.dumps(_json_value(value), ensure_ascii=True, separators=(",", ":"))


def _empty_state() -> Dict[str, Any]:
    return {
        "revision": 0,
        "command_revision": 0,
        "devices": {},
        "operations": {},
        "desired": {},
        "actual": {},
    }


class InMemoryStateStore:
    """Deterministic adapter with the same copy semantics as SQLite."""

    def __init__(self, initial: Optional[Mapping[str, Any]] = None) -> None:
        self._lock = threading.Lock()
        self._state = copy.deepcopy(dict(initial)) if initial is not None else _empty_state()

    def load(self) -> Dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._state)

    def save(self, state: Mapping[str, Any]) -> None:
        with self._lock:
            self._state = copy.deepcopy(_json_value(state))

    def close(self) -> None:
        return None


MemoryStateStore = InMemoryStateStore


class SQLiteStateStore:
    """SQLite adapter using WAL and forward-only schema migrations."""

    def __init__(self, path: str) -> None:
        if not path:
            raise ValueError("database path must not be empty")
        if path != ":memory:":
            parent = os.path.dirname(os.path.abspath(path))
            if parent:
                os.makedirs(parent, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA busy_timeout=5000")
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=NORMAL")
        self._cached_state: Optional[Dict[str, Any]] = None
        self._migrate()

    @property
    def journal_mode(self) -> str:
        with self._lock:
            row = self._connection.execute("PRAGMA journal_mode").fetchone()
            return str(row[0]).lower()

    @property
    def schema_version(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ).fetchone()
            return int(row[0])

    def _migrate(self) -> None:
        migrations = (
            (
                1,
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS classroom_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS devices (
                    device_id TEXT PRIMARY KEY,
                    hostname TEXT NOT NULL,
                    group_name TEXT NOT NULL,
                    online INTEGER NOT NULL,
                    last_seen REAL NOT NULL,
                    metadata_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS operations (
                    command_id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    action_json TEXT NOT NULL,
                    target_ids_json TEXT NOT NULL,
                    submitted_at REAL NOT NULL,
                    expires_at REAL
                );
                CREATE TABLE IF NOT EXISTS operation_results (
                    command_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    PRIMARY KEY (command_id, device_id),
                    FOREIGN KEY (command_id) REFERENCES operations(command_id)
                        ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS desired_state (
                    device_id TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    PRIMARY KEY (device_id, domain)
                );
                CREATE TABLE IF NOT EXISTS actual_state (
                    device_id TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    PRIMARY KEY (device_id, domain)
                );
                CREATE TABLE IF NOT EXISTS audit_log (
                    command_id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    submitted_at REAL NOT NULL,
                    action_json TEXT NOT NULL
                );
                """,
            ),
            (
                2,
                """
                CREATE INDEX IF NOT EXISTS idx_operations_revision
                    ON operations(revision);
                CREATE INDEX IF NOT EXISTS idx_audit_submitted_at
                    ON audit_log(submitted_at);
                CREATE INDEX IF NOT EXISTS idx_devices_group
                    ON devices(group_name);
                """,
            ),
        )
        with self._lock:
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)"
            )
            current = {
                int(row[0])
                for row in self._connection.execute("SELECT version FROM schema_migrations")
            }
            for version, script in migrations:
                if version in current:
                    continue
                with self._connection:
                    self._connection.executescript(script)
                    self._connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (version, time.time()),
                    )

    def load(self) -> Dict[str, Any]:
        with self._lock:
            state = _empty_state()
            row = self._connection.execute(
                "SELECT value FROM classroom_meta WHERE key='revision'"
            ).fetchone()
            if row is not None:
                state["revision"] = int(row[0])
            row = self._connection.execute(
                "SELECT value FROM classroom_meta WHERE key='command_revision'"
            ).fetchone()
            if row is not None:
                state["command_revision"] = int(row[0])

            for row in self._connection.execute("SELECT * FROM devices"):
                metadata = json.loads(row["metadata_json"])
                metadata.update(
                    {
                        "hostname": row["hostname"],
                        "group": row["group_name"],
                        "online": bool(row["online"]),
                        "last_seen": float(row["last_seen"]),
                    }
                )
                state["devices"][row["device_id"]] = metadata

            results: Dict[str, Dict[str, Any]] = {}
            for row in self._connection.execute("SELECT * FROM operation_results"):
                results.setdefault(row["command_id"], {})[row["device_id"]] = json.loads(
                    row["result_json"]
                )
            for row in self._connection.execute(
                "SELECT * FROM operations ORDER BY revision"
            ):
                action = json.loads(row["action_json"])
                state["operations"][row["command_id"]] = {
                    "revision": int(row["revision"]),
                    "action": action,
                    "target_ids": json.loads(row["target_ids_json"]),
                    "submitted_at": float(row["submitted_at"]),
                    "expires_at": (
                        float(row["expires_at"]) if row["expires_at"] is not None else None
                    ),
                    "statuses": results.get(row["command_id"], {}),
                }

            for table, key in (("desired_state", "desired"), ("actual_state", "actual")):
                for row in self._connection.execute(
                    "SELECT device_id, domain, state_json FROM {}".format(table)
                ):
                    state[key].setdefault(row["device_id"], {})[row["domain"]] = json.loads(
                        row["state_json"]
                    )
            self._cached_state = copy.deepcopy(state)
            return state

    def save(self, state: Mapping[str, Any]) -> None:
        normalized = _json_value(state)
        previous = self._cached_state or _empty_state()
        now = time.time()
        with self._lock, self._connection:
            self._save_meta(normalized, previous)
            self._save_devices(normalized, previous)
            self._save_operations(normalized, previous)
            self._save_domain_table("desired_state", "desired", normalized, previous)
            self._save_domain_table("actual_state", "actual", normalized, previous)

            self._connection.execute(
                "DELETE FROM audit_log WHERE submitted_at < ?",
                (now - AUDIT_RETENTION_SECONDS,),
            )
            self._cached_state = copy.deepcopy(normalized)

    def _save_meta(self, current: Mapping[str, Any], previous: Mapping[str, Any]) -> None:
        for key in ("revision", "command_revision"):
            value = int(current.get(key, 0))
            if value == int(previous.get(key, 0)) and self._cached_state is not None:
                continue
            self._connection.execute(
                "INSERT OR REPLACE INTO classroom_meta(key, value) VALUES (?, ?)",
                (key, str(value)),
            )

    def _save_devices(self, current: Mapping[str, Any], previous: Mapping[str, Any]) -> None:
        current_devices = dict(current.get("devices", {}))
        previous_devices = dict(previous.get("devices", {}))
        for device_id in set(previous_devices) - set(current_devices):
            self._connection.execute("DELETE FROM devices WHERE device_id=?", (device_id,))
        for device_id, device in current_devices.items():
            if device == previous_devices.get(device_id) and self._cached_state is not None:
                continue
            metadata = dict(device)
            hostname = str(metadata.pop("hostname", device_id))
            group = str(metadata.pop("group", "default"))
            online = bool(metadata.pop("online", False))
            last_seen = float(metadata.pop("last_seen", 0.0))
            self._connection.execute(
                "INSERT OR REPLACE INTO devices VALUES (?, ?, ?, ?, ?, ?)",
                (device_id, hostname, group, int(online), last_seen, _json_dumps(metadata)),
            )

    def _save_operations(self, current: Mapping[str, Any], previous: Mapping[str, Any]) -> None:
        current_operations = dict(current.get("operations", {}))
        previous_operations = dict(previous.get("operations", {}))
        for command_id in set(previous_operations) - set(current_operations):
            self._connection.execute(
                "DELETE FROM operations WHERE command_id=?", (command_id,)
            )
        for command_id, operation in current_operations.items():
            old_operation = previous_operations.get(command_id, {})
            base = {key: value for key, value in operation.items() if key != "statuses"}
            old_base = {
                key: value for key, value in old_operation.items() if key != "statuses"
            }
            base_changed = base != old_base or self._cached_state is None
            action = operation.get("action", {})
            if base_changed:
                self._connection.execute(
                    "INSERT OR REPLACE INTO operations VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        command_id,
                        int(operation["revision"]),
                        str(action.get("kind", "")),
                        _json_dumps(action),
                        _json_dumps(operation.get("target_ids", [])),
                        float(operation.get("submitted_at", 0.0)),
                        operation.get("expires_at"),
                    ),
                )
                self._connection.execute(
                    "INSERT OR IGNORE INTO audit_log "
                    "(command_id, revision, kind, submitted_at, action_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        command_id,
                        int(operation["revision"]),
                        str(action.get("kind", "")),
                        float(operation.get("submitted_at", 0.0)),
                        _json_dumps(action),
                    ),
                )
            current_statuses = dict(operation.get("statuses", {}))
            old_statuses = dict(old_operation.get("statuses", {}))
            if base_changed:
                old_statuses = {}
            for device_id in set(old_statuses) - set(current_statuses):
                self._connection.execute(
                    "DELETE FROM operation_results WHERE command_id=? AND device_id=?",
                    (command_id, device_id),
                )
            for device_id, result in current_statuses.items():
                if result == old_statuses.get(device_id) and self._cached_state is not None:
                    continue
                self._connection.execute(
                    "INSERT OR REPLACE INTO operation_results VALUES (?, ?, ?)",
                    (command_id, device_id, _json_dumps(result)),
                )

    def _save_domain_table(
        self,
        table: str,
        key: str,
        current: Mapping[str, Any],
        previous: Mapping[str, Any],
    ) -> None:
        current_flat = {
            (device_id, domain): value
            for device_id, domains in current.get(key, {}).items()
            for domain, value in domains.items()
        }
        previous_flat = {
            (device_id, domain): value
            for device_id, domains in previous.get(key, {}).items()
            for domain, value in domains.items()
        }
        for device_id, domain in set(previous_flat) - set(current_flat):
            self._connection.execute(
                "DELETE FROM {} WHERE device_id=? AND domain=?".format(table),
                (device_id, domain),
            )
        for identity, value in current_flat.items():
            if value == previous_flat.get(identity) and self._cached_state is not None:
                continue
            self._connection.execute(
                "INSERT OR REPLACE INTO {} VALUES (?, ?, ?)".format(table),
                (identity[0], identity[1], _json_dumps(value)),
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> "SQLiteStateStore":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()


StateStore = SQLiteStateStore


__all__ = [
    "AUDIT_RETENTION_SECONDS",
    "InMemoryStateStore",
    "MemoryStateStore",
    "SCHEMA_VERSION",
    "SQLiteStateStore",
    "StateStore",
]
