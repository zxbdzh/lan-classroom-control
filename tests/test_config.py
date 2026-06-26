import pytest
import os
import tempfile
import time
from common.config import Config, get_config


class TestConfig:
    def test_default_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "test_config.json")
            config = Config(config_path)
            assert config.get("teacher.tcp_port") == 9528
            assert config.get("teacher.udp_broadcast_port") == 9527
            assert config.get("student.auto_connect") is True
            assert config.get("ui.theme") == "default"

    def test_get_set(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "test_config.json")
            config = Config(config_path)
            config.set("teacher.tcp_port", 8888)
            assert config.get("teacher.tcp_port") == 8888

    def test_get_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "test_config.json")
            config = Config(config_path)
            assert config.get("nonexistent.key", "default") == "default"
            assert config.get("nonexistent.key") is None

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "test_config.json")
            config1 = Config(config_path)
            config1.set("teacher.tcp_port", 7777)
            config1.set("student.auto_connect", False)
            config1.save()
            config2 = Config(config_path)
            assert config2.get("teacher.tcp_port") == 7777
            assert config2.get("student.auto_connect") is False

    def test_nested_set(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "test_config.json")
            config = Config(config_path)
            config.set("new.section.key", "value")
            assert config.get("new.section.key") == "value"

    def test_dict_item_access(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "test_config.json")
            config = Config(config_path)
            assert config["teacher.tcp_port"] == 9528
            config["teacher.tcp_port"] = 6666
            assert config["teacher.tcp_port"] == 6666

    def test_corrupted_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "test_config.json")
            with open(config_path, "w") as f:
                f.write("{invalid json")
            config = Config(config_path)
            assert config.get("teacher.tcp_port") == 9528
