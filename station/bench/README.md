# 图传调试台

1. 用 USB 数据线把 K230 接到电脑。看到板子指示灯亮后，关闭 CanMV 和正在使用板子的其他窗口。
2. 双击桌面的 **图传调试台.bat**。看到「自检通过」后，第一次选 **1. 标准清晰度，可靠传输**。镜头摆好后，输入 `1` 并按回车，允许电脑临时连接板子的 Wi-Fi。
3. 连接板子期间 Windows 可能显示「无互联网」，画面仍能传到电脑。看到播放器弹出时观察画面有没有停住或提前断开。录完后等调试台提示清理结束，再按提示填写观察。
4. 录完会尝试切回原来的 Wi-Fi。自动切换失败时，按调试台显示的网络名称和密码，从屏幕右下角手动连接。结束后如果界面提示没切回，手动连回原来的 Wi-Fi。
5. 恢复联网后告诉我「看最新日志」。调试记录在 `station/bench/runs/`，每次都有一套。双击 `summary.md` 可以看录到了几秒。画面和截图留在这台电脑。

**如果 Windows 读不到 Wi-Fi 状态**，调试台会让你选择手动连，或者退出后在「设置 → 隐私和安全性 → 位置」开启位置服务，再打开调试台。选手动时，请预留时间在录制前连到板子，结束后切回原来的网络。

**如果提示本次上电已经用过相机**，拔掉板子的全部电源，等所有指示灯全灭 10 秒，再接回并重新打开调试台。完全断电后仍提示时，把最新记录留给我检查。

2026 年 11 月 25 日方案 7 已在实板显示画面，录制提前停止。下一轮先运行下面的方案 8，再根据结果决定如何复查图传。

## 测画面延迟

方案 6 录制稳定以后，用一个新的上电周期测延迟。方案 4 会截取整屏，截图保存在本机。

1. 大数字出来后，把 K230 镜头对准**左半边的数字**。当右半边的画面也能清楚看到这组数字时，保持板子位置。
2. 播放开始 10 秒后会每隔约 4 秒截一次图，正常会保存 7 张。截图时保持这个窗口在最前面。按 Esc 可以提前停止。
3. 录完可选择打开截图目录。从同一张图读左、右两个数字，按提示输入。读不清的跳过。至少取 5 张，记录两边相差多少毫秒的中位数和最大值，最大值不超过 300 毫秒才通过。也可先保留截图，恢复联网后说「看最新日志」。

## 维护和复现

- 仓库入口是 `station/bench/图传调试台.bat`。桌面入口指向当前工作区。电脑端使用项目 Python 环境以及 VLC 3。播放器可通过忽略提交的 `station/bench/runtime/vlc/` 提供，也可以在 `station/bench/config.local.json` 设 `vlc` 路径，或使用 Windows 上已安装的 VLC。板端网络信息从同一个本地配置读取。该文件缺失时复用已有 `k230/ap/config.local.json` 的 `wifi` 项。
- 方案文件在 `plans/`。`version` 是方案版本，`caching_ms` 控制播放缓冲，`criteria` 给出实际录制时长和帧数相对于目标的最低比例。达到门槛还要求收到指定尺寸、确认实际传输方式、板端完成清理和退出。自动切换模式会检查网络是否切回。
- `runs/` 不提交。保留串口、VLC 输出、录像、截图和用户观察。向公开仓库提交现场诊断时，只从摘要中选取必要的已脱敏结构化数据。自由备注留在本地。
- 启动时先验 Python 组件、VLC、本地配置、板端媒体状态和 `info`；从启动失败中保存的 `*-preflight` 记录没有开始采集。延迟模式把 VLC 嵌在时钟旁边，具体视频输出由播放器和驱动决定。屏幕刷新与画面读取会影响毫秒数的精度。
- 方案 6 额外使用 `ffprobe` 从录像核对帧率。它可位于 `station/bench/runtime/ffprobe.exe` 或系统 `PATH`，取到的中段图像 `motion-preview.png` 仅在本机保存。
- 方案 7 先做约 60 秒纯 TCP 对照，这段没有画面。随后在同一个板载 AP 和 TCP 8554 上播放并录像。等待清理和网络恢复提示后再结束调试台。
- 会话开始前先检查本次上电的媒体标志，只补齐 `/sdcard/hcv/hcv/` 中不一致的模块。若使用 `/sdcard` 上的标志，已经确认完全断电后仍被旧标志拦截，可现场手动删除 `/sdcard/hcv/run/hcv-media-session-used` 后再检查。一次上电只启动一次媒体会话。
- 电脑回环复现命令如下。在测试窗口连续输入 `1`、`1`，录制结束后按提示填写观察。需要现成的 FFmpeg、MediaMTX 和 VLC。

