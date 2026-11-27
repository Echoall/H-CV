"""Check a board API inventory against methods used by the M3 probes."""

import argparse
from pathlib import Path


REQUIRED = {
    "MediaManager": {"link", "init", "deinit"},
    "Encoder": {"SetOutBufs", "Create", "Start", "Stop", "Destroy",
                "GetStream", "ReleaseStream"},
    "rtsp_server": {
        "rtspserver_init",
        "rtspserver_createsession",
        "rtspserver_start",
        "rtspserver_stop",
        "rtspserver_deinit",
        "rtspserver_getrtspurl",
        "rtspserver_sendvideodata",
    },
}


def read_inventory(path):
    inventory = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        parts = line.strip().split(",")
        if len(parts) >= 2 and parts[0] == "HCV_API":
            inventory[parts[1]] = set(parts[2:])
    return inventory


def missing_methods(inventory):
    return {
        name: sorted(REQUIRED[name] - inventory.get(name, set()))
        for name in REQUIRED
        if REQUIRED[name] - inventory.get(name, set())
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("log", type=Path)
    args = parser.parse_args()
    missing = missing_methods(read_inventory(args.log))
    if missing:
        for name, methods in missing.items():
            print("HCV_API_MISSING,%s,%s" % (name, ",".join(methods)))
        return 1
    print("HCV_API_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
