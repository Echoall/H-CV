"""File-backed board and operator doubles for the complete M2 field rehearsal."""

from contextlib import contextmanager, ExitStack
import json
from pathlib import Path
import shutil
import sys
from unittest.mock import patch


def emulate(directory, args):
    action, *args = args
    if action == "info":
        print("synthetic M2 board")
    elif action == "get":
        shutil.copyfile(directory / Path(args[0]).name, args[1])
    elif action == "put":
        shutil.copyfile(args[0], directory / Path(args[1]).name)
    elif action == "run":
        log = Path(args[args.index("--log") + 1])
        script = Path(args[0]).read_text(encoding="utf-8")
        if "HCV_CAPTURE_CUE = True" in script:
            cue = "HCV_M2_CAPTURE_CUE"
        else:
            shutil.copyfile(directory / (log.parent.name + ".bin"), directory / "m1-ball-insertion.bin")
            cue = "HCV_FIELD_CUE,frame=" + ("89" if "SIGNAL_FRAME = 90\n" in script else "0")
        log.write_text("synthetic USB\n" + cue + "\n", encoding="utf-8")
        print(cue, flush=True)
    else:
        raise ValueError(action)


@contextmanager
def rehearsal(output):
    import cv2
    import numpy as np
    from k230.hcv.config import calibration_fingerprint
    from protocol.python.hcv_proto import encode_ball
    from tools.calib import m2_field as field
    from tools.synth.ruler import generate_ruler

    remote = output / "synthetic-board"
    remote.mkdir()
    config = json.loads((field.ROOT / "reports/data/M1/m1-config.json").read_text())
    image, truth = generate_ruler(0.4, seed=12, scale_px_per_mm=1.4)
    cv2.imencode(".pgm", image)[1].tofile(remote / "m2-ruler.pgm")
    field.write_json(remote / "m2-reference.json", dict(samples=60, config=config, bg_profile=[150] * 640,
                                                        fingerprint=calibration_fingerprint(config)))
    cases = {"normal_%+dmm" % mm: mm for mm in field.STATIC_POSITIONS_MM}
    cases.update(normal_empty=None, off_empty=None, side_empty=None, insertion=0, removal=0)
    cases.update({"off_+0mm": 0, "side_+0mm": 0})
    for label, target in cases.items():
        with (remote / (label + ".bin")).open("wb") as binary:
            binary.write(b"HCV1 160 12 180\n")
            for i in range(180):
                present = valid = target is not None
                if label in ("insertion", "removal"):
                    present = (i >= 92) == (label == "insertion")
                    valid = i >= 94 if label == "insertion" else i < 100
                position = int(target * 10) if valid else -32768
                packet = encode_ball(i, round(i * 1000 / 90), 4 | int(valid), 200 if valid else 0,
                                     position, position, 0, 1, -32768)
                pixels = np.full((12, 160), 80 if label.startswith("off") else 192, np.uint8)
                if label.startswith("side"):
                    pixels[:, :80] = 112
                if present:
                    cv2.circle(pixels, (round(truth[target] / 4), 6), 3, 8, -1)
                binary.write(packet + pixels.tobytes())

    prompts, tones = [], []

    def operator(prompt):
        prompts.append(prompt.replace(str(output), "<run>"))
        if "实测角度" in prompt:
            return "0"
        if "画面帧号" in prompt:
            # Inspect the same decoded, numbered video that the operator would see.
            insertion = "进入" in prompt
            name = "insertion" if insertion else "removal"
            reader = cv2.VideoCapture(str(output / "measurements" / name / "roi.mp4"))
            try:
                for i in range(180):
                    ok, frame = reader.read()
                    if not ok:
                        break
                    if bool(frame[12, 160].mean() < 60) == insertion:
                        return str(i)
            finally:
                reader.release()
            raise ValueError("synthetic video never changed state")
        return ""

    left, right = round(truth[-120]), round(truth[120])
    with ExitStack() as stack:
        for module, name, value in [
            (field, "board_command", lambda port, *args: [sys.executable, __file__, str(remote), *map(str, args)]),
            (field, "tone", lambda high=False: tones.append("H" if high else "L")),
            (field.time, "sleep", lambda _: None),
            (cv2, "selectROI", lambda *args: (left * 2, 204, (right - left) * 2, 18)),
            (cv2, "imshow", lambda *args: None), (cv2, "waitKey", lambda _: ord("y")),
            (cv2, "destroyAllWindows", lambda: None),
        ]:
            stack.enter_context(patch.object(module, name, value))
        stack.enter_context(patch("builtins.input", operator))
        try:
            yield
        finally:
            field.write_json(output / "operator.json", dict(prompts=prompts, cues="".join(tones)))


if __name__ == "__main__":
    emulate(Path(sys.argv[1]), sys.argv[2:])
