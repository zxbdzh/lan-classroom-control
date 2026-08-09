import json
import os
from typing import Any, Dict


class Config:
    def __init__(self, config_path: str = None):
        self._data: Dict[str, Any] = {}
        self._config_path = config_path or os.path.join(
            os.path.expanduser("~"), ".lan_classroom", "config.json"
        )
        self._set_defaults()
        self._load()

    def _set_defaults(self):
        self._data = {
            "teacher": {
                "udp_broadcast_port": 9527,
                "tcp_port": 9528,
                "broadcast_port": 9529,
                "inspection_port": 9530,
                "bulk_port": 9531,
                "multicast_group": "239.255.95.29",
                "heartbeat_interval": 3,
                "heartbeat_timeout": 10,
                "control_lease_seconds": 10,
                "max_students": 100,
                "broadcast_fps": 20,
                "broadcast_quality": 70,
                "broadcast_max_width": 1920,
                "broadcast_max_height": 1080,
                "silence_message": "请保持安静，注意教师屏幕",
                "audit_retention_days": 90,
                "internal_cidrs": [],
                "encryption_key": "",
            },
            "student": {
                "udp_listen_port": 9527,
                "tcp_port": 9528,
                "broadcast_port": 9529,
                "inspection_port": 9530,
                "bulk_port": 9531,
                "multicast_group": "239.255.95.29",
                "heartbeat_interval": 3,
                "control_lease_seconds": 10,
                "server_ip": "",
                "auto_connect": True,
                "encryption_key": "",
                "screen_monitor_enabled": True,
                "screen_monitor_fps": 1,
                "screen_monitor_quality": 45,
                "screen_monitor_max_width": 640,
                "screen_monitor_max_height": 360,
            },
            "ui": {
                "theme": "light",
                "language": "zh_CN",
                "seat_columns": 6,
                "seat_rows": 10,
                "seat_sort": "hostname_trailing_number",
                "sidebar_collapsed": False,
            }
        }

    def _load(self):
        if os.path.exists(self._config_path):
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                self._deep_update(self._data, loaded)
            except (json.JSONDecodeError, IOError):
                pass

    def _deep_update(self, base: dict, update: dict):
        for key, value in update.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_update(base[key], value)
            else:
                base[key] = value

    def save(self):
        os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
        with open(self._config_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    def get(self, key: str, default: Any = None) -> Any:
        keys = key.split(".")
        value = self._data
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value

    def set(self, key: str, value: Any):
        keys = key.split(".")
        data = self._data
        for k in keys[:-1]:
            if k not in data or not isinstance(data[k], dict):
                data[k] = {}
            data = data[k]
        data[keys[-1]] = value

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def __setitem__(self, key: str, value: Any):
        self.set(key, value)
        self.save()


_config_instance = None


def get_config() -> Config:
    global _config_instance
    if _config_instance is None:
        _config_instance = Config()
    return _config_instance
