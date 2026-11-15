# VirtualBox + MCP 后台验收环境

记录日期：2026-11-15。**已补测教师端来宾安装和七页主窗口的实际按钮流程，并修复六处问题；完整自动化回归为 357 passed。** 两台学生的远控、黑屏、文件分发、测试程序禁用、禁网恢复均有实际证据。广播另出现一次 Win10 持续黑帧，停止后重新开始才恢复，原因尚未确认；有效签名更新、硬件唤醒与真实 WiFi 仍未实测。不能把这些结果写成全部稳定性通过。此前十二次有效 Viewer 退出样本为 0.80–1.75 秒，不代表输入延迟。

## 教师端安装与主窗口实际验收

本阶段使用独立 NAT Network `ClassroomUILab`（`10.77.15.0/24`）：Win7 学生 `.3`、Win10 学生 `.4`、Win10 教师 `.5`。教师是 Win10 关机快照的链接克隆，克隆中的学生服务已禁用。三台均以 headless 方式运行；只向来宾发送键鼠，未操作宿主窗口、剪贴板、网卡或防火墙。此前宿主实验教师已经停止，本阶段使用已安装的真实教师程序及默认端口，不以实验 HTTP API 代替按钮点击。

| 范围 | 实际结果 |
| --- | --- |
| Win10 教师安装 | 独立来宾首次安装、数次覆盖升级、快捷方式启动与关闭通过；没有额外安装 Python。最新安装日志成功且不要求重启 |
| Win7 教师安装 | 同一最终教师包在 Win7 首次安装后的覆盖升级退出 0，不要求重启；真实窗口启动并发现两台学生，指定 Win10 黑屏及恢复通过。此项不是在 Win7 再重复全部七页链路 |
| 教室与作用域 | 主机名/IP 搜索及清空、分组保存、全班/分组/已选作用域、操作抽屉显隐均实际点击。`UI-Win` 组只包含 Win7；分组黑屏时两台均在线，只有 Win7 黑屏，Win10 保持桌面 |
| 座位与设置 | Win7/Win10 教师均能拖动座位，重开程序后位置保留；Win10 的深色主题、7×11 座位和黑屏文字 `UI-LAB-` 跨升级保留。保留内网 CIDR 最终为空，没有把输入过程中被 IME 吞掉的字符当成保存成功 |
| 黑屏与广播 | 双机黑屏、全部恢复、双机广播与停止均实际显示过对应画面。最终包再次确认 Win7 没有代理防火墙弹窗；Win10 广播有一次未出图，另行记录于下方 |
| 巡视与远控 | 开始/停止巡视、双击全屏查看、从全屏按钮启动原版独立 Viewer、原版信任提示、Viewer 内键鼠、关闭后返回巡视均已测试。Win7/Win10 都有可见画面和输入效果；最新包另确认远控 Win10 时 Win7 保持黑屏，关闭后 Win10 恢复黑屏 |
| 文件页 | 通过实际选择框添加 91 字节文件及含嵌套文件的目录，再点击分发；两个队列实际显示完成。两台共四个落盘文件取回后 SHA-256 与源文件一致。服务模式接收目录为 `C:\Users\Public\Desktop\课堂文件` |
| 应用策略 | 两台分别刷新进程、确认结束自建测试程序、按完整路径加入永久黑名单、重复启动四秒内被结束、解除后重复启动仍存活。切换设备后旧进程表立即清空。结论只适用于测试程序，不能外推为任意软件启动前拦截 |
| 禁网与恢复 | 两台各有可用基线、禁网、恢复三份报告：公网 TCP80/TCP53、UDP DNS 和 HTTP 从通到断再恢复，教师 `.5:9528` 始终可达。禁网规则数为 0→2→0，学生服务持续运行 |
| 设备维护 | 两台分别从“已选”作用域实际注销、重启、关机；注销到登录界面，重启看到启动画面，关机后 VBox 状态为 `poweroff`。重新后台启动、登录后服务自动运行并重新连接教师；未以 applied 回执代替这些系统观察 |
| 扫描、唤醒、更新 | 扫描按钮已点击；唤醒仅确认 1/1 魔术包发送，未验证硬件开机；更新选择框取消及缺少 manifest 的 ZIP 错误提示已测，有效签名更新成功链路未测 |
| 操作记录 | 通过原生保存框实际导出 CSV 并取回；保留原有失败与过期记录，不删除失败来制造全绿 |

### 本阶段修复与回归

1. 关机/重启/注销按钮引用不存在的 `name`，点击会抛出异常；改为正确的 `names`，六个确认/取消回归先失败后通过。
2. Win7 实际广播时出现代理程序的防火墙弹窗；学生安装器补充桌面代理专属入站规则并读回确认存在，最终广播再次检查无弹窗。
3. 应用策略切换设备后保留旧设备进程列表；切换或原设备消失时清空进程表及待处理查询，避免误操作和迟到结果回填，两个回归先失败后通过。
4. 学生服务在会话 0 执行 `shutdown /l` 不能注销实际桌面；改为查找活动会话并调用 `WTSLogoffSession`。先保留实际失败，再在两台从教师按钮复测登录界面和恢复。
5. 深色主题的确认/错误弹窗文字对比不足；将 `QDialog`、`QMessageBox` 纳入深色背景，重新安装后实际检查确认框及错误框。
6. 座位按下时重建控件，销毁了继续接收鼠标移动的拖动源；改为只更新选中样式。回归确认原控件存活；最终教师包在 Win7、Win10 都实测拖动和重开持久化。

最终完整回归 `seat-all-regression.xml`：**357 passed，0 failures/errors/skips，116.362 秒**。针对 UI 的 `seat-drag-green.xml` 为 16 passed。构建采用 CPython 3.8.10，安装器及冻结目录的 PE 6.1 门禁通过；兼容结论以实际来宾运行范围为准。

当前可用包：

