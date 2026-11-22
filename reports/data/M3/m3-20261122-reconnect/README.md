# M3 reconnect evidence

- Git commit: `55c2e5d`
- K230 firmware: `v1.4-19-ga7de1c8`
- MicroPython: `1.21.0`
- Port: `COM3`, USB VID:PID `1209:ABD1`

## Reproduction commands

```powershell
.\.venv\Scripts\python.exe tools\board\board.py --port auto run tools\board\m3_api_inventory.py --timeout 10 --stream --log reports/data/M3/m3-20261122-reconnect/api-inventory.log
.\.venv\Scripts\python.exe tools\analysis\check_m3_api_inventory.py reports/data/M3/m3-20261122-reconnect/api-inventory.log
.\.venv\Scripts\python.exe tools\board\board.py --port auto run tools\board\m3_ch0_rtsp_probe.py --timeout 12 --stream --log reports/data/M3/m3-20261122-reconnect/ch0-baseline.log
.\.venv\Scripts\python.exe tools\board\board.py --port auto info
```

`sta.log` is a bounded WLAN preflight. It does not start media and records the failed STA connection to the host WLAN.
