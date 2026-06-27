import os
import struct
import threading
import uuid
import base64
from typing import Callable, Dict, List, Optional
from common.protocol import MessageType, build_message
from common.file_transfer import CHUNK_SIZE, FILE_HEADER_FORMAT, FILE_HEADER_SIZE
from common.logger import get_logger

logger = get_logger("file_distributor")


class FileDistributor:
    def __init__(self):
        self._active_transfers: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self.on_progress: Optional[Callable] = None
        self.on_complete: Optional[Callable] = None

    def send_file_to_students(self, file_path: str, students: List,
                              progress_callback: Optional[Callable] = None,
                              is_update: bool = False,
                              target_version: str = "") -> str:
        transfer_id = str(uuid.uuid4())
        file_size = os.path.getsize(file_path)
        file_name = os.path.basename(file_path)
        total_chunks = (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE

        import hashlib
        md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                md5.update(chunk)
        md5_hex = md5.hexdigest()

        thread = threading.Thread(
            target=self._send_file_worker,
            args=(transfer_id, file_path, file_name, file_size,
                  total_chunks, md5_hex, students, progress_callback,
                  is_update, target_version),
            daemon=True
        )
        thread.start()

        with self._lock:
            self._active_transfers[transfer_id] = {
                "file_path": file_path,
                "file_name": file_name,
                "file_size": file_size,
                "students": len(students),
                "thread": thread,
                "is_update": is_update,
            }
        return transfer_id

    def _send_file_worker(self, transfer_id: str, file_path: str, file_name: str,
                          file_size: int, total_chunks: int, md5_hex: str,
                          students: List, progress_callback: Optional[Callable],
                          is_update: bool = False, target_version: str = ""):
        try:
            start_params = {
                "transfer_id": transfer_id,
                "file_name": file_name,
                "file_size": file_size,
                "total_chunks": total_chunks,
                "md5": md5_hex,
            }
            if is_update:
                start_params["is_update"] = True
                start_params["target_version"] = target_version
            start_msg = build_message(MessageType.FILE_SEND_START, start_params)
            for student in students:
                if student.conn and student.conn.is_alive():
                    student.conn.send_message(start_msg)

            sent_bytes = 0
            with open(file_path, "rb") as f:
                for chunk_index in range(total_chunks):
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    header = struct.pack(FILE_HEADER_FORMAT, 1, chunk_index, len(chunk))
                    chunk_data = header + chunk
                    chunk_b64 = base64.b64encode(chunk_data).decode('ascii')

                    data_msg = build_message(MessageType.FILE_SEND_DATA, {
                        "transfer_id": transfer_id,
                        "chunk_index": chunk_index,
                        "data": chunk_b64,
                    })

                    for student in students:
                        if student.conn and student.conn.is_alive():
                            try:
                                student.conn.send_message(data_msg)
                            except Exception:
                                pass

                    sent_bytes += len(chunk)
                    if progress_callback:
                        progress_callback(transfer_id, sent_bytes, file_size)
                    if self.on_progress:
                        self.on_progress(transfer_id, sent_bytes, file_size)

            end_msg = build_message(MessageType.FILE_SEND_END, {
                "transfer_id": transfer_id,
                "total_chunks": total_chunks,
                "md5": md5_hex,
            })
            for student in students:
                if student.conn and student.conn.is_alive():
                    student.conn.send_message(end_msg)

            logger.info(f"File distribution complete: {file_name} to {len(students)} students")
            if self.on_complete:
                self.on_complete(transfer_id, True, "")

        except Exception as e:
            logger.error(f"File distribution error: {e}")
            if self.on_complete:
                self.on_complete(transfer_id, False, str(e))
        finally:
            with self._lock:
                if transfer_id in self._active_transfers:
                    del self._active_transfers[transfer_id]

    def get_transfer_info(self, transfer_id: str) -> Optional[dict]:
        with self._lock:
            return self._active_transfers.get(transfer_id)

    def get_active_transfers(self) -> Dict[str, dict]:
        with self._lock:
            return dict(self._active_transfers)
