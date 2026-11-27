"""Operator-led ruler calibration and field acceptance with a saved partial report."""

import argparse
from contextlib import nullcontext
from datetime import datetime
import json
from pathlib import Path
import subprocess

from tools.calib import m2_field as field


def wizard(output, simulate=False, port="auto"):
    output.mkdir(parents=True, exist_ok=False)
    measurements = output / "measurements"
    summary = dict(simulation=simulate, complete=False, passed=False, step="电脑与板子自检")
    try:
        if simulate:
            from tools.analysis.m2_field_replay import rehearsal
            session = rehearsal(output)
        else:
            session = nullcontext()
        with session:
            print("本轮按提示固定相机、摆球和切灯。每次准备好再按回车。结果保存在", output)
            try:
                subprocess.run(field.board_command(port, "info"), check=True, timeout=20)
            except subprocess.SubprocessError as exc:
                raise RuntimeError("20 秒内未确认板子响应，采集还没开始。请保留本轮报告。") from exc
            summary["step"] = "输入水平仪角度"
            angle = float(input("请输入尺面实测角度（度，允许 −0.2 至 +0.2） > "))
            if not abs(angle) <= 0.2:
                raise ValueError("请先把尺面调平至 ±0.2° 内")
            summary["level_deg"] = angle
            summary["step"] = "标定、七点、光照、进出球"
            passed = field.field_run(port, measurements, None, angle)
            summary.update(complete=True, passed=passed, step="全部采集完毕")
    except (EOFError, KeyboardInterrupt, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        summary["error"] = str(exc) or type(exc).__name__
    finally:
        path = measurements / "results.json"
        results = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        summary["cases"] = {name: row["passed"] for name, row in results.items()}
        field.write_json(output / "summary.json", summary)
        status = "全部通过" if summary["passed"] else ("有项目未达标" if summary["complete"] else "未完成")
        text = [f"# M2 {'电脑模拟' if simulate else '现场'}", "", f"{status}，已记录 {len(results)} 项。",
                summary.get("error", ""), "", "详见 measurements/results.csv。" if results else "本轮尚无测量数据。"]
        (output / "summary.md").write_text("\n".join(text) + "\n", encoding="utf-8")
        print(status, summary.get("error", ""), output)
    return 0 if summary["passed"] else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--port", default="auto")
    args = parser.parse_args()
    path = args.output or Path(__file__).resolve().parent / "runs" / (
        datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-m2")
    raise SystemExit(wizard(path, args.simulate, args.port))
