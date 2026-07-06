import pytest
import os
import tempfile
import hashlib
import time
from common.file_transfer import (
    FileTransferSender,
    FileTransferReceiver,
    CHUNK_SIZE,
    TARGET_DIR_CUSTOM,
)


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def test_file(temp_dir):
    file_path = os.path.join(temp_dir, "test.txt")
    content = b"Hello, World! " * 100
    with open(file_path, "wb") as f:
        f.write(content)
    return file_path, content


@pytest.fixture
def large_test_file(temp_dir):
    file_path = os.path.join(temp_dir, "large.bin")
    size = 2 * 1024 * 1024
    content = os.urandom(size)
    with open(file_path, "wb") as f:
        f.write(content)
    return file_path, content


class TestFileTransferSender:
    def test_compute_md5(self, test_file):
        file_path, content = test_file
        sender = FileTransferSender()
        md5 = sender.compute_md5(file_path)
        expected = hashlib.md5(content).hexdigest()
        assert md5 == expected

    def test_prepare_file(self, test_file):
        file_path, content = test_file
        sender = FileTransferSender()
        info = sender.prepare_file(file_path, "trans-001")
        assert info["transfer_id"] == "trans-001"
        assert info["file_name"] == "test.txt"
        assert info["file_size"] == len(content)
        assert info["total_chunks"] == (len(content) + CHUNK_SIZE - 1) // CHUNK_SIZE


