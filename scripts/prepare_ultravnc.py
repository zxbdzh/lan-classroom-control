"""Fetch the pinned upstream portable distribution without running its programs."""

import argparse
import hashlib
import os
from pathlib import Path
import urllib.request
import zipfile

VERSION = "1.8.3.2"
URL = "https://uvnc.eu/download/1800/UltraVNC_1832.zip"
SHA256 = "8984e920169238de2a878ca3af71b7b8ac4feb3bbe295916dcd3ff1219456aad"
LICENSE_URL = "https://raw.githubusercontent.com/ultravnc/UltraVNC/main/LICENSE"


def prepare(archive: Path, destination: Path) -> None:
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.is_file():
        with urllib.request.urlopen(URL, timeout=60) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != SHA256:
            raise ValueError("UltraVNC download SHA-256 mismatch")
        archive.write_bytes(data)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise ValueError("Cached UltraVNC SHA-256 mismatch")
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        for name in (
            "winvnc.exe", "vncviewer.exe", "ddengine64.dll", "vnchooks.dll",
            "authSSP.dll", "logging.dll", "logmessages.dll",
        ):
            (destination / name).write_bytes(bundle.read("x64/" + name))
    with urllib.request.urlopen(LICENSE_URL, timeout=30) as response:
        (destination / "LICENSE-UltraVNC.txt").write_bytes(response.read())
    (destination / "UPSTREAM.txt").write_text(
        f"UltraVNC {VERSION}, unmodified x64 binaries.\n"
        f"Download: {URL}\nSHA-256: {SHA256}\n"
        "Official project and source: https://github.com/ultravnc/UltraVNC\n"
        "Release: https://uvnc.com/downloads/ultravnc/175-ultravnc-1-8-3-2.html\n",
        encoding="utf-8",
    )


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, default=root / ".artifacts/ultravnc/UltraVNC_1832.zip")
    parser.add_argument("--destination", type=Path, default=root / "third_party/ultravnc")
    args = parser.parse_args()
    prepare(args.archive, args.destination)
    print(f"Prepared UltraVNC {VERSION}: {os.path.abspath(args.destination)}")


if __name__ == "__main__":
    main()
