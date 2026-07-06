import json
import os
import subprocess
import sys
import threading
from typing import List, Optional
from common.logger import get_logger

logger = get_logger("net_control")

RULE_NAME_PREFIX = "LanClassroomNetControl"
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
        self._whitelist_ips: List[str] = []
        self._state_file = os.path.join(
            os.path.expanduser("~"), ".lan_classroom", "net_control_state.json"
        )

    def block_internet(self, whitelist_ips: Optional[List[str]] = None) -> bool:
        with self._lock:
            if self._blocked:
                return True
            self._whitelist_ips = whitelist_ips or []
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
        with self._lock:
            if not self._blocked:
                return True
        if sys.platform == "win32":
            result = self._unblock_windows()
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
            logger.error("Cannot block internet: no administrator privilege")
            return False

        self._cleanup_windows_rules()
        self._save_windows_firewall_policy()

        for idx, ip in enumerate(self._normalize_whitelist_ips(self._whitelist_ips)):
            allow_rule = f"{RULE_NAME_PREFIX}_AllowTeacher_{idx}"
            if not self._run_cmd([
                "netsh", "advfirewall", "firewall", "add", "rule",
                f"name={allow_rule}",
                "dir=out",
                "action=allow",
                "protocol=any",
                f"remoteip={ip}",
                "enable=yes",
                "profile=any",
            ]):
                return False

        if not self._add_windows_web_block_rules():
            return False
        self._add_windows_browser_block_rules()

        return self._run_cmd([
            "netsh", "advfirewall", "set", "allprofiles",
            "firewallpolicy", "blockinbound,blockoutbound"
        ])

    def _unblock_windows(self) -> bool:
        if not self._check_admin():
            logger.warning("Cannot fully unblock: no administrator privilege, trying anyway")

        restored = self._restore_windows_firewall_policy()
        self._cleanup_windows_rules()
        return restored

        try:
            result = subprocess.run(
                ["netsh", "advfirewall", "firewall", "show", "rule", "name=all"],
                capture_output=True, text=True, timeout=15
            )
            lines = result.stdout.splitlines()
            rule_names = []
            for line in lines:
                if "规则名称:" in line or "Rule Name:" in line:
                    name = line.split(":", 1)[1].strip()
                    if name.startswith(RULE_NAME_PREFIX):
                        rule_names.append(name)

            success = True
            for name in rule_names:
                if not self._run_cmd([
                    "netsh", "advfirewall", "firewall", "delete", "rule", f"name={name}"
                ]):
                    success = False
            return success
        except Exception as e:
            logger.error(f"Windows unblock internet failed: {e}")
            return False

    def _normalize_whitelist_ips(self, ips: List[str]) -> List[str]:
        result = []
        for ip in ips:
            ip = str(ip).strip()
            if ip and ip not in result:
                result.append(ip)
        return result

    def _cleanup_windows_rules(self) -> bool:
        known_rules = [
            f"{RULE_NAME_PREFIX}_BlockAllTCP",
            f"{RULE_NAME_PREFIX}_BlockDNS",
            f"{RULE_NAME_PREFIX}_BlockWebTCP",
            f"{RULE_NAME_PREFIX}_BlockWebUDP",
            f"{RULE_NAME_PREFIX}_BlockDnsUDP",
            f"{RULE_NAME_PREFIX}_BlockDnsTCP",
        ]
        known_rules.extend(f"{RULE_NAME_PREFIX}_Allow_{idx}" for idx in range(32))
        known_rules.extend(f"{RULE_NAME_PREFIX}_AllowTeacher_{idx}" for idx in range(32))
        known_rules.extend(f"{RULE_NAME_PREFIX}_BlockBrowser_{idx}" for idx in range(128))

        for name in known_rules:
            self._run_cmd([
                "netsh", "advfirewall", "firewall", "delete", "rule", f"name={name}"
            ], log_failure=False)
        return True

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
