"""自动更新功能测试"""
import os
import sys
import base64
import struct
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
            "total_chunks": 1,
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
            "total_chunks": 1,
            "md5": "",
            "target_version": "1.0.8",
        })
        client.cancel()
        assert client._active_transfer is None

    def test_update_chunks_reject_mismatched_header_and_ignore_duplicates(self):
        from common.file_transfer import FILE_HEADER_FORMAT

        client = self._create_client()
        client.handle_start({
            "transfer_id": "t1",
            "file_name": "update.zip",
            "file_size": 4,
            "total_chunks": 1,
            "target_version": "1.0.8",
        })
        mismatched = struct.pack(FILE_HEADER_FORMAT, 1, 1, 4) + b"data"
        assert client.handle_data({
            "transfer_id": "t1",
            "chunk_index": 0,
            "data": base64.b64encode(mismatched).decode("ascii"),
        }) is False

        chunk = struct.pack(FILE_HEADER_FORMAT, 1, 0, 4) + b"data"
        params = {
            "transfer_id": "t1",
            "chunk_index": 0,
            "data": base64.b64encode(chunk).decode("ascii"),
        }
        assert client.handle_data(params) is True
        assert client.handle_data(params) is True
        assert client._active_transfer["received_chunks"] == 1
        client.cancel()

    def test_incomplete_update_is_rejected_before_signature_verification(self):
        from common.file_transfer import CHUNK_SIZE

        client = self._create_client()
        client.handle_start({
            "transfer_id": "t1",
            "file_name": "update.zip",
            "file_size": CHUNK_SIZE + 1,
            "total_chunks": 2,
            "target_version": "1.0.8",
        })

        with patch("student.core.update_client.verify_manifest") as verify:
            assert client.handle_end({"transfer_id": "t1"}) is False

        verify.assert_not_called()

    def test_update_start_rejects_inconsistent_chunk_count(self):
        client = self._create_client()

        with pytest.raises(ValueError, match="metadata"):
            client.handle_start({
                "transfer_id": "t1",
                "file_name": "update.zip",
                "file_size": 4,
                "total_chunks": 2,
                "target_version": "1.0.8",
            })

    def test_service_install_layout_uses_parent_of_service_directory(self):
        from student.core.update_client import UpdateClient

        client = UpdateClient(self.send_func, service_mode=True)
        executable = os.path.join(
            "C:\\Program Files", "LanClassroom", "Student", "Service",
            UpdateClient.SERVICE_EXE,
        )
        with patch.object(sys, "frozen", True, create=True), patch.object(
            sys, "executable", executable
        ):
            layout = client._resolve_install_layout()

        assert layout["service_layout"] is True
        assert layout["target_dir"] == os.path.join(
            "C:\\Program Files", "LanClassroom", "Student"
        )

    def test_service_updater_uses_scm_directory_swap_and_rollback(self):
        from student.core.update_client import UpdateClient

        script = UpdateClient._build_updater_script(
            zip_path=r"C:\Temp\student.zip",
            staging_dir=r"C:\Program Files\LanClassroom\Student\.update-staging",
            target_dir=r"C:\Program Files\LanClassroom\Student",
            target_version="2.1.0",
            restart_cmd="",
            service_layout=True,
            current_pid=42,
        )

        assert 'sc stop "LanClassroomStudentV2"' in script
        assert 'taskkill /IM "LanClassroom-Student-Agent.exe" /F' in script
        assert 'LanClassroom-Student-Service.exe" --wait 20 start' in script
        assert ".Service.rollback" in script
        assert ".Agent.rollback" in script
        assert "Expand-Archive" not in script

    def test_update_rejects_version_with_batch_metacharacters(self, tmp_path):
        client = self._create_client()
        package = tmp_path / "student.zip"
        with zipfile.ZipFile(str(package), "w") as archive:
            archive.writestr("student.exe", b"binary")

        assert client._apply_update(str(package), "2.1.0&whoami") is False


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
        from Crypto.PublicKey import ECC
        from common.update_security import build_manifest, sign_manifest

        key = ECC.generate(curve="Ed25519")
        manifest = build_manifest(self.update_zip, "1.0.8", 1)
        with open(self.update_zip + ".manifest.json", "wb") as handle:
            handle.write(manifest)
        with open(self.update_zip + ".sig", "w", encoding="ascii") as handle:
            handle.write(sign_manifest(manifest, key.export_key(format="PEM").encode("ascii")))
        self.key_patcher = patch(
            "teacher.core.update_server.load_public_key",
            return_value=key.public_key().export_key(format="PEM").encode("ascii"),
        )
        self.key_patcher.start()

        self.student_manager = MagicMock()
        self.file_distributor = MagicMock()
        self.file_distributor.send_file_to_students.return_value = "transfer_id_1"

    def teardown_method(self):
        self.key_patcher.stop()
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
