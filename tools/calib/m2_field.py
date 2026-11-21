"""Guide a single K230 session for static calibration, lighting and transitions."""

import argparse
import csv
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.analysis.m1_ball_evidence import decode_recording
from tools.calib.ruler_lut import build_calibration


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
    subprocess.run(board_command(
        port, "run", BOARD_TOOLS / "m2_reference.py", "--timeout", 30,
        "--log", output / "reference.log",
    ), check=True)
    for name in ("m2-ruler.pgm", "m2-reference.json"):
        subprocess.run(board_command(port, "get", REMOTE + name, output / name), check=True)
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


def record(port, directory, transition=False, calibration_path=None):
    directory.mkdir()
    params = "CAPTURE_SECONDS = %d\nSIGNAL_FRAME = %d\n" % (
        8 if transition else 2, 90 if transition else 1,
    )
    if calibration_path is not None:
        params += "CALIBRATION_PATH = %r\n" % calibration_path
    with tempfile.TemporaryDirectory(prefix="hcv-field-") as temporary:
        script = Path(temporary) / "capture.py"
        script.write_text(params + (BOARD_TOOLS / "m1_ball_insertion.py").read_text(),
                          encoding="utf-8")
        if transition:
            for _ in range(5):
                tone()
                time.sleep(0.86)
        command = board_command(port, "run", script, "--timeout", 38, "--stream",
                                "--log", directory / "board.log")
        signalled = False
        with subprocess.Popen(command, stdout=subprocess.PIPE, text=True,
                              encoding="utf-8", errors="replace") as process:
            for line in process.stdout:
                if line.startswith("HCV_FIELD_CUE,"):
                    signalled = True
                    tone(high=True)
                    print(f"采集提示 {line.strip()}", flush=True)
            if process.wait() or not signalled:
                raise RuntimeError(f"capture did not finish with a cue; see {directory / 'board.log'}")
    capture = directory / "capture.bin"
    subprocess.run(board_command(port, "get", REMOTE + "m1-ball-insertion.bin", capture),
                   check=True)
    result = decode_recording(capture, directory, None)
    with (directory / "ball.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not all(int(row["status"]) & CALIB_OK for row in rows):
        raise RuntimeError("K230 did not mark every recorded frame as calibrated")
    return rows, result


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


def field_run(port, output, strip, level_deg):
    output.mkdir(parents=True)
    input("相机和纸尺固定，横尺调平，台灯正常，清空检测区。准备后按回车。")
    image, reference = capture_reference(port, output)
    config = reference["config"]
    calibration = build_calibration(image, strip or select_ruler(image), reference, config, level_deg)
    if not confirm_numbering(image, calibration, output):
        raise ValueError("ruler numbering was not confirmed")
    pending = output / "calib.pending.json"
    write_json(pending, calibration)
    remote_pending = REMOTE + "calib.pending.json"
    subprocess.run(board_command(port, "put", pending, remote_pending), check=True)
    input("把钢球球心对准尺子上实际印刷的 0 刻度。放稳后按回车。")
    zero_rows, _ = record(port, output / "normal_+0mm", calibration_path=remote_pending)
    zero = static_result(zero_rows, 0)
    zero["zero_check_passed"] = zero["valid"] >= 100 and abs(zero["mean_mm"]) < 3
    results = {"normal_+0mm": zero}
    save_results(output, results)
    if not zero["zero_check_passed"]:
        raise ValueError("zero check failed: put the ball on the physical 0 tick and verify numbering")
    subprocess.run(board_command(port, "put", pending, REMOTE + "calib.json"),
                   check=True)
    pending.replace(output / "calib.json")

    def measure(label, prompt, target_mm):
        input(prompt + "，放稳后按回车。")
        rows, _ = record(port, output / label)
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
    args = parser.parse_args()
    if not abs(args.level_deg) <= 0.2:
        parser.error("ruler must be level within ±0.2°")
    return 0 if field_run(args.port, args.output.resolve(), args.strip, args.level_deg) else 2


if __name__ == "__main__":
    raise SystemExit(main())