- 教师：`.artifacts/candidate-teacher-ui-seats-20261115/installer/LanClassroom-Teacher-Setup.exe`，SHA-256 `0048bb2f411635f6cd685956033ee410e3c2913c23130248ca81142fdff83c8d`。
- 学生：`.artifacts/candidate-teacher-ui-final-20261115/installer/LanClassroom-Student-Setup.exe`，SHA-256 `5fdb906da505fc38f1f089857192137708c207d825ac3a55a4255d83ccd8149b`。
- 包已用于来宾安装，未提交、推送或发布；之前 `candidate-dual-*`、`candidate-teacher-ui-dialogs-*` 为历史候选。

### 遗留与证据边界

- **Win10 广播黑帧尚未根治**：17:22:40 的双机广播回执均 applied，但 Win10 一直没有可见画面，Win7 正常。停止后 17:25:34 重开，Win10 随后有可见画面。原黑帧与重试图均保留，不能根据回执或重试成功改写为首次通过，也不能外推为远控黑帧已经根治。
- Win10 另有此前一次 0x133 蓝屏，以及本阶段启动时黑屏、GuestControl 不响应的情况；定向重置来宾及重启学生服务后恢复，但根因未确认。本轮后续真实电源流程通过不代表该稳定性问题已解决。
- Win7 最终状态探测的 MCP 同步命令仍返回非零/超时，报告已经完整落盘并取回；不将此调用计为正常同步执行。
- 有效签名更新、真实硬件网络唤醒、新设备扫描发现、完整卸载矩阵、真实 WiFi、原失败终端、多屏、目标系统 100 次切换与延迟 P95、HTTPS/QUIC/真实 IPv6 出口、已建立外部连接仍未实测。

证据目录为 `.artifacts/virtualbox-lab/20261115/teacher-ui/`，包括安装日志、`teacher-final.db`（正常关闭后取回，避免 WAL 丢失）、`teacher-win7-final.db`、`ui-audit-final.csv`、六份 `*-network-*.txt`、进程报告和截图 01–32。验收脚本只自动断言报告、哈希和回执；可见画面及系统状态另由实际截图和 VBox 状态核对。收尾时两台学生服务 Running、桌面代理各一、VNC Stopped/进程零、测试进程零、临时规则零、代理入站规则各一，公网 TCP80 恢复；教师期望状态及黑名单均解除。

独立脚本 `F:\tmp\classroom-virtualbox\20261115\tools\validate_teacher_ui_evidence.py` 实际输出 `UI_EVIDENCE_CHECKS_PASSED tests=357 file_hashes=4 network_reports=6 power_applied=6 final_guests=2 audit_rows=55`。生成的 `teacher-ui-validation.json` 明确标为 `partial_pass_with_unresolved_stability_issues`，列出原有一条失败及一条过期的黑屏指令、修复前的注销失败、广播黑帧和未测项，不把报告核验通过写成所有功能稳定性通过。

## 禁网、远控与文件分发组合验收

继续使用下述同一教师及两台 NAT 来宾，未修改产品代码或重建安装包。先取得两台可用出口基线，再同时禁网；禁网时保留原有 Viewer，关闭后重新连接，并通过项目通道向两台同时分发一个目录。最后验证只解除一台及全部恢复。宿主窗口、剪贴板、键鼠、网卡和防火墙均未操作。

| 组合场景 | 实测结果 |
| --- | --- |
| 两台同时禁网 | 原可用的 `1.1.1.1:80`、`8.8.8.8:53`、HTTP NCSI、公网与 NAT UDP DNS 均被阻断；教师 `10.0.2.2:19532` 保持可达，真实回执、后续指令正常 |
| 禁网前已连接的 Viewer | 两台原版 Viewer 仍可更新画面、输入字母 `b`、鼠标操作；MCP 只预先打开来宾运行框，字母与鼠标由 Viewer 输入，不将 MCP 键鼠当作远控输入 |
| 禁网后重新连接 | 两台均关闭原窗口后启动新 Viewer，取得对应来宾的可见画面；Win10 新窗口还通过 Viewer 打开开始菜单及 Escape 关闭 |
| 禁网时同批发文件 | 通过项目通道分发空文件、中文文件、嵌套 8 MiB 文件；两台落盘成功，独立计算的 6 项 SHA-256 全部与夹具一致。MCP 只传测试脚本及取回报告 |
| 指定 Win7 解除 | Win7 的相同 TCP、HTTP、两种 UDP DNS 探测恢复，Win10 相同探测继续被阻断；两台 Viewer 仍在 |
| 全部恢复与收尾 | 两台恢复出网；学生服务 Running，VNC Stopped、进程数 0、临时规则 0，仅保留必要入站规则；教师无活动 Viewer |

本轮新增四次真实退出计时：Win7 **0.828 / 0.860 秒**，Win10 **0.860 / 0.906 秒**，第一项均在禁网中退出原窗口，第二项退出重新连接的窗口。不将其解释为首屏或输入延迟。8 MiB 夹具内容可高度压缩，实际目录传输约 33 KiB，不能据此推算大文件吞吐。

异常保留：Win7 首次全部恢复后，公网 UDP DNS 单次超时，但同次 TCP、HTTP、NAT DNS 已恢复且禁网规则为 0；未更改策略，重复相同探测后公网 DNS 返回 65 字节。保留 `probe-win7-combo-final-restored.txt` 和 `*-retry.txt`，原因未确认，不把重测通过写成零失败。部分 Win7 PowerShell 同步 MCP 调用超时，但完整报告已落盘并取回；Win10 的一次 18 秒禁网探测没有报告，改用 45 秒上限的新标签后实际 21 秒完成。工具调用失败与实际网络结果分开记录。

Win10 部分早期 Viewer 截图仍为黑帧或旧画面，不能仅凭窗口标题计成功；后续可见截图、键盘字母及鼠标造成的画面变化均已人工核对，验证脚本只纳入这些稳定截图。未进行输入延迟测量。

