"""教师端跨设备应用规则的程序名约束。"""

PROTECTED_PROCESS_NAMES = frozenset({
    "system", "system idle process", "registry", "smss.exe", "csrss.exe",
    "wininit.exe", "services.exe", "lsass.exe", "winlogon.exe", "svchost.exe",
    "fontdrvhost.exe", "dwm.exe", "explorer.exe", "lanclassroom-student.exe",
    "lanclassroom-student-service.exe", "lanclassroom-student-agent.exe", "winvnc.exe",
})


def normalize_program_name(program: str) -> str:
    value = program.strip().strip('"').casefold()
    if (not value.endswith(".exe") or len(value) <= 4
            or any(character in value for character in '\\/:*?"<>|')
            or any(ord(character) < 32 for character in value)):
        raise ValueError("请填写程序名，例如 chrome.exe；不支持路径、网址或通配符")
    if value in PROTECTED_PROCESS_NAMES:
        raise ValueError("该程序属于受保护的系统或课堂控制进程，不能禁用")
    return value
