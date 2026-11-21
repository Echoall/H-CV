import csv, math, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from k230.hcv.geometry import compensate
from tools.synth.generate import project_s_to_u

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "reports/data/P2-T02"
BASE = {
    "height_mm": 300.0,
    "scale_px_per_mm": 1.6,
    "center_u": 320.0,
    "pivot_mm": -125.0,
    "marker_s_mm": [-110.0, 0.0, 110.0],
}
ANGLES = [-20.0, -10.0, 0.0, 10.0, 20.0]
POSITIONS = [-120.0, -60.0, 0.0, 60.0, 120.0]

def project(values, theta):
    geometry = {
        key: value for key, value in BASE.items() if key != "marker_s_mm"
    }
    return [
        float(project_s_to_u(value, theta_deg=theta, **geometry))
        for value in values
    ]

reference = project(BASE["marker_s_mm"], 0.0)
rows = []
for parameter, deltas in (
    ("height_mm", [-10.0, 0.0, 10.0]),
    ("pivot_mm", [-3.0, 0.0, 3.0]),
    ("scale_px_per_mm", [-0.01, 0.0, 0.01]),
):
    for delta in deltas:
        model = dict(BASE)
        if parameter == "scale_px_per_mm":
            model[parameter] = BASE[parameter] * (1.0 + delta)
        else:
            model[parameter] = BASE[parameter] + delta
        errors = []
        invalid = 0
        for theta in ANGLES:
            current = project(BASE["marker_s_mm"], theta)
            for missing in (0, 1, 2):
                observed = [value if i != missing else None for i, value in enumerate(current)]
                for position in POSITIONS:
                    geometry = {
                        key: value for key, value in BASE.items()
                        if key != "marker_s_mm"
                    }
                    measured = float(
                        project_s_to_u(position, theta_deg=theta, **geometry)
                    )
                    try:
                        mapped, mode, _ = compensate(measured, observed, reference, model=model)
                    except ValueError:
                        invalid += 1
                        continue
                    if mode != "GEOMETRIC" or mapped is None:
                        invalid += 1
                        continue
                    recovered = (float(mapped) - BASE["center_u"]) / BASE["scale_px_per_mm"]
                    errors.append(abs(recovered - position))
        rows.append({
            "parameter": parameter,
            "delta": delta,
            "max_abs_error_mm": max(errors) if errors else None,
            "rms_error_mm": math.sqrt(sum(value * value for value in errors) / len(errors)) if errors else None,
            "valid_cases": len(errors),
            "invalid_cases": invalid,
        })
OUT.mkdir(parents=True, exist_ok=True)
with (OUT / "geometric-sensitivity.csv").open("w", newline="", encoding="utf-8") as stream:
    writer = csv.DictWriter(stream, fieldnames=rows[0])
    writer.writeheader(); writer.writerows(rows)
lines = ["# P2-T02 两点几何参数敏感性", "", "模型使用名义 H=300 mm、合页=-125 mm、scale=1.6 px/mm。数据由真实模型生成，再用扰动后的参数补偿。invalid 表示模型无法从两点一致地估计 θ。", "", "| 参数 | 扰动 | 最大绝对误差 mm | RMS mm | 有效样本 | invalid |", "|---|---:|---:|---:|---:|---:|"]
for row in rows:
    lines.append("| {parameter} | {delta:g} | {max_abs_error_mm} | {rms_error_mm} | {valid_cases} | {invalid_cases} |".format(**row))
(OUT / "geometric-sensitivity.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
for row in rows:
    print(row)
