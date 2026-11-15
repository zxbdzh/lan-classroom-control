"""在从不激活的 Windows 桌面运行命令，保持标准输入输出透传。"""

from __future__ import annotations

import argparse
import subprocess
import sys
import uuid


def run(command: list[str], timeout_seconds: int) -> int:
    import win32api
    import win32con
    import win32event
    import win32process
    import win32service

    desktop_name = "ClassroomVBox_" + uuid.uuid4().hex
    desktop = win32service.CreateDesktop(
        desktop_name, 0, win32con.GENERIC_ALL, None
    )
    duplicated_handles = []
    process = None
    thread = None
    try:
        startup = win32process.STARTUPINFO()
        # 永不调用 SwitchDesktop；子进程及其意外窗口只能留在此隐藏桌面。
        startup.lpDesktop = "winsta0\\" + desktop_name
        startup.dwFlags = win32con.STARTF_USESTDHANDLES | win32con.STARTF_USESHOWWINDOW
        startup.wShowWindow = win32con.SW_HIDE
        current_process = win32api.GetCurrentProcess()
        for attribute, standard_handle in (
            ("hStdInput", win32api.STD_INPUT_HANDLE),
            ("hStdOutput", win32api.STD_OUTPUT_HANDLE),
            ("hStdError", win32api.STD_ERROR_HANDLE),
        ):
            handle = win32api.DuplicateHandle(
                current_process,
                win32api.GetStdHandle(standard_handle),
                current_process,
                0,
                True,
                win32con.DUPLICATE_SAME_ACCESS,
            )
            duplicated_handles.append(handle)
            setattr(startup, attribute, handle)

        process, thread, _process_id, _thread_id = win32process.CreateProcess(
            command[0], subprocess.list2cmdline(command),
            None, None, True, win32con.CREATE_NO_WINDOW,
            None, None, startup,
        )
        wait_milliseconds = (
            timeout_seconds * 1000 if timeout_seconds else win32event.INFINITE
        )
        if win32event.WaitForSingleObject(process, wait_milliseconds) != win32con.WAIT_OBJECT_0:
            win32process.TerminateProcess(process, 124)
            win32event.WaitForSingleObject(process, 3000)
            return 124
        return win32process.GetExitCodeProcess(process)
    except KeyboardInterrupt:
        if process is not None:
            win32process.TerminateProcess(process, 130)
            win32event.WaitForSingleObject(process, 3000)
        return 130
    finally:
        if thread is not None:
            win32api.CloseHandle(thread)
        if process is not None:
            win32api.CloseHandle(process)
        for handle in duplicated_handles:
            win32api.CloseHandle(handle)
        desktop.CloseDesktop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=0)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    command = arguments.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command or arguments.timeout < 0:
        parser.error("需要可执行程序路径，超时不可为负数")
    return run(command, arguments.timeout)


if __name__ == "__main__":
    sys.exit(main())
