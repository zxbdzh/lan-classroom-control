import json
import ipaddress
import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from typing import List, Optional
from common.logger import get_logger

logger = get_logger("net_control")

RULE_NAME_PREFIX = "LanClassroomNetControl"
RULE_GROUP = "LanClassroomControlV2"
WEB_PORTS_TCP = "80,443"
WEB_PORTS_UDP = "443"
BROWSER_PROCESS_NAMES = [
    "chrome.exe",
    "msedge.exe",
    "firefox.exe",
    "iexplore.exe",
    "opera.exe",
    "opera_gx.exe",
    "brave.exe",
    "vivaldi.exe",
    "browser.exe",
    "qqbrowser.exe",
    "360se.exe",
    "360chrome.exe",
    "sogouexplorer.exe",
    "liebao.exe",
    "maxthon.exe",
]


class NetController:
    def __init__(self):
        self._blocked = False
        self._lock = threading.Lock()
        self._policy_lock = threading.RLock()
        self._whitelist_ips: List[str] = []
        self.last_error = ""
        self._state_file = os.path.join(
            os.path.expanduser("~"), ".lan_classroom", "net_control_state.json"
        )

    def block_internet(self, whitelist_ips: Optional[List[str]] = None) -> bool:
        with self._policy_lock:
            return self._block_internet(whitelist_ips)

    def _block_internet(self, whitelist_ips: Optional[List[str]] = None) -> bool:
        with self._lock:
            requested = list(whitelist_ips or [])
            # Windows 规则可能被系统或其他软件清理，重复下发必须重新检查并应用。
            if sys.platform != "win32" and self._blocked and requested == self._whitelist_ips:
                return True
            self._whitelist_ips = requested
        self.last_error = ""
        if sys.platform == "win32":
            result = self._block_windows()
        else:
            result = self._block_linux()
        if result:
            with self._lock:
                self._blocked = True
            logger.info("Internet blocked successfully")
        else:
            logger.error("Internet block failed")
        return result

    def unblock_internet(self) -> bool:
        with self._policy_lock:
            return self._unblock_internet()

    def _unblock_internet(self) -> bool:
        with self._lock:
            was_blocked = self._blocked
        if sys.platform == "win32":
            result = self._unblock_windows()
        elif not was_blocked:
            return True
        else:
            result = self._unblock_linux()
        if result:
            with self._lock:
                self._blocked = False
                self._whitelist_ips = []
            logger.info("Internet unblocked successfully")
        else:
            logger.error("Internet unblock failed")
        return result

    def is_blocked(self) -> bool:
        with self._lock:
            return self._blocked

    def _run_cmd(self, cmd: List[str], log_failure: bool = True) -> bool:
        try:
            kwargs = {
                "capture_output": True,
                "text": True,
                "timeout": 15,
            }
            if sys.platform == "win32":
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                kwargs["creationflags"] = creationflags
            result = subprocess.run(cmd, **kwargs)
            if result.returncode == 0:
                return True
            else:
                if log_failure:
                    logger.error(f"Command failed (exit {result.returncode}): {' '.join(cmd)}")
                    logger.error(f"  stdout: {result.stdout.strip()}")
                    logger.error(f"  stderr: {result.stderr.strip()}")
                return False
        except subprocess.TimeoutExpired:
            logger.error(f"Command timeout: {' '.join(cmd)}")
            return False
        except Exception as e:
            logger.error(f"Command error: {e}, cmd: {' '.join(cmd)}")
            return False

    def _block_windows(self) -> bool:
        if not self._check_admin():
            self.last_error = "禁网需要学生端系统服务或管理员权限"
            return False
        try:
            with self._windows_policy() as (policy, dispatch):
                if int(policy.LocalPolicyModifyState) != 0:
                    raise RuntimeError("域策略禁止本地防火墙规则生效")
                for profile in (1, 2, 4):
                    enabled = policy._oleobj_.Invoke(
                        policy._oleobj_.GetIDsOfNames("FirewallEnabled"),
                        0, 2, True, profile,
                    )
                    if not enabled:
                        raise RuntimeError("请先启用 Windows 防火墙的全部网络配置")
                allowed = self._allowed_networks(self._whitelist_ips)
                ranges = self._external_networks(allowed)
                self._remove_policy_rules(policy)
                # 阻断规则优先于允许规则，因此只阻断白名单的补集，保留教师连接。
                for version in (4, 6):
                    networks = [
                        value for value in ranges
                        if ipaddress.ip_network(value).version == version
                    ]
                    if not networks:
                        raise ValueError("禁网白名单不能覆盖全部 IPv4 或 IPv6 地址")
                    for index, remote_ips in enumerate(self._chunk_remote_ips(networks)):
                        rule = dispatch("HNetCfg.FWRule")
                        rule.Name = f"{RULE_NAME_PREFIX}_BlockExternal_{version}_{index}"
                        rule.Description = "课堂禁网；解除课堂控制时自动移除"
                        rule.Grouping = RULE_GROUP
                        rule.Protocol = 256
                        rule.Direction = 2
                        rule.Action = 0
                        rule.Profiles = 0x7FFFFFFF
                        rule.RemoteAddresses = remote_ips
                        rule.Enabled = True
                        policy.Rules.Add(rule)
                        saved = policy.Rules.Item(rule.Name)
                        if not saved.Enabled or int(saved.Action) != 0:
                            raise RuntimeError("防火墙未保存阻断规则")
            return True
        except Exception as exc:
            self.last_error = str(exc)
            logger.error(f"Cannot apply network policy: {exc}")
            if not self._cleanup_windows_rules():
                # 清理失败时仍视为有残留，租约恢复必须继续重试。
                with self._lock:
                    self._blocked = True
            else:
                with self._lock:
                    self._blocked = False
            return False

    @staticmethod
    @contextmanager
    def _windows_policy():
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        try:
            yield win32com.client.Dispatch("HNetCfg.FwPolicy2"), win32com.client.Dispatch
        finally:
            pythoncom.CoUninitialize()

    @staticmethod
    def _remove_policy_rules(policy) -> None:
        names = [
            str(rule.Name) for rule in policy.Rules
            if str(rule.Name).startswith(RULE_NAME_PREFIX)
            or str(rule.Grouping) == RULE_GROUP
        ]
        for name in names:
            policy.Rules.Remove(name)
        if any(
            str(rule.Name).startswith(RULE_NAME_PREFIX)
            or str(rule.Grouping) == RULE_GROUP
            for rule in policy.Rules
        ):
            raise RuntimeError("课堂防火墙规则清理失败")

    def _unblock_windows(self) -> bool:
        if not self._check_admin():
            logger.warning("Cannot fully unblock: no administrator privilege, trying anyway")

        restored = self._restore_windows_firewall_policy()
        cleaned = self._cleanup_windows_rules()
        return restored and cleaned

    def _normalize_whitelist_ips(self, ips: List[str]) -> List[str]:
        result = []
        for ip in ips:
            ip = str(ip).strip()
            if ip and ip not in result:
                result.append(ip)
        return result

    def _allowed_networks(self, values: List[str]) -> List[str]:
        defaults = [
            "127.0.0.0/8",
            "169.254.0.0/16",
            "224.0.0.0/4",
            "255.255.255.255/32",
            "::1/128",
            "fe80::/10",
            "ff00::/8",
        ]
        normalized = []
        for value in defaults + self._normalize_whitelist_ips(values):
            try:
                network = ipaddress.ip_network(str(value), strict=False)
                text = str(network)
                if text not in normalized:
                    normalized.append(text)
            except ValueError:
                raise ValueError(f"禁网白名单必须是有效的 IP 或 CIDR 网段：{value}")
            if network.prefixlen == 0:
                raise ValueError("禁网白名单不能填写 0.0.0.0/0 或 ::/0，否则会放行全部外网")
        return normalized

    @staticmethod
    def _external_networks(allowed: List[str]) -> List[str]:
        result = []
        for version, universe in (
            (4, ipaddress.ip_network("0.0.0.0/0")),
            (6, ipaddress.ip_network("::/0")),
        ):
            remaining = [universe]
            networks = [
                ipaddress.ip_network(value, strict=False)
                for value in allowed
                if ipaddress.ip_network(value, strict=False).version == version
            ]
            for allowed_network in networks:
                next_remaining = []
                for candidate in remaining:
                    if not candidate.overlaps(allowed_network):
                        next_remaining.append(candidate)
                    elif allowed_network.supernet_of(candidate) or allowed_network == candidate:
                        continue
                    elif candidate.supernet_of(allowed_network):
                        next_remaining.extend(candidate.address_exclude(allowed_network))
                remaining = next_remaining
            result.extend(str(network) for network in remaining)
        return result

    @staticmethod
    def _chunk_remote_ips(networks: List[str], max_chars: int = 3000) -> List[str]:
        chunks = []
        current = []
        current_length = 0
        for network in networks:
            added = len(network) + (1 if current else 0)
            if current and current_length + added > max_chars:
                chunks.append(",".join(current))
                current = []
                current_length = 0
            current.append(network)
            current_length += len(network) + (1 if len(current) > 1 else 0)
        if current:
            chunks.append(",".join(current))
        return chunks

    def _cleanup_windows_rules(self) -> bool:
        try:
            with self._windows_policy() as (policy, _dispatch):
                self._remove_policy_rules(policy)
            return True
        except Exception as exc:
            self.last_error = str(exc)
            logger.error(f"Cannot remove classroom firewall rules: {exc}")
            return False

    def restore_stale_rules(self) -> bool:
        if sys.platform != "win32" or not self._check_admin():
            return False
        restored = self._restore_windows_firewall_policy()
        cleaned = self._cleanup_windows_rules()
        if restored and cleaned:
            with self._lock:
                self._blocked = False
                self._whitelist_ips = []
        return restored and cleaned

    def _add_windows_web_block_rules(self) -> bool:
        rules = [
            (
                f"{RULE_NAME_PREFIX}_BlockWebTCP",
                "TCP",
                WEB_PORTS_TCP,
                "Block HTTP and HTTPS over TCP",
            ),
            (
                f"{RULE_NAME_PREFIX}_BlockWebUDP",
                "UDP",
                WEB_PORTS_UDP,
                "Block QUIC over UDP 443",
            ),
            (
                f"{RULE_NAME_PREFIX}_BlockDnsUDP",
                "UDP",
                "53",
                "Block DNS over UDP",
            ),
            (
                f"{RULE_NAME_PREFIX}_BlockDnsTCP",
                "TCP",
                "53",
                "Block DNS over TCP",
            ),
        ]
        for name, protocol, ports, description in rules:
            if not self._run_cmd([
                "netsh", "advfirewall", "firewall", "add", "rule",
                f"name={name}",
                "dir=out",
                "action=block",
                f"protocol={protocol}",
                f"remoteport={ports}",
                "remoteip=any",
                "enable=yes",
                "profile=any",
                f"description={description}",
            ]):
                return False
        return True

    def _add_windows_browser_block_rules(self) -> int:
        browser_paths = self._find_windows_browser_paths()
        added = 0
        for idx, path in enumerate(browser_paths[:128]):
            rule_name = f"{RULE_NAME_PREFIX}_BlockBrowser_{idx}"
            if self._run_cmd([
                "netsh", "advfirewall", "firewall", "add", "rule",
                f"name={rule_name}",
                "dir=out",
                "action=block",
                f"program={path}",
                "enable=yes",
                "profile=any",
                "description=Block browser network access while LanClassroom net control is enabled",
            ], log_failure=False):
                added += 1
        logger.info(f"Browser block rules added: {added}/{len(browser_paths)}")
        return added

    def _find_windows_browser_paths(self) -> List[str]:
        roots = [
            os.environ.get("ProgramFiles", ""),
            os.environ.get("ProgramFiles(x86)", ""),
            os.environ.get("LocalAppData", ""),
        ]
        relative_paths = [
            ("Google", "Chrome", "Application", "chrome.exe"),
            ("Microsoft", "Edge", "Application", "msedge.exe"),
            ("Mozilla Firefox", "firefox.exe"),
            ("Internet Explorer", "iexplore.exe"),
            ("Opera", "launcher.exe"),
            ("Opera GX", "launcher.exe"),
            ("BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
            ("Vivaldi", "Application", "vivaldi.exe"),
            ("Tencent", "QQBrowser", "QQBrowser.exe"),
            ("360", "360se6", "Application", "360se.exe"),
            ("360Chrome", "Chrome", "Application", "360chrome.exe"),
            ("SogouExplorer", "SogouExplorer.exe"),
            ("Maxthon", "Application", "Maxthon.exe"),
        ]

        found = []
        seen = set()
        for root in roots:
            if not root:
                continue
            for parts in relative_paths:
                path = os.path.join(root, *parts)
                if os.path.exists(path):
                    key = path.lower()
                    if key not in seen:
                        seen.add(key)
                        found.append(path)
        return found

    def _save_windows_firewall_policy(self):
        if os.path.exists(self._state_file):
            return
        policies = self._read_windows_firewall_policies()
        try:
            os.makedirs(os.path.dirname(self._state_file), exist_ok=True)
            with open(self._state_file, "w", encoding="utf-8") as f:
                json.dump({"policies": policies}, f)
        except Exception as e:
            logger.warning(f"Save firewall policy state failed: {e}")

    def _read_windows_firewall_policies(self) -> dict:
        default_policy = "blockinbound,allowoutbound"
        policies = {
            "domainprofile": default_policy,
            "privateprofile": default_policy,
            "publicprofile": default_policy,
        }
        try:
            result = subprocess.run(
                ["netsh", "advfirewall", "show", "allprofiles"],
                capture_output=True, text=True, timeout=15
            )
            current_profile = None
            for line in result.stdout.splitlines():
                stripped = line.strip()
                lower = stripped.lower()
                if lower.startswith("domain profile"):
                    current_profile = "domainprofile"
                elif lower.startswith("private profile"):
                    current_profile = "privateprofile"
                elif lower.startswith("public profile"):
                    current_profile = "publicprofile"
                elif current_profile and "firewall policy" in lower:
                    policy = stripped.split()[-1].lower().replace(" ", "")
                    if "," in policy:
                        policies[current_profile] = policy
        except Exception as e:
            logger.warning(f"Read firewall policy failed, using default restore policy: {e}")
        return policies

    def _restore_windows_firewall_policy(self) -> bool:
        if not os.path.exists(self._state_file):
            return True
        policies = {
            "domainprofile": "blockinbound,allowoutbound",
            "privateprofile": "blockinbound,allowoutbound",
            "publicprofile": "blockinbound,allowoutbound",
        }
        try:
            if os.path.exists(self._state_file):
                with open(self._state_file, "r", encoding="utf-8") as f:
                    state = json.load(f)
                policies.update(state.get("policies", {}))
        except Exception as e:
            logger.warning(f"Read firewall policy state failed, using default restore policy: {e}")

        success = True
        for profile, policy in policies.items():
            if not self._run_cmd([
                "netsh", "advfirewall", "set", profile, "firewallpolicy", policy
            ]):
                success = False

        try:
            if success and os.path.exists(self._state_file):
                os.remove(self._state_file)
        except Exception as e:
            logger.warning(f"Remove firewall policy state failed: {e}")
        return success

    def _block_linux(self) -> bool:
        import os
        if os.geteuid() != 0:
            logger.error("Cannot block internet: no root privilege")
            return False
        try:
            cmds = [
                ["iptables", "-N", "LAN_CLASSROOM"],
                ["iptables", "-A", "LAN_CLASSROOM", "-j", "DROP"],
                ["iptables", "-I", "OUTPUT", "1", "-p", "tcp", "--dport", "80", "-j", "LAN_CLASSROOM"],
                ["iptables", "-I", "OUTPUT", "1", "-p", "tcp", "--dport", "443", "-j", "LAN_CLASSROOM"],
                ["iptables", "-I", "OUTPUT", "1", "-p", "udp", "--dport", "53", "-j", "LAN_CLASSROOM"],
            ]
            for ip in self._whitelist_ips:
                cmds.insert(1, [
                    "iptables", "-I", "LAN_CLASSROOM", "1", "-d", ip, "-j", "ACCEPT"
                ])
            all_ok = True
            for cmd in cmds:
                if not self._run_cmd(cmd):
                    all_ok = False
            return all_ok
        except Exception as e:
            logger.error(f"Linux block internet failed: {e}")
            return False

    def _unblock_linux(self) -> bool:
        try:
            cmds = [
                ["iptables", "-D", "OUTPUT", "-p", "tcp", "--dport", "80", "-j", "LAN_CLASSROOM"],
                ["iptables", "-D", "OUTPUT", "-p", "tcp", "--dport", "443", "-j", "LAN_CLASSROOM"],
                ["iptables", "-D", "OUTPUT", "-p", "udp", "--dport", "53", "-j", "LAN_CLASSROOM"],
                ["iptables", "-F", "LAN_CLASSROOM"],
                ["iptables", "-X", "LAN_CLASSROOM"],
            ]
            for cmd in cmds:
                self._run_cmd(cmd)
            return True
        except Exception as e:
            logger.error(f"Linux unblock internet failed: {e}")
            return False

    def _check_admin(self) -> bool:
        if sys.platform == "win32":
            try:
                import ctypes
                return ctypes.windll.shell32.IsUserAnAdmin() != 0
            except Exception:
                return False
        else:
            import os
            return os.geteuid() == 0

    def check_admin_privilege(self) -> bool:
        return self._check_admin()
