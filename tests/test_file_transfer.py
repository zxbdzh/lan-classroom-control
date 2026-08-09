import pytest
import os
import tempfile
import hashlib
import time
from common.file_transfer import (
    FileTransferSender,
    FileTransferReceiver,
    CHUNK_SIZE,
    FILE_HEADER_FORMAT,
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
    def test_resume_uses_hash_named_partial_file(self, temp_dir):
        content = os.urandom(CHUNK_SIZE * 2 + 123)
        source = os.path.join(temp_dir, "resume.bin")
        with open(source, "wb") as handle:
            handle.write(content)
        sha256 = hashlib.sha256(content).hexdigest()
        params = {
            "transfer_id": "first-attempt",
            "file_name": "resume.bin",
            "file_size": len(content),
            "total_chunks": 3,
            "sha256": sha256,
        }
        save_dir = os.path.join(temp_dir, "received")
        first = FileTransferReceiver(save_dir)
        assert first.handle_start(params)["resume_chunk"] == 0
        import struct
        first.handle_data({
            "transfer_id": "first-attempt",
            "chunk_index": 0,
            "data": struct.pack(FILE_HEADER_FORMAT, 1, 0, CHUNK_SIZE) + content[:CHUNK_SIZE],
        })
        first._active_transfers["first-attempt"]["file"].close()

        second = FileTransferReceiver(save_dir)
        params["transfer_id"] = "second-attempt"
        assert second.handle_start(params)["resume_chunk"] == 1
        for chunk_index in (1, 2):
            chunk = content[chunk_index * CHUNK_SIZE:(chunk_index + 1) * CHUNK_SIZE]
            second.handle_data({
                "transfer_id": "second-attempt",
                "chunk_index": chunk_index,
                "data": struct.pack(FILE_HEADER_FORMAT, 1, chunk_index, len(chunk)) + chunk,
            })
        success, result = second.handle_end({"transfer_id": "second-attempt"})
        assert success is True
        with open(result, "rb") as handle:
            assert handle.read() == content

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

    def test_folder_distribution_preserves_the_source_root(self, temp_dir):
        from teacher.core.file_distributor import FileDistributor

        source = os.path.join(temp_dir, "课程资料")
        nested = os.path.join(source, "第一章")
        os.makedirs(nested)
        with open(os.path.join(nested, "讲义.txt"), "wb") as handle:
            handle.write(b"lesson")
        sent_messages = []

        class FakeConn:
            def is_alive(self):
                return True

            def send_message(self, message):
                sent_messages.append(message)
                return True

        class FakeStudent:
            conn = FakeConn()

        distributor = FileDistributor(ack_timeout=0.05)
        transfer_id = distributor.send_file_to_students(source, [FakeStudent()])
        deadline = time.time() + 3
        while distributor.get_transfer_info(transfer_id) and time.time() < deadline:
            time.sleep(0.01)

        receiver = FileTransferReceiver(os.path.join(temp_dir, "received"))
        result = None
        for message in sent_messages:
            params = message["params"]
            if message["type"] == "file_send_start":
                receiver.handle_start(params)
            elif message["type"] == "file_send_data":
                assert receiver.handle_data(params)
            elif message["type"] == "file_send_end":
                success, result = receiver.handle_end(params)
                assert success

        assert os.path.basename(result) == "课程资料"
        assert os.path.isfile(os.path.join(result, "第一章", "讲义.txt"))

    def test_completion_waits_for_student_integrity_ack(self, test_file):
        from teacher.core.file_distributor import FileDistributor

        file_path, _ = test_file
        sent_messages = []
        completed = []

        class FakeConn:
            def is_alive(self):
                return True

            def send_message(self, message):
                sent_messages.append(message)
                return True

        class FakeStudent:
            student_id = "pc-01"
            conn = FakeConn()

        distributor = FileDistributor(ack_timeout=0.5)
        distributor.on_complete = lambda *args: completed.append(args)
        transfer_id = distributor.send_file_to_students(file_path, [FakeStudent()])
        deadline = time.time() + 2
        while not any(item["type"] == "file_send_start" for item in sent_messages):
            assert time.time() < deadline
            time.sleep(0.01)
        distributor.record_ack({
            "transfer_id": transfer_id,
            "student_id": "pc-01",
            "stage": "start",
            "resume_chunk": 0,
        })
        while not any(item["type"] == "file_send_end" for item in sent_messages):
            assert time.time() < deadline
            time.sleep(0.01)
        assert completed == []
        distributor.record_ack({
            "transfer_id": transfer_id,
            "student_id": "pc-01",
            "stage": "end",
            "success": True,
            "result": "ok",
        })
        while not completed:
            assert time.time() < deadline
            time.sleep(0.01)
        assert completed == [(transfer_id, True, "")]
