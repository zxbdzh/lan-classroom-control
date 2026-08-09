"""Detached Ed25519 update manifests shared by build and runtime code."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any, Dict, Optional

from Crypto.PublicKey import ECC
from Crypto.Signature import eddsa


class UpdateVerificationError(ValueError):
    pass


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(package_path: str, version: str, created_at: int) -> bytes:
    payload = {
        "created_at": int(created_at),
        "package": os.path.basename(package_path),
        "sha256": sha256_file(package_path),
        "size": os.path.getsize(package_path),
        "version": str(version),
    }
    return json.dumps(
        payload, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sign_manifest(manifest: bytes, private_key_pem: bytes) -> str:
    key = ECC.import_key(private_key_pem)
    if not key.has_private():
        raise UpdateVerificationError("Ed25519 private key is required")
    signature = eddsa.new(key, "rfc8032").sign(bytes(manifest))
    return base64.b64encode(signature).decode("ascii")


def verify_manifest(
    manifest: bytes,
    signature_base64: str,
    public_key_pem: bytes,
    *,
    package_path: Optional[str] = None,
    expected_version: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        signature = base64.b64decode(signature_base64, validate=True)
        key = ECC.import_key(public_key_pem)
        eddsa.new(key.public_key(), "rfc8032").verify(bytes(manifest), signature)
    except Exception as exc:
        raise UpdateVerificationError("invalid Ed25519 update signature") from exc
    try:
        value = json.loads(bytes(manifest).decode("utf-8"))
    except Exception as exc:
        raise UpdateVerificationError("invalid update manifest JSON") from exc
    required = {"created_at", "package", "sha256", "size", "version"}
    if not isinstance(value, dict) or set(value) != required:
        raise UpdateVerificationError("update manifest fields are invalid")
    if expected_version is not None and str(value["version"]) != str(expected_version):
        raise UpdateVerificationError("update manifest version does not match")
    if package_path is not None:
        if int(value["size"]) != os.path.getsize(package_path):
            raise UpdateVerificationError("update package size does not match")
        if str(value["sha256"]).casefold() != sha256_file(package_path):
            raise UpdateVerificationError("update package SHA-256 does not match")
    return value


def default_public_key_path() -> str:
    return os.path.join(os.path.dirname(__file__), "update_public_key.pem")


def load_public_key(path: Optional[str] = None) -> bytes:
    with open(path or default_public_key_path(), "rb") as handle:
        return handle.read()


__all__ = [
    "UpdateVerificationError", "build_manifest", "load_public_key", "sha256_file",
    "sign_manifest", "verify_manifest",
]
