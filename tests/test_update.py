"""自动更新功能测试"""
import os
import sys
import tempfile
import shutil
import zipfile
import pytest
from unittest.mock import MagicMock, patch
from common.version import version_compare, is_newer, get_version


# --------------------------------------------------------------------
# 版本号管理测试
# --------------------------------------------------------------------
class TestVersionCompare:
    def test_equal_versions(self):
        assert version_compare("1.0.0", "1.0.0") == 0

    def test_greater_version(self):
        assert version_compare("1.0.1", "1.0.0") == 1
        assert version_compare("1.1.0", "1.0.9") == 1
        assert version_compare("2.0.0", "1.9.9") == 1

    def test_lesser_version(self):
        assert version_compare("1.0.0", "1.0.1") == -1
        assert version_compare("1.0.9", "1.1.0") == -1

    def test_different_length(self):
        assert version_compare("1.0", "1.0.0") == 0
        assert version_compare("1.0.1", "1.0") == 1

    def test_non_numeric(self):
        assert version_compare("1.0a", "1.0b") == -1


class TestIsNewer:
    def test_newer_version(self):
        assert is_newer("2.0.0", "1.0.0") is True

    def test_older_version(self):
        assert is_newer("1.0.0", "2.0.0") is False

    def test_equal_version(self):
        assert is_newer("1.0.0", "1.0.0") is False

    def test_empty_candidate(self):
        assert is_newer("", "1.0.0") is False


# --------------------------------------------------------------------
# 协议消息类型测试
# --------------------------------------------------------------------
class TestUpdateProtocol:
    def test_update_message_types_exist(self):
        from common.protocol import MessageType
        assert MessageType.UPDATE_CHECK.value == "update_check"
        assert MessageType.UPDATE_REQUEST.value == "update_request"
        assert MessageType.UPDATE_PROGRESS.value == "update_progress"

    def test_build_update_check_message(self):
        from common.protocol import MessageType, build_message, parse_message_type
        msg = build_message(MessageType.UPDATE_CHECK, {
            "version": "1.0.8",
            "md5": "abc123",
            "file_name": "update.zip",
        })
        assert parse_message_type(msg) == MessageType.UPDATE_CHECK
        assert msg["params"]["version"] == "1.0.8"
        assert msg["params"]["md5"] == "abc123"


# --------------------------------------------------------------------
# UpdateClient 测试（不真正执行 updater）
# --------------------------------------------------------------------
class TestUpdateClient:
    def setup_method(self):
        self.sent_messages = []
        self.send_func = lambda msg: self.sent_messages.append(msg)

    def _create_client(self):
        from student.core.update_client import UpdateClient
        return UpdateClient(self.send_func)

    def test_check_and_request_skips_when_not_newer(self):
        client = self._create_client()
        with patch("student.core.update_client.is_newer", return_value=False):
            client.check_and_request({"version": "0.0.1"})
        # 不应发送任何消息
        assert len(self.sent_messages) == 0

    def test_check_and_request_sends_when_newer(self):
        client = self._create_client()
        with patch("student.core.update_client.is_newer", return_value=True), \
             patch("student.core.update_client.get_version", return_value="1.0.0"):
            client.check_and_request({"version": "1.0.8"})
        # 应发送 UPDATE_REQUEST
        assert len(self.sent_messages) == 1
        assert self.sent_messages[0]["type"] == "update_request"
        assert self.sent_messages[0]["params"]["target_version"] == "1.0.8"

    def test_handle_start_creates_active_transfer(self):
        client = self._create_client()
        client.handle_start({
            "transfer_id": "t1",
            "file_name": "update.zip",
            "file_size": 100,
            "total_chunks": 2,
            "md5": "",
            "target_version": "1.0.8",
        })
        assert client._active_transfer is not None
        assert client._active_transfer["transfer_id"] == "t1"
        assert client._active_transfer["target_version"] == "1.0.8"
        # 清理临时文件
        client.cancel()

    def test_get_progress_no_active(self):
        client = self._create_client()
        assert client.get_progress() == 0.0

    def test_cancel_clears_active_transfer(self):
        client = self._create_client()
        client.handle_start({
            "transfer_id": "t1",
            "file_name": "update.zip",
            "file_size": 100,
            "total_chunks": 2,
            "md5": "",
            "target_version": "1.0.8",
        })
        client.cancel()
        assert client._active_transfer is None


