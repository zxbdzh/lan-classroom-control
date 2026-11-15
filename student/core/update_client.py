import os
import sys
import struct
import hashlib
import base64
import re
import shutil
import threading
import time
import zipfile
import tempfile
import posixpath
from typing import Callable, Optional
from common.version import get_version, is_newer
from common.file_transfer import CHUNK_SIZE, FILE_HEADER_FORMAT, FILE_HEADER_SIZE
from common.protocol import MessageType, build_message
from common.logger import get_logger
from common.update_security import load_public_key, sha256_file, verify_manifest

logger = get_logger("update_client")


class UpdateClient:
    """学生端更新客户端。

    流程：
    1. 收到 UPDATE_CHECK 通知 -> 对比版本号 -> 需要更新则发送 UPDATE_REQUEST
    2. 教师端通过 FILE_SEND_START/DATA/END 推送更新包（params.is_update=True）
    3. handle_end 校验 SHA-256 和 Ed25519 签名并预解压到同盘暂存目录
    4. updater.bat 停止服务和会话代理，目录级替换并通过 SCM 恢复服务
    """

    SERVICE_NAME = "LanClassroomStudentV2"
    SERVICE_EXE = "LanClassroom-Student-Service.exe"
    AGENT_EXE = "LanClassroom-Student-Agent.exe"

    def __init__(
        self,
        send_message_func: Callable[[dict], None],
        *,
        service_mode: bool = False,
    ):
        self._send = send_message_func
        self._service_mode = bool(service_mode)
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
        file_size = int(params.get("file_size", 0))
        total_chunks = int(params.get("total_chunks", 0))
        md5 = params.get("md5", "")
        sha256 = params.get("sha256", "")
        target_version = params.get("target_version", "")
        expected_chunks = (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE
        if (
            not transfer_id
            or file_size < 0
            or total_chunks < 0
            or total_chunks != expected_chunks
        ):
            raise ValueError("invalid update transfer metadata")

        save_path = os.path.join(self._temp_dir, f"update_{int(time.time())}.zip")
        with self._lock:
            previous = self._active_transfer
            if previous:
                try:
                    previous["file"].close()
                finally:
                    try:
                        os.remove(previous["save_path"])
                    except OSError:
                        pass
            self._active_transfer = {
                "transfer_id": transfer_id,
                "file_name": file_name,
                "save_path": save_path,
                "file_size": file_size,
                "total_chunks": total_chunks,
                "received_chunks": 0,
                "received_indices": set(),
                "md5": md5,
                "sha256": sha256,
                "update_manifest": params.get("update_manifest", ""),
                "update_signature": params.get("update_signature", ""),
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
        version, idx, chunk_len = struct.unpack(
            FILE_HEADER_FORMAT, data[:FILE_HEADER_SIZE]
        )
        if version != 1 or idx != chunk_index or chunk_len > CHUNK_SIZE:
            return False
        chunk_data = data[FILE_HEADER_SIZE:FILE_HEADER_SIZE + chunk_len]
        if len(chunk_data) != chunk_len:
            return False

        with self._lock:
            transfer = self._active_transfer
            if not transfer or transfer["transfer_id"] != transfer_id:
                return False
            if chunk_index < 0 or chunk_index >= transfer["total_chunks"]:
                return False
            if chunk_index in transfer["received_indices"]:
                return True
            file_obj = transfer["file"]
            offset = chunk_index * CHUNK_SIZE
            file_obj.seek(offset)
            file_obj.write(chunk_data)
            file_obj.flush()
            transfer["received_indices"].add(chunk_index)
            transfer["received_chunks"] += 1
            received = transfer["received_chunks"]
            total = transfer["total_chunks"]
            transfer_id_now = transfer["transfer_id"]

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
            expected_sha256 = transfer.get("sha256", "")
            target_version = transfer.get("target_version", "")
            manifest_b64 = transfer.get("update_manifest", "")
            signature = transfer.get("update_signature", "")
            file_obj.close()
            self._active_transfer = None

        if transfer["received_chunks"] != transfer["total_chunks"]:
            logger.error("Update transfer is incomplete")
            self._notify_state("failed", "更新包下载不完整")
            try:
                os.remove(save_path)
            except OSError:
                pass
            return False
        with open(save_path, "r+b") as handle:
            handle.truncate(int(transfer["file_size"]))

        self._report_progress(transfer_id, 1.0)

        # MD5 校验
        if expected_sha256:
            actual_sha256 = sha256_file(save_path)
            if actual_sha256 != expected_sha256:
                logger.error("Update SHA-256 mismatch")
                self._notify_state("failed", "更新包 SHA-256 校验失败")
                try:
                    os.remove(save_path)
                except OSError:
                    pass
                return False
        elif expected_md5:
            actual_md5 = self._compute_file_md5(save_path)
            if actual_md5 != expected_md5:
                logger.error(f"Update MD5 mismatch: expected {expected_md5}, got {actual_md5}")
                self._notify_state("failed", "更新包校验失败，MD5 不匹配")
                try:
                    os.remove(save_path)
                except Exception:
                    pass
                return False

        try:
            manifest = base64.b64decode(manifest_b64, validate=True)
            verify_manifest(
                manifest,
                signature,
                load_public_key(),
                package_path=save_path,
                expected_version=target_version,
            )
        except Exception as exc:
            logger.error(f"Update signature verification failed: {exc}")
            self._notify_state("failed", "更新包签名校验失败")
            try:
                os.remove(save_path)
            except OSError:
                pass
            return False

        logger.info(f"Update package verified, applying: {save_path}")
        self._notify_state("installing", "更新包已下载完成，正在安装")
        # 上报完成
        self._report_progress(transfer_id, 1.0, status="done")

        # 应用更新（生成 updater 脚本并退出主程序）
        return self._apply_update(save_path, target_version)

    # ------------------------------------------------------------------
    # 应用更新：先安全解压，主程序退出后由 updater.bat 切换目录
    # ------------------------------------------------------------------
    def _apply_update(self, zip_path: str, target_version: str) -> bool:
        if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,63}", target_version or ""):
            logger.error("Update version contains unsupported characters")
            self._notify_state("failed", "更新版本号格式无效")
            return False
        if not zipfile.is_zipfile(zip_path):
            logger.error(f"Update file is not a valid zip: {zip_path}")
            self._notify_state("failed", "更新包格式错误")
            return False
        members = []
        try:
            with zipfile.ZipFile(zip_path, "r") as archive:
                for member in archive.infolist():
                    normalized = posixpath.normpath(member.filename.replace("\\", "/"))
                    if (
                        normalized == ".."
                        or normalized.startswith("../")
                        or normalized.startswith("/")
                        or ":" in normalized.split("/", 1)[0]
                    ):
                        raise ValueError("update archive contains an unsafe path")
                    members.append(normalized)
        except Exception as exc:
            logger.error(f"Unsafe update archive: {exc}")
            self._notify_state("failed", "更新包包含不安全路径")
            return False

        layout = self._resolve_install_layout()
        member_keys = {item.casefold() for item in members}
        service_archive = {
            "service/{}".format(self.SERVICE_EXE.casefold()),
            "agent/{}".format(self.AGENT_EXE.casefold()),
        }.issubset(member_keys)
        if layout["service_layout"] and not service_archive:
            logger.error("Student service update is missing Service/Agent directories")
            self._notify_state("failed", "更新包缺少服务或会话代理目录")
            return False

        staging_parent = layout["target_dir"] if layout["service_layout"] else self._temp_dir
        try:
            staging_dir = tempfile.mkdtemp(prefix=".update-staging-", dir=staging_parent)
            with zipfile.ZipFile(zip_path, "r") as archive:
                archive.extractall(staging_dir)
        except Exception as exc:
            logger.error(f"Prepare update staging directory failed: {exc}")
            self._notify_state("failed", "准备更新暂存目录失败")
            return False

        script_path = os.path.join(
            self._temp_dir,
            "updater_{}_{}.bat".format(os.getpid(), int(time.time() * 1000)),
        )
        try:
            with open(script_path, "w", encoding="gbk") as f:
                f.write(self._build_updater_script(
                    zip_path=zip_path,
                    staging_dir=staging_dir,
                    target_dir=layout["target_dir"],
                    target_version=target_version,
                    restart_cmd=layout["restart_cmd"],
                    service_layout=layout["service_layout"],
                    current_pid=os.getpid(),
                ))
        except Exception as e:
            logger.error(f"Write updater script failed: {e}")
            self._notify_state("failed", f"生成更新脚本失败: {e}")
            shutil.rmtree(staging_dir, ignore_errors=True)
            return False

        logger.info(f"Update script generated: {script_path}")
        self._notify_state("restarting", "正在重启以应用更新")

        # 启动 updater（detached），然后立即退出主程序
        try:
            import subprocess
            DETACHED_PROCESS = 0x00000008
            CREATE_NO_WINDOW = 0x08000000
            subprocess.Popen(
                ["cmd", "/c", script_path],
                creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
                close_fds=True,
            )
        except Exception as e:
            logger.error(f"Launch updater failed: {e}")
            self._notify_state("failed", f"启动更新脚本失败: {e}")
            shutil.rmtree(staging_dir, ignore_errors=True)
            return False

        # 给主程序一点时间刷日志，然后强制退出（不触发 Qt 关闭流程）
        logger.info("Exiting main program to apply update...")
        time.sleep(0.5)
        os._exit(0)

    def _resolve_install_layout(self) -> dict:
        frozen = bool(getattr(sys, "frozen", False))
        if not frozen:
            target_dir = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
            return {
                "target_dir": target_dir,
                "service_layout": False,
                "restart_cmd": '"{}" "{}"'.format(
                    sys.executable, os.path.join(target_dir, "student", "main.py")
                ),
            }

        executable = os.path.abspath(sys.executable)
        executable_dir = os.path.dirname(executable)
        in_service_dir = os.path.basename(executable_dir).lower() == "service"
        service_layout = self._service_mode or in_service_dir
        if service_layout:
            return {
                "target_dir": os.path.dirname(executable_dir),
                "service_layout": True,
                "restart_cmd": "",
            }
        return {
            "target_dir": executable_dir,
            "service_layout": False,
            "restart_cmd": '"{}"'.format(executable),
        }

    @classmethod
    def _build_updater_script(
        cls,
        *,
        zip_path: str,
        staging_dir: str,
        target_dir: str,
        target_version: str,
        restart_cmd: str,
        service_layout: bool,
        current_pid: int,
    ) -> str:
        """Build a Win7-compatible updater with rollback for service installs."""
        wait_block = f"""set /a wait_count=0
:wait_loop
tasklist /fi "PID eq {current_pid}" 2>nul | find "{current_pid}" >nul
if errorlevel 1 goto do_update
set /a wait_count+=1
if %wait_count% geq 30 goto do_update
ping 127.0.0.1 -n 2 >nul
goto wait_loop

:do_update
"""
        cleanup = f"""rmdir /s /q "{staging_dir}" 2>nul
del /q "{zip_path}" 2>nul
(goto) 2>nul & del "%~f0"
"""
        if not service_layout:
            return f"""@echo off
{wait_block}xcopy "{staging_dir}\\*" "{target_dir}\\" /E /I /Y /Q >nul
if errorlevel 1 goto cleanup
echo {target_version}> "{os.path.join(target_dir, 'version.txt')}"
start "" {restart_cmd}
:cleanup
{cleanup}"""

        service_dir = os.path.join(target_dir, "Service")
        agent_dir = os.path.join(target_dir, "Agent")
        service_backup = os.path.join(target_dir, ".Service.rollback")
        agent_backup = os.path.join(target_dir, ".Agent.rollback")
        staged_service = os.path.join(staging_dir, "Service")
        staged_agent = os.path.join(staging_dir, "Agent")
        vnc_dir = os.path.join(target_dir, "UltraVNC")
        vnc_backup = os.path.join(target_dir, ".UltraVNC.rollback")
        staged_vnc = os.path.join(staging_dir, "UltraVNC")
        service_exe = os.path.join(service_dir, cls.SERVICE_EXE)
        agent_exe = os.path.join(agent_dir, cls.AGENT_EXE)
        return f"""@echo off
{wait_block}sc stop "{cls.SERVICE_NAME}" >nul 2>&1
sc stop "LanClassroomVNC" >nul 2>&1
taskkill /IM "{cls.AGENT_EXE}" /F >nul 2>&1
ping 127.0.0.1 -n 3 >nul
rmdir /s /q "{service_backup}" 2>nul
rmdir /s /q "{agent_backup}" 2>nul
rmdir /s /q "{vnc_backup}" 2>nul
move /Y "{service_dir}" "{service_backup}" >nul
if errorlevel 1 goto rollback
move /Y "{agent_dir}" "{agent_backup}" >nul
if errorlevel 1 goto rollback
move /Y "{staged_service}" "{service_dir}" >nul
if errorlevel 1 goto rollback
move /Y "{staged_agent}" "{agent_dir}" >nul
if errorlevel 1 goto rollback
if exist "{staged_vnc}" (
    if exist "{vnc_dir}" move /Y "{vnc_dir}" "{vnc_backup}" >nul
    move /Y "{staged_vnc}" "{vnc_dir}" >nul
    if errorlevel 1 goto rollback
)
"{service_exe}" --wait 20 start >nul 2>&1
if errorlevel 1 goto rollback
echo {target_version}> "{os.path.join(target_dir, 'version.txt')}"
rmdir /s /q "{service_backup}" 2>nul
rmdir /s /q "{agent_backup}" 2>nul
rmdir /s /q "{vnc_backup}" 2>nul
goto cleanup

:rollback
if exist "{service_backup}" (
    rmdir /s /q "{service_dir}" 2>nul
    move /Y "{service_backup}" "{service_dir}" >nul
)
if exist "{agent_backup}" (
    rmdir /s /q "{agent_dir}" 2>nul
    move /Y "{agent_backup}" "{agent_dir}" >nul
)
if exist "{vnc_backup}" (
    rmdir /s /q "{vnc_dir}" 2>nul
    move /Y "{vnc_backup}" "{vnc_dir}" >nul
)
if exist "{service_exe}" "{service_exe}" --wait 20 start >nul 2>&1

:cleanup
{cleanup}"""

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
