# M3 AP + CHN0 first-session evidence

- Git commit before the board run: `d7f964a`
- K230 firmware: `v1.4-19-ga7de1c8`
- MicroPython: `1.21.0`
- This was the first media session after a complete power cycle.
- The host WLAN was not switched and VLC was not started.

## Reproduction

```powershell
.\.venv\Scripts\python.exe tools\board\board.py --port auto put <local-config.local.json> /sdcard/hcv/config.local.json
.\.venv\Scripts\python.exe tools\board\board.py --port auto put k230/hcv/ch0_rtsp_probe.py /sdcard/hcv/hcv/ch0_rtsp_probe.py
.\.venv\Scripts\python.exe tools\board\board.py --port auto put tools/board/m3_ch0_ap_first_probe.py /sdcard/hcv/m3_ch0_ap_first_probe.py
.\.venv\Scripts\python.exe tools\board\board.py --port auto run tools/board/m3_ch0_ap_first_probe.py --timeout 20 --stream --log reports/data/M3/m3-20261122-ap-first/ap-first.log
.\.venv\Scripts\python.exe tools\board\board.py --port auto info
```

The local configuration file is intentionally excluded from the repository. The saved AP URL is redacted to `<board-ap-ip>`.
