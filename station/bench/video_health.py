"""Check the first 15 seconds after a single normal RTSP client exit."""
from datetime import datetime
import sys
import time
from urllib.parse import urlsplit


WINDOW = 15
MAX_GAP = 1.75   # One 1 Hz report plus 750 ms for polling, scheduling and serial delivery.


def assess(run, clean):
    capture = run.summary["capture"]
    client = capture.get("receiver_exit_at") or capture.get("client_done_at")
    normal = bool(client and capture.get("exit_code") == 0 and not capture.get("client_end_reason")
                  and not capture.get("operator_cancelled"))
    origin = datetime.fromisoformat(client).timestamp() if client else None
    if run.summary["plan"]["rtp_diagnostic"]:
        closed = capture.get("upstream_closed_ns")
        origin = max(origin, closed / 1e9) if origin is not None and closed else None
    path = run.directory / "board.serial.log"
    rows = {"MAIN": [], "ENCODER": []}
    stops, cleanups = [], []
    invalid = 0
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.is_file() else []
    for line in lines:
        timestamp, _, body = line.partition(" ")
        try:
            if body == "HCV_SERVICE_STOP_BEGIN":
                stops.append(datetime.fromisoformat(timestamp).timestamp())
            elif body == "HCV_CH0_CONFIG_CLEANUP":
                cleanups.append(datetime.fromisoformat(timestamp).timestamp())
            elif body.startswith(("HCV_VIDEO_MAIN,", "HCV_VIDEO_ENCODER,")):
                label, *values = body.split(",")
                stamp = datetime.fromisoformat(timestamp).timestamp()
                if origin is not None and origin <= stamp <= origin + WINDOW:
                    rows[label.removeprefix("HCV_VIDEO_")].append((stamp, tuple(map(int, values))))
        except ValueError:
            invalid += 1
    remaining = stops[0] - origin if len(stops) == 1 and origin is not None else None
    normal_cleanup = bool(clean and len(cleanups) == len(stops) == 1 and cleanups[0] >= stops[0])
    result = {"simulation": run.summary["simulation"], "client_normal": normal, "client_exit_at": client,
              "observation_start_epoch": origin, "stop_begin_epoch": stops[0] if len(stops) == 1 else None,
              "exit_to_stop_s": remaining, "cleanup_confirmed": normal_cleanup, "invalid_records": invalid, "threads": {}}
    for name, reports in rows.items():
        times = [r[0] for r in reports]
        bounds = [origin, *times, origin + WINDOW] if origin is not None else []
        gaps = [b - a for a, b in zip(bounds, bounds[1:])]
        values = [r[1] for r in reports]
        width = 1 if name == "MAIN" else 3
        advancing = (len(values) >= 2 and all(len(v) == width for v in values)
                     and all(all(y > x for x, y in zip(a, b)) for a, b in zip(values, values[1:])))
        result["threads"][name] = dict(samples=len(times), max_gap_s=max(gaps, default=None),
                                       first=values[0] if values else None, last=values[-1] if values else None,
                                       continuous=bool(gaps and min(gaps) >= 0 and max(gaps) <= MAX_GAP and advancing))
    usb = run.summary.get("usb_presence", {})
    result["usb_observed"] = bool(usb.get("samples", 0) > usb.get("unknown", 0))
    result["usb_loss_observed"] = bool(usb.get("first_absent_at") or usb.get("pnp_1010_at"))
    result["passed"] = bool(not invalid and normal and normal_cleanup and remaining is not None and remaining >= WINDOW
                            and not result["usb_loss_observed"]
                            and all(t["continuous"] for t in result["threads"].values()))
    run.summary["post_client_health"] = result
    return result["passed"]


def simulator(url, duration):
    return [sys.executable, "-u", "-m", "station.bench.video_health", url, str(duration)]


if __name__ == "__main__":
    endpoint = urlsplit(sys.argv[1])
    print("HCV_AP_READY")
    print(f"HCV_RTSP_PRIVATE,{endpoint.hostname},{endpoint.port},{endpoint.path[1:]}")
    print("HCV_CH0_CONFIG_READY")
    began = time.monotonic()
    for second in range(1, int(sys.argv[2]) + 1):
        time.sleep(max(0, began + second - time.monotonic()))
        print(f"HCV_VIDEO_MAIN,{second}")
        print(f"HCV_VIDEO_ENCODER,{60*second},{60*second},{60*second}")
    print("HCV_SERVICE_STOP_BEGIN")
    print("HCV_CH0_CONFIG_CLEANUP")
