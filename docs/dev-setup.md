# H-CV 开发环境

使用 Python 3.10 或更新版本。以下命令在仓库根目录的 PowerShell 中执行，使用虚拟环境的解释器，无须激活环境。

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q
```

在 macOS 或 Linux 上，把 `.\.venv\Scripts\python.exe` 换成 `.venv/bin/python`。

测试会在临时目录中读取配置，无需连接板卡。开始配置实机时，把 `k230/config.example.json` 复制为 `k230/config.json`，再根据实测调整参数。可在同目录放置 `config.local.json`，仅保存本机覆盖项，例如 Wi-Fi 密码。嵌套对象按字段覆盖，数组和标量整体替换。配置先合并再校验。示例参数来自 ICD §5.1，板卡和摄像头实测安排在 P0。