```powershell
python -m station.bench.bench --simulate <ffmpeg.exe> <mediamtx.exe>
```
## 方案 8 无线链路体检

双击 [方案8无线链路体检.bat](方案8无线链路体检.bat)。保持 K230 接着 USB，关闭 CanMV，把电脑和板子的距离保持在约 0.5–1 米。有无线适配器电源设置时选「最高性能」。输入 `1` 后开始，正常约 5 分钟，期间电脑会临时连接 K230 的 AP。同一次上电即可运行，无需转动镜头、重新拍摄或给板子断电。

测试使用 8554 端口。TCP 从板子发到电脑，按 25、50、100、200 kB/s 各测 30 秒；反方向测 100 kB/s 30 秒。UDP 测 100、200 kB/s 各 30 秒。最后从板子向电脑做 300 kB/s 的 60 秒 TCP 检查。`runs/*-p8-link/summary.json` 保存每档两端的字节数、时长、最大收包间隔和错误；信号或速率字段取不到时留空。TCP 重置发起端需要另外捕获报文，出现重置时该字段记为 `unknown`。

测试前可在 Windows 的「设置 → 隐私和安全性 → 位置」打开位置服务，让调试台记录 Wi-Fi 信号百分比和收发速率。若 Windows 仍拒绝读取网络状态，按提示手动连接 K230 的 AP。结束后若原 Wi-Fi 未自动恢复，用右下角网络图标切回。

### TCP 重置复现后的取证

只有方案 8 再次记录到连接重置时，才用管理员 PowerShell 对下一轮取证。先运行下面两条命令。确认没有进行中的抓包且筛选器列表为空，再设置本轮筛选。

```powershell
pktmon status
pktmon filter list
```

```powershell
$captureDir = Join-Path $env:TEMP ('hcv-rst-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
New-Item -ItemType Directory -Path $captureDir | Out-Null
$captureFile = Join-Path $captureDir 'link.etl'
pktmon filter add HCV8554Syn -t TCP SYN -p 8554
pktmon filter add HCV8554Rst -t TCP RST -p 8554
pktmon start --capture --comp nics --pkt-size 96 --file-size 16 --file-name $captureFile
```

确认启动成功后，在另一窗口运行方案 8。测试一结束，就在原管理员 PowerShell 停止抓包并转换。如果启动失败或取消测试，也要清除本轮添加的筛选器。`pktmon filter remove` 会清除全部筛选器，只在已经确认原本为空时执行。

```powershell
pktmon stop
pktmon filter remove
pktmon etl2pcap $captureFile --out (Join-Path $captureDir 'link.pcapng')
pktmon etl2txt $captureFile --timestamp --out (Join-Path $captureDir 'link.txt')
```

将每档的 TCP 建连时刻和两端报错时刻与抓包对齐。按连接去掉重复记录，汇报 RST 首次出现的时刻、电脑网卡看到的方向和标志位。只有实测捕获到对应 RST 时才报告方向。ETL、PCAPNG、抓包文本和实际地址留在本机。
