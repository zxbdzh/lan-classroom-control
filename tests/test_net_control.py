import ipaddress
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from student.core.net_control import NetController, RULE_NAME_PREFIX


class FakeRules:
    def __init__(self):
        self.values = {}
        self.fail_add = False
        self.fail_remove = False

    def __iter__(self):
        return iter(list(self.values.values()))

    def Add(self, rule):
        if self.fail_add:
            raise RuntimeError("rule rejected")
        self.values[rule.Name] = rule

    def Item(self, name):
        return self.values[name]

    def Remove(self, name):
        if self.fail_remove:
            raise RuntimeError("access denied")
        self.values.pop(name, None)


@pytest.fixture
def firewall(monkeypatch, tmp_path):
    policy = SimpleNamespace(
        LocalPolicyModifyState=0, Rules=FakeRules(), _oleobj_=MagicMock()
    )
    policy._oleobj_.Invoke.return_value = True

    @contextmanager
    def fake_policy():
        yield policy, lambda _name: SimpleNamespace()

    monkeypatch.setattr(NetController, "_windows_policy", staticmethod(fake_policy))
    monkeypatch.setattr(NetController, "_check_admin", lambda _self: True)
    controller = NetController()
    controller._state_file = str(tmp_path / "net_state.json")
    return controller, policy


def test_block_ipv4_ipv6_keeps_only_teacher_and_explicit_allowed_networks(firewall):
    controller, policy = firewall
    with patch("sys.platform", "win32"):
        assert controller.block_internet(["192.168.1.2", "2001:db8::2"])
    networks = [
        ipaddress.ip_network(value)
        for rule in policy.Rules
        for value in rule.RemoteAddresses.split(",")
    ]
    for address, blocked in (
        ("8.8.8.8", True), ("2606:4700:4700::1111", True),
        ("192.168.1.2", False), ("2001:db8::2", False),
        ("192.168.1.3", True), ("10.0.0.1", True), ("127.0.0.1", False),
    ):
        assert any(ipaddress.ip_address(address) in network for network in networks) == blocked
    assert all(rule.Protocol == 256 and rule.Direction == 2 and rule.Action == 0
               for rule in policy.Rules)
    assert controller.is_blocked()


def test_disabled_firewall_and_domain_policy_report_failure(firewall):
    controller, policy = firewall
    with patch("sys.platform", "win32"):
        policy._oleobj_.Invoke.return_value = False
        assert not controller.block_internet(["192.168.1.2"])
        assert "防火墙" in controller.last_error
        policy._oleobj_.Invoke.return_value = True
        policy.LocalPolicyModifyState = 1
        assert not controller.block_internet(["192.168.1.2"])
        assert "域策略" in controller.last_error
    assert not controller.is_blocked()


def test_failed_rule_install_rolls_back(firewall):
    controller, policy = firewall
    policy.Rules.fail_add = True
    with patch("sys.platform", "win32"):
        assert not controller.block_internet(["192.168.1.2"])
    assert not list(policy.Rules)
    assert not controller.is_blocked()


def test_failed_restore_keeps_blocked_state_until_retry(firewall):
    controller, policy = firewall
    with patch("sys.platform", "win32"):
        assert controller.block_internet(["192.168.1.2"])
        policy.Rules.fail_remove = True
        assert not controller.unblock_internet()
        assert controller.is_blocked()
        policy.Rules.fail_remove = False
        assert controller.unblock_internet()
    assert not controller.is_blocked()


def test_restore_removes_legacy_rules_without_touching_other_rules(firewall):
    controller, policy = firewall
    policy.Rules.Add(SimpleNamespace(Name=RULE_NAME_PREFIX + "_Old", Grouping=""))
    policy.Rules.Add(SimpleNamespace(Name="OtherProduct", Grouping=""))
    with patch("sys.platform", "win32"):
        assert controller.restore_stale_rules()
    assert set(policy.Rules.values) == {"OtherProduct"}


def test_repeated_block_and_changed_teacher_whitelist(firewall):
    controller, policy = firewall
    with patch("sys.platform", "win32"):
        assert controller.block_internet(["192.168.1.2"])
        previous = list(policy.Rules)
        assert controller.block_internet(["192.168.1.2"])
        assert list(policy.Rules) == previous
        assert controller.block_internet(["192.168.1.3"])
        assert list(policy.Rules) != previous


def test_repeated_block_repairs_rules_removed_outside_the_client(firewall):
    controller, policy = firewall
    with patch("sys.platform", "win32"):
        assert controller.block_internet(["192.168.1.2"])
        policy.Rules.values.clear()
        assert controller.block_internet(["192.168.1.2"])
    assert list(policy.Rules)


def test_repeated_block_rechecks_disabled_firewall(firewall):
    controller, policy = firewall
    with patch("sys.platform", "win32"):
        assert controller.block_internet(["192.168.1.2"])
        policy._oleobj_.Invoke.return_value = False
        assert not controller.block_internet(["192.168.1.2"])
    assert "防火墙" in controller.last_error


@pytest.mark.parametrize("network", ["0.0.0.0/0", "::/0"])
def test_block_rejects_exemption_that_allows_the_entire_internet(firewall, network):
    controller, policy = firewall
    with patch("sys.platform", "win32"):
        assert not controller.block_internet(["192.168.1.2", network])
    assert "白名单" in controller.last_error
    assert not list(policy.Rules)
    assert not controller.is_blocked()


def test_block_rejects_url_instead_of_silently_ignoring_invalid_exemption(firewall):
    controller, policy = firewall
    with patch("sys.platform", "win32"):
        assert not controller.block_internet(["https://www.baidu.com"])
    assert "IP 或 CIDR" in controller.last_error
    assert not list(policy.Rules)


def test_linux_unblock_success_when_commands_accept():
    controller = NetController()
    controller._blocked = True
    with patch("sys.platform", "linux"), patch.object(
        controller, "_run_cmd", return_value=True
    ):
        assert controller.unblock_internet()