独立断言脚本为 `F:\tmp\classroom-virtualbox\20261115\tools\validate_combo_evidence.py`，实际输出 `COMBO_EVIDENCE_PASSED guests=2 network_reports=8 file_hashes=6 viewer_exits=4`。汇总 `.artifacts/virtualbox-lab/20261115/combo-validation.json` 纳入 8 份通过的网络报告，并另列上述一次 DNS 异常原报告；其余证据为 `dual-combo-*.json`、`combo-file-distribution.json`、`combo-project-file-hashes-*.txt`、`viewer-*-combo-*.json/png`、`combo-final-state-*.txt`。

本组合阶段没有增加 HTTPS、QUIC、真实 IPv6 出口或已建立外部连接的验收结论；`1.1.1.1:443` 基线仍不可用。当时教师主窗口按钮与教师端来宾安装尚未测，后续成绩见文首；真实 WiFi 仍未测。

## Win7、双机和原版 Viewer 补充验收

本轮使用同一个真实 `TeacherRuntime/Classroom` 管理两台来宾，监听宿主回环 TCP 19532、实验 API 19533。旧单机教师的控制通道停止，避免两个教师争用。仍只通过 NAT 单播发现两个固定 MAC，未向机房广播。Win7/Win10 的 VNC 分别由 `127.0.0.7:15900`、`127.0.0.10:15900` 转发，两个独立 Viewer 在同一未激活的私有桌面运行；不触碰宿主输入和剪贴板。

| 验收项 | 实际结果与证据 |
| --- | --- |
| 同一教师同时黑屏 | 单条 `silence.start` 包含两个不同设备 ID，两台都 applied/input_locked=true；截图为 `dual-win7-silence.png`、`dual-win10-silence.png`，优化候选另复测两次 |
| 指定一台远控 | Win7 进入真实 Viewer 时 Win10 保持黑屏；反向同样通过，分别有 `optimized-*-silence-during-*-viewer.png` |
| Win7 黑屏键鼠 | 黑屏时虚拟 Win+R 未打开运行框；进入 Viewer 后截图核对原开始菜单仍在、没有遗留运行框。`win7-locked-win-r.png` 与 `win7-lock-check-underlying.png` 配对保存 |
| 原版 Viewer 的画面与输入 | 两台窗口标题对应各自来宾；Win7 鼠标打开开始菜单、键盘在命令行输入，Win10 键盘在运行框输入 `a`、鼠标取消运行框；不是 RFB 探针或 MCP 输入冒充 Viewer 输入 |
| 两种退出恢复 | 两台均通过“保留黑屏退出后恢复黑屏”和“远控内解除黑屏，退出保持桌面”；见 `viewer-exit-*.json`、`*-resumed-silence.png`、`*-exit-stays-desktop.png` |
| 双 Viewer 并存 | 同一教师同时有两个原版进程，分别取得各自桌面；先关闭 Win7 后，Win10 同一 Viewer 仍连接并显示桌面。见 `viewer-concurrent-both-win7.png`、`viewer-concurrent-win10-final.png`、`viewer-concurrent-win10-after-win7-close.png` |
| Win7 项目发文件 | 真实项目通道成功分发空文件、中文文件、8 MiB、嵌套目录、同名目录第二份，五次结果均成功；来宾独立 SHA-256 与发送夹具一致。见 `win7-file-distribution-receipts.json`、`win7-project-file-hashes.txt` |
| Win7 禁软件 | 普通权限 LabExec 启动自建 `lab-block-target.exe`；基线和解除后持续运行，禁用、重复启动、学生服务重启后均在 4 秒观察窗内结束。见 `blacklist-win7-*.txt`、`win7-dual-service-restart.txt`；当前实现是启动后终止 |
| 收尾检查 | 两台学生服务 Running，VNC Stopped、进程数 0、临时禁网/VNC 规则 0，外部 TCP 80 可达；保留必要的学生服务入站放行规则。见 `final-state-dual-*-final.txt` |

慢退出的最小复现不依赖 Viewer：`stop-win10-no-viewer-baseline.json` 中 SCM 停止请求只耗 18ms，但完整停止为 **9526ms**。原先将等待从 5 秒延长至 15 秒，只解决失败回执，没有改善等待。当前先正常停止 0.6 秒，再核实本项目服务路径、进程映像和父子关系，仅结束这些进程；使用带创建时间保护的 psutil 进程对象，并确认进程和服务都退出，之后才恢复桌面。停止失败仍保留恢复状态。

八次有效 Viewer 关闭计时包含教师轮询、真实停止回执与代理恢复：Win7 **0.804 / 0.837 / 0.904 / 0.850 秒**，Win10 **1.746 / 0.862 / 0.868 / 0.897 秒**，最后一项为最终安装器覆盖安装后的复测。这是有限样本，不能换算成输入延迟或 WiFi 性能。第一次无认证信任提示等待过久被服务拒绝，另有测试入口读取窗口过早的 400，原始失败记录保留；等待窗口创建并及时确认后，取得上述有效成绩。

当前候选为 `.artifacts/candidate-dual-20261115/`。完整自动化回归 **347 passed、0 failures、0 errors，116.043 秒**，证据为 `regression-dual.xml`；专门停止/归属验证为 **13 passed**，证据为 `remote-bounded-regression.xml`。新停止实现在两台安装并实跑。安装器额外排除 Viewer 的 `.vnc` 实验设置；Win10 远控后同版本覆盖安装发现 `vnchooks.dll` 被桌面进程占用，已改为比较版本/内容，相同文件不强制替换，实际更新遇到占用才安排系统重启替换。安装升级过程单独保留日志，不用服务可运行替代安装成功。

最终学生安装器 SHA-256 为 `35b3d406666798795ffad6b34c180fd599dbd54a15983aeb43b6ddff862864bd`，教师安装器为 `dce9be11e7920679ed25098657d707977a512d166d8b832aed5fd7f89644d2b9`。最终学生安装器在两台覆盖安装都返回 **0**，日志均不需要重启；Win10 明确记录 `vnchooks.dll` SHA-256 相同并跳过替换。随后两台原版 Viewer 再连接、退出和全部恢复均 applied。证据为 `win7-hookfix-install*`、`win10-hookfix-install*`、`viewer-start-final-package-*.json`、`viewer-exit-*-final-package.json`、`dual-hookfix-final-restore.json`。教师安装器只通过编译、PE 检查和宿主隐藏桌面只读启动诊断，尚未在来宾安装。

