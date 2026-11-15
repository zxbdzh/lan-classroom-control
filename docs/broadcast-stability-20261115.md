# 广播黑帧与 Win10 稳定性修复记录

日期：2026-11-15。**本轮修复广播断流后的恢复缺陷；历史蓝屏取得驱动路径线索，并对实验环境作可回滚规避。真实 WiFi、原故障物理终端和长期稳定性尚未通过验收，不能宣称所有历史问题已根治。**

## 产品修复

1. TCP 广播原来可以保持连接却永久没有完整画面。现在读取超时为 2 秒，空流或半帧停滞都会关闭连接并自动重连；停止时主动唤醒读取，避免旧读取线程残留。没有将“连接仍在”当作“画面正常”。
2. 单个学生在媒体握手时断开，原来会使教师共用的媒体监听线程退出，之后的学生也不能连接。现在将监听错误与单连接握手失败分开，失败只影响当前连接，继续接收其他学生。
3. 广播先发布独立 TCP 备用画面，UDP 组播发送错误不再连带中断 TCP；异常和恢复只在状态变化时记日志。
4. 截屏初始化失败、GDI 句柄失效或空图时，在采集线程内销毁并重建整个 mss 上下文，以可打断的 0.5 秒等待退避。保留尚存活的线程引用，避免快速停启创建重复采集线程。

新增六个回归用例：截屏初始化/采集阶段失败恢复、TCP 空流/半帧重连、组播错误不阻断 TCP、媒体握手断开后仍能接受连接。原代码先失败，修复后通过。**这些确定性缺陷得到复现与修复，不等于已唯一解释历史每一次黑帧。**

## 构建与真实来宾验证

使用最终候选 `.artifacts/candidate-broadcast-recovery-final-20261115/`，三台均为 headless VirtualBox 来宾：Win7 学生 `10.77.15.3`、Win10 学生 `.4`、Win10 教师 `.5`。测试通过真实教师窗口按钮开始、停止及恢复，以教师桌面上的变色 `LIVE` 计数标记和两台学生截图指纹验证画面变化，不能仅凭 applied 回执判通过。只操作来宾，未切换宿主焦点、注入宿主键鼠或操作宿主剪贴板、网络、防火墙。

| 范围 | 本轮结果 |
| --- | --- |
| 专项回归 | 最终 41 passed；保留原先失败的红色报告 |
| 完整自动化回归 | 363 passed，0 failures/errors/skips，119.044 秒 |
| 最终构建 | 教师、学生服务、桌面代理冻结程序，两个安装器及 PE 6.1 门禁通过；三份隐藏桌面只读启动诊断通过 |
| 最终包安装 | 两台学生及 Win10 教师覆盖安装成功，日志均不要求重启 |
| 双机开始/停止广播 | 连续 10 轮均显示动态标记，Win7、Win10 每轮均验证多张截图变化 |
| Win10 强制 TCP 与断链 | 临时阻断来宾 UDP 9529；3 轮广播中将 Win10 虚拟网线断开 3 秒再恢复，无需停播重开即恢复动态画面，Win7 继续出图 |
| 双机持续广播 | 单次连续观察 300.5 秒，两台每个检查点均有有效且变化的标记，未发现黑帧或冻帧 |
| 换网卡后的首次冷启动 | Win10 学生正常关机后再开机，180 秒内仍未就绪，启动画面停滞、GuestControl 不可用；保留失败，不计通过 |
| Win10 学生兼容显示配置 | 只将显示控制器由 VBoxSVGA 改为 VBoxVGA，保留 VirtIO、2 vCPU、HyperV 和 3D 关闭，随后两次冷启动通过；服务 Running、桌面代理各一，就绪耗时 86.71 / 95.47 秒 |
| Win10 教师冷启动 | 保留 VBoxSVGA 显示配置，两次正常关机、后台再开机及来宾命令探测通过，就绪耗时 77.66 / 67.60 秒 |
| 冷启动后的产品复测 | 重开真实教师窗口后两台学生自动连接，再测 2 轮双机动态广播、同时黑屏、全部恢复，均有可见画面证据 |

