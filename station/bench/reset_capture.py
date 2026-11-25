"""Optional local Windows RST capture for an operator-started field session."""

import base64
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid


def summarize_packets(path, board):
    from scapy.all import IP, TCP, PcapNgReader
    from datetime import datetime, timezone

    events = []
    with PcapNgReader(str(path)) as packets:
        for packet in packets:
            ip = packet.getlayer(IP)
            tcp = ip.payload if ip is not None else None
            if not isinstance(tcp, TCP) or not int(tcp.flags) & 4:
                continue
            if board not in (ip.src, ip.dst) or {554, 8554}.isdisjoint((tcp.sport, tcp.dport)):
                continue
            events.append(dict(time=datetime.fromtimestamp(float(packet.time), timezone.utc).isoformat(),
                               direction="from_board" if ip.src == board else "toward_board",
                               sequence=int(tcp.seq)))
    return events


class ResetCapture:
    def __init__(self, root):
        self.control = root / "rst-control"
        self.control.mkdir()
        self.token = str(uuid.uuid4())
        self.source = Path(os.environ.get("SystemRoot", "C:/Windows")) / "Temp" / ("hcv-rst-" + self.token)
        self.process = None
        self.peer = None
        self.summary = {"status": "not_requested"}

    def wait_state(self, pending, seconds):
        deadline = time.monotonic() + seconds
        while True:
            try:
                value = (self.source / "state").read_text(encoding="utf-8").strip()
                if value and value not in pending:
                    return value
            except OSError:
                pass
            if self.process.poll() is not None:
                return "unavailable_or_declined"
            if time.monotonic() >= deadline:
                return "helper_timeout"
            time.sleep(0.15)

    def prepare(self):
        # Pass the fixed helper text as Unicode, with no user input evaluated as code.
        helper = Path(__file__).with_name("capture_rst.ps1").read_text(encoding="utf-8")
        folder = str(self.control).replace("'", "''")
        script = f"& {{\n{helper}\n}} '{folder}' {os.getpid()} '{self.token}'"
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        launch = ("$ErrorActionPreference='Stop'; $p=Start-Process -FilePath "
                  "(Join-Path $PSHOME 'powershell.exe') -Verb RunAs -WindowStyle Hidden "
                  f"-ArgumentList '-NoProfile','-NonInteractive','-EncodedCommand','{encoded}' "
                  "-PassThru; $p.WaitForExit(); exit $p.ExitCode")
        try:
            self.process = subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive",
                                            "-Command", launch], stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL)
            self.summary["status"] = self.wait_state(set(), 45)
        except OSError:
            self.summary["status"] = "helper_unavailable"
        if self.summary["status"] != "ready":
            (self.control / "finish").touch()
        return self.summary["status"]

    def start(self, board):
        if self.peer and board != self.peer:
            self.summary["peer_changed"] = True
        if self.summary["status"] == "ready":
            self.peer = board
            temp = self.control / "peer.tmp"
            temp.write_text(board, encoding="ascii")
            temp.replace(self.control / "peer")
            self.summary["status"] = self.wait_state({"ready"}, 8)

    def finish(self):
        (self.control / "finish").touch()
        if self.summary["status"] == "capturing":
            self.summary["status"] = self.wait_state({"ready", "capturing"}, 20)
            if self.summary["status"] == "complete":
                try:
                    for name in ("reset.etl", "reset.pcapng"):
                        shutil.copyfile(self.source / name, self.control / name)
                    self.summary["resets"] = summarize_packets(self.control / "reset.pcapng", self.peer)
                except (OSError, ValueError, ImportError, EOFError):
                    self.summary["status"] = "capture_available_analysis_incomplete"
        return self.summary
