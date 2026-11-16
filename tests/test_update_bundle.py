"""签名更新包须可单文件导入，失败原因不能伪装成离线设备。"""
import base64
import json
import os
import struct
import warnings
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from Crypto.PublicKey import ECC

from common.update_bundle import build_update_bundle, open_update_package
from common.update_security import UpdateVerificationError, build_manifest, sign_manifest
from teacher.core.teacher_server import TeacherServer
from teacher.core.update_server import UpdateServer


@pytest.fixture
def signed_package(tmp_path):
    package = tmp_path / "LanClassroom-Student.zip"
    with zipfile.ZipFile(str(package), "w") as archive:
        archive.writestr("Service/student.exe", b"student executable")
    key = ECC.generate(curve="Ed25519")
    manifest = build_manifest(str(package), "2.1.2", 1)
    signature = sign_manifest(manifest, key.export_key(format="PEM").encode("ascii"))
    public_key = key.public_key().export_key(format="PEM").encode("ascii")
    package.with_name(package.name + ".manifest.json").write_bytes(manifest)
    package.with_name(package.name + ".sig").write_text(signature, encoding="ascii")
    bundle = tmp_path / "LanClassroom-Student-Update.zip"
    _write_bundle(bundle, {package.name: package.read_bytes(), "manifest.json": manifest,
                           "manifest.sig": signature.encode("ascii")})
    return package, bundle, public_key, manifest, signature