# --------------------------------------------------------------------
# UpdateServer 测试
# --------------------------------------------------------------------
class TestUpdateServer:
    def setup_method(self):
        # 创建临时更新包
        self.tmp_dir = tempfile.mkdtemp()
        self.update_zip = os.path.join(self.tmp_dir, "update.zip")
        with zipfile.ZipFile(self.update_zip, "w") as zf:
            zf.writestr("version.txt", "1.0.8")

        self.student_manager = MagicMock()
        self.file_distributor = MagicMock()
        self.file_distributor.send_file_to_students.return_value = "transfer_id_1"

    def teardown_method(self):
        if os.path.exists(self.tmp_dir):
            shutil.rmtree(self.tmp_dir)

    def _create_server(self):
        from teacher.core.update_server import UpdateServer
        return UpdateServer(self.student_manager, self.file_distributor)

    def test_set_update_package_success(self):
        server = self._create_server()
        assert server.set_update_package(self.update_zip, "1.0.8") is True
        assert server.update_version == "1.0.8"
        assert server.update_md5 != ""

    def test_set_update_package_not_found(self):
        server = self._create_server()
        assert server.set_update_package("/nonexistent.zip", "1.0.8") is False

    def test_notify_update_without_package(self):
        server = self._create_server()
        count = server.notify_update()
        assert count == 0

    def test_notify_update_with_package(self):
        server = self._create_server()
        server.set_update_package(self.update_zip, "1.0.8")
        # 模拟一个在线学生
        student = MagicMock()
        student.student_id = "s1"
        student.hostname = "host1"
        student.conn.is_alive.return_value = True
        self.student_manager.get_online_students.return_value = [student]

        count = server.notify_update()
        assert count == 1
        student.conn.send_message.assert_called_once()

    def test_on_update_request_no_package(self):
        server = self._create_server()
        conn = MagicMock()
        conn.student_id = "s1"
        result = server.on_update_request(conn, {"current_version": "1.0.0"})
        assert result is False

    def test_on_update_request_with_package(self):
        server = self._create_server()
        server.set_update_package(self.update_zip, "1.0.8")
        conn = MagicMock()
        conn.student_id = "s1"
        student = MagicMock()
        student.hostname = "host1"
        student.ip = "192.168.1.10"
        self.student_manager.get_student.return_value = student

        result = server.on_update_request(conn, {"current_version": "1.0.0"})
        assert result is True
        # 应该调用了 file_distributor.send_file_to_students 且 is_update=True
        self.file_distributor.send_file_to_students.assert_called_once()
        call_kwargs = self.file_distributor.send_file_to_students.call_args
        assert call_kwargs.kwargs.get("is_update") is True
        assert call_kwargs.kwargs.get("target_version") == "1.0.8"

    def test_on_update_progress(self):
        server = self._create_server()
        conn = MagicMock()
        conn.student_id = "s1"
        server.on_update_progress(conn, {"progress": 0.5, "status": "downloading"})
        progress = server.get_update_progress()
        assert "s1" in progress
        assert progress["s1"]["progress"] == 0.5

    def test_clear_update_package(self):
        server = self._create_server()
        server.set_update_package(self.update_zip, "1.0.8")
        server.clear_update_package()
        assert server.update_file_path is None
        assert server.update_version is None


# --------------------------------------------------------------------
# FileDistributor is_update 参数测试
# --------------------------------------------------------------------
class TestFileDistributorUpdate:
    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.test_file = os.path.join(self.tmp_dir, "test.zip")
        with open(self.test_file, "wb") as f:
            f.write(b"fake zip content for testing")

    def teardown_method(self):
        if os.path.exists(self.tmp_dir):
            shutil.rmtree(self.tmp_dir)

    def test_send_file_with_is_update(self):
        from teacher.core.file_distributor import FileDistributor
        distributor = FileDistributor()
        student = MagicMock()
        student.conn.is_alive.return_value = False  # 避免真正发送

        transfer_id = distributor.send_file_to_students(
            file_path=self.test_file,
            students=[student],
            is_update=True,
            target_version="1.0.8",
        )
        info = distributor.get_transfer_info(transfer_id)
        assert info is not None
        assert info["is_update"] is True


# --------------------------------------------------------------------
# autostart 模块导入测试（Linux 下函数应返回 False 但不报错）
# --------------------------------------------------------------------
class TestAutostart:
    def test_module_importable(self):
        from student.core import autostart
        assert hasattr(autostart, "enable_autostart")
        assert hasattr(autostart, "is_autostart_enabled")
        assert hasattr(autostart, "ensure_autostart")

    def test_get_exe_path_returns_string(self):
        from student.core import autostart
        path = autostart.get_exe_path()
        assert isinstance(path, str)
        assert len(path) > 0

    def test_is_autostart_enabled_on_non_windows(self):
        from student.core import autostart
        if sys.platform != "win32":
            assert autostart.is_autostart_enabled() is False

    def test_enable_autostart_on_non_windows(self):
        from student.core import autostart
        if sys.platform != "win32":
            assert autostart.enable_autostart() is False
