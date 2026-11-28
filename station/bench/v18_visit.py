"""Operator-led v1.8 visit. Every physical camera stage requests a fresh power cycle and consent."""
import argparse
from datetime import datetime
from pathlib import Path
import subprocess
from types import SimpleNamespace

from station.bench import bench, hubei, hubei_m1, m1_v18, wlan


def check_stop(run, clean):
    cap = run.summary["capture"]
    serial = run.directory / "board.serial.log"
    lines = serial.read_text(encoding="utf-8").splitlines() if serial.is_file() else []
    stop_times = [datetime.fromisoformat(s.split(" ", 1)[0]) for s in lines
                  if s.endswith(" HCV_SERVICE_STOP_BEGIN")]
    clean_times = [datetime.fromisoformat(s.split(" ", 1)[0]) for s in lines
                   if s.endswith(" HCV_CH0_CONFIG_CLEANUP")]
    playing = cap.get("client_playing_at")
    ended = cap.get("client_end_at")
    ordered = bool(len(stop_times) == len(clean_times) == 1 and playing and ended
                   and datetime.fromisoformat(playing) < stop_times[0] <= datetime.fromisoformat(ended)
                   and stop_times[0] <= clean_times[0])
    run.summary["stop_order_verified"] = ordered
    return bool(clean and ordered and cap.get("frames", 0) > 0 and not cap.get("operator_cancelled")
                and cap.get("transport_observed") == ["tcp"] and cap.get("client_end_reason") == "eof")


def service_stop(folder, ask):
    vlc, _, _ = bench.tools()
    original = wlan.snapshot()
    if not original or not original[2] or original[1] == bench.config_wifi()["ssid"]:
        raise RuntimeError("请先接回可恢复的日常 Wi-Fi。")
    plan = dict(hubei.plan("ap"), id="v18-service-stop", title="板端主动停止图传",
                seconds=70, firmware_compare=True, supervised_stop=True)
    run = bench.Run(folder / "service-stop", plan, False)
    ok = False
    try:
        clean = hubei.physical(run, "ap", ask, original, vlc)
        ok = check_stop(run, clean)
        if ok:
            code, output, _ = run.command("after-info", bench.BOARD + ["info"], timeout=18)
            ok = code == 0 and hubei.image_matches(output, "v1.8")
    finally:
        restored = False
        try:
            restored = wlan.restore(original, diagnostics=run.summary["network"])
        finally:
            run.summary["network"]["restored"] = restored
            run.summary["passed"] = ok and restored
            run.finish()
            if not restored:
                print(wlan.MANUAL_RESTORE)
    return int(not run.summary["passed"])


def visit(root, ask=input):
    root.mkdir(parents=True)
    args = SimpleNamespace(runs=root, replay=None, skip_mode_list=False, timing_only=True)

    def longrun():
        mode = ask("M1 本轮回车按常规先枚举；若上次未进入采集，请输入 2，独立上电跳过枚举 > ").strip()
        if mode not in ("", "2"):
            raise RuntimeError("未选择 M1 测量方式，本项未开始。")
        args.skip_mode_list = mode == "2"
        return m1_v18.main(args, ask)

    stages = (("图传由板端停止", lambda: service_stop(root, ask)),
              ("独占找球约十分钟", longrun),
              ("图传与找球同时运行", lambda: hubei_m1.wizard(args, ask, v18_visit=root)))
    report = {"simulation": False, "stages": [], "completed": False}
    try:
        for title, action in stages:
            print(title, flush=True)
            if ask("准备开始这项测试请按回车，结束整次测试输入 q > ").strip().lower() == "q":
                break
            row = {"title": title, "started": bench.now(), "passed": False}
            report["stages"].append(row)
            bench.write_json(root / "visit.json", report)
            try:
                row["passed"] = action() == 0
            except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
                row["message"] = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
                print(row["message"])
            finally:
                row["ended"] = bench.now()
                bench.write_json(root / "visit.json", report)
            print("本项已记录。", root, flush=True)
        report["completed"] = len(report["stages"]) == len(stages)
    finally:
        report["passed"] = report["completed"] and all(s["passed"] for s in report["stages"])
        bench.write_json(root / "visit.json", report)
    return int(not report["passed"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, default=bench.HERE / "runs")
    parser.add_argument("--simulate", type=Path, nargs=2, metavar=("FFMPEG", "MEDIAMTX"))
    parser.add_argument("--from-receivers", action="store_true",
                        help="从接收器对照继续，随后做 S4 两档")
    args = parser.parse_args()
    with bench.only_instance(args.runs):
        root = (args.runs / (datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-v18-visit")).resolve()
        from station.bench.field_visit import visit as field_visit
        raise SystemExit(field_visit(root, simulation=args.simulate, from_receivers=args.from_receivers))