汇总断言脚本 `F:\tmp\classroom-virtualbox\20261115\tools\validate_dual_evidence.py` 已实际执行，输出 `DUAL_EVIDENCE_PASSED files=5 blacklist_phases=5 viewer_exits=8 regression=347`，汇总为 `dual-validation.json`。它独立核对五项落盘哈希、五个禁软件阶段、八次退出回执、黑屏截图像素、双 Viewer 数量、最终规则/进程/安装退出码及包哈希。最终专门远控回归再次通过 **13 passed**，证据为 `remote-bounded-final.xml`。窗口输入效果另由对应来宾和 Viewer 截图人工核对；没有把图片文件存在当成输入成功。

Win7 PowerShell 2.0 的 MCP 长脚本同步返回仍多次超时；报告实际落盘后，取回并校验内容才能作为证据。初版哈希辅助脚本使用了 .NET 3.5 不提供的公开 `Dispose()`，已改为 `Clear()` 并重新取得完整五项哈希。没有将失败脚本或工具超时标成正常同步通过。

本轮仍未测：教师主窗口全部按钮、教师端来宾安装、完整安装/卸载/系统重启矩阵、真实 WiFi、原失败终端、多屏、100 次目标系统切换、输入延迟、任意应用和完整路径禁软件、真实 HTTPS/QUIC/IPv6 出口、禁网前已建立连接、强杀服务与磁盘失败/文件断线恢复。此前 Win7/Win10 禁网及失联恢复的结果继续有效，范围仍限原探测。

## 已完成与证据

| 项目 | 实际结果 |
| --- | --- |
| Oracle VirtualBox 7.2.22 r175245 | 已安装 VBoxApplication 核心；MSI 返回码 0，不需要重启，没有打开 GUI；安装后 `VBoxManage --version` 返回 7.2.22r175245，程序签名有效 |
| 安装文件准备 | 下载包 SHA-256 与官方清单一致；不执行 EXE，仅从资源中提取 amd64 MSI 与 common.cab；MSI Oracle 签名有效，实际 Feature 表含 VBoxApplication |
| MCP 服务端 | mb85/mcp-virtualbox v0.4.1，发布 ZIP 的 SHA-256 与 GitHub Release digest 一致 |
| MCP 接入 | 由 Magpie Library 管理 classroom-virtualbox，仅分发给 Codex；条目为本机专用、direct 独立进程模式。与 Magpie 源配置逐项比对一致，原有 Codex 设置保持一致 |
| MCP 协议 | 实际执行 initialize、notifications/initialized、tools/list，识别 41 个工具，所需截图、虚拟键鼠、来宾命令与文件工具均存在 |
| VirtualBox 健康检查 | 安装后 `vm_list` 曾返回空列表；11 月 15 日当前会话使用原生 MCP 工具创建、后台启动及关闭了 LanClassroomLab-Win7 |
| 后台截图 | 原生 `vm_screenshot` 已查看固件、WinPE、Win7 登录及 1024×768 桌面；首次截图目录缺失已修正 |
| 虚拟键盘 | Win7 命令行实际执行 `ver`、`whoami`、`echo LAB_MCP_KEYBOARD_OK`，保存可见结果 |
| 虚拟鼠标 | 原生 SOAP 鼠标已实际点击 WinPE 窗口、Win7 用户登录、驱动确认及命令窗口；不只记录请求成功 |
| 鼠标后台服务 | Herdr 管理 vboxwebsrv，仅监听 127.0.0.1:18083，运行于独立隐藏桌面；MCP 自动拉起保持关闭 |
| 防止抢焦点 | MCP 在从不激活的独立 Windows 桌面启动；实测子进程所属桌面名为 ClassroomVBox_ 前缀。没有调用 SwitchDesktop 或宿主键鼠注入 |
| 宿主剪贴板 | Codex 配置禁用 vm_paste_text，避免改变用户剪贴板 |
| 管理员检查 | 曾验证普通终端安装守卫会拒绝且不弹 UAC；用户之后在管理员终端运行脚本，已成功完成安装；无自动重启 |
| 辅助脚本 | PowerShell 语法解析与 Python 语法检查通过；MSI 资源提取、MCP 探测和隐藏桌面启动已经实际执行 |

MCP 的 41 个工具是服务端目录；Codex 另禁用了其中的剪贴板工具。这里没有把“工具存在”记成“来宾操作通过”。

证据目录：`.artifacts/virtualbox-lab/20261114/`，包括原始 `mcp-probe.json`、完整 `tools.json`，以及 Magpie 分发后的 `magpie-distributed/`、安装后的 `post-install/` 探测结果。安装报告为 `F:\tmp\classroom-virtualbox\20261114\installation\installation-20261114-202649.json`，MSI 日志位于同目录。安装包资源与哈希记录位于 `F:\tmp\classroom-virtualbox\20261114\msi\extraction.json`。

11 月 15 日新增证据位于 `.artifacts/virtualbox-lab/20261115/`。`host-validation.json` 与固件截图只代表早期阶段；当前结果以 `win7-validation.json`、`probe-*.txt`、`teacher-*.json` 和 Win7 截图为准。`20261114/setup-status.json` 已同步，当前不再缺 Win7 镜像或管理员安装操作。

## Win7 镜像与部署进展

用户已提供 `XTWJ_Win7QJ_64_YH.esd` 的临时签名下载链接。已下载到 `F:\tmp\classroom-virtualbox\20261115\images\XTWJ_Win7QJ_64_YH.esd`，实际大小 4,565,488,620 字节，`wimlib-imagex verify` 完整归档校验通过。计算 SHA-256 为 `7972760c9702c86166bc88e547e51fe4b41f956941e7c567806c6a1e352dc984`；未获得发布方校验清单，不将计算值称为官方哈希匹配。签名下载地址未写入仓库或报告。

