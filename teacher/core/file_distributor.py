import os
import struct
import threading
import uuid
import base64
import time
import hashlib
import shutil
import tempfile
from typing import Callable, Dict, List, Optional
from common.protocol import MessageType, build_message
from common.file_transfer import CHUNK_SIZE, FILE_HEADER_FORMAT, FILE_HEADER_SIZE
from common.logger import get_logger

logger = get_logger("file_distributor")


class FileDistributor:
    def __init__(self, ack_timeout: float = 15.0):
        if ack_timeout <= 0:
            raise ValueError("ack_timeout must be positive")
        self.ack_timeout = float(ack_timeout)
        self._active_transfers: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self.on_progress: Optional[Callable] = None
        self.on_complete: Optional[Callable] = None

    def send_file_to_students(self, file_path: str, students: List,
                              progress_callback: Optional[Callable] = None,
                              is_update: bool = False,
                              target_version: str = "",
                              update_manifest: str = "",
                              update_signature: str = "",
                              target_dir_type: str = "default",
                              target_dir_path: str = "") -> str:
        transfer_id = str(uuid.uuid4())
        source_path = file_path
        cleanup_dir = ""
        is_archive = os.path.isdir(file_path)
        archive_root = os.path.basename(os.path.normpath(file_path)) if is_archive else ""
        if is_archive:
            cleanup_dir = tempfile.mkdtemp(prefix="lan-classroom-folder-")
            archive_base = os.path.join(cleanup_dir, archive_root)
            source_path = shutil.make_archive(
                archive_base,
                "zip",
                root_dir=os.path.dirname(os.path.abspath(file_path)),
                base_dir=os.path.basename(os.path.normpath(file_path)),
            )
        file_size = os.path.getsize(source_path)
        file_name = os.path.basename(source_path)
        total_chunks = (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE

        md5 = hashlib.md5()
        sha256 = hashlib.sha256()
        with open(source_path, "rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                md5.update(chunk)
                sha256.update(chunk)
        md5_hex = md5.hexdigest()
        sha256_hex = sha256.hexdigest()

        thread = threading.Thread(
            target=self._send_file_worker,
            args=(transfer_id, source_path, file_name, file_size,
                  total_chunks, md5_hex, sha256_hex, students, progress_callback,
                  is_update, target_version, target_dir_type, target_dir_path,
                  is_archive, archive_root, cleanup_dir,
                  update_manifest, update_signature),
            daemon=True
        )

        with self._lock:
            self._active_transfers[transfer_id] = {
                "file_path": source_path,
                "file_name": file_name,
                "file_size": file_size,
                "students": len(students),
                "thread": thread,
                "is_update": is_update,
                "target_dir_type": target_dir_type,
                "target_dir_path": target_dir_path,
                "sent_bytes": 0,
                "started_at": 0.0,
                "resume_chunks": {},
                "resume_event": threading.Event(),
                "expected_ids": set(),
                "end_acks": {},
                "end_event": threading.Event(),
            }
        thread.start()
        return transfer_id

    def _send_file_worker(self, transfer_id: str, file_path: str, file_name: str,
                          file_size: int, total_chunks: int, md5_hex: str,
                          sha256_hex: str,
                          students: List, progress_callback: Optional[Callable],
                          is_update: bool = False, target_version: str = "",
                          target_dir_type: str = "default", target_dir_path: str = "",
                          is_archive: bool = False, archive_root: str = "",
                          cleanup_dir: str = "", update_manifest: str = "",
                          update_signature: str = ""):
        try:
            with self._lock:
                if transfer_id in self._active_transfers:
                    self._active_transfers[transfer_id]["started_at"] = time.time()
            start_params = {
                "transfer_id": transfer_id,
                "file_name": file_name,
                "file_size": file_size,
                "total_chunks": total_chunks,
                "md5": md5_hex,
                "sha256": sha256_hex,
                "is_archive": is_archive,
                "archive_root": archive_root,
            }
            if is_update:
                start_params["is_update"] = True
                start_params["target_version"] = target_version
                start_params["update_manifest"] = update_manifest
                start_params["update_signature"] = update_signature
            else:
                start_params["target_dir_type"] = target_dir_type
                start_params["target_dir_path"] = target_dir_path
            start_msg = build_message(MessageType.FILE_SEND_START, start_params)
            expected_ids = {
                str(getattr(student, "student_id", ""))
                for student in students
                if getattr(student, "student_id", "")
                and student.conn
                and student.conn.is_alive()
            }
            with self._lock:
                transfer = self._active_transfers.get(transfer_id)
                if transfer is not None:
                    transfer["expected_ids"] = expected_ids
            for student in students:
                if student.conn and student.conn.is_alive():
                    student.conn.send_message(start_msg)

            with self._lock:
                transfer = self._active_transfers.get(transfer_id)
                resume_event = transfer.get("resume_event") if transfer else None
            if resume_event and expected_ids:
                resume_event.wait(0.8)

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
                            with self._lock:
                                transfer = self._active_transfers.get(transfer_id, {})
                                resume_from = int(
                                    transfer.get("resume_chunks", {}).get(
                                        str(getattr(student, "student_id", "")), 0
                                    )
                                )
                            if chunk_index < resume_from:
                                continue
                            try:
                                student.conn.send_message(data_msg)
                            except Exception:
                                pass

                    sent_bytes += len(chunk)
                    if progress_callback:
                        progress_callback(transfer_id, sent_bytes, file_size)
                    if self.on_progress:
                        self.on_progress(transfer_id, sent_bytes, file_size)
                    with self._lock:
                        if transfer_id in self._active_transfers:
                            self._active_transfers[transfer_id]["sent_bytes"] = sent_bytes

            end_msg = build_message(MessageType.FILE_SEND_END, {
                "transfer_id": transfer_id,
                "total_chunks": total_chunks,
                "md5": md5_hex,
                "sha256": sha256_hex,
            })
            for student in students:
                if student.conn and student.conn.is_alive():
                    student.conn.send_message(end_msg)

            logger.info(f"File distribution complete: {file_name} to {len(students)} students")
            success = True
            error = ""
            if expected_ids and not is_update:
                with self._lock:
                    transfer = self._active_transfers.get(transfer_id)
                    end_event = transfer.get("end_event") if transfer else None
                if end_event:
                    end_event.wait(self.ack_timeout)
                with self._lock:
                    transfer = self._active_transfers.get(transfer_id, {})
                    end_acks = dict(transfer.get("end_acks", {}))
                failed_ids = sorted(
                    student_id
                    for student_id, result in end_acks.items()
                    if not result.get("success", False)
                )
                missing_ids = sorted(expected_ids - set(end_acks))
                success = not failed_ids and not missing_ids
                details = []
                if failed_ids:
                    details.append("校验失败：{}".format(", ".join(failed_ids)))
                if missing_ids:
                    details.append("未确认：{}".format(", ".join(missing_ids)))
                error = "；".join(details)
            if self.on_complete:
                self.on_complete(transfer_id, success, error)

        except Exception as e:
            logger.error(f"File distribution error: {e}")
            with self._lock:
                if transfer_id in self._active_transfers:
                    self._active_transfers[transfer_id]["error"] = str(e)
            if self.on_complete:
                self.on_complete(transfer_id, False, str(e))
        finally:
            with self._lock:
                if transfer_id in self._active_transfers:
                    del self._active_transfers[transfer_id]
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)

    def get_transfer_info(self, transfer_id: str) -> Optional[dict]:
        with self._lock:
            return self._active_transfers.get(transfer_id)

    def record_ack(self, params: dict) -> None:
        transfer_id = str(params.get("transfer_id") or "")
        student_id = str(params.get("student_id") or "")
        if not transfer_id or not student_id:
            return
        with self._lock:
            transfer = self._active_transfers.get(transfer_id)
            if not transfer:
                return
            stage = str(params.get("stage") or "end")
            if stage == "start":
                transfer["resume_chunks"][student_id] = max(
                    0, int(params.get("resume_chunk", 0))
                )
                if transfer.get("expected_ids", set()).issubset(
                    set(transfer["resume_chunks"])
                ):
                    transfer["resume_event"].set()
                return
            if stage == "end":
                transfer["end_acks"][student_id] = {
                    "success": bool(params.get("success", False)),
                    "result": str(params.get("result") or ""),
                }
                if transfer.get("expected_ids", set()).issubset(
                    set(transfer["end_acks"])
                ):
                    transfer["end_event"].set()

    def get_active_transfers(self) -> Dict[str, dict]:
        with self._lock:
            return dict(self._active_transfers)
