import configparser
import hashlib
import hmac
import os
import sys
from typing import Iterable

from common.logger import get_logger

logger = get_logger("student_access")

DEFAULT_GUARD_PASSWORD = "yhfbcs"
GUARD_DIR_NAME = "LanClassroom"
GUARD_FILE_NAME = "student_guard.ini"


def get_guard_config_paths() -> Iterable[str]:
    """Return guard config locations from most to least authoritative."""
    paths = []
    if os.name == "nt":
        program_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        paths.append(os.path.join(program_data, GUARD_DIR_NAME, GUARD_FILE_NAME))

    paths.append(os.path.join(os.path.expanduser("~"), ".lan_classroom", GUARD_FILE_NAME))

    if getattr(sys, "frozen", False):
        paths.append(os.path.join(os.path.dirname(sys.executable), GUARD_FILE_NAME))

    return paths


def verify_guard_password(password: str) -> bool:
    expected = _load_expected_password()
    if not expected:
        expected = DEFAULT_GUARD_PASSWORD
    return hmac.compare_digest(password or "", expected)


def write_guard_password(password: str, path: str = None) -> bool:
    target = path or next(iter(get_guard_config_paths()))
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        parser = configparser.ConfigParser()
        parser["Guard"] = {
            "exit_password": password,
            "password_sha256": hashlib.sha256(password.encode("utf-8")).hexdigest(),
        }
        with open(target, "w", encoding="utf-8") as f:
            parser.write(f)
        return True
    except Exception as e:
        logger.warning(f"Write guard password failed: {e}")
        return False


def _load_expected_password() -> str:
    for path in get_guard_config_paths():
        password = _read_password_file(path)
        if password:
            return password
    return os.environ.get("LAN_CLASSROOM_INSTALL_PASSWORD", DEFAULT_GUARD_PASSWORD)


def _read_password_file(path: str) -> str:
    if not path or not os.path.exists(path):
        return ""
    try:
        parser = configparser.ConfigParser()
        parser.read(path, encoding="utf-8")
        return parser.get("Guard", "exit_password", fallback="").strip()
    except Exception as e:
        logger.warning(f"Read guard password failed: {path}: {e}")
        return ""
