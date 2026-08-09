from types import SimpleNamespace
import threading

import pytest

from student.core.v2.lease import TemporaryControlKind, TemporaryControlLease
from student.core.v2.power import (
    PowerAction,
    PowerController,
    WakeOnLanSender,
    build_magic_packet,
)


class ManualClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_temporary_control_lease_restores_at_ten_seconds():
    clock = ManualClock()
    restored = []
    lease = TemporaryControlLease(timeout=10.0, clock=clock)
    lease.activate(TemporaryControlKind.BROADCAST, lambda: restored.append("broadcast"))
    lease.activate(TemporaryControlKind.INTERNET_BLOCK, lambda: restored.append("network"))

    clock.advance(9.999)
    assert lease.check_expired() == []
    clock.advance(0.001)
    results = lease.check_expired()

    assert restored == ["broadcast", "network"]
    assert all(result.success for result in results)
    assert lease.active_controls() == ()


def test_heartbeat_renews_temporary_control_deadline():
    clock = ManualClock()
    restored = []
    lease = TemporaryControlLease(timeout=10.0, clock=clock)
    lease.activate(TemporaryControlKind.SILENCE, lambda: restored.append(True))

    clock.advance(8.0)
    assert lease.renew() == 18.0
    clock.advance(3.0)

    assert lease.check_expired() == []
    assert restored == []


def test_failed_restore_is_retried():
    clock = ManualClock()
    attempts = []
    lease = TemporaryControlLease(timeout=10.0, check_interval=0.5, clock=clock)

    def restore():
        attempts.append(len(attempts) + 1)
        return len(attempts) > 1

    lease.activate(TemporaryControlKind.REMOTE_CONTROL, restore)
    clock.advance(10.0)
    first = lease.check_expired()
    clock.advance(0.5)
    second = lease.check_expired()

    assert not first[0].success
    assert second[0].success
    assert attempts == [1, 2]


def test_concurrent_restore_attempts_invoke_callback_once():
    clock = ManualClock()
    entered = threading.Event()
    release = threading.Event()
    calls = []
    results = []
    lease = TemporaryControlLease(timeout=10.0, clock=clock)

    def restore():
        calls.append("restore")
        entered.set()
        release.wait(1.0)

    lease.activate(TemporaryControlKind.BROADCAST, restore)
    clock.advance(10.0)
    first = threading.Thread(target=lambda: results.append(lease.check_expired()))
    second = threading.Thread(target=lambda: results.append(lease.check_expired()))
    first.start()
    assert entered.wait(1.0)
    second.start()
    release.set()
    first.join(1.0)
    second.join(1.0)

    assert calls == ["restore"]
    assert sum(len(item) for item in results) == 1


def test_permanent_blacklist_cannot_be_registered_as_temporary_control():
    lease = TemporaryControlLease()

    with pytest.raises(ValueError, match="not a temporary control"):
        lease.activate("blacklist", lambda: None)


def test_power_controller_is_disabled_until_explicitly_enabled():
    calls = []
    controller = PowerController(
        allow_execution=False,
        runner=lambda *args, **kwargs: calls.append(args),
        platform="win32",
    )

    result = controller.shutdown()

    assert not result.success
    assert calls == []


@pytest.mark.parametrize(
    "action, expected",
    [
        (PowerAction.SHUTDOWN, ["shutdown.exe", "/s", "/t", "0"]),
        (PowerAction.RESTART, ["shutdown.exe", "/r", "/t", "0"]),
        (PowerAction.LOGOFF, ["shutdown.exe", "/l"]),
    ],
)
def test_power_commands_are_mockable(action, expected):
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stderr="")

    controller = PowerController(True, runner=runner, platform="win32")

    result = controller.execute(action)

    assert result.success
    assert calls[0][0] == expected
    assert calls[0][1]["timeout"] == 10


def test_magic_packet_has_standard_layout():
    packet = build_magic_packet("00:11:22:33:44:55")

    assert len(packet) == 102
    assert packet[:6] == b"\xff" * 6
    assert packet[6:] == bytes.fromhex("001122334455") * 16


def test_wol_sender_broadcasts_requested_repetitions():
    class FakeSocket:
        def __init__(self):
            self.options = []
            self.sent = []
            self.closed = False

        def setsockopt(self, *args):
            self.options.append(args)

        def sendto(self, packet, destination):
            self.sent.append((packet, destination))

        def close(self):
            self.closed = True

    fake_socket = FakeSocket()
    sender = WakeOnLanSender(lambda *args: fake_socket)

    result = sender.send("001122334455", "192.168.1.255", 9, repetitions=2)

    assert result.success
    assert result.packets_sent == 2
    assert [item[1] for item in fake_socket.sent] == [
        ("192.168.1.255", 9),
        ("192.168.1.255", 9),
    ]
    assert fake_socket.closed


def test_invalid_magic_packet_mac_is_rejected():
    with pytest.raises(ValueError):
        build_magic_packet("not-a-mac")
