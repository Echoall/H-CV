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

使用 MicroPython 的 mpremote USB raw REPL 传输。它提供脚本执行和板上文件读写，便于在命令行采集原始输出。庐山派当前固件是否响应 raw REPL 要通过实机确认。电脑没有枚举到板卡时，先确认数据线和 TF 卡。若 CanMV IDE 已连接到同一个串口，先断开 IDE 中的连接。

在板卡连接前后分别执行 ports，对照新增设备，然后用实际端口替换下例 COM3。

    .\.venv\Scripts\python.exe tools/board/board.py ports
    .\.venv\Scripts\python.exe tools/board/board.py --port COM3 info
    .\.venv\Scripts\python.exe tools/board/board.py --port COM3 backup
    .\.venv\Scripts\python.exe tools/board/board.py --port COM3 ls /sdcard

backup 读取 TF 卡目录和已有 /sdcard/main.py，保存在 reports/data/P0-EXP00/board-backup/。原始备份在本机忽略，公开仓库只提交检查过的证据。首次 put 会先检查备份，写入目标限于 /sdcard/hcv/。reset 命令会显式触发 MicroPython 软重启。

    .\.venv\Scripts\python.exe tools/board/board.py --port COM3 run tools/board/hello_k230.py --timeout 5 --stream


首次 info 和 backup 成功后，运行以下真实板卡验收。把 COM3 换成板卡端口。命令会做 20 次上传、运行、取回，检查每次 1000 行都连续无丢失，最后发起一个 5 秒超时测试并确认再次执行 info 成功。output 必须是尚不存在的目录。

    .\.venv\Scripts\python.exe tools/board/verify.py --port COM3 --output reports/data/P0-T02/board-01

原始日志公开前先检查路径、固件输出和备份是否含私人信息。
