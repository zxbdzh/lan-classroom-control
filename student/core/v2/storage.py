import json
import ntpath
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from common.logger import get_logger

logger = get_logger("student_v2_storage")


def default_state_database_path() -> str:
    if os.name == "nt":
        base = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        return os.path.join(base, "LanClassroom", "student-v2.db")
    return os.path.join(os.path.expanduser("~"), ".lan_classroom", "student-v2.db")


def find_legacy_device_id(config_paths: Optional[List[str]] = None) -> Optional[str]:
    """Find the newest valid v1 device ID from local user profiles."""
    paths = list(config_paths) if config_paths is not None else _legacy_config_paths()
    candidates = []
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            value = str((data.get("student") or {}).get("student_id") or "")
            device_id = str(uuid.UUID(value))
            candidates.append((os.path.getmtime(path), device_id))
        except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError):
            continue
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _legacy_config_paths() -> List[str]:
    if os.name != "nt":
        return [os.path.join(os.path.expanduser("~"), ".lan_classroom", "config.json")]
    users_root = os.path.join(os.environ.get("SystemDrive", "C:"), "Users")
    paths = []
    try:
        for entry in os.scandir(users_root):
            if entry.is_dir(follow_symlinks=False):
                paths.append(
                    os.path.join(entry.path, ".lan_classroom", "config.json")
                )
    except OSError as exc:
        logger.debug(f"Cannot scan legacy student identities: {exc}")
    return paths


def normalize_executable_name(executable_name: str) -> str:
    """Return the case-insensitive Windows basename used for matching."""
    value = (executable_name or "").strip().replace("/", "\\")
    name = ntpath.basename(value).strip()
    if not name:
        raise ValueError("executable_name must not be empty")
    return name.casefold()


def normalize_windows_path(path: str) -> str:
    """Normalize a Windows executable path independent of the host OS."""
    value = (path or "").strip().replace("/", "\\")
    if not value:
        raise ValueError("path must not be empty")
    return ntpath.normcase(ntpath.normpath(value))


@dataclass(frozen=True)
class ProcessBlacklistRule:
    rule_id: int
    executable_name: str
    full_path: Optional[str]
    created_at: float


class StudentStateStore:
    """Thread-safe SQLite storage for durable student identity and policy."""

    def __init__(
        self,
        database_path: Optional[str] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        database_path = database_path or default_state_database_path()
        if not database_path:
            raise ValueError("database_path must not be empty")
        self.database_path = database_path
        self._clock = clock
        self._lock = threading.RLock()
        self._closed = False

        if database_path != ":memory:":
            parent = os.path.dirname(os.path.abspath(database_path))
            os.makedirs(parent, exist_ok=True)

        self._connection = sqlite3.connect(
            database_path,
            timeout=10.0,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        with self._lock:
            self._ensure_open()
            self._connection.execute("PRAGMA foreign_keys = ON")
            if self.database_path != ":memory:":
                self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS student_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS process_blacklist (
                    rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    executable_name TEXT NOT NULL,
                    executable_key TEXT NOT NULL,
                    full_path TEXT,
                    path_key TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    UNIQUE(executable_key, path_key)
                );
                """
            )
            self._connection.commit()

    def get_or_create_device_id(self, preferred_id: Optional[str] = None) -> str:
        with self._lock:
            self._ensure_open()
            row = self._connection.execute(
                "SELECT value FROM student_meta WHERE key = ?",
                ("device_id",),
            ).fetchone()
            if row is not None:
                value = str(row["value"])
                try:
                    return str(uuid.UUID(value))
                except (ValueError, AttributeError, TypeError):
                    logger.warning("Stored device ID is invalid; replacing it")

            device_id = str(uuid.uuid4())
            if preferred_id:
                try:
                    device_id = str(uuid.UUID(str(preferred_id)))
                except (ValueError, AttributeError, TypeError):
                    logger.warning("Preferred device ID is invalid; generating a new one")
            self._connection.execute(
                "INSERT OR REPLACE INTO student_meta(key, value) VALUES (?, ?)",
                ("device_id", device_id),
            )
            self._connection.commit()
            return device_id

    def add_blacklist_rule(
        self,
        executable_name: str,
        full_path: Optional[str] = None,
    ) -> ProcessBlacklistRule:
        executable_key = normalize_executable_name(executable_name)
        path_key = normalize_windows_path(full_path) if full_path else ""
        display_path = ntpath.normpath(full_path.strip()) if full_path else None
        display_name = ntpath.basename(executable_name.strip().replace("/", "\\"))

        with self._lock:
            self._ensure_open()
            self._connection.execute(
                """
                INSERT OR IGNORE INTO process_blacklist(
                    executable_name, executable_key, full_path, path_key, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (display_name, executable_key, display_path, path_key, self._clock()),
            )
            self._connection.commit()
            row = self._connection.execute(
                """
                SELECT rule_id, executable_name, full_path, created_at
                FROM process_blacklist
                WHERE executable_key = ? AND path_key = ?
                """,
                (executable_key, path_key),
            ).fetchone()
            if row is None:
                raise RuntimeError("failed to store blacklist rule")
            return self._row_to_rule(row)

    def list_blacklist_rules(self) -> List[ProcessBlacklistRule]:
        with self._lock:
            self._ensure_open()
            rows = self._connection.execute(
                """
                SELECT rule_id, executable_name, full_path, created_at
                FROM process_blacklist
                ORDER BY rule_id
                """
            ).fetchall()
            return [self._row_to_rule(row) for row in rows]

    def remove_blacklist_rule(self, rule_id: int) -> bool:
        with self._lock:
            self._ensure_open()
            cursor = self._connection.execute(
                "DELETE FROM process_blacklist WHERE rule_id = ?",
                (int(rule_id),),
            )
            self._connection.commit()
            return cursor.rowcount > 0

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("student state store is closed")

    @staticmethod
    def _row_to_rule(row: sqlite3.Row) -> ProcessBlacklistRule:
        return ProcessBlacklistRule(
            rule_id=int(row["rule_id"]),
            executable_name=str(row["executable_name"]),
            full_path=str(row["full_path"]) if row["full_path"] else None,
            created_at=float(row["created_at"]),
        )

    def __enter__(self) -> "StudentStateStore":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()
