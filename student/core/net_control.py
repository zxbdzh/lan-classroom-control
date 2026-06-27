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

    def _run_cmd(self, cmd: List[str]) -> bool:
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

        rule_name = f"{RULE_NAME_PREFIX}_BlockAllTCP"
        cmd = [
            "netsh", "advfirewall", "firewall", "add", "rule",
            f"name={rule_name}",
            "dir=out",
            "action=block",
            "protocol=TCP",
            "remoteport=80,443,8080,8443",
            "enable=yes",
            "profile=any",
        ]
        if not self._run_cmd(cmd):
            return False

        rule_name_udp = f"{RULE_NAME_PREFIX}_BlockDNS"
        cmd_dns = [
            "netsh", "advfirewall", "firewall", "add", "rule",
            f"name={rule_name_udp}",
            "dir=out",
            "action=block",
            "protocol=UDP",
            "remoteport=53",
            "enable=yes",
            "profile=any",
        ]
        self._run_cmd(cmd_dns)

        if self._whitelist_ips:
            for idx, ip in enumerate(self._whitelist_ips):
                allow_rule = f"{RULE_NAME_PREFIX}_Allow_{idx}"
                allow_cmd = [
                    "netsh", "advfirewall", "firewall", "add", "rule",
                    f"name={allow_rule}",
                    "dir=out",
                    "action=allow",
                    "protocol=any",
                    f"remoteip={ip}",
                    "enable=yes",
                    "profile=any",
                ]
                self._run_cmd(allow_cmd)

        return True

    def _unblock_windows(self) -> bool:
        if not self._check_admin():
            logger.warning("Cannot fully unblock: no administrator privilege, trying anyway")

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