class TestFileTransferReceiver:
    def test_full_transfer_small_file(self, temp_dir, test_file):
        file_path, content = test_file
        sender = FileTransferSender()
        save_dir = os.path.join(temp_dir, "received")
        receiver = FileTransferReceiver(save_dir)

        transfer_id = "test-trans-001"
        file_size = len(content)
        file_name = "test.txt"
        total_chunks = (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE
        md5 = sender.compute_md5(file_path)

        start_params = {
            "transfer_id": transfer_id,
            "file_name": file_name,
            "file_size": file_size,
            "total_chunks": total_chunks,
            "md5": md5,
        }
        receiver.handle_start(start_params)

        import struct
        from common.file_transfer import FILE_HEADER_FORMAT
        with open(file_path, "rb") as f:
            for chunk_index in range(total_chunks):
                chunk = f.read(CHUNK_SIZE)
                header = struct.pack(FILE_HEADER_FORMAT, 1, chunk_index, len(chunk))
                data = header + chunk
                receiver.handle_data({
                    "transfer_id": transfer_id,
                    "chunk_index": chunk_index,
                    "data": data,
                })

        success, result = receiver.handle_end({
            "transfer_id": transfer_id,
            "total_chunks": total_chunks,
            "md5": md5,
        })
        assert success is True
        assert os.path.exists(result)
        with open(result, "rb") as f:
            received = f.read()
        assert received == content

    def test_full_transfer_large_file(self, temp_dir, large_test_file):
        file_path, content = large_test_file
        sender = FileTransferSender()
        save_dir = os.path.join(temp_dir, "received")
        receiver = FileTransferReceiver(save_dir)

        transfer_id = "test-large-001"
        file_size = len(content)
        file_name = "large.bin"
        total_chunks = (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE
        md5 = sender.compute_md5(file_path)

        receiver.handle_start({
            "transfer_id": transfer_id,
            "file_name": file_name,
            "file_size": file_size,
            "total_chunks": total_chunks,
            "md5": md5,
        })

        import struct
        from common.file_transfer import FILE_HEADER_FORMAT
        with open(file_path, "rb") as f:
            for chunk_index in range(total_chunks):
                chunk = f.read(CHUNK_SIZE)
                header = struct.pack(FILE_HEADER_FORMAT, 1, chunk_index, len(chunk))
                data = header + chunk
                receiver.handle_data({
                    "transfer_id": transfer_id,
                    "chunk_index": chunk_index,
                    "data": data,
                })

        success, result = receiver.handle_end({
            "transfer_id": transfer_id,
            "total_chunks": total_chunks,
            "md5": md5,
        })
        assert success is True
        assert os.path.exists(result)
        with open(result, "rb") as f:
            received = f.read()
        assert received == content

    def test_transfer_cancel(self, temp_dir, test_file):
        file_path, content = test_file
        save_dir = os.path.join(temp_dir, "received")
        receiver = FileTransferReceiver(save_dir)

        transfer_id = "cancel-test"
        receiver.handle_start({
            "transfer_id": transfer_id,
            "file_name": "test.txt",
            "file_size": len(content),
            "total_chunks": 1,
            "md5": "",
        })
        receiver.cancel_transfer(transfer_id)
        assert transfer_id not in receiver._active_transfers

    def test_empty_file(self, temp_dir):
        save_dir = os.path.join(temp_dir, "received")
        receiver = FileTransferReceiver(save_dir)
        sender = FileTransferSender()

        file_path = os.path.join(temp_dir, "empty.txt")
        with open(file_path, "wb") as f:
            pass

        transfer_id = "empty-test"
        md5 = sender.compute_md5(file_path)
        receiver.handle_start({
            "transfer_id": transfer_id,
            "file_name": "empty.txt",
            "file_size": 0,
            "total_chunks": 0,
            "md5": md5,
        })
        success, result = receiver.handle_end({
            "transfer_id": transfer_id,
            "total_chunks": 0,
            "md5": md5,
        })
        assert success is True

    def test_target_custom_dir(self, temp_dir, test_file):
        file_path, content = test_file
        sender = FileTransferSender()
        save_dir = os.path.join(temp_dir, "received")
        target_dir = os.path.join(temp_dir, "student-target")
        receiver = FileTransferReceiver(save_dir)

        transfer_id = "custom-dir-test"
        md5 = sender.compute_md5(file_path)
        receiver.handle_start({
            "transfer_id": transfer_id,
            "file_name": "custom.txt",
            "file_size": len(content),
            "total_chunks": 1,
            "md5": md5,
            "target_dir_type": TARGET_DIR_CUSTOM,
            "target_dir_path": target_dir,
        })

        import struct
        from common.file_transfer import FILE_HEADER_FORMAT
        header = struct.pack(FILE_HEADER_FORMAT, 1, 0, len(content))
        receiver.handle_data({
            "transfer_id": transfer_id,
            "chunk_index": 0,
            "data": header + content,
        })

        success, result = receiver.handle_end({
            "transfer_id": transfer_id,
            "total_chunks": 1,
            "md5": md5,
        })
        assert success is True
        assert os.path.dirname(result) == target_dir
        with open(result, "rb") as f:
            assert f.read() == content

    def test_chinese_filename(self, temp_dir, test_file):
        file_path, content = test_file
        sender = FileTransferSender()
        save_dir = os.path.join(temp_dir, "received")
        receiver = FileTransferReceiver(save_dir)

        transfer_id = "cn-test"
        file_name = "测试文件.txt"
        file_size = len(content)
        total_chunks = 1
        md5 = sender.compute_md5(file_path)

        receiver.handle_start({
            "transfer_id": transfer_id,
            "file_name": file_name,
            "file_size": file_size,
            "total_chunks": total_chunks,
            "md5": md5,
        })

        import struct
        from common.file_transfer import FILE_HEADER_FORMAT
        header = struct.pack(FILE_HEADER_FORMAT, 1, 0, file_size)
        data = header + content
        receiver.handle_data({
            "transfer_id": transfer_id,
            "chunk_index": 0,
            "data": data,
        })

        success, result = receiver.handle_end({
            "transfer_id": transfer_id,
            "total_chunks": total_chunks,
            "md5": md5,
        })
        assert success is True
        assert os.path.basename(result) == file_name


class TestFileDistributor:
    def test_start_message_contains_target_dir(self, temp_dir, test_file):
        from teacher.core.file_distributor import FileDistributor

        file_path, _ = test_file
        sent_messages = []

        class FakeConn:
            def is_alive(self):
                return True

            def send_message(self, msg):
                sent_messages.append(msg)
                return True

        class FakeStudent:
            conn = FakeConn()

        distributor = FileDistributor()
        target_dir = os.path.join(temp_dir, "target")
        transfer_id = distributor.send_file_to_students(
            file_path=file_path,
            students=[FakeStudent()],
            target_dir_type=TARGET_DIR_CUSTOM,
            target_dir_path=target_dir,
        )

        deadline = time.time() + 3
        while distributor.get_transfer_info(transfer_id) and time.time() < deadline:
            time.sleep(0.01)

        start_messages = [msg for msg in sent_messages if msg.get("type") == "file_send_start"]
        assert start_messages
        params = start_messages[0]["params"]
        assert params["target_dir_type"] == TARGET_DIR_CUSTOM
        assert params["target_dir_path"] == target_dir
