import hashlib
from types import SimpleNamespace
from unittest.mock import patch
import uuid

import pytest

from common.device_identity import machine_fingerprint, normalize_mac


@pytest.mark.parametrize("value", [
    "02:10:20:30:40:AB", "02-10-20-30-40-ab", "0210.2030.40ab", "0210203040ab",
])
def test_mac_formats_have_the_same_identity(value):
    assert normalize_mac(value) == "0210203040ab"


@pytest.mark.parametrize("value", ["", None, "00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff", "not-a-mac"])
def test_missing_or_invalid_mac_is_not_an_identity(value):
    assert normalize_mac(value) == ""


@pytest.mark.parametrize("identifier", [
    "00000000-0000-0000-0000-000000000000",
    "ffffffff-ffff-ffff-ffff-ffffffffffff", "not-a-uuid",
])
def test_unavailable_firmware_uuid_does_not_merge_every_machine(identifier):
    with patch("common.device_identity.os", SimpleNamespace(name="nt")), patch(
        "pythoncom.CoInitialize"
    ), patch("pythoncom.CoUninitialize"), patch("win32com.client.GetObject") as wmi:
        wmi.return_value.ExecQuery.return_value = [SimpleNamespace(UUID=identifier)]
        assert machine_fingerprint() == ""


def test_fingerprint_is_stable_and_does_not_expose_raw_uuid():
    identifier = uuid.UUID("9a828b62-1204-4f51-a39b-c87bd8f01db7")
    with patch("common.device_identity.os", SimpleNamespace(name="nt")), patch(
        "pythoncom.CoInitialize"
    ), patch("pythoncom.CoUninitialize") as cleanup, patch("win32com.client.GetObject") as wmi:
        wmi.return_value.ExecQuery.return_value = [SimpleNamespace(UUID=str(identifier))]
        assert machine_fingerprint() == "smbios:" + hashlib.sha256(identifier.bytes).hexdigest()
        cleanup.assert_called_once_with()


def test_unavailable_wmi_keeps_legacy_identity_path_usable():
    with patch("common.device_identity.os", SimpleNamespace(name="nt")), patch(
        "pythoncom.CoInitialize"
    ), patch("pythoncom.CoUninitialize"), patch(
        "win32com.client.GetObject", side_effect=RuntimeError("WMI unavailable")
    ):
        assert machine_fingerprint() == ""


def test_wmi_objects_are_released_before_uninitializing_com():
    released = []

    class Products:
        def __iter__(self):
            yield SimpleNamespace(UUID="9a828b62-1204-4f51-a39b-c87bd8f01db7")

        def __del__(self):
            released.append("products")

    class Provider:
        def ExecQuery(self, _query):
            return Products()

    def teardown():
        assert released == ["products"]

    with patch("common.device_identity.os", SimpleNamespace(name="nt")), patch(
        "pythoncom.CoInitialize"
    ), patch("pythoncom.CoUninitialize", side_effect=teardown), patch(
        "win32com.client.GetObject", side_effect=lambda _path: Provider()
    ):
        assert machine_fingerprint().startswith("smbios:")
