"""单文件更新容器：封装原始 ZIP 和签名，不改变学生端接收的字节。"""
from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from typing import Any, Dict, Optional

from common.update_security import UpdateVerificationError, verify_manifest

MANIFEST_NAME = "manifest.json"
SIGNATURE_NAME = "manifest.sig"
MAX_MANIFEST_SIZE = 64 * 1024
MAX_SIGNATURE_SIZE = 4096


@dataclass
class UpdatePackage:
    path: str
    manifest: bytes
    signature: str
    metadata: Dict[str, Any]
    temporary_directory: Optional[tempfile.TemporaryDirectory] = None

    def close(self) -> None:
        if self.temporary_directory is not None:
            self.temporary_directory.cleanup()
            self.temporary_directory = None


def _read_metadata(archive: zipfile.ZipFile, name: str, limit: int) -> bytes:
    entry = archive.getinfo(name)
    if entry.file_size > limit:
        raise UpdateVerificationError("更新包清单或签名文件过大")
    return archive.read(entry)


def open_update_package(
    path: str, public_key: bytes, expected_version: Optional[str] = None,
) -> UpdatePackage:
    """验证单文件容器或旧式 ZIP + 同名清单/签名；调用者负责 close。"""
    temporary = None
    try:
        # 旧包可保持原路径，新容器必须单独解出载荷，避免向旧学生端发送嵌套 ZIP。
        with zipfile.ZipFile(path, "r") as archive:
            names = archive.namelist()
            bundled = MANIFEST_NAME in names or SIGNATURE_NAME in names
            if bundled:
                if len(names) != 3 or len(set(names)) != 3:
                    raise UpdateVerificationError("更新包内容不完整或存在重复文件")
                manifest = _read_metadata(archive, MANIFEST_NAME, MAX_MANIFEST_SIZE)
                signature = _read_metadata(archive, SIGNATURE_NAME, MAX_SIGNATURE_SIZE).decode("ascii").strip()
                # 必须先验签再信任清单中的路径及长度，绝不 extractall 不可信容器。
                metadata = verify_manifest(
                    manifest, signature, public_key, expected_version=expected_version,
                )
                name = metadata["package"]
                if (not isinstance(name, str) or name in ("", ".", "..")
                        or any(char in name for char in ("/", "\\", ":"))
                        or not name.lower().endswith(".zip")
                        or name in (MANIFEST_NAME, SIGNATURE_NAME)
                        or set(names) != {name, MANIFEST_NAME, SIGNATURE_NAME}):
                    raise UpdateVerificationError("更新包载荷名称无效")
                entry = archive.getinfo(name)
                if entry.is_dir() or entry.file_size != int(metadata["size"]):
                    raise UpdateVerificationError("更新包大小与签名清单不一致")
                temporary = tempfile.TemporaryDirectory(prefix="classroom-update-")
                payload = os.path.join(temporary.name, name)
                with archive.open(entry) as source, open(payload, "wb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
        if not bundled:
            payload = path
            try:
                with open(path + ".manifest.json", "rb") as handle:
                    manifest = handle.read(MAX_MANIFEST_SIZE + 1)
                with open(path + ".sig", "rt", encoding="ascii") as handle:
                    signature = handle.read(MAX_SIGNATURE_SIZE + 1).strip()
            except FileNotFoundError as exc:
                raise UpdateVerificationError(
                    "缺少更新清单或签名：请选择单文件更新包；旧式 ZIP 需将 .manifest.json 和 .sig 放在同一目录"
                ) from exc
            if len(manifest) > MAX_MANIFEST_SIZE or len(signature) > MAX_SIGNATURE_SIZE:
                raise UpdateVerificationError("更新包清单或签名文件过大")
        metadata = verify_manifest(
            manifest, signature, public_key, package_path=payload,
            expected_version=expected_version,
        )
        return UpdatePackage(payload, manifest, signature, metadata, temporary)
    except Exception:
        if temporary is not None:
            temporary.cleanup()
        raise


def build_update_bundle(package: str, output: str, public_key: bytes) -> None:
    """校验签名后生成可直接选择的单文件包；不得覆写被签名的载荷。"""
    if os.path.normcase(os.path.abspath(package)) == os.path.normcase(os.path.abspath(output)):
        raise ValueError("更新容器不能覆盖原始签名载荷")
    verified = open_update_package(package, public_key)
    try:
        name = verified.metadata["package"]
        if name != os.path.basename(verified.path) or any(c in name for c in ("/", "\\", ":")):
            raise UpdateVerificationError("更新包名称与签名清单不一致")
        # 内层 ZIP 已压缩，外层仅归档，避免重新压缩造成不必要的构建时间。
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.write(verified.path, arcname=name)
            archive.writestr(MANIFEST_NAME, verified.manifest)
            archive.writestr(SIGNATURE_NAME, verified.signature)
    finally:
        verified.close()
