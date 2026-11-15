import base64
import hashlib
import os
import shutil
import struct
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional

from common.file_transfer import CHUNK_SIZE, FILE_HEADER_FORMAT
from common.logger import get_logger
from common.protocol import MessageType, build_message

logger = get_logger("file_distributor")


class FileDistributor:
    def __init__(self, ack_timeout: float = 15.0):
        if ack_timeout <= 0:
            raise ValueError("ack_timeout must be positive")
        self.ack_timeout = float(ack_timeout)
        self._active_transfers: Dict[str, dict] = {}
        self._completed_transfers: Dict[str, dict] = {}
        self._lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="classroom-files")
        self._rate_lock = threading.Lock()
        self._next_chunk_at = 0.0
        self.on_progress: Optional[Callable] = None
        self.on_complete: Optional[Callable] = None

    def send_file_to_students(
        self, file_path: str, students: List, progress_callback: Optional[Callable] = None,
        is_update: bool = False, target_version: str = "", update_manifest: str = "",
        update_signature: str = "", target_dir_type: str = "default",
        target_dir_path: str = "",
    ) -> str:
        if not os.path.exists(file_path):
            raise FileNotFoundError(file_path)
        if not students:
            raise ValueError("没有可分发的目标设备")
        transfer_id = str(uuid.uuid4())
        transfer = {
            "file_path": file_path, "file_name": os.path.basename(file_path),
            "file_size": 0, "students": len(students), "sent_bytes": 0,
            "started_at": time.time(), "resume_chunks": {}, "end_acks": {},
            "devices": {}, "is_update": is_update,
            "target_dir_type": target_dir_type, "target_dir_path": target_dir_path,
        }
        with self._lock:
            self._active_transfers[transfer_id] = transfer
        worker = threading.Thread(
            target=self._prepare_and_send,
            args=(transfer_id, file_path, list(students), progress_callback, {
                "is_update": is_update, "target_version": target_version,
                "update_manifest": update_manifest, "update_signature": update_signature,
                "target_dir_type": target_dir_type, "target_dir_path": target_dir_path,
            }),
            name="classroom-file-prepare", daemon=True,
        )
        transfer["thread"] = worker
        worker.start()
        return transfer_id

    def _prepare_and_send(self, transfer_id, file_path, students, progress_callback, extra):
        cleanup_dir = ""
        success, error = False, ""
        try:
            source = file_path
            is_archive = os.path.isdir(source)
            root = os.path.basename(os.path.normpath(source)) if is_archive else ""
            if is_archive:
                cleanup_dir = tempfile.mkdtemp(prefix="lan-classroom-folder-")
                source = shutil.make_archive(
                    os.path.join(cleanup_dir, root), "zip",
                    root_dir=os.path.dirname(os.path.abspath(file_path)), base_dir=root,
                )
            md5, sha256 = hashlib.md5(), hashlib.sha256()
            with open(source, "rb") as handle:
                for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
                    md5.update(chunk)
                    sha256.update(chunk)
            size = os.path.getsize(source)
            params = {
                "transfer_id": transfer_id, "file_name": os.path.basename(source),
                "file_size": size, "total_chunks": (size + CHUNK_SIZE - 1) // CHUNK_SIZE,
                "md5": md5.hexdigest(), "sha256": sha256.hexdigest(),
                "is_archive": is_archive, "archive_root": root, **extra,
            }
            with self._lock:
                transfer = self._active_transfers[transfer_id]
                transfer["file_size"] = size * len(students)
                for index, student in enumerate(students):
                    key = str(getattr(student, "student_id", "") or f"legacy-{index}")
                    transfer["devices"][key] = {
                        "state": "queued", "result": "", "sent_bytes": 0,
                        "start_event": threading.Event(), "end_event": threading.Event(),
                    }
            futures = []
            for index, student in enumerate(students):
                key = str(getattr(student, "student_id", "") or f"legacy-{index}")
                futures.append(self._pool.submit(
                    self._send_one, student, key, source, params, progress_callback
                ))
            for future in futures:
                future.result()
            failures = [
                f"{key}：{value['result']}"
                for key, value in transfer["devices"].items()
                if value["state"] != "complete"
            ]
            success = not failures
            error = "；".join(failures)
        except Exception as exc:
            error = str(exc)
            logger.error(f"File distribution failed: {exc}")
        finally:
            with self._lock:
                transfer = self._active_transfers.pop(transfer_id)
                transfer.update(success=success, error=error)
                self._completed_transfers[transfer_id] = transfer
                while len(self._completed_transfers) > 100:
                    self._completed_transfers.pop(next(iter(self._completed_transfers)))
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)
            if self.on_complete:
                self.on_complete(transfer_id, success, error)

    def _send_one(self, student, key, source, params, progress_callback):
        transfer_id = params["transfer_id"]
        with self._lock:
            transfer = self._active_transfers[transfer_id]
            device = transfer["devices"][key]
        try:
            connection = student.conn
            if not connection or not connection.is_alive():
                raise RuntimeError("设备离线")

            def send(kind, payload):
                if not connection.is_alive() or not connection.send_message(
                    build_message(kind, payload)
                ):
                    raise RuntimeError("连接中断，可重试续传")

            device["state"] = "starting"
            send(MessageType.FILE_SEND_START, params)
            needs_ack = bool(getattr(student, "student_id", "")) and not params["is_update"]
            if needs_ack and not device["start_event"].wait(self.ack_timeout):
                raise RuntimeError("未收到接收准备回执")
            if device.get("start_error"):
                raise RuntimeError(device["start_error"])
            resume = int(transfer["resume_chunks"].get(key, 0))
            if not 0 <= resume <= params["total_chunks"]:
                raise RuntimeError("无效的续传位置")
            device["sent_bytes"] = min(resume * CHUNK_SIZE, params["file_size"])
            device["state"] = "sending"
            with open(source, "rb") as handle:
                handle.seek(resume * CHUNK_SIZE)
                for index in range(resume, params["total_chunks"]):
                    chunk = handle.read(CHUNK_SIZE)
                    packet = struct.pack(FILE_HEADER_FORMAT, 1, index, len(chunk)) + chunk
                    self._pace(len(chunk))
                    send(MessageType.FILE_SEND_DATA, {
                        "transfer_id": transfer_id, "chunk_index": index,
                        "data": base64.b64encode(packet).decode("ascii"),
                    })
                    with self._lock:
                        device["sent_bytes"] += len(chunk)
                        total_sent = sum(item["sent_bytes"] for item in transfer["devices"].values())
                        transfer["sent_bytes"] = total_sent
                    for callback in (progress_callback, self.on_progress):
                        if callback:
                            callback(transfer_id, total_sent, transfer["file_size"])
            device["state"] = "verifying"
            send(MessageType.FILE_SEND_END, {
                "transfer_id": transfer_id, "total_chunks": params["total_chunks"],
                "md5": params["md5"], "sha256": params["sha256"],
            })
            if needs_ack:
                if not device["end_event"].wait(self.ack_timeout):
                    raise RuntimeError("未收到落盘校验回执")
                result = transfer["end_acks"].get(key, {})
                if not result.get("success"):
                    raise RuntimeError(result.get("result") or "接收失败")
                device["result"] = result.get("result", "")
            device["state"] = "complete"
        except Exception as exc:
            device["state"] = "failed"
            device["result"] = str(exc)

    def _pace(self, size: int) -> None:
        # 全部文件任务合计限速 4 MiB/s，给心跳、控制和远控保留发送机会。
        with self._rate_lock:
            now = time.monotonic()
            scheduled = max(now, self._next_chunk_at)
            self._next_chunk_at = scheduled + size / (4 * 1024 * 1024)
        delay = scheduled - now
        if delay > 0:
            time.sleep(delay)

    def record_ack(self, params: dict) -> None:
        transfer_id = str(params.get("transfer_id") or "")
        student_id = str(params.get("student_id") or "")
        with self._lock:
            transfer = self._active_transfers.get(transfer_id)
            device = transfer["devices"].get(student_id) if transfer else None
            if not device:
                return
            stage = str(params.get("stage") or "end")
            if stage == "start":
                if params.get("success", True):
                    transfer["resume_chunks"][student_id] = int(params.get("resume_chunk", 0))
                else:
                    device["start_error"] = str(params.get("result") or "接收目录不可用")
                device["start_event"].set()
            elif stage == "end":
                transfer["end_acks"][student_id] = {
                    "success": bool(params.get("success")), "result": str(params.get("result") or ""),
                }
                device["end_event"].set()

    def get_transfer_info(self, transfer_id: str) -> Optional[dict]:
        with self._lock:
            return self._active_transfers.get(transfer_id)

    def get_active_transfers(self) -> Dict[str, dict]:
        with self._lock:
            return dict(self._active_transfers)

    def get_completed_transfer(self, transfer_id: str) -> Optional[dict]:
        with self._lock:
            return self._completed_transfers.get(transfer_id)
