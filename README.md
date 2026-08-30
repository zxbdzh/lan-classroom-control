<div align="center">

# 局域网机房控制系统 v2

[English](README.en.md)

**面向学校机房和培训教室的 Windows 局域网 C/S 管控系统。** 教师端以座位拓扑管理 20–60 台固定学生机，学生端由 Windows 服务和登录会话代理协作运行。

<sub>// 屏幕广播 · 黑屏肃静 · 网络管控 · 文件分发 · 应用策略</sub>

<br />

![Python](https://img.shields.io/badge/Python-3.8-3776ab?logo=python&logoColor=white)
![PyQt5](https://img.shields.io/badge/PyQt5-5.15.2-41cd52)
![Platform](https://img.shields.io/badge/Windows-7%20SP1%20%7C%2010%20x64-0078d4?logo=windows&logoColor=white)
![Tests](https://img.shields.io/badge/tests-pytest%20%C3%9796-f2a1c1)
![License](https://img.shields.io/badge/License-MIT-green)

</div>

---

## 功能

- 座位图：按主机名尾号初排，支持拖动、空座、行列设置和全班/分组/已选作用域。
- 屏幕广播：20 FPS MJPEG UDP 组播，800ms 未收到首帧时使用独立 TCP 媒体通道降级；广播期间可锁定学生键鼠。
- 巡视与远控：主屏 640×360 / 1 FPS 缩略图，聚焦和远控为 720p / 15 FPS，UDP 上行同时提供有界单槽 TCP 备用链路，支持全屏查看。
- 黑屏肃静：覆盖学生全部显示器、显示教师提示语并锁定键鼠。
- 网络管控：通过 Windows 防火墙阻止外部网段，同时保留局域网、教师地址、发现/媒体通道、文件分发和配置的内网 CIDR。
- 文件分发：文件/文件夹队列、SHA-256、断点续传、安全 ZIP 解压，服务模式固定保存到公共桌面“课堂文件”，不会自动执行。
- 应用策略：单机进程列表，按 PID + 创建时间安全结束进程；可按程序名和可选完整路径设置永久黑名单。
- 设备维护：关机、重启、注销、网络唤醒和设备扫描。
- 操作记录：命令级和设备级状态，SQLite/WAL 持久化，保留 90 天并可导出 CSV。
- 主题：浅色默认，支持深色；目标分辨率为 1366×768，支持 100%–125% 缩放。

## 系统要求

- Windows 7 SP1 x64 或 Windows 10 x64。
- Python 3.8（源码运行）；发布冻结运行时固定为官方 CPython 3.8.10；PyQt5 5.15.2。
- 学生端安装和服务运行需要管理员权限。
- Windows 7 需要 KB4490628、KB4474419 和 KB2999226（Universal C Runtime）。

Win7/Win10 真机兼容性必须在发布前按机型验证。当前自动化开发环境为 Windows 11，不能替代该项验证。

## 运行

```powershell
uv sync --locked --python 3.8 --group dev
uv run teacher
uv run student
```

生产部署使用安装器。学生端生产架构为：

```text
Windows 服务（网络/防火墙/进程/电源/教师连接）
    ↕ Windows 命名管道
登录会话代理（覆盖层/截屏/键鼠/远控输入，默认无托盘）
```

## 教师端页面

教师端包含“教室、巡视、文件、应用策略、设备维护、操作记录、设置”七个工作区。顶部始终显示作用域和常用课堂命令；批量关机、重启、注销和永久应用策略需要确认。

## 网络端口

| 端口 | 协议 | 用途 |
|---|---|---|
| 9527 | UDP | 教师发现广播 |
| 9528 | TCP | 控制、心跳、ACK |
| 9529 | UDP | 教师屏幕组播 |
| 9530 | UDP | 学生巡视画面上行 |
| 9531 | TCP | 媒体/批量降级通道 |

默认组播地址为 `239.255.95.29`。

## 控制协议

v2 状态命令使用稳定命令 ID、单调修订号、300ms/700ms 重试、`received`/`applied` 双阶段 ACK、设备级结果、去重和命令覆盖。高频远控输入使用同一 TCP 通道即时发送，不进入审计和重试队列，但学生端仅在远控租约有效时执行。广播、肃静、网络限制、巡视、远控和键鼠锁属于临时控制；连续 10 秒收不到教师心跳即自动恢复。永久应用黑名单不会被断线、“全部恢复”或重启清除。

## 安全边界

本版本按项目决策采用自动发现、自动连接、无身份认证和明文控制通道。任何能进入同一局域网并访问端口的主机都可能伪装教师或观察/篡改流量。系统只能部署在隔离且受信任的教学 VLAN，禁止映射到公网。

发布更新包必须同时具备 SHA-256 和 Ed25519 清单签名。发布私钥不得提交到仓库；仓库只包含学生端信任的公钥。

## 测试

```powershell
uv run --locked --group dev pytest tests -q
```

测试覆盖协议校验、媒体分片/重组与 TCP 降级、命令重试/去重/租约、SQLite 状态、进程保护、网络规则、断点文件传输、签名更新、服务 IPC、师生集成和 Qt 离屏布局。

本机真实 UDP 组播传输门禁（默认 96 KiB/帧、20 FPS、100 帧，要求投递率不低于 99%、P95 不高于 100 ms）：

```powershell
uv run --locked python scripts/v2_network_probe.py
```

该命令验证本机真实 socket、分片和重组路径，不代替交换机及 20–60 台终端现场测试。

## 构建发布

发布工作流生成独立教师/学生安装器。学生端分别打包服务和会话代理，安装器注册自动服务和登录启动项。构建完成后，Actions 创建 Release 草稿，以 `RELEASE.md` 作为前言并追加 GitHub 自动生成的 Release Notes，审核后再人工发布。同一标签的任务重跑会覆盖草稿附件并保留首次生成的说明；Release 一经正式发布，工作流将拒绝覆盖。创建 `v*` 标签前需在 GitHub Actions 配置 `UPDATE_SIGNING_PRIVATE_KEY_B64`，内容为与 [update_public_key.pem](common/update_public_key.pem) 对应私钥 PEM 的 Base64。

Release 构建还会解析教师端、学生服务、会话代理、Python/Qt 核心和安装器的 PE 头；任何目标的最低 OS 或子系统版本高于 Windows 7 的 `6.1` 都会直接阻止发布。该静态门禁不能替代 Win7/Win10 真机安装与运行验收。

本地生成新密钥（仅初始化信任根时执行）：

```powershell
uv run python scripts/release_manifest.py keygen `
  --private update-private.pem `
  --public common/update_public_key.pem
```

私钥应立即移入凭据系统，并从工作目录安全删除。
