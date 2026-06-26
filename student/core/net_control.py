import subprocess
import sys
import threading
from typing import List, Optional
from common.logger import get_logger

logger = get_logger("net_control")

RULE_NAME_PREFIX = "LanClassroomNetControl"


class NetController:
    def __init__(self):
        self._blocked = False
        self._lock = threading.Lock()
        self._whitelist_ips: List[str] = []

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
            logger.info("Internet blocked")
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
            logger.info("Internet unblocked")
        return result

    def is_blocked(self) -> bool:
        with self._lock:
            return self._blocked

    def _block_windows(self) -> bool:
        try:
            rule_name = f"{RULE_NAME_PREFIX}_BlockHTTP"
            cmd = [
                "netsh", "advfirewall", "firewall", "add", "rule",
                f"name={rule_name}",
                "dir=out",
                "action=block",
                "protocol=TCP",
                "remoteport=80,443",
                "enable=yes"
            ]
            subprocess.run(cmd, capture_output=True, text=True, timeout=10)

            if self._whitelist_ips:
                for ip in self._whitelist_ips:
                    allow_rule = f"{RULE_NAME_PREFIX}_Allow_{ip.replace('.', '_')}"
                    allow_cmd = [
                        "netsh", "advfirewall", "firewall", "add", "rule",
                        f"name={allow_rule}",
                        "dir=out",
                        "action=allow",
                        "protocol=any",
                        f"remoteip={ip}",
                        "enable=yes"
                    ]
                    subprocess.run(allow_cmd, capture_output=True, text=True, timeout=10)
            return True
        except Exception as e:
            logger.error(f"Windows block internet failed: {e}")
            return False

    def _unblock_windows(self) -> bool:
        try:
            result = subprocess.run(
                ["netsh", "advfirewall", "firewall", "show", "rule", "name=all"],
                capture_output=True, text=True, timeout=10
            )
            lines = result.stdout.splitlines()
            rule_names = []
            for line in lines:
                if "规则名称:" in line or "Rule Name:" in line:
                    name = line.split(":", 1)[1].strip()
                    if name.startswith(RULE_NAME_PREFIX):
                        rule_names.append(name)
            for name in rule_names:
                subprocess.run(
                    ["netsh", "advfirewall", "firewall", "delete", "rule", f"name={name}"],
                    capture_output=True, text=True, timeout=10
                )
            return True
        except Exception as e:
            logger.error(f"Windows unblock internet failed: {e}")
            return False

    def _block_linux(self) -> bool:
        try:
            cmds = [
                ["iptables", "-N", "LAN_CLASSROOM"],
                ["iptables", "-A", "LAN_CLASSROOM", "-j", "DROP"],
                ["iptables", "-I", "OUTPUT", "1", "-p", "tcp", "--dport", "80", "-j", "LAN_CLASSROOM"],
                ["iptables", "-I", "OUTPUT", "1", "-p", "tcp", "--dport", "443", "-j", "LAN_CLASSROOM"],
            ]
            for ip in self._whitelist_ips:
                cmds.insert(1, [
                    "iptables", "-I", "LAN_CLASSROOM", "1", "-d", ip, "-j", "ACCEPT"
                ])
            for cmd in cmds:
                subprocess.run(cmd, capture_output=True, timeout=5)
            return True
        except Exception as e:
            logger.error(f"Linux block internet failed: {e}")
            return False

    def _unblock_linux(self) -> bool:
        try:
            cmds = [
                ["iptables", "-D", "OUTPUT", "-p", "tcp", "--dport", "80", "-j", "LAN_CLASSROOM"],
                ["iptables", "-D", "OUTPUT", "-p", "tcp", "--dport", "443", "-j", "LAN_CLASSROOM"],
                ["iptables", "-F", "LAN_CLASSROOM"],
                ["iptables", "-X", "LAN_CLASSROOM"],
            ]
            for cmd in cmds:
                subprocess.run(cmd, capture_output=True, timeout=5)
            return True
        except Exception as e:
            logger.error(f"Linux unblock internet failed: {e}")
            return False

    def check_admin_privilege(self) -> bool:
        if sys.platform == "win32":
            try:
                import ctypes
                return ctypes.windll.shell32.IsUserAnAdmin() != 0
            except Exception:
                return False
        else:
            import os
            return os.geteuid() == 0