强制 TCP 阶段的 Win10 媒体日志有三组“收到帧 → stalled/reconnecting → 再收到帧”，证明实际走了超时重连路径。采样不能作为精确恢复延迟或 P95。临时组播阻断规则在该项结束后删除。

## 历史蓝屏与启动停滞

保留原始 `111526-18921-01.dmp`，使用微软调试引擎和匹配内核符号读取 `.bugcheck`、`kv` 与模块信息。转储为 0x133，第一参数为 1，运行时间 16 分 28 秒，记录的是 1 vCPU 环境。中断栈从时钟中断经过 `E1G6032E+0x3af`、`E1G6032E+0x5222`，之后涉及 NDIS。

**这将调查指向 Intel PRO/1000 仿真网卡驱动路径，单份小转储不能唯一确认根因；没有运行完整的 `!analyze` 扩展，也没有证据认定是 Python 异常直接造成蓝屏。启动停滞的唯一根因仍未确认。**

实验环境已做以下可回滚处理：

- Win10 学生和教师正常关机，各建立 `before-virtio-net-20261115` 冷快照后，从 Intel PRO/1000 改为 VirtIO 网卡。Win7 保持原配置，MAC、NAT Network 和实验地址保留。
- NetKVM 从 Fedora virtio-win MSI 中仅提取驱动数据，不执行 MSI；驱动目录签名有效，签发者为 Microsoft Windows Hardware Compatibility Publisher。实装版本 `100.103.104.30200`。
- 来宾报告已确认 `Red Hat VirtIO Ethernet Adapter`、`netkvm Running`、`E1G60 Stopped`。两台保持现有 2 vCPU、HyperV 半虚拟化和 3D 关闭；显示控制器的后续隔离见下文。未调整宿主 Hyper-V/VBS 或电源策略。
- 如需回滚，先正常关闭对应来宾，再恢复上述冷快照；本轮没有实际执行快照回滚。

持续广播结束后的两份报告显示来宾已运行约 22 分钟，没有新增 minidump 或本次启动后的 41/1001/6008 事件；旧 dump 仍保留。**随后首次冷启动又出现停滞，明确说明更换虚拟网卡不足以解决启动问题。** 21:34:22 开始的启动在 180 秒内未就绪；VirtualBox 的 OS 探测仍能识别 WinNT、读取内核栈，未报告 bugcheck，ACPI 电源按钮能正常关机。这些观察不能证明启动停滞的根因已查清，也不能把它改记为单纯 MCP 调用故障。

为继续隔离，保存学生虚拟机的 `.vbox` 配置，只将其显示控制器由 VBoxSVGA 改为 VBoxVGA。随后两次冷启动均进入可见桌面，学生服务及代理自动就绪；四份最终冷启动健康报告均未出现新 dump 或本次启动后的异常关机事件。学生当前显示驱动仍为 `VirtualBox Graphics Adapter (WDDM) 7.2.22.25245`，不是卸载 Guest Additions 或禁用全部驱动。

教师保留 VBoxSVGA 也通过了两次冷启动，因此**兼容显示配置只能记录为当前学生虚拟机的有限规避结果，不能唯一认定显示驱动就是历史停滞根因，更不能据四次启动宣称稳定性已根治。** 当前学生实验配置保留 VBoxVGA，教师保留 VBoxSVGA；可以在对应来宾正常关机后用 `VBoxManage modifyvm <实验虚拟机> --graphicscontroller vboxsvga` 恢复显示设置。本轮没有恢复该显示设置再重复失败注入，也没有对来宾强制断电。

## 最终包

| 安装器 | SHA-256 |
| --- | --- |
| `installer/LanClassroom-Teacher-Setup.exe` | `BE7D84E84F1A2A8317AA56E0A1FAC0C04B5B5022239A82723B6AA5B72188F9B4` |
| `installer/LanClassroom-Student-Setup.exe` | `3B2E704E1645713C288ECFE1FAC35EAABCF8CE944D7A8ED82AF40F031FBAEEF1` |

两个安装器位于上述候选目录，已用于本轮来宾覆盖安装，候选包未发布。

## 证据与结论边界

