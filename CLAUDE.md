# CLAUDE.md - 局域网机房控制系统

> 此文件为 AI 助手（Claude）提供项目上下文，描述项目架构、约定和开发规范。

## 项目概述

**局域网机房控制系统** — 面向学校机房/培训机构的局域网管控 C/S 系统。

- **教师端**：控制中心，管理所有学生终端
- **学生端**：被控终端，静默运行于系统托盘

### 核心功能

| 功能 | 说明 |
|------|------|
| 屏幕广播 | 教师屏幕实时广播，广播期间禁用学生键鼠 |
| 黑屏肃静 | 一键黑屏 + 键鼠锁定 |
| 网络管控 | 系统防火墙级别禁用/启用学生上网（Windows: netsh） |
| 文件分发 | 教师一键下发文件给全体或指定学生，MD5 校验 |
| 自动发现 | UDP 广播自动发现教师端，学生端一键接入 |

---

## 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| 语言 | Python 3.10+ | 主开发语言 |
| GUI | PyQt5 | 跨平台桌面 UI（兼容 Win7） |
| 截屏 | mss + Pillow | 高性能跨平台截屏 |
| 键鼠禁用 | ctypes BlockInput (Win) / pynput (其他) | 系统级键鼠拦截 |
| 网络管控 | netsh (Win) / iptables (Linux) | 系统防火墙规则 |
| 加密 | pycryptodome (AES-256-CBC) | 通信加密预留 |
| 通信 | TCP + UDP | UDP 发现 + TCP 可靠传输 |
| 测试 | pytest + pytest-mock | 96 个测试用例 |

---

## 项目结构

```
lan-classroom-control/
├── common/                  # 公共模块（教师端和学生端共用）
│   ├── protocol.py          # 协议定义：消息类型枚举、序列化/反序列化、二进制帧头
│   ├── crypto.py            # AES-256-CBC 加密解密、密码派生、大数据分块处理
│   ├── config.py            # JSON 配置管理，嵌套键访问，自动持久化
│   ├── logger.py            # 日志：控制台+文件双输出，RotatingFileHandler
│   ├── discover.py          # UDP 发现：TeacherDiscover（广播）+ StudentDiscoverListener（监听+去重）
│   ├── tcp_conn.py          # TCP 连接管理：TCPServer/TCPClient/TCPConnection，粘包半包处理
│   ├── heartbeat.py         # 心跳：TeacherHeartbeatManager（超时检测）+ StudentHeartbeatSender
│   ├── screen_capture.py    # 截屏：ScreenCapturer（mss截屏+JPEG压缩）+ FrameDiffCalculator（帧差）
│   └── file_transfer.py     # 文件传输：FileTransferSender + FileTransferReceiver，分块+MD5校验
│
├── teacher/                 # 教师端
│   ├── main.py              # 入口：创建 QApplication + TeacherMainWindow
│   ├── core/
│   │   ├── teacher_server.py    # 核心调度：管理 TCP Server、发现、心跳、广播、文件分发
│   │   ├── student_manager.py   # 学生管理：CRUD、分组、搜索、MAC 绑定、回调
│   │   ├── screen_broadcast.py  # 屏幕广播：截屏→JPEG→TCP 发送，支持指定/全体学生
│   │   └── file_distributor.py  # 文件分发：一对多并发传输，进度回调
│   └── ui/
│       └── main_window.py       # PyQt5 主窗口：工具栏+学生树+屏幕墙+右键菜单，深色主题
│
├── student/                 # 学生端
│   ├── main.py              # 入口：StudentApp 类，系统托盘运行，关闭窗口不退出
│   ├── core/
│   │   ├── student_client.py    # 核心客户端：指令分发，自动发现教师，状态管理
│   │   ├── input_blocker.py     # 键鼠禁用：Windows BlockInput + pynput fallback
│   │   └── net_control.py       # 网络管控：Windows 防火墙 / Linux iptables
│   └── ui/
│       └── overlay.py           # 全屏覆盖层：黑屏/广播画面，无边框置顶，拦截关闭事件
│
├── tests/                   # 测试（96 用例）
│   ├── conftest.py          # 公共 fixture：sys.path 设置
│   ├── test_protocol.py      # 协议层：15 用例
│   ├── test_crypto.py        # 加密：12 用例
│   ├── test_config.py        # 配置：7 用例
│   ├── test_discover.py      # UDP 发现：5 用例
│   ├── test_tcp_connection.py# TCP 连接：8 用例
│   ├── test_heartbeat.py     # 心跳：7 用例
│   ├── test_student_manager.py# 学生管理：17 用例
│   ├── test_file_transfer.py # 文件传输：9 用例
│   ├── test_input_blocker.py# 键鼠禁用：5 用例（mock）
│   ├── test_net_control.py   # 网络管控：8 用例（mock）
│   └── test_integration.py   # 集成测试：7 用例
│
├── .github/
│   └── workflows/
│       └── release.yml       # CI/CD：自动打包发布
│
├── CLAUDE.md                # 本文件
├── README.md                # 用户文档
├── requirements.txt         # 依赖
└── .gitignore
```

