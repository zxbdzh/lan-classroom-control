import os
import struct
import hashlib
import threading
import time
from typing import Callable, Dict, Optional, Tuple
from common.logger import get_logger

logger = get_logger("file_transfer")

CHUNK_SIZE = 64 * 1024
FILE_HEADER_FORMAT = "!IIQ"
FILE_HEADER_SIZE = struct.calcsize(FILE_HEADER_FORMAT)


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

    def start_transfer(self, transfer_id: str, file_path: str,
                       send_func: Callable, progress_callback: Optional[Callable] = None):
        file_size = os.path.getsize(file_path)
        total_chunks = (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE
        file_name = os.path.basename(file_path)
        md5 = self.compute_md5(file_path)

        start_msg = {
            "transfer_id": transfer_id,
            "file_name": file_name,
            "file_size": file_size,
            "total_chunks": total_chunks,
            "md5": md5,
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
        }
        send_func("end", end_msg)
        logger.info(f"File transfer complete: {file_name}, size: {file_size}")


class FileTransferReceiver:
    def __init__(self, save_dir: str):
        self.save_dir = save_dir
        self._active_transfers: Dict[str, dict] = {}
        self._lock = threading.Lock()
        os.makedirs(save_dir, exist_ok=True)

    def handle_start(self, params: dict):
        transfer_id = params["transfer_id"]
        file_name = params["file_name"]
        file_size = params["file_size"]
        total_chunks = params["total_chunks"]
        md5 = params.get("md5", "")

        save_path = os.path.join(self.save_dir, file_name)
        with self._lock:
            self._active_transfers[transfer_id] = {
                "file_name": file_name,
                "save_path": save_path,
                "file_size": file_size,
                "total_chunks": total_chunks,
                "received_chunks": 0,
                "md5": md5,
                "start_time": time.time(),
                "file": open(save_path, "wb"),
            }
        logger.info(f"Receiving file: {file_name}, size: {file_size}")

    def handle_data(self, params: dict) -> bool:
        transfer_id = params["transfer_id"]
        chunk_index = params["chunk_index"]
        data = params["data"]

        with self._lock:
            transfer = self._active_transfers.get(transfer_id)
            if not transfer:
                return False

        if len(data) < FILE_HEADER_SIZE:
            return False

        _, idx, chunk_len = struct.unpack(FILE_HEADER_FORMAT, data[:FILE_HEADER_SIZE])
        chunk_data = data[FILE_HEADER_SIZE:FILE_HEADER_SIZE + chunk_len]

        file_obj = transfer["file"]
        offset = chunk_index * CHUNK_SIZE
        file_obj.seek(offset)
        file_obj.write(chunk_data)
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
            expected_md5 = transfer.get("md5", "")
            file_obj.close()

            del self._active_transfers[transfer_id]

        if expected_md5:
            actual_md5 = self._compute_file_md5(save_path)
            if actual_md5 != expected_md5:
                logger.error(f"MD5 mismatch: expected {expected_md5}, got {actual_md5}")
                return False, "MD5 mismatch"

        elapsed = time.time() - transfer["start_time"]
        logger.info(f"File received: {transfer['file_name']}, time: {elapsed:.2f}s")
        return True, save_path

    def _compute_file_md5(self, file_path: str) -> str:
        md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                md5.update(chunk)
        return md5.hexdigest()

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
