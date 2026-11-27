"""Single-board v1.8 M1 field measurement and offline assessment of its serial log."""
import argparse
from pathlib import Path
import re

from station.bench import bench, hubei
from tools.analysis.m1_board import analyze


MODULES = ("__init__", "calibration", "compat", "config", "detect_profile", "guard", "health",
           "link_uart", "media_session_guard", "proto", "tracker")


def prepare(run, *, combined=False):
    modules = MODULES + ("hubei_detection",) if combined else MODULES
    files = [(f"k230/hcv/{name}.py", f"hcv/{name}.py") for name in modules]
    runner = "hubei_m1_runner.py" if combined else "main_hcv.py"
    files.extend([("k230/main_hcv.py", runner),
                  ("reports/data/M1/m1-config.json", "m1_v18/config.json")])
    for source, target in files:
        code, _, _ = run.command("upload-" + Path(target).name, bench.BOARD + [
            "put", str(bench.ROOT / source), "/sdcard/hcv/" + target, "--hiwonder-v18"], timeout=45)
        if code != 0:
            raise RuntimeError("板端文件核对失败：" + target)


def assess(path):
    metrics = analyze(path)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    starts = [s for s in lines if s.startswith("HCV_M1_BEGIN,")]
    if len(starts) != 1 or sum(s.startswith("HCV_M1_SUMMARY,") for s in lines) != 1:
        raise ValueError("需要单次完整长跑记录")
    summary = metrics["summary"]
    rounds = metrics.get("rounds", [])
    samples = [s for s in lines if s.startswith("HCV_M1_SAMPLE,")]
    sensor = any(re.search(r"find sensor gc2093.*output 1280x720@90\b", s) for s in lines)
    spans = [int(s.split(",")[1]) for s in lines if s.startswith("HCV_M1_CAPTURE_SPAN_US,")]
    sample_count = sum(int(s.split(",", 2)[1].split("=")[1]) for s in samples)
    evidence = {
        "sensor_selected_720p90": sensor,
        "configured_m1": "mode=1280x720x90," in starts[0] and "detector_fast=1," in starts[0],
        "ten_minutes": summary["elapsed_us"] >= 600_000_000,
        "rounds_complete": bool(rounds and rounds[-1].get("final") == 1
                                and [r["round"] for r in rounds] == list(range(len(rounds)))
                                and sum(r["frames"] for r in rounds) == summary["frames"]),
        "process_windows_complete": bool(samples and all(",process=" in s for s in samples)
                                         and sample_count == summary["frames"]),
        "p95_within_6ms": 0 < metrics["max_sample_process_p95_us"] <= 6000,
        "parser_clean": summary.get("rx_crc") == summary.get("rx_len") == 0,
    }
    span = spans[0] if len(spans) == 1 else None
    evidence["capture_span_complete"] = span is not None and 600_000_000 <= span <= summary["elapsed_us"]
    metrics["checks"] = evidence
    metrics["frames_in_sample_windows"] = sample_count
    metrics["capture_span_us"] = span
    metrics["wall_duration_dropped"] = None if span is None else max(0, span * 90 // 1_000_000 - summary["frames"])
    return metrics


def verify_modes(run):
    code, output, _ = run.command("sensor-modes", bench.BOARD + [
        "run", str(bench.ROOT / "tools/board/m1_v18_modes.py"), "--timeout", "12"], timeout=30)
    if code != 0 or not re.search(r"^HCV_M1_SENSOR_MODE v1\.8-\S+ \S*gc2093\S* 1280x720@90 1\s*$",
                                  output, re.MULTILINE | re.IGNORECASE):
        raise RuntimeError("未确认板上 GC2093 的 720p90 模式，请保留枚举日志。")


def field(run, ask):
    hubei.verify_power(run, ask, "v1.8")
    verify_modes(run)
    prepare(run)
    print("保持相机固定，让画面中的检测区空着。采集约 10 分钟。")
    hubei.confirm_capture(ask)
    log = run.directory / "m1.serial.log"
    code, _, _ = run.command("m1", bench.BOARD + ["run", str(bench.ROOT / "tools/board/m1_v18_longrun.py"),
        "--timeout", "680", "--stream", "--log", str(log.resolve())], timeout=710)
    if log.is_file():
        run.summary["measurement"] = assess(log)
    if code != 0 or not log.is_file():
        raise RuntimeError("板上长跑未确认正常返回。")
    if "HCV_M1_V18_RETURNED" not in log.read_text(encoding="utf-8", errors="replace").splitlines():
        raise RuntimeError("板上长跑未确认正常返回。")
    code, output, _ = run.command("after-info", bench.BOARD + ["info"], timeout=18)
    if code != 0 or not hubei.image_matches(output, "v1.8"):
        raise RuntimeError("长跑完成后未读回板端版本。")


def main(args, ask=input):
    directory = args.runs / (bench.datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-m1-v18")
    run = bench.Run(directory, {"id": "m1-v18", "title": "v1.8 独占找球长跑"}, bool(args.replay))
    try:
        if args.replay:
            run.summary["measurement"] = assess(args.replay)
        else:
            field(run, ask)
        m = run.summary["measurement"]
        run.summary["timing_checks_passed"] = all(m["checks"].values())
        run.summary["passed"] = (run.summary["timing_checks_passed"] and
                                 m["wall_duration_dropped"] == 0 and m["summary"]["valid"] == 0)
        run.summary["message"] = ("长跑指标达标" if run.summary["passed"] else
                                  "长跑结果待复核，请查看丢帧、空场 VALID 和分项指标。")
    except (RuntimeError, ValueError, KeyError, OSError, EOFError, KeyboardInterrupt) as error:
        run.summary.update(passed=False, message=str(error) or type(error).__name__)
    finally:
        run.finish()
    print(run.summary["message"], directory)
    return int(not run.summary["passed"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, default=bench.HERE / "runs")
    parser.add_argument("--replay", type=Path, help="用既有串口日志离线复算")
    args = parser.parse_args()
    with bench.only_instance(args.runs):
        raise SystemExit(main(args))