镜像元数据为中文 Windows 7 Ultimate x86_64、Build 7601、SP1、单索引 1；它是用户提供的定制镜像，验收记录应保留具体镜像身份。镜像收据为 `.artifacts/virtualbox-lab/20261115/esd-receipt.json`。

ESD 是系统内容映像，不能直接作为 VirtualBox 启动光盘。已从微软官方 ADK/WinPE 下载中只读提取部署所需文件，核对安装器签名及其内嵌组件清单；未在宿主安装 ADK、未请求提权。使用有效微软签名的 Oscdimg 生成 UDF 部署光盘 `F:\tmp\classroom-virtualbox\20261115\winpe-media-v1\win7-lab-esd.iso`，大小 4,960,038,912 字节。准备过程文件位于同一实验目录的 `tools/` 与 `winpe-media-v1/`。

实际启动 WinPE 后，通过原生 MCP 点击最大化命令窗口，并用虚拟键盘执行磁盘检查。截图 `.artifacts/virtualbox-lab/20261115/winpe-target-disk.png` 证明唯一磁盘为 40 GiB 空白 VBOX HARDDISK、SATA 接口，部署介质为 D:。确认 UUID 后，仅在该虚拟盘建立 BIOS/MBR 引导及系统分区，应用 ESD 并写入 Win7 引导文件。随后已完成桌面登录及 VirtualBox 7.2.22 Guest Additions 安装、重启；来宾启动诊断返回 `Windows-7-6.1.7601-SP1`。驱动确认只在虚拟机内处理，没有关闭驱动签名强制。

当前安装仅包含 VirtualBox 核心组件，不含桥接、Host-only、宿主 USB 或 Python 功能；没有打开 VirtualBox GUI，也没有自动重启宿主机。

已找到但**不完整、不能用于安装**的 Win10 镜像：
`F:\Downloads\Archives\zh-cn_windows_10_consumer_editions_version_22h2_updated_oct_2025_x64_dvd_38efd00d.iso`。
11 月 15 日读取 ISO9660 主卷描述符：声明卷大小为 7,168,839,680 字节，实际文件只有 308,445,184 字节，未覆盖声明卷范围。未挂载或启动；不能把文件存在当作镜像可用。

专用虚拟机为 `LanClassroomLab-Win7`，UUID 为 `4f3b9f27-2a2c-41f1-b35e-0f5ae9da3778`，目录为 `F:\VMs\LanClassroomLab\VirtualBox\LanClassroomLab-Win7`。配置为 2 vCPU、3 GiB 内存、40 GiB 动态磁盘、BIOS、NAT 和 USB tablet，关闭 3D、共享剪贴板与拖放，以 headless 模式运行；准备 Win10 时已通过 ACPI 正常关闭，减少并行资源竞争。除原有两个部署快照，已保存关机快照 `win7-student-offline-before-network-20261115`，UUID 为 `1c3948b4-8b0a-43f5-90d1-2f2f7a5cbdf8`；当前光驱为 Guest Additions。

在线快照本次未通过：进度停留，暂停尝试后返回 `VERR_VM_UNEXPECTED_UNSTABLE_STATE`，底层停在 `SUSPENDED_EXT_LS`，常规恢复和关机也未成功。通过 VirtualBox COM 核对虚拟机 UUID 和 SessionPID 后，仅结束这台实验机的 `VBoxHeadless` 进程，保存关机快照并重新启动，系统及学生服务已恢复。本环境后续使用关机快照；没有据此声称在线快照可靠，也尚未验证从新快照回滚。

## Win10 LTSC 2021 镜像与部署

用户新提供的本地镜像为 `F:\Downloads\Archives\Win10_LTSC_2021_天空驱动包_2026.1.iso`，实际大小及 UDF 声明卷大小均为 **10,335,715,328 字节**；SHA-256 为 `f753b5b43df70a2ba0c84739ddd8a90fbb705fb220a072afafa1706518c58860`。没有发布方校验清单，不称为官方哈希匹配。它是用户提供的定制镜像，不将名称中的 2026.1 当作 Windows 补丁版本。

ISO9660 层只有 UDF 提示文件，不能据此认定镜像没有系统文件。通过宿主无盘符只读挂载检查到 `sources/install.wim`，大小 10,004,304,488 字节；仅复制该文件到实验目录后，用 wimlib 完整校验通过。单索引元数据为 Windows 10 企业版 LTSC、x64、EnterpriseS、Build 19044；来宾命令行实际显示 **10.0.19044.1288**。宿主只读挂载随后已卸载，没有分配盘符或打开资源管理器。

新增虚拟机 `LanClassroomLab-Win10`，UUID 为 `a3aeda46-7f21-49ba-bea8-2abfd6f3d0fc`，位于 `F:\VMs\LanClassroomLab\VirtualBox\LanClassroomLab-Win10`。当前配置为 **2 vCPU、4 GiB 内存、64 GiB 动态磁盘、BIOS、NAT、`paravirtprovider=hyperv`** 和 USB tablet，关闭 3D、共享剪贴板、拖放，使用 headless 启动。这里的 HyperV 是来宾半虚拟化配置，没有更改宿主 Hyper-V/VBS。原光盘启动曾长时间停在加载画面，改用已验证的 WinPE，确认唯一目标是新建空白 64 GiB VBOX HARDDISK 后建立分区；DISM `/CheckIntegrity` 应用系统映像及 `bcdboot` 写入引导均成功。

最初 2 vCPU、HyperV 来宾半虚拟化配置下，首次配置停留在“准备就绪”，VirtualBox 日志记录多次时钟追赶失败；中断配置后重启触发 Windows 安装恢复错误。保留现场快照后，已实际从 `win10-wim-applied-before-first-boot-20261115`（UUID `21db0603-de1a-42ac-a1f8-79b7cbcdfe61`）关机快照回滚，临时改为 1 vCPU、`paravirtprovider=none`，完成系统配置并进入桌面。后来此配置在首次教师断联测试附近发生 **0x133 DPC_WATCHDOG_VIOLATION**，该次恢复测试作废。已保存 VBox 日志、Windows 事件及 minidump；没有确认蓝屏根因，不将其归因于项目禁网，也不宣称已经根治。

