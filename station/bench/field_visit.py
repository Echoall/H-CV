"""Walk the operator through the November 28 visit and preserve evidence after every stage."""
import json
import subprocess
import sys
from types import SimpleNamespace

from station.bench import bench, m1_v18, player_compare, sensor_reuse, session_review, v18_dual


def visit(root, ask=input, *, simulation=None):
    root.mkdir(parents=True)
    simulated = bool(simulation)
    args = SimpleNamespace(runs=root, simulate=simulation, replay=None, skip_mode_list=False,
                           timing_only=True, framedrop=False, rtp_diagnostic=False, optical=True)
    result = {"simulation": simulated, "stages": [], "full_media_reuse": "unverified",
              "live_reconnect_health": "unverified"}
    print("本入口能检查相机单帧复用；完整视频会话的复用仍待验证。各独立实拍轮请按提示单独上电。"
          "最后一步复核已有日志。", flush=True)

    def sensor():
        if not simulated:
            return sensor_reuse.field(root, ask)
        trial = subprocess.run([sys.executable, "-m", "pytest", "-q", "tests/test_v18_sensor_reuse.py"],
                               cwd=bench.ROOT, capture_output=True, timeout=60)
        (root / "sensor-repl-fixtures.log").write_bytes(trial.stdout + trial.stderr)
        return trial.returncode

    def longrun():
        if simulated:
            args.replay = bench.ROOT / "reports/data/M1/pool-ref-longrun.log"
        else:
            mode = ask("独立长跑回车先枚举传感器。若此前枚举后采集未开始，输入 2 跳过枚举 > ").strip()
            if mode not in ("", "2"):
                raise RuntimeError("已取消独立长跑。")
            args.skip_mode_list = mode == "2"
        return m1_v18.main(args, ask)

    def players():
        code = player_compare.wizard(args, ask)
        if code or simulated:
            return code
        records = [json.loads(p.read_text(encoding="utf-8"))
                   for p in root.glob("*-players/*/summary.json")]
        optical = {r["plan"]["receiver_ab"]: r.get("optical_latency", {}) for r in records}
        result["optical_samples"] = {name: m.get("sample_count", 0) for name, m in optical.items()}
        result["optical_measured"] = (len(optical) == 2 and all(m.get("sample_count", 0) >= 5
                                                               for m in optical.values()))
        result["optical_within_target"] = {name: (m.get("passed") if m.get("sample_count", 0) >= 5 else None)
                                           for name, m in optical.items()}
        return 0 if result["optical_measured"] else "video_passed_optical_pending"

    def review():
        archive = bench.ROOT / "reports/data/M3/bench/2026-11-28-v18-ap"
        session_review.review(archive / "board.log", archive / "field.json", root / "s2-history.json")
        return None

    stages = (
        ("S0a", "取两次单帧，检查释放相机后能否重新打开。", sensor),
        ("S1", "保持相机固定，做约十分钟的独立找球计时。", longrun),
        ("接收器对照", "相机对准电脑左侧时钟，两轮保持相同取景，每轮单独确认。", players),
        ("S4-60", "镜头朝向不限。用 1080p60 同时传视频、找球。", lambda: v18_dual.run_mode(root, v18_dual.MODES[0], ask, simulation)),
        ("S4-90", "保持相同取景。改用 720p90 同时传视频、找球。", lambda: v18_dual.run_mode(root, v18_dual.MODES[1], ask, simulation)),
        ("S2 历史记录", "最后复核已存的断连日志。这一步只读取电脑上的文件。", review),
    )
    try:
        for name, prompt, action in stages:
            print(name, prompt, flush=True)
            if not simulated and ask("回车继续；输入 q 保存并退出 > ").strip().lower() == "q":
                break
            row = {"stage": name, "started": bench.now(), "status": "running"}
            result["stages"].append(row)
            bench.write_json(root / "visit.json", result)
            try:
                code = action()
                if isinstance(code, str):
                    row["status"] = code
                else:
                    row["status"] = ("history_only" if code is None else "passed" if code == 0 else "failed")
            except (OSError, RuntimeError, ValueError, EOFError, KeyboardInterrupt, subprocess.SubprocessError) as exc:
                row.update(status="failed", message=str(exc) or "已取消")
            finally:
                row["ended"] = bench.now()
                bench.write_json(root / "visit.json", result)
            if row["status"] == "failed" and not simulated:
                print("这一步未完成，日志已保存。", flush=True)
                break
    finally:
        result["finished_available_steps"] = len(result["stages"]) == len(stages)
        result["full_visit_accepted"] = False
        bench.write_json(root / "visit.json", result)
        print("结果目录", root, flush=True)
    if any(r["status"] == "failed" for r in result["stages"]):
        return 1
    return 2  # The complete physical visit still lacks S0b/S0c and live S2 evidence.
