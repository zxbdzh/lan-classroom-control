"""只读加载已校验的 Oracle EXE 资源，提取 MSI，不启动安装器或触发提权。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


INSTALLER_SHA256 = "afd0ff1656cbe04ed88ce619594ae021419d97cce89fa9bdc443e9a963f1713f"


def extract(installer: Path, directory: Path) -> dict:
    import win32api
    import win32com.client

    if hashlib.sha256(installer.read_bytes()).hexdigest() != INSTALLER_SHA256:
        raise ValueError("Oracle EXE SHA-256 不匹配")
    directory.mkdir(parents=True, exist_ok=True)
    # AS_DATAFILE 不执行程序入口，因此不触发 EXE 的 requireAdministrator 清单。
    module = win32api.LoadLibraryEx(str(installer), 0, 2)
    files = {}
    try:
        for header_name in ("HDR_00", "HDR_02"):
            header = win32api.LoadResource(module, 10, header_name)
            resource_name = header[4:32].split(b"\0", 1)[0].decode("ascii")
            filename = header[32:].split(b"\0", 1)[0].decode("ascii")
            destination = directory / filename
            if destination.resolve().parent != directory.resolve():
                raise ValueError("安装包资源路径超出目标目录")
            contents = win32api.LoadResource(module, 10, resource_name)
            if destination.exists():
                if destination.read_bytes() != contents:
                    raise ValueError(f"保留已有的不匹配文件：{destination}")
            else:
                destination.write_bytes(contents)
            files[filename] = {
                "path": str(destination),
                "size": len(contents),
                "sha256": hashlib.sha256(contents).hexdigest(),
            }
    finally:
        win32api.FreeLibrary(module)

    installer_api = win32com.client.Dispatch("WindowsInstaller.Installer")
    msi = next(directory.glob("*_amd64.msi"))
    database = installer_api.OpenDatabase(str(msi), 0)
    view = database.OpenView("SELECT `Feature` FROM `Feature`")
    view.Execute()
    features = []
    while True:
        record = view.Fetch()
        if record is None:
            break
        # StringData 是带索引的 COM 属性，不能按普通方法调用。
        features.append(record._oleobj_.Invoke(
            record._oleobj_.GetIDsOfNames("StringData"), 0, 2, True, 1
        ))
    view.Close()
    if "VBoxApplication" not in features:
        raise ValueError("MSI 不包含预期的 VBoxApplication 功能")
    report = {
        "oracle_exe_sha256": INSTALLER_SHA256,
        "files": files,
        "msi_features": features,
        "installation_executed": False,
    }
    (directory / "extraction.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", required=True, type=Path)
    parser.add_argument("--directory", required=True, type=Path)
    arguments = parser.parse_args()
    print(json.dumps(
        extract(arguments.installer.resolve(), arguments.directory.resolve()),
        ensure_ascii=False, indent=2,
    ))
