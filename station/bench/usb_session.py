"""Observe K230 enumeration during a media session without opening its serial port."""
from contextlib import contextmanager
from datetime import datetime
import json
import subprocess
from threading import Event, Thread
import time

from station.bench import bench, power


def removals(started, ended):
    # Both timestamps are generated here, never taken from operator input.
    begin, end = (datetime.fromisoformat(value).isoformat() for value in (started, ended))
    script = """
$ErrorActionPreference='Stop'
try {
  $events=Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-Kernel-PnP/Device Management';Id=1010;
    StartTime=([datetimeoffset]'START').LocalDateTime;EndTime=([datetimeoffset]'END').LocalDateTime}
  $events | Where-Object {$_.ToXml() -match 'VID_1209.{1,12}PID_ABD1'} |
    Sort-Object TimeCreated | ForEach-Object {$_.TimeCreated.ToString('yyyy-MM-ddTHH:mm:ss.fffzzz')}
} catch { if ($_.FullyQualifiedErrorId -notlike 'NoMatchingEventsFound*') {exit 1} }
exit 0
""".replace("'START'", repr(begin)).replace("'END'", repr(end))
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                                capture_output=True, text=True, errors='replace', timeout=12)
        if result.returncode == 0:
            return [datetime.fromisoformat(line.strip()).isoformat(timespec='milliseconds')
                    for line in result.stdout.splitlines() if line.strip()]
    except (ValueError, OSError, subprocess.TimeoutExpired):
        pass
    return None


@contextmanager
def monitor(run, board_id):
    stop = Event()
    status = run.summary['usb_presence'] = dict(started=bench.now(), sample_period_s=0.5,
                                              samples=0, unknown=0, first_absent_at=None)
    def poll():
        deadline = time.monotonic()
        with (run.directory / 'usb-presence.jsonl').open('w', encoding='utf-8', buffering=1) as log:
            while not stop.is_set():
                try:
                    present = board_id in power.boards()
                except OSError:
                    present = None
                stamp = bench.now()
                status['samples'] += 1
                if present is None:
                    status['unknown'] += 1
                if present is False and status['first_absent_at'] is None:
                    status['first_absent_at'] = stamp
                log.write(json.dumps(dict(at=stamp, present=present)) + '\n')
                deadline += 0.5
                if deadline < time.monotonic():
                    deadline = time.monotonic() + 0.5
                stop.wait(max(0, deadline - time.monotonic()))
    worker = Thread(target=poll, daemon=True)
    worker.start()
    try:
        yield
    finally:
        status['ended'] = bench.now()
        stop.set()
        worker.join(timeout=3)
        status['poller_stopped'] = not worker.is_alive()
        status['pnp_1010_at'] = removals(status['started'], status['ended'])
