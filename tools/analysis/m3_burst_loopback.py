"""Exercise the five field shapes with local sockets, without USB or Wi-Fi switching."""

import argparse
import json
from pathlib import Path
import socket
from threading import Thread

from station.bench import link_check
from tools.board import m3_link_check


def run(output, seconds=60):
    rows = []
    for index, (protocol, direction, rate, _) in enumerate(m3_link_check.BURST_TRIALS):
        trial = (protocol, direction, rate, seconds)
        shape = m3_link_check.BURST_SHAPES[index]
        with socket.socket() as local:
            local.bind(("127.0.0.1", 0))
            port = local.getsockname()[1]
        m3_link_check.PORT = link_check.PORT = port
        sender = []

        def serve():
            sender.append(m3_link_check.serve("127.0.0.1", index, trial, shape=shape))

        worker = Thread(target=serve, daemon=True)
        worker.start()
        receipt = link_check.client_trial("127.0.0.1", index, trial, observe_close=True)
        worker.join(10)
        if not sender:
            raise RuntimeError("loopback sender did not finish")
        row = dict(shape=shape, protocol=protocol, direction=direction, target_kBps=rate,
                   seconds=seconds, board=sender[0], pc=receipt)
        link_check.summarize_trial(row)
        rows.append(row)
        output.write_text(json.dumps({"simulation": True, "trials": rows}, indent=2) + "\n", encoding="utf-8")
    return all(row["sustained_target"] and (row["pc"].get("terminal") == "EOF" if row["protocol"] == "tcp"
                                            else row["udp_loss_pct"] == 0) for row in rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    raise SystemExit(int(not run(args.output)))
