"""Guide a single K230 session for static calibration, lighting and transitions."""

import argparse
import csv
from itertools import count
import json
from math import ceil
import statistics
import subprocess
import sys
import tempfile
from threading import Timer
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.analysis.m1_ball_evidence import decode_recording
from tools.calib.ruler_lut import (
    BALL_POSITIONS_MM,
    ball_anchor,
    build_ball_calibration,
    build_calibration,
    compare_calibrations,
)
from k230.hcv.calibration import runtime_calibration


BOARD_TOOLS = ROOT / "tools" / "board"
REMOTE = "/sdcard/hcv/"
STATIC_POSITIONS_MM = (0, -50, 50, -100, 100, -120, 120)
CALIB_OK = 1 << 2
VALID = 1


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def save_results(output, results):
    write_json(output / "results.json", results)
    fields = ["case", *dict.fromkeys(key for row in results.values() for key in row)]
    with (output / "results.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(dict(case=case, **row) for case, row in results.items())


def board_command(port, *args):
    return [sys.executable, "-u", str(BOARD_TOOLS / "board.py"), "--port", port, *map(str, args)]


def capture_reference(port, output):
    countdown()
    run_cued_capture(port, output, "m2_reference.py", "HCV_CAPTURE_CUE = True\n",
                     "HCV_M2_CAPTURE_CUE", 30, log_path=output / "reference.log")
    for name in ("m2-ruler.pgm", "m2-reference.json"):
        subprocess.run(board_command(port, "get", REMOTE + name, output / name), check=True, timeout=120)
    reference = json.loads((output / "m2-reference.json").read_text(encoding="utf-8"))
    image = cv2.imdecode(np.fromfile(output / "m2-ruler.pgm", dtype=np.uint8),
                         cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise RuntimeError("could not decode the captured ruler image")
    return image, reference


def select_ruler(image):
    title = "Drag across long ticks only, from -12 cm to +12 cm, then Enter"
    view = cv2.resize(image, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
    try:
        x, y, width, height = cv2.selectROI(title, view, False, False)
    finally:
        cv2.destroyAllWindows()
    if min(width, height) < 4:
        raise ValueError("select the narrow strip containing the tips of the long ruler ticks")
    return round(x / 2), round((x + width) / 2), y // 2, (y + height + 1) // 2


def confirm_numbering(image, calibration, output):
    view = cv2.resize(cv2.cvtColor(image, cv2.COLOR_GRAY2BGR), None, fx=2, fy=2)
    markers = calibration["markers_ref"]
    center_missing = not any(marker["x_mm"] == 0 for marker in markers)
    if center_missing:
        origin = np.interp(0, [marker["x_mm"] for marker in markers],
                           [marker["u_px"] for marker in markers])
        markers = [*markers, {"x_mm": 0, "u_px": origin, "v_px": markers[0]["v_px"]}]
    for marker in markers:
        x, y = round(2 * marker["u_px"]), round(2 * marker["v_px"])
        value = int(marker["x_mm"] / 10)
        color = (0, 200, 255) if value == 0 else (80, 255, 80)
        cv2.line(view, (x, y), (x, max(0, y - 18)), color, 2)
        label = "0*" if value == 0 and center_missing else str(value)
        cv2.putText(view, label, (x - 9, max(12, y - 23)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
    success, preview = cv2.imencode(".png", view)
    if not success:
        raise RuntimeError("could not encode the numbered preview")
    preview.tofile(output / "numbered-ruler.png")
    title = "Confirm 0 cm matches the ruler center. Y to continue, N to stop"
    print("请确认画面中的 0 号刻度与摆杆中心一致。按 Y 继续，按 N 或 Esc 退出。", flush=True)
    if center_missing:
        print("0* 由相邻刻度插值，请对照尺子上实际印刷的 0。", flush=True)
    try:
        cv2.imshow(title, view)
        while True:
            key = cv2.waitKey(100) & 0xFF
            if key in (ord("y"), ord("Y")):
                return True
            if key in (ord("n"), ord("N"), 27) or (
                cv2.getWindowProperty(title, cv2.WND_PROP_VISIBLE) < 1
            ):
                break
    finally:
        cv2.destroyAllWindows()
    return False


def tone(high=False):
    if sys.platform == "win32":
        import winsound
        winsound.Beep(1250 if high else 440, 140)
    else:
        print("\a", end="", flush=True)


def countdown():
    for _ in range(5):
        tone()
        time.sleep(0.86)


def run_cued_capture(port, directory, source, params, cue, timeout, log_path=None):
    if log_path is None:
        directory.mkdir()
        log_path = directory / "board.log"
    with tempfile.TemporaryDirectory(prefix="hcv-field-") as temporary:
        script = Path(temporary) / "capture.py"
        script.write_text(params + (BOARD_TOOLS / source).read_text(encoding="utf-8"), encoding="utf-8")
        command = board_command(port, "run", script, "--timeout", timeout, "--stream",
                                "--log", log_path)
        signalled = False
        with subprocess.Popen(command, stdout=subprocess.PIPE, text=True,
                              encoding="utf-8", errors="replace") as process:
            timer = Timer(timeout + 25, process.kill)
            timer.start()
            try:
                for line in process.stdout:
                    if not signalled and line.startswith(cue):
                        signalled = True
                        tone(high=True)
                        print(f"采集提示 {line.strip()}", flush=True)
                if process.wait() or not signalled:
                    raise RuntimeError(f"采集中断或缺少提示信号，请保留 {log_path}")
            except BaseException:
                if process.poll() is None:
                    process.kill()
                raise
            finally:
                timer.cancel()


def record(port, directory, transition=False, calibration_path=None, seconds=2):
    params = "CAPTURE_SECONDS = %d\nSIGNAL_FRAME = %d\n" % (
        8 if transition else seconds, 90 if transition else 1,
    )
    if calibration_path is not None:
        params += "CALIBRATION_PATH = %r\n" % calibration_path
    countdown()
    run_cued_capture(port, directory, "m1_ball_insertion.py", params, "HCV_FIELD_CUE,", 38)
    capture = directory / "capture.bin"
    subprocess.run(board_command(port, "get", REMOTE + "m1-ball-insertion.bin", capture),
                   check=True, timeout=600)
    result = decode_recording(capture, directory, None)
    with (directory / "ball.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not all(int(row["status"]) & CALIB_OK for row in rows):
        raise RuntimeError("K230 did not mark every recorded frame as calibrated")
    return rows, result


def capture_ball_samples(port, directory, fingerprint):
    countdown()
    run_cued_capture(port, directory, "m2_reference.py", "HCV_BALL_SAMPLES = True\n",
                     "HCV_M2_CAPTURE_CUE", 24)
    samples_file = directory / "ball-pixels.json"
    subprocess.run(board_command(port, "get", REMOTE + "m2-ball-pixels.json", samples_file),
                   check=True, timeout=120)
    packet = json.loads(samples_file.read_text(encoding="utf-8"))
    if packet["fingerprint"] != fingerprint or len(packet["u_px"]) != 120:
        raise ValueError("ball samples do not match the reference or expected frame count")
    return packet["u_px"]


def collect_ball_points(port, output, fingerprint):
    output.mkdir()
    points = {}
    for mm in BALL_POSITIONS_MM:
        for attempt in count(1):
            input(f"把球心对准 {mm / 10:+g} cm 刻线，手移开，放稳后按回车采集。")
            columns = capture_ball_samples(port, output / f"{mm:+d}mm-{attempt}", fingerprint)
            try:
                anchor = ball_anchor(mm, columns)
            except ValueError as error:
                print(f"{error}。请重新放球，按回车重测；Ctrl+C 可终止。", flush=True)
                continue
            print(f"{mm:+d} mm，{anchor['n']} 个有效检测，标准差 {anchor['std_px']:.3f} px。",
                  flush=True)
            points[mm] = columns
            break
    return points


def static_result(rows, target_mm):
    positions = [int(row["pos_filt"]) / 10 for row in rows if int(row["status"]) & VALID]
    result = {"frames": len(rows), "valid": len(positions), "target_mm": target_mm}
    if target_mm is None:
        return dict(result, passed=len(rows) >= 100 and not positions)
    mean = statistics.mean(positions) if positions else None
    return dict(
        result,
        mean_mm=mean,
        std_mm=statistics.stdev(positions) if len(positions) > 1 else None,
        mean_error_mm=mean - target_mm if mean is not None else None,
        passed=len(positions) >= 100 and abs(mean - target_mm) <= 2,
    )


def transition_result(rows, first_changed, insertion, max_interval_ms, fps):
    if not 1 <= first_changed < len(rows):
        raise ValueError("mark a frame after the start and before the end of the recording")
    valid = [bool(int(row["status"]) & VALID) for row in rows]
    expected = insertion
    change = next((frame for frame in range(first_changed, len(rows))
                   if valid[frame] == expected), None)
    window = 3 if insertion else 9
    frames = None if change is None else change - first_changed + 1
    prior = valid[max(0, first_changed - window):first_changed]
    started_in_state = all(flag != expected for flag in prior)
    stable = False
    if change is not None and len(valid) - change >= window:
        following = valid[change:change + window] if insertion else valid[change:]
        stable = all(flag == expected for flag in following)
    return {
        "first_image_change": first_changed,
        "first_protocol_change": change,
        "frames_including_change": frames,
        "frame_interval_max_ms": max_interval_ms,
        "passed": (
            started_in_state and stable and frames is not None and frames <= window
            and max_interval_ms < 2000 / fps
        ),
    }


def field_run(port, output, strip, level_deg, calib_mode="ruler", compare_with=None):
    output.mkdir(parents=True)
    input("相机和纸尺固定，横尺调平，台灯正常，清空检测区。准备好拍底图再按回车，五声低音后听高音。")
    image, reference = capture_reference(port, output)
    config = reference["config"]
    static_seconds = max(2, ceil(120 / config["sensor"]["fps"]))
    if compare_with is not None:
        runtime_calibration(compare_with, config)
        other_mode = "ball" if calib_mode == "ruler" else "ruler"
        if compare_with.get("method", "ruler") != other_mode:
            raise ValueError(f"comparison file must contain the {other_mode} calibration")
    if calib_mode == "ball":
        points = collect_ball_points(port, output / "ball-anchors", reference["fingerprint"])
        calibration = build_ball_calibration(points, reference, config, level_deg)
    else:
        calibration = build_calibration(image, strip or select_ruler(image), reference, config, level_deg)
        if not confirm_numbering(image, calibration, output):
            raise ValueError("ruler numbering was not confirmed")
    pending = output / "calib.pending.json"
    write_json(pending, calibration)
    remote_pending = REMOTE + "calib.pending.json"
    subprocess.run(board_command(port, "put", pending, remote_pending), check=True, timeout=120)
    input("把钢球球心对准尺子上实际印刷的 0 刻度。放稳后按回车。")
    zero_rows, _ = record(port, output / "normal_+0mm", calibration_path=remote_pending,
                         seconds=static_seconds)
    zero = static_result(zero_rows, 0)
    zero["zero_check_passed"] = zero["valid"] >= 100 and abs(zero["mean_mm"]) < 3
    results = {"normal_+0mm": zero}
    save_results(output, results)
    if not zero["zero_check_passed"]:
        raise ValueError("zero check failed: put the ball on the physical 0 tick and verify numbering")
    subprocess.run(board_command(port, "put", pending, REMOTE + "calib.json"),
                   check=True, timeout=120)
    pending.replace(output / "calib.json")

    if compare_with is not None:
        print("请重新摆放七个静态点，两套 LUT 将使用同一批新采集的像素坐标。", flush=True)
        points = collect_ball_points(port, output / "comparison-points", reference["fingerprint"])
        ruler, ball = (calibration, compare_with) if calib_mode == "ruler" else (compare_with, calibration)
        write_json(output / "lut-comparison.json", compare_calibrations(ruler, ball, points, config))
        return True

    def measure(label, prompt, target_mm):
        input(prompt + "，放稳后按回车。")
        rows, _ = record(port, output / label, seconds=static_seconds)
        results[label] = static_result(rows, target_mm)
        save_results(output, results)
        print(label, results[label], flush=True)

    for light, prompt in (("normal", "正常照明"), ("off", "关闭台灯"), ("side", "把灯移到一侧")):
        measure(f"{light}_empty", f"{prompt}，取走球并清出检测区", None)
        positions = STATIC_POSITIONS_MM[1:] if light == "normal" else (0,)
        for position in positions:
            measure(f"{light}_{position:+d}mm",
                    f"{prompt}，把球心对准 {position / 10:+g} cm 刻线", position)
    input("恢复正常照明，拿走球。按回车后听五声低音，听到高音再把球滚入并停在中间。")
    for label, insertion in (("insertion", True), ("removal", False)):
        if not insertion:
            input("保持球在检测区内。按回车后听五声低音，听到高音再拿走球。")
        directory = output / label
        rows, result = record(port, directory, transition=True)
        if result["valid_frames"] in (0, result["frames"]):
            results[label] = dict(result, passed=False, reason="recording did not change VALID")
        else:
            frame = int(input(f"查看 {directory / 'roi.mp4'}，输入球第一次"
                              f"{'进入' if insertion else '离开'}检测区的画面帧号。"))
            results[label] = transition_result(
                rows, frame, insertion, result["frame_interval_max_ms"], config["sensor"]["fps"]
            )
        save_results(output, results)
        print(label, results[label], flush=True)
    return all(item["passed"] for item in results.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new directory for results and raw recordings")
    parser.add_argument("--port", default="auto")
    parser.add_argument("--level-deg", type=float, required=True, help="measured ruler angle")
    parser.add_argument("--strip", type=int, nargs=4, metavar=("LEFT", "RIGHT", "TOP", "BOTTOM"))
    parser.add_argument("--calib-mode", choices=("ruler", "ball"), default="ruler")
    parser.add_argument("--compare-with", type=Path,
                        help="after the zero check, compare with an existing LUT at seven fresh points")
    args = parser.parse_args()
    if not abs(args.level_deg) <= 0.2:
        parser.error("ruler must be level within ±0.2°")
    other = json.loads(args.compare_with.read_text(encoding="utf-8")) if args.compare_with else None
    return 0 if field_run(args.port, args.output.resolve(), args.strip, args.level_deg,
                          args.calib_mode, other) else 2


if __name__ == "__main__":
    raise SystemExit(main())
