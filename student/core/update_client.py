import os
import sys
import struct
import hashlib
import base64
import threading
import time
import zipfile
import tempfile
import shutil
from typing import Callable, Optional
from common.version import get_version, is_newer, version_compare, set_version
from common.file_transfer import CHUNK_SIZE, FILE_HEADER_FORMAT, FILE_HEADER_SIZE
from common.protocol import MessageType, build_message
from common.logger import get_logger

logger = get_logger("update_client")


class UpdateClient:
    """学生端更新客户端。

    流程：
    1. 收到 UPDATE_CHECK 通知 -> 对比版本号 -> 需要更新则发送 UPDATE_REQUEST
    2. 教师端通过 FILE_SEND_START/DATA/END 推送更新包（params.is_update=True）
    3. handle_end 校验 MD5 -> 写入临时 zip -> 生成 updater.bat -> os._exit(0)
    4. updater.bat 等待主程序退出 -> 解压覆盖 -> 写入 version.txt -> 重启 exe
    """

    def __init__(self, send_message_func: Callable[[dict], None]):
        self._send = send_message_func
        self._active_transfer: Optional[dict] = None
        self._lock = threading.Lock()
        self.on_progress: Optional[Callable[[float], None]] = None
        self.on_state_changed: Optional[Callable[[str, str], None]] = None
        # 更新包临时保存路径
        self._temp_dir = os.path.join(tempfile.gettempdir(), "lan_classroom_update")
        os.makedirs(self._temp_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # 收到 UPDATE_CHECK 通知
    # ------------------------------------------------------------------
    def check_and_request(self, params: dict):
        """收到教师端的更新通知，对比版本后决定是否请求更新。"""
        new_version = params.get("version", "")
        if not new_version:
            logger.warning("Update check without version field")
            return
        if not is_newer(new_version):
            logger.info(f"Current version {get_version()} >= server version {new_version}, skip update")
            return
        logger.info(f"New version available: {new_version}, requesting update")
        self._notify_state("requesting", f"发现新版本 {new_version}，正在请求下载")
        # 记录待更新版本号，供后续 handle_end 写入
        request_msg = build_message(MessageType.UPDATE_REQUEST, {
            "current_version": get_version(),
            "target_version": new_version,
        })
        self._send(request_msg)

    # ------------------------------------------------------------------
    # 接收更新包数据（来自教师端 FILE_SEND_START/DATA/END with is_update=True）
    # ------------------------------------------------------------------
    def handle_start(self, params: dict):
        transfer_id = params.get("transfer_id", "")
        file_name = params.get("file_name", "update.zip")
        file_size = params.get("file_size", 0)
        total_chunks = params.get("total_chunks", 0)
        md5 = params.get("md5", "")
        target_version = params.get("target_version", "")

        save_path = os.path.join(self._temp_dir, f"update_{int(time.time())}.zip")
        with self._lock:
            self._active_transfer = {
                "transfer_id": transfer_id,
                "file_name": file_name,
                "save_path": save_path,
                "file_size": file_size,
                "total_chunks": total_chunks,
                "received_chunks": 0,
                "md5": md5,
                "target_version": target_version,
                "file": open(save_path, "wb"),
                "start_time": time.time(),
            }
        logger.info(f"Receiving update package: {file_name}, size: {file_size}, target: {target_version}")
        self._notify_state("downloading", f"开始下载更新包 {target_version}")
        if self.on_progress:
            self.on_progress(0.0)

    def handle_data(self, params: dict) -> bool:
        transfer_id = params.get("transfer_id", "")
        chunk_index = params.get("chunk_index", 0)
        data_param = params.get("data", "")

        with self._lock:
            transfer = self._active_transfer
            if not transfer or transfer["transfer_id"] != transfer_id:
                return False

        if isinstance(data_param, str):
            try:
                data = base64.b64decode(data_param)
            except Exception as e:
                logger.warning(f"Update base64 decode error chunk {chunk_index}: {e}")
                return False
        elif isinstance(data_param, bytes):
            data = data_param
        else:
            return False

        if len(data) < FILE_HEADER_SIZE:
            return False
        _, idx, chunk_len = struct.unpack(FILE_HEADER_FORMAT, data[:FILE_HEADER_SIZE])
        chunk_data = data[FILE_HEADER_SIZE:FILE_HEADER_SIZE + chunk_len]

        with self._lock:
            if not self._active_transfer:
                return False
            file_obj = self._active_transfer["file"]
            offset = chunk_index * CHUNK_SIZE
            file_obj.seek(offset)
            file_obj.write(chunk_data)
            self._active_transfer["received_chunks"] += 1
            received = self._active_transfer["received_chunks"]
            total = self._active_transfer["total_chunks"]
            transfer_id_now = self._active_transfer["transfer_id"]

        if total > 0 and self.on_progress:
            self.on_progress(received / total)
        # 每收到 10% 上报一次进度给教师端
        if total > 0 and (received % max(1, total // 10) == 0 or received == total):
            self._report_progress(transfer_id_now, received / total)
        return True

    def handle_end(self, params: dict) -> bool:
        transfer_id = params.get("transfer_id", "")
        with self._lock:
            transfer = self._active_transfer
            if not transfer or transfer["transfer_id"] != transfer_id:
                logger.warning("Update end without active transfer")
                return False
            file_obj = transfer["file"]
            save_path = transfer["save_path"]
            expected_md5 = transfer.get("md5", "")
            target_version = transfer.get("target_version", "")
            file_obj.close()
            self._active_transfer = None

        self._report_progress(transfer_id, 1.0)

        # MD5 校验
        if expected_md5:
            actual_md5 = self._compute_file_md5(save_path)
            if actual_md5 != expected_md5:
                logger.error(f"Update MD5 mismatch: expected {expected_md5}, got {actual_md5}")
                self._notify_state("failed", "更新包校验失败，MD5 不匹配")
                try:
                    os.remove(save_path)
                except Exception:
                    pass
                return False

        logger.info(f"Update package verified, applying: {save_path}")
        self._notify_state("installing", "更新包已下载完成，正在安装")
        # 上报完成
        self._report_progress(transfer_id, 1.0, status="done")

        # 应用更新（生成 updater 脚本并退出主程序）
        return self._apply_update(save_path, target_version)

    # ------------------------------------------------------------------
    # 应用更新：生成 updater.bat，主程序退出后由脚本接管
    # ------------------------------------------------------------------
    def _apply_update(self, zip_path: str, target_version: str) -> bool:
        if not zipfile.is_zipfile(zip_path):
            logger.error(f"Update file is not a valid zip: {zip_path}")
            self._notify_state("failed", "更新包格式错误")
            return False

        # 目标目录：exe 所在目录（打包后）或项目根（开发模式）
        if getattr(sys, 'frozen', False):
            target_dir = os.path.dirname(sys.executable)
            program_path = sys.executable
            restart_cmd = f'"{program_path}"'
        else:
            target_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            restart_cmd = f'"{sys.executable}" "{os.path.join(target_dir, "student", "main.py")}"'

        script_path = os.path.join(self._temp_dir, "updater.bat")
        try:
            with open(script_path, "w", encoding="gbk") as f:
                f.write(self._build_updater_script(
                    zip_path=zip_path,
                    target_dir=target_dir,
                    target_version=target_version,
                    restart_cmd=restart_cmd,
                ))
        except Exception as e:
            logger.error(f"Write updater script failed: {e}")
            self._notify_state("failed", f"生成更新脚本失败: {e}")
            return False

        logger.info(f"Update script generated: {script_path}")
        self._notify_state("restarting", "正在重启以应用更新")

        # 启动 updater（detached），然后立即退出主程序
        try:
            import subprocess
            DETACHED_PROCESS = 0x00000008
            subprocess.Popen(
                ["cmd", "/c", script_path],
                creationflags=DETACHED_PROCESS,
                close_fds=True,
            )
        except Exception as e:
            logger.error(f"Launch updater failed: {e}")
            self._notify_state("failed", f"启动更新脚本失败: {e}")
            return False

        # 给主程序一点时间刷日志，然后强制退出（不触发 Qt 关闭流程）
        logger.info("Exiting main program to apply update...")
        time.sleep(0.5)
        os._exit(0)

    @staticmethod
    def _build_updater_script(zip_path: str, target_dir: str,
                              target_version: str, restart_cmd: str) -> str:
        """生成 updater.bat 脚本。

        脚本流程：等待主程序退出 -> 解压覆盖 -> 写 version.txt -> 重启程序 -> 自删除
        """
        # 用 -wait taskkill 等待主程序结束，最多等 10 秒
        # Expand-Archive 默认会覆盖（-Force）
        return f"""@echo off
chcp 65001 >nul
echo Applying LanClassroom update...

REM 等待主程序退出（最多 10 秒）
set /a wait_count=0
:wait_loop
tasklist /fi "PID eq {os.getpid()}" 2>nul | find "{os.getpid()}" >nul
if not errorlevel 1 (
    set /a wait_count+=1
    if {os.getpid()}==0 goto do_update
    if %wait_count% geq 10 goto do_update
    timeout /t 1 /nobreak >nul
    goto wait_loop
)

:do_update
echo Extracting update package...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -Path '{zip_path}' -DestinationPath '{target_dir}' -Force"
if errorlevel 1 (
    echo Update extract failed.
    timeout /t 5 /nobreak >nul
    goto cleanup
)

echo Writing version file...
echo {target_version}> "{os.path.join(target_dir, 'version.txt')}"

echo Restarting application...
start "" {restart_cmd}

:cleanup
REM 自删除脚本
(goto) 2>nul & del "%~f0"
"""

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------
    def _compute_file_md5(self, file_path: str) -> str:
        md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                md5.update(chunk)
        return md5.hexdigest()

    def _report_progress(self, transfer_id: str, progress: float, status: str = "downloading"):
        try:
            msg = build_message(MessageType.UPDATE_PROGRESS, {
                "transfer_id": transfer_id,
                "progress": progress,
                "status": status,
            })
            self._send(msg)
        except Exception as e:
            logger.debug(f"Report progress failed: {e}")

    def _notify_state(self, state: str, message: str):
        logger.info(f"Update state: {state} - {message}")
        if self.on_state_changed:
            try:
                self.on_state_changed(state, message)
            except Exception:
                pass

    def get_progress(self) -> float:
        with self._lock:
            if not self._active_transfer or self._active_transfer["total_chunks"] == 0:
                return 0.0
            return self._active_transfer["received_chunks"] / self._active_transfer["total_chunks"]

    def cancel(self):
        """取消当前更新下载。"""
        with self._lock:
            if self._active_transfer:
                try:
                    self._active_transfer["file"].close()
                    if os.path.exists(self._active_transfer["save_path"]):
                        os.remove(self._active_transfer["save_path"])
                except Exception:
                    pass
                self._active_transfer = None
        logger.info("Update cancelled")
