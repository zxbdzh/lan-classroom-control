import os
import struct
import hashlib
import threading
import time
import base64
import zipfile
import tempfile
import shutil
from typing import Callable, Dict, Optional, Tuple
from common.logger import get_logger

logger = get_logger("file_transfer")

CHUNK_SIZE = 64 * 1024
FILE_HEADER_FORMAT = "!IIQ"
FILE_HEADER_SIZE = struct.calcsize(FILE_HEADER_FORMAT)

TARGET_DIR_DEFAULT = "default"
TARGET_DIR_DESKTOP = "desktop"
TARGET_DIR_DOWNLOADS = "downloads"
TARGET_DIR_DOCUMENTS = "documents"
TARGET_DIR_C_DOWNLOADS = "c_downloads"
TARGET_DIR_CUSTOM = "custom"


class FileTransferSender:
    def __init__(self):
        self._active_transfers: Dict[str, dict] = {}
        self._lock = threading.Lock()

    def prepare_file(self, file_path: str, transfer_id: str) -> dict:
        file_size = os.path.getsize(file_path)
        file_name = os.path.basename(file_path)
        info = {
            "transfer_id": transfer_id,
            "file_path": file_path,
            "file_name": file_name,
            "file_size": file_size,
            "total_chunks": (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE,
            "sent_chunks": 0,
            "md5": "",
        }
        return info

    def compute_md5(self, file_path: str) -> str:
        md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                md5.update(chunk)
        return md5.hexdigest()

    def compute_sha256(self, file_path: str) -> str:
        digest = hashlib.sha256()
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def start_transfer(self, transfer_id: str, file_path: str,
                       send_func: Callable, progress_callback: Optional[Callable] = None):
        file_size = os.path.getsize(file_path)
        total_chunks = (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE
        file_name = os.path.basename(file_path)
        md5 = self.compute_md5(file_path)
        sha256 = self.compute_sha256(file_path)

        start_msg = {
            "transfer_id": transfer_id,
            "file_name": file_name,
            "file_size": file_size,
            "total_chunks": total_chunks,
            "md5": md5,
            "sha256": sha256,
        }
        send_func("start", start_msg)

        sent_bytes = 0
        with open(file_path, "rb") as f:
            chunk_index = 0
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                header = struct.pack(FILE_HEADER_FORMAT, 1, chunk_index, len(chunk))
                data = header + chunk
                send_func("data", {"transfer_id": transfer_id, "chunk_index": chunk_index, "data": data})
                sent_bytes += len(chunk)
                chunk_index += 1
                if progress_callback:
                    progress_callback(sent_bytes, file_size)

        end_msg = {
            "transfer_id": transfer_id,
            "total_chunks": total_chunks,
            "md5": md5,
            "sha256": sha256,
        }
        send_func("end", end_msg)
        logger.info(f"File transfer complete: {file_name}, size: {file_size}")


class FileTransferReceiver:
    def __init__(self, save_dir: str):
        self.save_dir = save_dir
        self._active_transfers: Dict[str, dict] = {}
        self._lock = threading.RLock()
        os.makedirs(save_dir, exist_ok=True)

    def handle_start(self, params: dict) -> dict:
        with self._lock:
            return self._handle_start(params)

    def _handle_start(self, params: dict) -> dict:
        transfer_id = params["transfer_id"]
        file_name = os.path.basename(params["file_name"]) or transfer_id
        file_size = params["file_size"]
        total_chunks = params["total_chunks"]
        if (
            not isinstance(file_size, int) or file_size < 0
            or total_chunks != (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE
        ):
            raise ValueError("invalid transfer size or chunk count")
        md5 = params.get("md5", "")
        sha256 = params.get("sha256", "")
        target_dir = self._resolve_target_dir(
            params.get("target_dir_type", TARGET_DIR_DEFAULT),
            params.get("target_dir_path", "")
        )

        final_path = os.path.join(target_dir, file_name)
        reserved_paths = {
            value["final_path"] for key, value in self._active_transfers.items()
            if key != transfer_id
        }
        # 文件名冲突自动加序号 (1), (2)...
        if os.path.exists(final_path) or final_path in reserved_paths:
            base, ext = os.path.splitext(file_name)
            i = 1
            while (
                os.path.exists(os.path.join(target_dir, f"{base}({i}){ext}"))
                or os.path.join(target_dir, f"{base}({i}){ext}") in reserved_paths
            ):
                i += 1
            final_path = os.path.join(target_dir, f"{base}({i}){ext}")
            logger.info(f"File name conflict, renamed to: {os.path.basename(final_path)}")
        content_key = (sha256 or md5 or transfer_id).replace("/", "_").replace("\\", "_")
        save_path = os.path.join(target_dir, f".{file_name}.{content_key}.part")
        with self._lock:
            # 重试复用连续落盘的部分文件，避免两个句柄同时写同一文件。
            for previous_id, previous in list(self._active_transfers.items()):
                if previous_id == transfer_id or previous["save_path"] == save_path:
                    previous["file"].close()
                    del self._active_transfers[previous_id]
        resume_chunk = 0
        if os.path.isfile(save_path):
            partial_size = min(os.path.getsize(save_path), int(file_size))
            resume_chunk = partial_size // CHUNK_SIZE
        mode = "r+b" if os.path.exists(save_path) else "w+b"
        with self._lock:
            previous = self._active_transfers.pop(transfer_id, None)
            if previous:
                previous["file"].close()
            self._active_transfers[transfer_id] = {
                "file_name": file_name,
                "save_path": save_path,
                "final_path": final_path,
                "target_dir": target_dir,
                "file_size": file_size,
                "total_chunks": total_chunks,
                "received_chunks": resume_chunk,
                "received_indices": set(range(resume_chunk)),
                "md5": md5,
                "sha256": sha256,
                "is_archive": bool(params.get("is_archive", False)),
                "archive_root": os.path.basename(str(params.get("archive_root", ""))),
                "start_time": time.time(),
                "file": open(save_path, mode),
            }
        logger.info(f"Receiving file: {file_name}, size: {file_size}, save to: {save_path}")
        return {"transfer_id": transfer_id, "resume_chunk": resume_chunk}

    def _resolve_target_dir(self, target_dir_type: str, target_dir_path: str = "") -> str:
        home_dir = os.path.expanduser("~")
        target_dir = self.save_dir

        if target_dir_type == TARGET_DIR_DESKTOP:
            target_dir = os.path.join(home_dir, "Desktop")
        elif target_dir_type == TARGET_DIR_DOWNLOADS:
            target_dir = os.path.join(home_dir, "Downloads")
        elif target_dir_type == TARGET_DIR_DOCUMENTS:
            target_dir = os.path.join(home_dir, "Documents")
        elif target_dir_type == TARGET_DIR_C_DOWNLOADS:
            if os.name == "nt":
                target_dir = os.path.join("C:\\", "LanClassroom", "Downloads")
        elif target_dir_type == TARGET_DIR_CUSTOM and target_dir_path:
            expanded = os.path.expandvars(os.path.expanduser(target_dir_path.strip()))
            if os.path.isabs(expanded):
                target_dir = expanded

        try:
            os.makedirs(target_dir, exist_ok=True)
            return target_dir
        except Exception as e:
            logger.warning(f"Create target dir failed: {target_dir}, fallback to {self.save_dir}, error: {e}")
            os.makedirs(self.save_dir, exist_ok=True)
            return self.save_dir

    def handle_data(self, params: dict) -> bool:
        transfer_id = params["transfer_id"]
        chunk_index = params["chunk_index"]
        data_param = params["data"]

        if isinstance(data_param, str):
            try:
                data = base64.b64decode(data_param)
            except Exception as e:
                logger.warning(f"Base64 decode error for chunk {chunk_index}: {e}")
                return False
        elif isinstance(data_param, bytes):
            data = data_param
        else:
            logger.warning(f"Invalid data type for chunk {chunk_index}: {type(data_param)}")
            return False

        if len(data) < FILE_HEADER_SIZE:
            return False

        _, idx, chunk_len = struct.unpack(FILE_HEADER_FORMAT, data[:FILE_HEADER_SIZE])
        if idx != chunk_index or not isinstance(chunk_index, int):
            return False
        chunk_data = data[FILE_HEADER_SIZE:FILE_HEADER_SIZE + chunk_len]
        if len(chunk_data) != chunk_len:
            return False

        with self._lock:
            transfer = self._active_transfers.get(transfer_id)
            if not transfer or not 0 <= chunk_index < transfer["total_chunks"]:
                return False
            expected_size = min(
                CHUNK_SIZE, transfer["file_size"] - chunk_index * CHUNK_SIZE
            )
            if chunk_len != expected_size or len(data) != FILE_HEADER_SIZE + chunk_len:
                return False
            if chunk_index in transfer["received_indices"]:
                return True
            # TCP 有序传输：拒绝跳块，文件长度才能作为可靠的续传位置。
            if chunk_index != transfer["received_chunks"]:
                return False
            file_obj = transfer["file"]
            file_obj.seek(chunk_index * CHUNK_SIZE)
            file_obj.write(chunk_data)
            file_obj.flush()
            transfer["received_indices"].add(chunk_index)
            transfer["received_chunks"] += 1
            return True

    def handle_end(self, params: dict) -> Tuple[bool, str]:
        transfer_id = params["transfer_id"]
        with self._lock:
            transfer = self._active_transfers.get(transfer_id)
            if not transfer:
                return False, "Transfer not found"

            file_obj = transfer["file"]
            save_path = transfer["save_path"]
            final_path = transfer["final_path"]
            expected_md5 = transfer.get("md5", "")
            expected_sha256 = transfer.get("sha256", "")
            file_obj.close()

            del self._active_transfers[transfer_id]

        if transfer["received_chunks"] < transfer["total_chunks"]:
            return False, "Transfer is incomplete"
        with open(save_path, "r+b") as handle:
            handle.truncate(int(transfer["file_size"]))

        if expected_sha256:
            actual_sha256 = self._compute_file_hash(save_path, "sha256")
            if actual_sha256 != expected_sha256:
                logger.error(
                    f"SHA-256 mismatch: expected {expected_sha256}, got {actual_sha256}"
                )
                os.remove(save_path)
                return False, "SHA-256 mismatch"
        elif expected_md5:
            actual_md5 = self._compute_file_md5(save_path)
            if actual_md5 != expected_md5:
                logger.error(f"MD5 mismatch: expected {expected_md5}, got {actual_md5}")
                os.remove(save_path)
                return False, "MD5 mismatch"

        os.replace(save_path, final_path)
        result_path = final_path
        if transfer.get("is_archive"):
            try:
                result_path = self._extract_archive_safely(
                    final_path, transfer["target_dir"], transfer.get("archive_root", "")
                )
                os.remove(final_path)
            except Exception as exc:
                logger.error(f"Archive extraction failed: {exc}")
                return False, f"Archive extraction failed: {exc}"

        elapsed = time.time() - transfer["start_time"]
        logger.info(f"File received: {transfer['file_name']}, time: {elapsed:.2f}s")
        return True, result_path

    def _compute_file_md5(self, file_path: str) -> str:
        return self._compute_file_hash(file_path, "md5")

    def _compute_file_hash(self, file_path: str, algorithm: str) -> str:
        digest = hashlib.new(algorithm)
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _extract_archive_safely(
        archive_path: str, target_dir: str, archive_root: str = ""
    ) -> str:
        staging = tempfile.mkdtemp(prefix=".classroom-extract-", dir=target_dir)
        destination = os.path.abspath(staging)
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                for member in archive.infolist():
                    member_path = os.path.abspath(os.path.join(destination, member.filename))
                    if os.path.commonpath([destination, member_path]) != destination:
                        raise ValueError("archive contains an unsafe path")
                    if member.filename.startswith(("/", "\\")):
                        raise ValueError("archive contains an absolute path")
                archive.extractall(destination)
            root = os.path.basename(archive_root) if archive_root else ""
            source = os.path.join(staging, root) if root else staging
            name = root or os.path.splitext(os.path.basename(archive_path))[0]
            result = os.path.join(target_dir, name)
            index = 1
            while os.path.exists(result):
                result = os.path.join(target_dir, f"{name}({index})")
                index += 1
            # 校验和完整解压均完成后才公开目录，不覆盖已发过的课程文件夹。
            shutil.move(source, result)
            return result
        finally:
            if os.path.exists(staging):
                shutil.rmtree(staging, ignore_errors=True)

    def get_progress(self, transfer_id: str) -> float:
        with self._lock:
            transfer = self._active_transfers.get(transfer_id)
            if not transfer or transfer["total_chunks"] == 0:
                return 0.0
            return transfer["received_chunks"] / transfer["total_chunks"]

    def cancel_transfer(self, transfer_id: str):
        with self._lock:
            transfer = self._active_transfers.get(transfer_id)
            if transfer:
                try:
                    transfer["file"].close()
                except Exception:
                    pass
                if os.path.exists(transfer["save_path"]):
                    os.remove(transfer["save_path"])
                del self._active_transfers[transfer_id]

    def close(self) -> None:
        with self._lock:
            for transfer in self._active_transfers.values():
                transfer["file"].close()
            self._active_transfers.clear()