def _write_bundle(path, entries):
    with zipfile.ZipFile(str(path), "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)


def test_single_zip_is_verified_and_transmits_original_payload(signed_package):
    package, bundle, key, manifest, signature = signed_package
    manager, distributor = MagicMock(), MagicMock()
    server = UpdateServer(manager, distributor)
    try:
        with patch("teacher.core.update_server.load_public_key", return_value=key):
            assert server.set_update_package(str(bundle), "2.1.2") is True
        assert server.update_file_path != str(bundle)
        assert Path(server.update_file_path).read_bytes() == package.read_bytes()
        assert server.update_manifest == manifest
        assert server.update_signature == signature
        conn = MagicMock(student_id="pc-01")
        assert server.on_update_request(conn, {"current_version": "2.1.0"})
        assert distributor.send_file_to_students.call_args.kwargs["file_path"] == server.update_file_path
        extracted = server.update_file_path
    finally:
        server.clear_update_package()
    assert not os.path.exists(extracted)


def test_detached_legacy_update_remains_supported(signed_package):
    package, _, key, _, _ = signed_package
    server = UpdateServer(MagicMock(), MagicMock())
    with patch("teacher.core.update_server.load_public_key", return_value=key):
        assert server.set_update_package(str(package), "2.1.2")
    assert server.update_file_path == str(package)
    server.clear_update_package()
    assert package.exists()


def test_invalid_signature_is_not_reported_as_zero_online_devices(signed_package):
    package, _, key, _, _ = signed_package
    package.with_name(package.name + ".sig").write_text("invalid", encoding="ascii")
    server = TeacherServer.__new__(TeacherServer)
    server.update_server = UpdateServer(MagicMock(), MagicMock())
    with patch("teacher.core.update_server.load_public_key", return_value=key):
        with pytest.raises(UpdateVerificationError, match="签名"):
            server.push_update(str(package), "2.1.2", ["pc-01"])


@pytest.mark.parametrize("entry", ["LanClassroom-Student.zip", "manifest.sig", "manifest.json"])
def test_tampered_bundle_is_rejected(signed_package, entry):
    package, bundle, key, manifest, signature = signed_package
    entries = {package.name: package.read_bytes(), "manifest.json": manifest,
               "manifest.sig": signature.encode("ascii")}
    # 等长修改载荷，确保不只是靠 ZIP 大小不一致来拒绝篡改。
    entries[entry] = bytes([entries[entry][0] ^ 1]) + entries[entry][1:]
    _write_bundle(bundle, entries)
    server = UpdateServer(MagicMock(), MagicMock())
    with patch("teacher.core.update_server.load_public_key", return_value=key):
        assert not server.set_update_package(str(bundle), "2.1.2")
    assert server.update_file_path is None


def test_release_builder_round_trips_without_changing_signed_payload(signed_package, tmp_path):
    package, _, key, manifest, signature = signed_package
    output = tmp_path / "单文件更新包.zip"
    build_update_bundle(str(package), str(output), key)
    with zipfile.ZipFile(str(output)) as archive:
        assert set(archive.namelist()) == {package.name, "manifest.json", "manifest.sig"}
        assert archive.read(package.name) == package.read_bytes()
    loaded = open_update_package(str(output), key)
    try:
        assert loaded.metadata["version"] == "2.1.2"
        assert loaded.manifest == manifest
        assert loaded.signature == signature
    finally:
        loaded.close()


def test_release_builder_cannot_overwrite_signed_payload(signed_package):
    package, _, key, _, _ = signed_package
    original = package.read_bytes()
    with pytest.raises(ValueError, match="覆盖"):
        build_update_bundle(str(package), str(package), key)
    assert package.read_bytes() == original


def test_bundle_requires_the_expected_version_and_trusted_key(signed_package):
    _, bundle, key, _, _ = signed_package
    with pytest.raises(UpdateVerificationError, match="version"):
        open_update_package(str(bundle), key, "2.1.3")
    other = ECC.generate(curve="Ed25519").public_key().export_key(format="PEM").encode("ascii")
    with pytest.raises(UpdateVerificationError, match="signature"):
        open_update_package(str(bundle), other)


@pytest.mark.parametrize("kind", ["duplicate", "extra", "missing", "oversized", "traversal"])
def test_malformed_bundle_is_rejected_before_extracting_payload(signed_package, kind):
    package, bundle, key, manifest, signature = signed_package
    entries = {package.name: package.read_bytes(), "manifest.json": manifest,
               "manifest.sig": signature.encode("ascii")}
    if kind == "extra":
        entries["unexpected.exe"] = b"unexpected"
    elif kind == "missing":
        entries.pop("manifest.sig")
    elif kind == "oversized":
        entries["manifest.json"] = b"x" * (65536 + 1)
    elif kind == "traversal":
        # 用测试密钥签名危险路径，验证路径校验并非仅依赖验签失败。
        signer = ECC.generate(curve="Ed25519")
        key = signer.public_key().export_key(format="PEM").encode("ascii")
        data = json.loads(manifest)
        data["package"] = "../outside.zip"
        entries["../outside.zip"] = entries.pop(package.name)
        entries["manifest.json"] = json.dumps(data).encode("utf-8")
        entries["manifest.sig"] = sign_manifest(
            entries["manifest.json"], signer.export_key(format="PEM").encode("ascii"),
        ).encode("ascii")
    _write_bundle(bundle, entries)
    if kind == "duplicate":
        with zipfile.ZipFile(str(bundle), "a") as archive, warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            archive.writestr("manifest.sig", signature)
    with patch("common.update_bundle.tempfile.TemporaryDirectory") as temporary:
        with pytest.raises((UpdateVerificationError, KeyError)):
            open_update_package(str(bundle), key)
        temporary.assert_not_called()


def test_missing_legacy_sidecar_explains_how_to_select_package(signed_package):
    package, _, key, _, _ = signed_package
    package.with_name(package.name + ".sig").unlink()
    with pytest.raises(UpdateVerificationError, match="同一目录"):
        open_update_package(str(package), key)


def test_zero_online_devices_is_not_a_signature_failure(signed_package):
    _, bundle, key, _, _ = signed_package
    manager = MagicMock()
    manager.get_student.return_value = None
    server = TeacherServer.__new__(TeacherServer)
    server.update_server = UpdateServer(manager, MagicMock())
    try:
        with patch("teacher.core.update_server.load_public_key", return_value=key):
            assert server.push_update(str(bundle), student_ids=["offline"]) == 0
        assert not server.update_server.last_error
    finally:
        server.update_server.clear_update_package()


def test_bundle_payload_passes_existing_student_receiver(signed_package, tmp_path):
    from common.file_transfer import CHUNK_SIZE, FILE_HEADER_FORMAT
    from student.core.update_client import UpdateClient

    package, bundle, key, _, _ = signed_package
    distributor = MagicMock()
    update = UpdateServer(MagicMock(), distributor)
    client = UpdateClient(MagicMock())
    client._temp_dir = str(tmp_path)
    try:
        with patch("teacher.core.update_server.load_public_key", return_value=key):
            assert update.set_update_package(str(bundle))
        assert update.on_update_request(MagicMock(student_id="pc-01"), {})
        params = distributor.send_file_to_students.call_args.kwargs
        data = Path(params["file_path"]).read_bytes()
        count = (len(data) + CHUNK_SIZE - 1) // CHUNK_SIZE
        client.handle_start({**params, "transfer_id": "signed-update",
                             "file_name": package.name, "file_size": len(data),
                             "total_chunks": count, "sha256": update.update_sha256})
        for index in range(count):
            chunk = data[index * CHUNK_SIZE:(index + 1) * CHUNK_SIZE]
            encoded = struct.pack(FILE_HEADER_FORMAT, count, index, len(chunk)) + chunk
            assert client.handle_data({"transfer_id": "signed-update", "chunk_index": index,
                                       "data": base64.b64encode(encoded).decode("ascii")})
        with patch("student.core.update_client.load_public_key", return_value=key), \
                patch.object(client, "_apply_update", return_value=True) as apply:
            assert client.handle_end({"transfer_id": "signed-update"})
        received, version = apply.call_args.args
        assert Path(received).read_bytes() == package.read_bytes()
        assert version == "2.1.2"
    finally:
        client.cancel()
        update.clear_update_package()


def test_notify_does_not_count_a_failed_socket_send(signed_package):
    _, bundle, key, _, _ = signed_package
    manager = MagicMock()
    student = MagicMock(student_id="pc-01", online=True)
    student.conn.send_message.return_value = False
    manager.get_online_students.return_value = [student]
    server = UpdateServer(manager, MagicMock())
    try:
        with patch("teacher.core.update_server.load_public_key", return_value=key):
            assert server.set_update_package(str(bundle))
        assert server.notify_update() == 0
        assert not server.get_update_progress()
    finally:
        server.clear_update_package()