---

## 架构设计

### 通信协议

```
教师端 (TCP Server :9528) ←──── TCP ────→ 学生端 (TCP Client)
         │
         └── UDP 广播 (:9527) ──→ 学生端监听
```

**消息格式**：JSON 消息 + 4字节长度头

```
[4字节 body长度][4字节 reserved][JSON body]
```

**消息类型**（`common/protocol.py` 的 `MessageType` 枚举）：
- `student_register` / `student_heartbeat` — 学生注册与心跳
- `teacher_discover` — UDP 发现广播
- `black_screen` — 黑屏控制
- `broadcast_start` / `broadcast_stop` / `broadcast_frame` — 屏幕广播
- `net_control` — 网络管控
- `file_send_start` / `file_send_data` / `file_send_end` / `file_send_ack` — 文件传输

### 数据流

**屏幕广播流程**：
1. 教师点击"开始广播" → `TeacherServer.start_broadcast()`
2. `ScreenBroadcaster` 启动截屏线程（mss，20FPS，JPEG quality=70）
3. 每帧 JPEG 数据通过 TCP 发送给目标学生的 `TCPConnection`
4. 学生端 `StudentClient._handle_broadcast_frame()` → `OverlayWindow.update_frame()`
5. 停止广播时同时解除键鼠禁用

**键鼠禁用策略**（广播+黑屏均触发）：
- Windows 优先：`ctypes.windll.user32.BlockInput(True)`
- Fallback：`pynput` 键鼠监听器（`suppress=True`）

### 线程模型

- **主线程**：PyQt5 事件循环（UI）
- **TCP accept 线程**：接受新连接
- **每连接 recv 线程**：独立接收消息
- **心跳发送线程**：学生端定时发送心跳
- **心跳检测线程**：教师端定时检测超时
- **截屏线程**：教师端截屏循环
- **广播线程**：教师端帧分发
- **文件传输线程**：独立线程逐块发送
- **重连线程**：学生端断线自动重连

---

## 开发规范

### Git 提交规范（Conventional Commits）

```
<type>(<scope>): <description>

[optional body]
```

**类型**：
| type | 说明 |
|------|------|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `docs` | 文档变更 |
| `style` | 代码格式（不影响逻辑） |
| `refactor` | 重构 |
| `test` | 测试相关 |
| `chore` | 构建/工具/依赖 |
| `ci` | CI/CD 配置 |

**scope**（可选）：
- `common` — 公共模块
- `teacher` — 教师端
- `student` — 学生端
- `ui` — 界面
- `protocol` — 协议
- `crypto` — 加密
- `test` — 测试

**示例**：
```
feat(teacher): add screen broadcast to selected students
fix(student): resolve overlay window not closing on disconnect
test(common): add sticky packet handling test for TCP
ci: add GitHub Actions release workflow
```

### 编码约定

1. **类型注解**：所有公共函数使用 Python type hints
2. **日志**：使用 `common.logger.get_logger("module_name")`，不使用 print
3. **线程安全**：共享状态使用 `threading.Lock`
4. **配置访问**：通过 `common.config.get_config()` 单例
5. **异常处理**：网络操作必须 try/except，记录日志，不抛出到上层
6. **文件路径**：所有路径使用 `os.path`，不硬编码分隔符

### 运行测试

```bash
cd /workspace/lan-classroom-control
python -m pytest tests/ -v
```

### 启动应用

```bash
# 教师端
python teacher/main.py

# 学生端（需管理员权限以启用完整功能）
python student/main.py
```

---

## 关键配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `teacher.udp_broadcast_port` | 9527 | UDP 发现广播端口 |
| `teacher.tcp_port` | 9528 | TCP 主控通道端口 |
| `teacher.heartbeat_timeout` | 15 | 心跳超时秒数 |
| `teacher.broadcast_fps` | 20 | 屏幕广播帧率 |
| `teacher.broadcast_quality` | 70 | JPEG 压缩质量 (1-100) |
| `student.auto_connect` | true | 自动连接发现的教师端 |
| `student.heartbeat_interval` | 5 | 心跳发送间隔秒数 |

配置文件路径：`~/.lan_classroom/config.json`

---

## 已知限制

1. **仅 Windows 完整功能**：键鼠禁用（BlockInput）和网络管控（netsh 防火墙）仅在 Windows 完整实现
2. **无加密通道**：当前通信未启用 AES 加密（预留接口）
3. **无身份认证**：学生端注册无需密码验证
4. **单教师**：不支持多教师同时管理同一批学生
5. **屏幕广播为全量**：增量帧差计算已实现但未启用

---

## 打包发布

使用 GitHub Actions 自动打包（见 `.github/workflows/release.yml`）：
- 触发条件：创建 tag `v*`
- 产出：`teacher-setup.exe`（教师端安装包）、`student-setup.exe`（学生端安装包）
- PyInstaller 打包，单目录模式