恢复后改回当前 2 vCPU/HyperV 配置，仅在实验来宾将显示、睡眠、磁盘空闲超时的 AC/DC 设置为 0，随后重新取得完整的禁网和失联恢复证据。原睡眠 AC 1800 秒/DC 900 秒，显示 AC 600/DC 300 秒；现有事件不能证明睡眠引发蓝屏。本轮没有更改宿主电源、网卡、防火墙、Hyper-V/VBS 或重启宿主。

Win10 专用测试教师由 Herdr `w3H:p2` 管理，只监听宿主回环 TCP 19530、HTTP 19531；仅匹配该来宾 MAC。回环 UDP 39527 转发到本机来宾 9527，VNC 回环 TCP 35900 转发到来宾 15900，避免与 Win7 测试端口及记录混用。当前光盘为 Guest Additions 和 `win10-test-tools-v1.iso`。Guest Additions 7.2.22 已静默安装、返回 0 并重启验证；MCP 命令及双向复制已实际通过。

证据位于 `.artifacts/virtualbox-lab/20261115/`：`win10-iso-receipt.json`、`win10-wim-verification.json`、`win10-wim-verification.verify.log`、`win10-wim-applied.png`、`win10-first-boot-preparing.png`、`win10-first-boot-VBox.log`、`win10-interrupted-setup-log.png`。用户 ISO 原文件保持不动；工作映像与辅助脚本位于 `F:\tmp\classroom-virtualbox\20261115/`。

## Win10 实测结果与范围

| 项目 | 本次实际结果 |
| --- | --- |
| 后台管理 | MCP 截图、虚拟键鼠的可见效果、普通 cmd/PowerShell 命令及双向复制通过；没有注入宿主键鼠或使用宿主剪贴板 |
| 安装与覆盖升级 | 首次学生安装和停止等待修正版的覆盖安装均退出 0；服务实际 RUNNING / AUTO_START / LocalSystem；服务与代理只读诊断成功。卸载、普通用户登录和系统重启后的全流程仍未测 |
| 防火墙关闭 | 镜像默认三个配置均关闭，项目真实返回 failed 并提示启用防火墙；没有虚报禁网成功 |
| 禁网与教师保留 | 仅启用来宾防火墙后，基线可用的 TCP 1.1.1.1:80、TCP 8.8.8.8:53、HTTP NCSI、公网和 NAT UDP DNS 均被阻断；教师 10.0.2.2:19530 可达且实际回执到达 |
| 手动和失联恢复 | `network.restore` 后相同探测恢复。蓝屏后的重测再次禁网，只停止教师通道，未发送解除，出网自动恢复、课堂规则清零；此时教师端口确实不可达。禁网期间 MCP 命令及复制仍可用 |
| 黑屏 | 实际黑屏截图及 input_locked=true 回执；虚拟 Win+R 在黑屏时未打开窗口，进入远控后也没有遗留运行窗口。本轮另补双机，见文首；不外推到安全桌面或多屏 |
| UltraVNC 与黑屏切换 | 项目启动真实 LanClassroomVNC 服务，返回 ready=true/15900。真实 RFB 取得来宾桌面并发送鼠标、Win+R，截图有可见效果；退出远控恢复尚有效的黑屏；远控期间解除黑屏，再退出保持桌面，均已实测 |
| 退出远控修复（上轮历史） | 原候选 5 秒等待出现 1053；延长至 15 秒后两次约 10.23 秒和 7.46 秒。当前已由文首的有界停止修正替代，本轮有限样本 0.80–1.75 秒 |
| 项目文件分发 | 通过真实 TeacherServer/FileDistributor/学生接收器分发空文件、中文文件、8 MiB 文件、嵌套目录及同名目录第二份，共 5 次；收到落盘成功回执，并在来宾独立计算 SHA-256，与发送文件逐项一致。不是 MCP 文件复制冒充项目分发 |
| 禁软件 | 临时复制 ping.exe 为 lab-block-target.exe，基线持续运行；教师下发省略 .exe 的策略后，新启动、重复启动及学生服务重启后的目标均在 4 秒观察窗内退出；明确解除后重新启动能持续运行。测试进程以 LabExec 管理员权限启动；其他应用、完整路径规则、普通用户与系统重启仍未验收 |
| 收尾 | 禁软件测试策略已显式清除，classroom.restore_all 为 applied；最终出网可用、课堂禁网规则为 0，VNC 服务停止且无进程、临时 VNC 规则为 0 |

主要证据：`win10-validation.json`、`probe-win10-*.txt`、`teacher-win10-*.json`、`win10-rfb-final-*.png`、`win10-rfb-stopfix-*.png`、`win10-silence-win-r-blocked.png`、`win10-stopfix-resumed-silence.png`、`win10-stopfix-clear-silence-exit-normal.png`、`win10-file-distribution-receipts.json`、`win10-project-file-hashes.json`、`blacklist-win10-*.json` 和 `win10-final-state.json`。RFB 探针首次鼠标后的帧仍可能为缓存帧，报告保留 pointer_frame_changed=false；随后键盘帧及画面人工检查提供实际输入证据，不篡改探针字段。

上轮候选为 `.artifacts/candidate-win10-stop-20261115/`，学生安装器 SHA-256 `a57bf570405004e59376d2fe7c264c5e0b026e7c2f5924206a1a8b0bf96c2581`，教师安装器 SHA-256 `a5e97dba726c817eb49f6feee1cead454a8fa51a8d9b4639d48990a65c7f4bbe`。当时完整回归 **346 passed，10 个 PyQt sip 弃用警告，115.89 秒**；结束时另有重连守护线程向已关闭捕获流写日志的异常，测试退出码为 0，未将其改为产品修复。证据为 `remote-stop-regression.xml`、`regression-win10-stop.xml`。上述候选已由文首双机候选替代，两台均已安装最终修正版。

