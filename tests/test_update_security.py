import os
import time

import pytest
from Crypto.PublicKey import ECC

from common.update_security import (
    UpdateVerificationError,
    build_manifest,
    sign_manifest,
    verify_manifest,
)


def test_signed_manifest_verifies_package(tmp_path):
    package = tmp_path / "student.zip"
    package.write_bytes(b"signed update package")
    key = ECC.generate(curve="Ed25519")
    manifest = build_manifest(str(package), "2.0.0", int(time.time()))
    signature = sign_manifest(manifest, key.export_key(format="PEM").encode("ascii"))

    value = verify_manifest(
        manifest,
        signature,
        key.public_key().export_key(format="PEM").encode("ascii"),
        package_path=str(package),
        expected_version="2.0.0",
    )

    assert value["sha256"]
    assert value["size"] == len(b"signed update package")


def test_tampered_package_is_rejected(tmp_path):
    package = tmp_path / "student.zip"
    package.write_bytes(b"original")
    key = ECC.generate(curve="Ed25519")
    manifest = build_manifest(str(package), "2.0.0", 1)
    signature = sign_manifest(manifest, key.export_key(format="PEM").encode("ascii"))
    package.write_bytes(b"tampered")

    with pytest.raises(UpdateVerificationError, match="SHA-256"):
        verify_manifest(
            manifest,
            signature,
            key.public_key().export_key(format="PEM").encode("ascii"),
            package_path=str(package),
        )


def test_manifest_signature_cannot_be_reused_after_edit(tmp_path):
    package = tmp_path / "student.zip"
    package.write_bytes(b"package")
    key = ECC.generate(curve="Ed25519")
    manifest = build_manifest(str(package), "2.0.0", 1)
    signature = sign_manifest(manifest, key.export_key(format="PEM").encode("ascii"))

    with pytest.raises(UpdateVerificationError, match="signature"):
        verify_manifest(
            manifest.replace(b"2.0.0", b"2.0.1"),
            signature,
            key.public_key().export_key(format="PEM").encode("ascii"),
        )
