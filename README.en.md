<div align="center">

# LAN Classroom Control v2

[中文](README.md)

**A Windows LAN client/server control system for school computer labs and training rooms.** The teacher console manages 20–60 fixed student machines with a seat-topology layout; the student side runs as a Windows service cooperating with a logon-session agent.

<sub>// Screen broadcast · Blackout · Network control · File distribution · App policies</sub>

<br />

![Python](https://img.shields.io/badge/Python-3.8-3776ab?logo=python&logoColor=white)
![PyQt5](https://img.shields.io/badge/PyQt5-5.15.2-41cd52)
![Platform](https://img.shields.io/badge/Windows-7%20SP1%20%7C%2010%20x64-0078d4?logo=windows&logoColor=white)
![Tests](https://img.shields.io/badge/tests-pytest%20%C3%97363-f2a1c1)
![License](https://img.shields.io/badge/License-MIT-green)

</div>

---

## Features

- **Seat map**: initial placement by hostname suffix; drag, empty seats, row/column settings, and whole-class / group / selected scopes
- **Screen broadcasting**: 20 FPS MJPEG over UDP multicast; falls back to a dedicated TCP media channel if no first frame arrives within 800ms; student keyboard/mouse can be locked during broadcast
- **Patrol & remote control**: 640×360 / 1 FPS thumbnails on the main screen; focus and remote control at 720p / 15 FPS; UDP uplink with a bounded single-slot TCP backup; fullscreen view
- **Blackout**: covers all student displays, shows a teacher message, locks keyboard/mouse
- **Network control**: blocks external subnets via Windows Firewall while keeping the LAN, teacher address, discovery/media channels, file distribution and configured internal CIDRs
- **File distribution**: file/folder queue, SHA-256, resume support, safe ZIP extraction; service mode saves to the public desktop "classroom files" folder and never auto-executes
- **App policies**: per-machine process list; safe process kill by PID + creation time; permanent blacklist by program name and optional full path
- **Device maintenance**: shutdown, reboot, logoff, Wake-on-LAN, device scan
- **Operation log**: command-level and device-level status; SQLite/WAL persistence, 90-day retention, CSV export
- **Theme**: light by default, dark supported; target resolution 1366×768, 100%–125% scaling

## Requirements

- Windows 7 SP1 x64 or Windows 10 x64
- Python 3.8 (source runs); the frozen runtime pins official CPython 3.8.10; PyQt5 5.15.2
- Student-side installation and service management require administrator privileges
- Windows 7 needs KB4490628, KB4474419 and KB2999226 (Universal C Runtime)

Win7/Win10 real-hardware compatibility must be verified per machine model before release. The current automated dev environment is Windows 11 and cannot substitute for that verification.

## Run

```powershell
uv sync --locked --python 3.8 --group dev
uv run teacher
uv run student
```

Production deployment uses the installer; see the repo for the student-side service architecture.

## License

[MIT](./LICENSE)
