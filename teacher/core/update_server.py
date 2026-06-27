import os
import hashlib
import threading
import uuid
from typing import Callable, Dict, List, Optional
from common.protocol import MessageType, build_message
from common.file_transfer import CHUNK_SIZE
from common.logger import get_logger

logger = get_logger("update_server")


class UpdateServer:
    """教师端更新推送服务。

    职责：
    1. 管理待推送的更新包（zip 文件 + 版本号）
    2. 向学生端发 UPDATE_CHECK 通知
    3. 收到 UPDATE_REQUEST 后通过 FileDistributor 发送更新包（is_update=True）
    4. 跟踪各学生端的更新进度
    """

    def __init__(self, student_manager, file_distributor):
        self.student_manager = student_manager
        self.file_distributor = file_distributor
        self._lock = threading.Lock()
        # 待推送的更新包信息
        self.update_file_path: Optional[str] = None
        self.update_version: Optional[str] = None
        self.update_md5: Optional[str] = None
        # 各学生端更新进度: {student_id: {"progress": float, "status": str, "last_update": float}}
        self._progress: Dict[str, dict] = {}
        # 已请求但未完成的学生集合
        self._pending: Dict[str, str] = {}  # student_id -> transfer_id

    # ------------------------------------------------------------------
    # 设置更新包
    # ------------------------------------------------------------------
    def set_update_package(self, file_path: str, version: str) -> bool:
        """设置待推送的更新包，计算 MD5。"""
        if not os.path.isfile(file_path):
            logger.error(f"Update package not found: {file_path}")
            return False
        try:
            md5 = self._compute_file_md5(file_path)
        except Exception as e:
            logger.error(f"Compute update MD5 failed: {e}")
            return False
        with self._lock:
            self.update_file_path = file_path
            self.update_version = version
            self.update_md5 = md5
        logger.info(f"Update package set: {file_path}, version={version}, md5={md5}")
        return True

    def clear_update_package(self):
        with self._lock:
            self.update_file_path = None
            self.update_version = None
            self.update_md5 = None
            self._progress.clear()
            self._pending.clear()
        logger.info("Update package cleared")

    # ------------------------------------------------------------------
    # 通知学生端有新版本
    # ------------------------------------------------------------------
    def notify_update(self, student_ids: Optional[List[str]] = None) -> int:
        """向指定学生发送 UPDATE_CHECK，无参数则发送给所有在线学生。"""
        if not self.update_file_path or not self.update_version:
            logger.warning("No update package set, skip notify")
            return 0
        if student_ids is None:
            students = self.student_manager.get_online_students()
        else:
            students = []
            for sid in student_ids:
                s = self.student_manager.get_student(sid)
                if s and s.online:
                    students.append(s)

        msg = build_message(MessageType.UPDATE_CHECK, {
            "version": self.update_version,
            "md5": self.update_md5 or "",
            "file_name": os.path.basename(self.update_file_path),
        })
        count = 0
        for student in students:
            if student.conn and student.conn.is_alive():
                try:
                    student.conn.send_message(msg)
                    count += 1
                    with self._lock:
                        if student.student_id not in self._progress:
                            self._progress[student.student_id] = {
                                "progress": 0.0,
                                "status": "notified",
                                "last_update": 0,
                            }
                except Exception as e:
                    logger.warning(f"Notify update to {student.hostname} failed: {e}")
        logger.info(f"Notified {count} students of new version {self.update_version}")
        return count

    # ------------------------------------------------------------------
    # 收到学生端的 UPDATE_REQUEST，开始推送更新包
    # ------------------------------------------------------------------
    def on_update_request(self, conn, params: dict) -> bool:
        if not self.update_file_path:
            logger.warning("Update request received but no package set")
            return False
        student_id = conn.student_id or ""
        if not student_id:
            logger.warning(f"Update request from unknown conn {conn.addr}")
            return False

        student = self.student_manager.get_student(student_id)
        if not student:
            logger.warning(f"Update request from unknown student {student_id}")
            return False

        current_version = params.get("current_version", "")
        target_version = params.get("target_version", self.update_version)
        logger.info(f"Update request from {student.hostname} ({student.ip}): "
                    f"{current_version} -> {target_version}")

        with self._lock:
            self._progress[student_id] = {
                "progress": 0.0,
                "status": "downloading",
                "last_update": 0,
            }

        # 复用 FileDistributor 发送，标记 is_update=True
        transfer_id = self.file_distributor.send_file_to_students(
            file_path=self.update_file_path,
            students=[student],
            progress_callback=self._make_progress_callback(student_id),
            is_update=True,
            target_version=self.update_version,
        )
        with self._lock:
            self._pending[student_id] = transfer_id
        return True

    # ------------------------------------------------------------------
    # 收到学生端的 UPDATE_PROGRESS，更新进度
    # ------------------------------------------------------------------
    def on_update_progress(self, conn, params: dict):
        student_id = conn.student_id or ""
        if not student_id:
            return
        progress = float(params.get("progress", 0.0))
        status = params.get("status", "downloading")
        with self._lock:
            self._progress[student_id] = {
                "progress": progress,
                "status": status,
                "last_update": 0,
            }
        if status == "done":
            logger.info(f"Student {student_id} update complete")
            with self._lock:
                self._pending.pop(student_id, None)

    # ------------------------------------------------------------------
    # 查询更新进度
    # ------------------------------------------------------------------
    def get_update_progress(self) -> Dict[str, dict]:
        with self._lock:
            return {sid: info.copy() for sid, info in self._progress.items()}

    def get_student_progress(self, student_id: str) -> Optional[dict]:
        with self._lock:
            return self._progress.get(student_id, {}).copy() if student_id in self._progress else None

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    def _make_progress_callback(self, student_id: str) -> Callable:
        def callback(transfer_id: str, sent_bytes: int, file_size: int):
            progress = sent_bytes / file_size if file_size > 0 else 0.0
            with self._lock:
                self._progress[student_id] = {
                    "progress": progress,
                    "status": "downloading",
                    "last_update": 0,
                }
        return callback

    @staticmethod
    def _compute_file_md5(file_path: str) -> str:
        md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                md5.update(chunk)
        return md5.hexdigest()
