"""实测 VirtualBox MCP 握手和只读工具，不在宿主执行管控动作。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
from datetime import datetime, timezone


def probe(python: str, server: str, report_directory: Path) -> dict:
    launcher = Path(__file__).with_name("run_on_private_desktop.py")
    report_directory.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update({
        "VBOXWEB_AUTOSTART": "false",
        "VBOXMANAGE_DEBUG": "false",
        "SCREENSHOT_DIR": str(report_directory / "screenshots"),
    })
    process = subprocess.Popen(
        [python, str(launcher), "--", server],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    messages: queue.Queue = queue.Queue()
    errors = []

    def read_messages() -> None:
        try:
            for line in process.stdout:
                messages.put(json.loads(line))
        except Exception as error:
            messages.put({"reader_error": str(error)})
        finally:
            messages.put({"stream_closed": True})

    def read_errors() -> None:
        for line in process.stderr:
            errors.append(line.rstrip())

    readers = [
        threading.Thread(target=read_messages, daemon=True),
        threading.Thread(target=read_errors, daemon=True),
    ]
    for reader in readers:
        reader.start()

    request_id = 0

    def send(method: str, params: dict, notification: bool = False) -> dict:
        nonlocal request_id
        message = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            request_id += 1
            message["id"] = request_id
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()
        if notification:
            return {}
        while True:
            reply = messages.get(timeout=30)
            if "reader_error" in reply or "stream_closed" in reply:
                raise RuntimeError(f"MCP transport closed: {reply}; {errors}")
            if reply.get("id") == request_id:
                if "error" in reply:
                    raise RuntimeError(str(reply["error"]))
                return reply["result"]

    report = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "scope": "mcp_handshake_and_read_only_host_probe",
        "guest_tests": {
            "windows7_boot": "not_tested",
            "background_screenshot": "not_tested",
            "guest_keyboard_mouse": "not_tested",
            "guest_command_execution": "not_tested",
            "internet_block_and_restore": "not_tested",
        },
    }
    try:
        report["initialize"] = send("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "classroom-vbox-probe", "version": "1.0.0"},
        })
        send("notifications/initialized", {}, notification=True)
        tools = send("tools/list", {})
        (report_directory / "tools.json").write_text(
            json.dumps(tools, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        report["tool_names"] = [tool["name"] for tool in tools["tools"]]
        required_tools = {
            "vm_screenshot", "vm_keyboard_type", "vm_keyboard_scancode",
            "vm_mouse_click", "vm_guest_exec", "vm_guest_copy_to", "vm_list",
        }
        report["required_tools_present"] = required_tools.issubset(report["tool_names"])
        # 这里只读真实列表；缺少 VBoxManage 时保留错误，绝不用 dry-run 冒充成功。
        report["vm_list"] = send("tools/call", {"name": "vm_list", "arguments": {}})
        report["protocol_passed"] = report["required_tools_present"]
        report["virtualbox_available"] = not report["vm_list"].get("isError", False)
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        for reader in readers:
            reader.join(timeout=2)
        report["stderr"] = errors
        (report_directory / "mcp-probe.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True)
    parser.add_argument("--server", required=True)
    parser.add_argument("--report-directory", required=True, type=Path)
    args = parser.parse_args()
    result = probe(args.python, args.server, args.report_directory.resolve())
    print(json.dumps({
        "protocol_passed": result["protocol_passed"],
        "tool_count": len(result["tool_names"]),
        "virtualbox_available": result["virtualbox_available"],
        "vm_list": result["vm_list"],
        "report": str(args.report_directory / "mcp-probe.json"),
    }, ensure_ascii=False, indent=2))
