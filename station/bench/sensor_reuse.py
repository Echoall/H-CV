"""S0 operator-led measurement of a sensor's normal release and reacquisition."""
import argparse
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from station.bench import bench, hubei, power


STAGES = ("first", "same_runtime", "after_reset")
PROMPTS = ("首次采集一帧，随后释放相机。", "保持连线，在同一个解释器中再次采集。",
           "已确认解释器软复位，最后采集一帧。")


def frame_and_release(output, token, stage):
    marks = [line for line in output.splitlines() if line.startswith("HCV_S0_")]
    return marks == [f"HCV_S0_FRAME,{token},{stage},320,240", f"HCV_S0_RELEASED,{token},{stage}"]


def measure(run, ask, *, confirm=hubei.confirm_capture, sensor_soft_reset=False):
    token = uuid4().hex
    guard = (bench.ROOT / "k230/hcv/guard.py").read_text(encoding="utf-8")
    script = (bench.ROOT / "tools/board/m3_v18_sensor_session.py").read_text(encoding="utf-8")
    run.summary["sensor_sessions"] = {}
    run.summary["soft_reboot_confirmed"] = False
    stages = STAGES if sensor_soft_reset else STAGES[:2]
    for stage, prompt in zip(stages, PROMPTS):
        if stage == "after_reset":
            result, output, _ = run.command("soft-reset", bench.BOARD + ["soft-reset-confirmed"], timeout=35)
            if result != 0 or output.splitlines().count("HCV_S0_SOFT_REBOOT_CONFIRMED") != 1:
                raise RuntimeError("没有读到解释器重启确认，本轮结束。")
            run.summary["soft_reboot_confirmed"] = True
        print(prompt, flush=True)
        confirm(ask)
        source = f"{guard}\n{script}\nrun({token!r}, {stage!r})\n".encode()
        code, output, _ = run.command(stage, bench.BOARD + ["run", "-", "--stream", "--timeout", "12"],
                                      timeout=28, data=source)
        ok = code == 0 and frame_and_release(output, token, stage)
        run.summary["sensor_sessions"][stage] = ok
        if not ok:
            raise RuntimeError("未确认有效画面和正常释放，已保存本轮日志。")
    code, output, _ = run.command("after-info", bench.BOARD + ["info"], timeout=18)
    if code != 0 or not hubei.image_matches(output, "v1.8"):
        raise RuntimeError("采集结束后未读回 v1.8 板端版本。")


def field(root, ask=input, *, sensor_soft_reset=False):
    run = bench.Run(root / "s0", dict(id="v18-sensor-reuse", title="S0 相机会话复用"), False)
    run.summary["passed"] = False
    try:
        power.reconnect(ask)
        code, output, _ = run.command("before-info", bench.BOARD + ["info"], timeout=18)
        if code != 0 or not hubei.image_matches(output, "v1.8"):
            raise RuntimeError("未读到匹配的幻尔 v1.8 板端版本。")
        measure(run, ask, sensor_soft_reset=sensor_soft_reset)
        run.summary["passed"] = True
    except (RuntimeError, OSError, EOFError, KeyboardInterrupt) as exc:
        run.summary["message"] = str(exc) or "现场取消"
    finally:
        run.finish()
    return int(not run.summary["passed"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, default=bench.HERE / "runs")
    parser.add_argument("--sensor-soft-reset", action="store_true", help="单独追加一次解释器软复位后的 Sensor 测试")
    args = parser.parse_args()
    with bench.only_instance(args.runs):
        raise SystemExit(field(args.runs / datetime.now().strftime("%Y%m%d-%H%M%S-%f"),
                               sensor_soft_reset=args.sensor_soft_reset))
