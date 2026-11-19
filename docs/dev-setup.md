# H-CV 开发环境

使用 Python 3.10 或更新版本。以下命令在仓库根目录的 PowerShell 中执行，使用虚拟环境的解释器，无须激活环境。

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q
```

在 macOS 或 Linux 上，把 `.\.venv\Scripts\python.exe` 换成 `.venv/bin/python`。

测试会在临时目录中读取配置，无需连接板卡。开始配置实机时，把 `k230/config.example.json` 复制为 `k230/config.json`，再根据实测调整参数。可在同目录放置 `config.local.json`，仅保存本机覆盖项，例如 Wi-Fi 密码。嵌套对象按字段覆盖，数组和标量整体替换。配置先合并再校验。示例参数来自 ICD §5.1，板卡和摄像头实测安排在 P0。示例的 Wi-Fi 密码是占位符，启用 AP 或 STA 时必须在本地配置提供 8–63 字符密码。仅调试板卡时，可用本地配置 {"wifi": {"mode": "off"}}。日志通过 format_config_for_log() 输出脱敏配置。

requirements.lock 是本次 Windows / CPython 3.14 的依赖快照，可在相同环境用 -r requirements.lock 复现。其他 Python 3.10+ 环境用 requirements.txt。

## P0-T02 板卡访问原型

TF 卡通过 USB 显示成电脑磁盘时，请停止从该磁盘写入、删除或复制文件，再使用串口工具。在电脑与板卡同时写 FAT 文件系统可能损坏 TF 卡。
使用 MicroPython 的 mpremote USB raw REPL 传输。它提供脚本执行和板上文件读写，便于在命令行采集原始输出。庐山派当前固件是否响应 raw REPL 要通过实机确认。电脑没有枚举到板卡时，先确认数据线和 TF 卡。若 CanMV IDE 已连接到同一个串口，先断开 IDE 中的连接。

在板卡连接前后分别执行 ports，对照新增设备，已知 CanMV USB VID/PID（1209:abd1）只有一个时，默认自动选口。也可以用实际端口替换下例 COM3。需要排查握手线影响时，可在子命令前加 --dtr 0 --rts 0；默认沿用 mpremote 的设置。

    .\.venv\Scripts\python.exe tools/board/board.py ports
    .\.venv\Scripts\python.exe tools/board/board.py --port COM3 info
    .\.venv\Scripts\python.exe tools/board/board.py --port COM3 ls /sdcard
    .\.venv\Scripts\python.exe tools/board/board.py --port COM3 backup

backup 单独执行，显示进度并使用 10 分钟预算。它记录全部文件的路径与大小，保存 /sdcard 根目录的 Python 文件，并对这些脚本和不超过 1 MiB 的小文件计算 SHA-256。大文件只记录元数据并标记 hashed=false。备份目录为 reports/data/P0-EXP00/board-backup/。更换 TF 卡或板卡时，将旧备份归档到其他本机目录，再备份新卡。原始备份在本机忽略，公开仓库只提交检查过的证据。put、run 和实机验收脚本启动前都要求已有完整备份，写入目标限于 /sdcard/hcv/。上传先写临时文件，再比较 SHA-256。旧文件会保留到新文件校验和替换成功。run 超时会先发送一个独立 Ctrl-C 回到 raw REPL，再尝试软复位和媒体清理。退出码 3 表示需要现场断电重接。cleanup 可单独尝试媒体清理。reset 命令会显式触发 MicroPython 软重启。

    .\.venv\Scripts\python.exe tools/board/board.py --port COM3 run tools/board/hello_k230.py --timeout 5 --stream


首次 info 和 backup 成功后，运行以下真实板卡验收。把 COM3 换成板卡端口。命令会做 20 次上传、运行、取回，检查每次 1000 行都连续无丢失，验证调用 os.exitpoint() 的 Python 循环在 5 秒超时后中断，并确认 info 还能成功。循环带 30 秒板端保护，保护触发不计入五秒验收。用 --stage transfer、--stage timeouts 或 --stage sleep 可单独复验传输、协作循环或短休眠循环。无保护忙循环的首次实机结果见 P0-T02 报告。output 必须是尚不存在的目录。

    .\.venv\Scripts\python.exe tools/board/verify.py --port COM3 --output reports/data/P0-T02/board-01

原始日志公开前先检查路径、固件输出和备份是否含私人信息。


## P0-EXP00 盘点准备

串口工具完成 P0-T02 的实机验收后，可以开始板卡盘点。命令只采集固件、内存和 API 数据。确认固件后单独验证摄像头取帧，并记录 raw REPL 软复位是否启动原 main.py。

    .\.venv\Scripts\python.exe tools/board/board.py --port COM3 run k230/exp/exp00_inventory.py --timeout 60 --stream --log reports/data/P0-EXP00/inventory.raw.log
    .\.venv\Scripts\python.exe tools/board/config_smoke.py --port COM3 --log reports/data/P0-EXP00/config-smoke.log

固件镜像的文件名和 SHA-256 通过用户原来的镜像文件另记。脚本不读取 Wi-Fi 本地配置。配置测试使用公开示例和测试用密码。


## P0-EXP02 光照阶跃

白纸放在镜头下方。开始前准备台灯，跟着串口输出里 HCV_LIGHT 提示在 off 和 on 之间切换。每个状态持续 3 秒，共五轮。采集当前用经实测的 1280×720@90 和 640×360 灰度通道配置。

    .\.venv\Scripts\python.exe tools/board/board.py --port COM3 put k230/hcv/guard.py /sdcard/hcv/guard.py
    .\.venv\Scripts\python.exe tools/board/board.py --port COM3 run k230/exp/exp02_isp_step_response.py --timeout 55 --stream --log exp02-auto.log
    .\.venv\Scripts\python.exe tools/analysis/exp02_response.py exp02-auto.log exp02-steps.csv

稳定时间以灯光操作的提示事件为起点，包含人工操作延迟。传感器源码列出的曝光、增益和白平衡接口待另测实际作用。
