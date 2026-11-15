"""Reject Windows release binaries whose PE minimum version exceeds a target."""

from __future__ import annotations

import argparse
import json
import os
import struct
from dataclasses import asdict, dataclass
from typing import Iterable, List, Sequence, Tuple


def exclude_host_ucrt(binaries: Iterable[tuple]) -> List[tuple]:
    # PE 最低版本不能发现新版 UCRT 的 API-set 依赖，必须在打包阶段排除。
    return [
        entry for entry in binaries
        if os.path.basename(entry[0]).lower() != "ucrtbase.dll"
        and not os.path.basename(entry[0]).lower().startswith("api-ms-win-")
    ]


def check_runtime_directory(directory: str) -> None:
    binaries = [(name, "", "") for name in os.listdir(directory)]
    retained = exclude_host_ucrt(binaries)
    if len(retained) != len(binaries):
        raise ValueError("{} bundles host UCRT/API-set DLLs".format(directory))


@dataclass(frozen=True)
class PeCompatibility:
    path: str
    magic: str
    operating_system: str
    subsystem: str


def _version_text(value: Tuple[int, int]) -> str:
    return "{}.{}".format(value[0], value[1])


def parse_version(value: str) -> Tuple[int, int]:
    parts = str(value).strip().split(".")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ValueError("version must use major.minor format")
    return int(parts[0]), int(parts[1])


def read_compatibility(path: str) -> PeCompatibility:
    with open(path, "rb") as handle:
        dos_header = handle.read(64)
        if len(dos_header) != 64 or dos_header[:2] != b"MZ":
            raise ValueError("not a PE file")
        pe_offset = struct.unpack_from("<I", dos_header, 0x3C)[0]
        handle.seek(pe_offset)
        pe_header = handle.read(24)
        if len(pe_header) != 24 or pe_header[:4] != b"PE\0\0":
            raise ValueError("invalid PE signature")
        optional_size = struct.unpack_from("<H", pe_header, 20)[0]
        optional = handle.read(optional_size)

    if len(optional) < 52:
        raise ValueError("truncated PE optional header")
    magic_value = struct.unpack_from("<H", optional, 0)[0]
    magic_names = {0x10B: "PE32", 0x20B: "PE32+"}
    if magic_value not in magic_names:
        raise ValueError("unsupported PE optional header")
    os_version = struct.unpack_from("<HH", optional, 40)
    subsystem_version = struct.unpack_from("<HH", optional, 48)
    return PeCompatibility(
        path=os.path.normpath(path),
        magic=magic_names[magic_value],
        operating_system=_version_text(os_version),
        subsystem=_version_text(subsystem_version),
    )


def check_compatibility(
    paths: Iterable[str], maximum: Tuple[int, int]
) -> List[PeCompatibility]:
    results = []
    for path in paths:
        item = read_compatibility(path)
        operating_system = parse_version(item.operating_system)
        subsystem = parse_version(item.subsystem)
        if operating_system > maximum or subsystem > maximum:
            raise ValueError(
                "{} requires Windows {} / subsystem {}, above {}".format(
                    item.path,
                    item.operating_system,
                    item.subsystem,
                    _version_text(maximum),
                )
            )
        results.append(item)
    return results


def main(argv: Sequence[str] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maximum-version", default="6.1")
    parser.add_argument("--runtime-directory", action="append", default=[])
    parser.add_argument("files", nargs="+")
    args = parser.parse_args(argv)
    try:
        maximum = parse_version(args.maximum_version)
        for directory in args.runtime_directory:
            check_runtime_directory(directory)
        results = check_compatibility(args.files, maximum)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps([asdict(item) for item in results], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