上轮未测的原版独立 Viewer、双机黑屏及独立关闭，本轮已补齐，见文首。其他未测范围继续保留。蓝屏材料为 `win10-disconnect-bugcheck-VBox.log`、`win10-crash-diagnostics.json`、`win10-111526-18921-01.dmp`；电源设置记录为 `win10-lab-power-policy.txt`。

## 工具位置与复现

- MCP 二进制：`F:\DevTools\virtualbox-mcp\v0.4.1\mcp-virtualbox.exe`。
- VirtualBox 命令行：`C:\Program Files\Oracle\VirtualBox\VBoxManage.exe`，版本 `7.2.22r175245`。
- 隐藏桌面启动环境：`F:\DevTools\virtualbox-mcp\venv\Scripts\python.exe`，Python 3.13.15 / pywin32 312，仅用于宿主辅助工具；不改变学生端 Python 3.8.10。
- MCP 管理入口：Magpie Library 的 `classroom-virtualbox`，实际源配置位于 `C:\Users\1\.config\magpie\library.json`；通过 Magpie CLI 添加和同步，不再以单独手写 Codex 注册作为管理方式。
- Codex 接收位置：`C:\Users\1\.codex\config.toml` 的 `mcp_servers.classroom-virtualbox`。Magpie 分发保留了 `vm_paste_text` 禁用及原有超时设置。修改前已保存配置备份；备份可能含现有 MCP 凭据，不应上传或附到验收报告。
- 截图输出：`F:\tmp\classroom-virtualbox\screenshots`。
- 下载脚本：`scripts/Prepare-VirtualBoxLab.ps1`；资源提取：`scripts/extract_virtualbox_installer.py`。
- 隐藏桌面启动：`scripts/run_on_private_desktop.py`；协议探测：`scripts/probe_virtualbox_mcp.py`。

11 月 14 日协议验证使用本地客户端真实启动同一隐藏桌面启动器及服务端；11 月 15 日当前会话已暴露并实际调用 `classroom-virtualbox` 的原生工具，完成空机生命周期、截图与固件键盘操作。

本机 Magpie CLI 为 `F:\software\magpie-windows-amd64.exe`。本次以 `magpie library mcp add` 纳入 Library，目标 `agents=codex`，并以 `magpie library mcp router classroom-virtualbox direct` 设置独立进程分发。Magpie 的默认共享 HTTP 路由在本次探测中对该新条目返回 404，未进一步定位内部原因；direct 模式重新握手通过，无需重启正在运行的 Magpie。

重新执行只读协议探测：

```powershell
F:\DevTools\virtualbox-mcp\venv\Scripts\python.exe `
  scripts\probe_virtualbox_mcp.py `
  --python F:\DevTools\virtualbox-mcp\venv\Scripts\python.exe `
  --server F:\DevTools\virtualbox-mcp\v0.4.1\mcp-virtualbox.exe `
  --report-directory .artifacts\virtualbox-lab\20261114
```

鼠标工具依赖 `vboxwebsrv`。现已由 Herdr 的 `Classroom Lab` → `VirtualBox` → `vbox-web` 管理（本次 pane 为 `w3H:p1`），通过独立隐藏桌面启动器执行以下命令，监听仅限 `127.0.0.1:18083`：

```powershell
& 'F:\DevTools\virtualbox-mcp\venv\Scripts\python.exe' `
  'F:\github\lan-classroom-control\scripts\run_on_private_desktop.py' -- `
  'C:\Program Files\Oracle\VirtualBox\vboxwebsrv.exe' `
  --host 127.0.0.1 --port 18083 --authentication null