证据根目录 `.artifacts/virtualbox-lab/20261115/`：

- `broadcast-recovery-red.xml`、`broadcast-handshake-red.xml`：修复前确定性失败；`broadcast-handshake-green.xml`、`broadcast-recovery-final-all.xml`：修复后专项及完整回归。
- `broadcast-final-live-verified/`：最终包双机 10 轮，含教师标记前置检查、两台学生截图及逐轮指纹。
- `broadcast-final-tcp-link-recovery/`：最终包强制 TCP 的三轮断链自动恢复，含学生媒体日志。
- `broadcast-final-continuous/`：最终包双机 300.5 秒持续广播的定期截图与动态指纹。
- `win10-bugcheck-symbolized-final.txt`、`win10-111526-18921-01.dmp`：蓝屏原始证据及符号化栈。
- `virtio-migration/`：变更前后虚拟机配置、两台驱动及运行状态。
- `virtio-cold-boots/`：更换网卡后首次冷启动失败、停滞截图、VBox 日志与只读来宾调试记录。
- `win10-legacy-display-cold-boots/`、`teacher-virtio-cold-boots/`：学生兼容显示配置和教师原显示配置各两次成功冷启动、桌面截图与驱动/服务报告。
- `broadcast-final-postboot/`、`stability-postboot-*.png`：冷启动后的两轮广播、双机黑屏及恢复截图，教师最终记录显示 2/2 已执行。
- `stability-after-tests-*.txt`、`stability-final-collection.json`、`stability-teacher-final.json`：本轮收尾状态、同步命令异常备注和教师程序/动态标记均退出的证明。
- `broadcast-stability-validation.json`：独立脚本核验后的汇总，明确为 `bounded_vm_pass_with_unverified_field_stability`，纳入首次冷启动失败。
- `stability-final-*-install.log`、`stability-final-teacher-install.log`：最终包覆盖安装日志。

保留原教师 UI 阶段的 Win10 黑帧证据；第一阶段候选在 `broadcast-tcp-link-recovery/` 的冻帧失败也保留，后来才补充握手断线回归并修复。`broadcast-final-live-rounds/` 是教师/标记没有正确启动的无效前置运行，实际截图为桌面及运行框，不计为产品通过或产品回归失败；补充标记前置检查后使用新目录重跑，没有覆盖旧报告。

本轮结论限定于已列出的真实虚拟机场景。真实 WiFi 干扰与多机竞争、原失败终端、长时间现场稳定性、多屏和目标系统 100 次切换仍未验证；最终稳定性包没有在 Win7 教师端重复全部按钮链路。原有效签名更新、硬件唤醒、HTTPS/QUIC/真实 IPv6 等未测项也不因此改变。

## 收尾与最终结论

两台学生最终均为服务 Running、桌面代理各一、VNC Stopped/进程零、测试进程零、临时网络/VNC 规则零、临时组播阻断规则零，必要的代理入站规则各一，公网 TCP80 已恢复。教师窗口和动态标记已正常退出，进程数均为零；两台学生的临时控制已解除，来宾继续 headless 运行，没有关闭既有 MCP 服务。

Win7 最后一次 PowerShell 同步调用仍返回超时；报告实际完整落盘并取回，来宾内时间比此前基线报告更新，内容逐项校验通过，未将该同步调用记为正常成功。来宾与宿主有时钟偏差，报告新旧只在同一来宾内比较，未使用它们推算输入或恢复延迟。

独立核验脚本 `F:\tmp\classroom-virtualbox\20261115\tools\validate_stability_evidence.py` 输出：

```text
STABILITY_EVIDENCE_VERIFIED tests=363 broadcast=10 tcp_recovery=3 continuous>=300 cold_boot_passed=4 cold_boot_failed_retained=1 final_guests=2
```

**结论仍为部分通过：广播断流恢复的修复在所列场景中通过；蓝屏与启动停滞取得线索及有限环境规避结果，原冷启动失败保留，唯一根因与长期现场稳定性尚未确认。** 未把四次成功启动、约 22 分钟无新 dump 或停止重开后的成功改写为历史问题全部根治。
