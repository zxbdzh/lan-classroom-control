import struct

import pytest

from scripts.check_pe_compat import (
    check_compatibility,
    check_runtime_directory,
    exclude_host_ucrt,
    parse_version,
    read_compatibility,
)


def write_pe(path, os_version=(6, 0), subsystem_version=(6, 0), magic=0x20B):
    pe_offset = 128
    data = bytearray(512)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, pe_offset)
    data[pe_offset:pe_offset + 4] = b"PE\0\0"
    struct.pack_into("<H", data, pe_offset + 20, 96)
    optional = pe_offset + 24
    struct.pack_into("<H", data, optional, magic)
    struct.pack_into("<HH", data, optional + 40, *os_version)
    struct.pack_into("<HH", data, optional + 48, *subsystem_version)
    path.write_bytes(data)


def test_reads_pe_os_and_subsystem_versions(tmp_path):
    binary = tmp_path / "student.exe"
    write_pe(binary, os_version=(6, 0), subsystem_version=(6, 1))

    result = read_compatibility(str(binary))

    assert result.magic == "PE32+"
    assert result.operating_system == "6.0"
    assert result.subsystem == "6.1"


def test_rejects_binary_above_windows_7_minimum(tmp_path):
    binary = tmp_path / "student.exe"
    write_pe(binary, os_version=(6, 2), subsystem_version=(6, 1))

    with pytest.raises(ValueError, match="above 6.1"):
        check_compatibility([str(binary)], (6, 1))


def test_rejects_invalid_version_and_non_pe_file(tmp_path):
    invalid = tmp_path / "invalid.exe"
    invalid.write_bytes(b"not a PE")

    with pytest.raises(ValueError, match="major.minor"):
        parse_version("6")
    with pytest.raises(ValueError, match="not a PE"):
        read_compatibility(str(invalid))


def test_pe_minimum_alone_does_not_allow_host_ucrt(tmp_path):
    binary = tmp_path / "ucrtbase.dll"
    write_pe(binary, os_version=(6, 1), subsystem_version=(6, 1))

    check_compatibility([str(binary)], (6, 1))
    with pytest.raises(ValueError, match="host UCRT"):
        check_runtime_directory(str(tmp_path))


def test_runtime_filter_preserves_python_and_visual_cpp_dependencies(tmp_path):
    names = [
        "UCRTBASE.dll", "api-ms-win-crt-runtime-l1-1-0.dll",
        "python38.dll", "VCRUNTIME140.dll", "_socket.pyd",
    ]
    binaries = [(name, str(tmp_path / name), "BINARY") for name in names]

    assert [entry[0] for entry in exclude_host_ucrt(binaries)] == names[2:]