```

命令应发给 Herdr 的专用空闲 pane，不在前台另开终端。服务已运行，不要重复启动。`--authentication null` 仅用于本机回环服务，不改 VirtualBox 全局认证配置。SOAP 点击已在 WinPE 和 Win7 中产生实际画面变化。

空机测试中，先发 Ctrl+Alt+Delete 再连续发 F12 曾触发固件 `Keyboard error:995`，根因未确认。随后关机冷启动，将固件启动提示延长至 10 秒，单次 F12 和文本 b 均产生可见效果。未把这一结果当成 Windows 的 Ctrl+Alt+Delete 通过；后续安装应使用冷启动与按画面逐步输入，避免高频盲发。

## Win7 实测结果与范围

| 项目 | 本次实际结果 |
| --- | --- |
| 系统与权限 | Win7 Ultimate SP1 x64，桌面用户 user；学生服务为 LocalSystem；MCP 使用独立临时实验账户，普通命令未提升权限 |
| 后台命令 | 原生 `vm_guest_exec` 执行 `ver`、`whoami` 和 `guest-command-proof.cmd`，输出 `LAB_GUEST_COMMAND_SUCCESS`；禁网期间重复通过 |
| 文件通道 | 原生 MCP 双向复制安装包、诊断与探测报告；项目自身五次分发和独立哈希已在本轮另行验证，见文首 |
| 安装与启动 | 修正版候选已安装到 `C:\ClassroomStudentV2`；学生服务实际为 RUNNING / AUTO_START / LocalSystem，服务及代理只读启动诊断成功 |
| 禁网前提失败 | 镜像默认关闭三个防火墙配置；真实 `network.block` 回执为 failed，明确提示先启用防火墙 |
| 正常禁网 | 仅在来宾启用防火墙后，经实际教师、协议回执与学生服务应用规则；IPv4 外部 TCP 80、TCP 53、HTTP 及公网/NAT UDP DNS 从可用变为不可用 |
| 保留教师 | 禁网时 `10.0.2.2:19528` 可连接，教师报告设备在线，实际禁网与恢复回执均到达 |
| 手动恢复 | 实际 `network.restore` 为 applied；相同出网探测重新可用，课堂规则数为 0，三个防火墙配置仍开启 |
| 失联恢复 | 第二次实际禁网后只停止教师控制通道，没有发送解除指令；来宾随后出网恢复、课堂规则为 0；教师端口在停止期间确实不可达 |
| 收尾 | 已重启测试教师通道、重新接入并执行真实 `classroom.restore_all`；未留下课堂禁网规则 |

教师测试脚本位于 `F:\tmp\classroom-virtualbox\20261115\tools\lab_teacher.py`，通过 Herdr `w3H:p4` 和隐藏桌面启动。仅监听宿主回环 TCP 19528、HTTP 19529；VirtualBox 将回环 UDP 29527 转发到来宾 9527，再发送单播发现包。操作入口核对唯一在线来宾的 MAC，不向机房广播命令。NAT 发现转发是实验接入方式，不作为真实 WiFi 自动发现通过的证据。

具体证据：`probe-baseline-final.txt`、`probe-blocked.txt`、`probe-restored.txt`、`probe-auto-restored.txt`，以及同目录 `teacher-firewall-disabled.json`、`teacher-block.json`、`teacher-during-block.json`、`teacher-restore.json`、`teacher-disconnect.json`、`teacher-final-restore-all.json`。本次不提供精确恢复延迟结论。

MCP 长脚本仍存在限制：PowerShell 2.0 与启动后台探测的命令多次返回 `VERR_TIMEOUT` 或工具非零状态，但来宾报告已实际落盘。探测按阶段标签取回报告并核验内容，不能把这些调用标成正常同步完成。普通 `cmd` 命令和双向文件通道确实通过；PowerShell 长任务的同步返回可靠性仍待排查。Win7 PowerShell 2.0 使用含中文注释的脚本时须保留 UTF-8 BOM，本次已修正探测脚本并重新取得有效 DNS 基线，旧无效 DNS 探测不作为成绩。

## 实测发现与修复

- 旧候选误收集宿主 Win11 的 `ucrtbase.dll`，Win7 提示缺少 `api-ms-win-core-sysinfo-l1-2-0.dll`。新共享 PyInstaller spec 排除宿主 UCRT 与 API-set 文件，依赖安装器已预检的来宾系统 UCRT；本地及 CI 构建均使用该 spec，并增加目录门禁。旧候选能被新门禁拦截，修正版已在同一 Win7 启动。没有向系统目录复制网上的 DLL。
- `_get_mac()` 以“冒号分六段”猜测 MAC，实际把 `fe80::...` 覆盖到真实链路地址上。现在只接受 AF_LINK 的六字节十六进制地址，并统一分隔符。先用实际地址顺序复现失败，再修复并在 Win7 重装核对身份。
- Win7 基础验收历史候选：`.artifacts/candidate-win7-20261115-identity/`。学生安装器 SHA-256 为 `bf2d56a1178572cdf7946cbdaad7e92af201e9afa65191222e54dc0165936069`，教师安装器为 `de7a0b62844be1d759c106d3e29c2d3a3f4c74c0af2cf1e1ba2229ad720ad3ae`。当时完整自动化回归 **343 passed**，115.777 秒；证据 `regression.xml`。该历史候选已被文首双机候选替代。

## 后续目标系统验收

1. 在已通过的双机和原版 Viewer 场景基础上，补真实 WiFi、原失败终端、多屏及目标系统 100 次切换。
2. 已通过两台项目分发及自建测试程序的完整路径禁用；继续补文件续传、磁盘失败与更多普通/提权应用。
3. 补充禁网前已建立的连接、可用 HTTPS/QUIC、真实 IPv6 出口、代理/其他白名单、服务被杀及守护恢复。本次 `1.1.1.1:443` 基线本来不可用，不能据此声称 HTTPS 已被有效阻断；IPv6 只有规则读回，没有可用出口实测。
4. 本阶段已补教师首次安装/覆盖升级和两台桌面注销后的恢复，继续补完整卸载矩阵及更多普通用户环境。早期旧程序错误弹窗和非零安装记录保留，服务可运行不能替代完整安装器生命周期通过。
5. 继续调查 Win10 广播黑帧、启动停滞和 0x133 蓝屏，再补真实 WiFi、原故障终端和多台竞争。Win10 关机快照回滚已实际通过，Win7 新关机快照回滚仍未测。

**当前成绩限于本台定制 Win7 x64、Win10 LTSC x64、NAT 环境和明确列出的场景。双机黑屏、原版独立 Viewer、文件分发及测试程序禁用已有实测；真实 WiFi 和原失败终端仍未实测。**

虚拟 NAT 不等于实际 WiFi；无线干扰、AP 隔离、多台竞争和真实显示器黑屏仍需现场设备。宿主检测到 hypervisor，但本轮没有修改 Hyper-V/VBS、物理网卡或宿主防火墙。

## 上游依据

- [Oracle 7.2.22 下载目录](https://download.virtualbox.org/virtualbox/7.2.22/)
- [Oracle SHA-256 清单](https://download.virtualbox.org/virtualbox/7.2.22/SHA256SUMS)
- [Oracle Windows 安装组件和静默 MSI 参数](https://docs.oracle.com/en/virtualization/virtualbox/7.2/user/installation.html)
- [mcp-virtualbox v0.4.1 发布与校验信息](https://github.com/mb85/mcp-virtualbox/releases/tag/v0.4.1)
- [上游键鼠、剪贴板实现](https://github.com/mb85/mcp-virtualbox/blob/v0.4.1/internal/tools/input.go)
- [上游来宾命令实现](https://github.com/mb85/mcp-virtualbox/blob/v0.4.1/internal/tools/guest.go)
- [Codex MCP 配置](https://developers.openai.com/codex/mcp)
- [微软 ADK 与 WinPE 官方下载及版本说明](https://learn.microsoft.com/en-us/windows-hardware/get-started/adk-install)
- [微软 DISM 映像应用参数](https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/dism-image-management-command-line-options-s14)
- [微软 Oscdimg 启动与 UDF 光盘参数](https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/oscdimg-command-line-options)
- [wimlib 官方 ESD 支持与工具下载](https://wimlib.net/)
