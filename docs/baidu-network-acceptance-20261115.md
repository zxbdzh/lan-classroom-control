# 百度真实禁网与恢复验收

日期：2026-11-15。使用已安装的最终稳定性候选包，在后台 VirtualBox 的 Win10 教师和 Win7、Win10 两台学生执行一次完整验证。**百度 HTTP、HTTPS 及对应 IPv4 TCP 80/443 的“正常 → 禁止 → 恢复”均通过。** 本次使用教师端“禁止外网”，百度是验证目标，未增加按网站选择性封禁功能。

## 验证方法与结果

两台学生均先在真实浏览器打开 `https://www.baidu.com/`：Win7 使用 IE，Win10 使用 Edge InPrivate；人工核对页面和地址栏。随后在已安装的真实教师主窗口、全班作用域点击“禁止外网”，两台回执均已执行；禁网后再用不同查询参数打开百度，保存实际失败页面。最后从教师窗口“全部恢复”，重新打开并确认百度页面恢复。

每个阶段另外在学生来宾内使用 WinHTTP 发起无代理、带禁用缓存请求头的独立 HTTP/HTTPS GET，没有跳过证书校验。基线保存百度解析出的 IPv4 `183.2.172.177`，后续直接对同一 IP 的 TCP 80/443 探测，避免把缓存解析成功或 DNS 不可用误认为网站仍能访问或已被正确阻断。

| 验证项 | Win7 SP1 x64 | Win10 LTSC 2021 x64 |
| --- | --- | --- |
| 禁网前浏览器 | 百度 HTTPS 页面可见 | 百度 HTTPS 页面可见 |
| 禁网前 HTTP / HTTPS | 均为 200，响应包含百度内容 | 均为 200，响应包含百度内容 |
| 禁网后浏览器新请求 | “无法显示此页” | “Internet 访问被阻止”，`ERR_NETWORK_ACCESS_DENIED` |
| 禁网后 HTTP / HTTPS | 两者均连接失败 | 两者均连接失败 |
| 固定百度 IP 的 TCP 80 / 443 | 两端口均为可达 → 不可达 → 可达 | 两端口均为可达 → 不可达 → 可达 |
| 恢复后浏览器 | 百度 HTTPS 页面重新显示 | 百度 HTTPS 页面重新显示 |
| 恢复后 HTTP / HTTPS | 均恢复为 200 | 均恢复为 200 |
| 教师 `10.77.15.5:9528` | 三阶段始终可达 | 三阶段始终可达 |
| 产品禁网规则数量 | 0 → 2 → 0 | 0 → 2 → 0 |
| 学生服务 | 三阶段均 Running | 三阶段均 Running |

两台浏览器恢复画面已人工核对。WinHTTP 得到的百度响应可能是针对程序访问的简化页面，不能单凭其中包含 `baidu` 推断完整桌面页面加载；浏览器截图独立支持页面可见结论。

## 证据与收尾

证据根目录：`.artifacts/virtualbox-lab/20261115/baidu-network/`。

- `baidu-win7-v3-{before,blocked,restored}.txt`、`baidu-win10-v3-{before,blocked,restored}.txt`：六份实际来宾探测报告。
- `baidu-{win7,win10}-browser-{before,blocked,restored}.png`：两台浏览器六张阶段截图。
- `baidu-teacher-running.png`、`baidu-teacher-block-check.png`、`baidu-teacher-restored.png`：教师作用域、在线状态及禁网/恢复回执。
- `baidu-win7-final-state.txt`、`baidu-win10-final-state-clean.txt`、`baidu-teacher-final.json`：最终两台禁网规则零、服务 Running、桌面代理各一、测试浏览器进程零、教师程序正常退出。
- `validation.json`：脚本独立断言六份报告、对应截图存在与最终恢复状态，并记录文件 SHA-256。画面内容由人工核对，脚本未冒充图像内容识别。

测试仅操作 headless 来宾，没有切换宿主桌面、注入宿主键鼠、操作宿主剪贴板或更改宿主网络、防火墙。最终解除禁网并关闭本轮测试浏览器及教师窗口，三台来宾保持后台运行。本轮未改产品代码或重建安装包。

## 保留的测试工具异常与未测范围

- 最初直接调用 MCP 鼠标工具，VirtualBox Web Service 超时。后续使用既有 GuestControl 传脚本，以及只针对运行中 headless 来宾的虚拟键盘启动脚本；教师按钮由来宾本地输入点击，未直接伪造教师回执或修改学生防火墙。
- Win7 的最初 v1/v2 探测脚本遇到 PowerShell 2.0 COM 参数兼容错误，原报告保留，不计入通过结果；修正脚本后 v3 的六份报告完整且通过断言。
- Win7 同步 PowerShell GuestControl 仍有超时，报告已实际落盘、取回并核对 `complete=true` 与逐项内容，不将同步调用写成正常成功。
- 本轮只验证百度 HTTP/HTTPS 的 IPv4 出口和浏览器新请求，没有专门验证 QUIC/HTTP3、真实 IPv6、持续复用的外部长连接、真实 WiFi、多站点及长期稳定性。这些未测项继续保留，历史蓝屏与启动停滞结论不因此改变。
