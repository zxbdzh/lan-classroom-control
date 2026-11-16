"""Stable machine hints for reconnect matching, not authentication credentials."""

import hashlib
import os
import uuid
from typing import Optional


def normalize_mac(value: str) -> str:
    address = str(value or "").strip().casefold()
    for separator in (":", "-", "."):
        address = address.replace(separator, "")
    if (
        len(address) != 12
        or any(character not in "0123456789abcdef" for character in address)
        or address in {"000000000000", "ffffffffffff"}
    ):
        return ""
    return address


def machine_fingerprint() -> str:
    if os.name != "nt":
        return ""
    pythoncom = None
    initialized = False
    try:
        import pythoncom
        pythoncom.CoInitialize()
        initialized = True
        identifier = _firmware_uuid()
        if identifier is not None:
            # 不发送原始硬件编号；仅用于跨网卡匹配，不能作为身份认证凭据。
            return "smbios:" + hashlib.sha256(identifier.bytes).hexdigest()
    except Exception:
        # 老旧主板缺少有效 UUID 时，保留持久设备 ID 与 MAC 的兼容路径。
        return ""
    finally:
        if initialized:
            pythoncom.CoUninitialize()
    return ""


def _firmware_uuid() -> Optional[uuid.UUID]:
    import win32com.client

    products = None
    product = None
    try:
        products = win32com.client.GetObject("winmgmts:").ExecQuery(
            "SELECT UUID FROM Win32_ComputerSystemProduct"
        )
        for product in products:
            identifier = uuid.UUID(str(product.UUID).strip())
            if identifier.int not in {0, (1 << 128) - 1}:
                return identifier
        return None
    finally:
        # 异常的 traceback 也可能保留此帧，必须先释放 COM 对象再退出 apartment。
        product = None
        products = None
