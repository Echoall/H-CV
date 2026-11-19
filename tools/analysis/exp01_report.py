"""Create the EXP01 report from recorded camera logs and retry results."""

import argparse
from collections import Counter
import csv
import json
from pathlib import Path
from statistics import median

if __package__:
    from .exp01_camera import summarize, cases, key
else:
    from exp01_camera import summarize, cases, key


def load_records(directory, retries):
    records = {}
    for location in [directory, *retries]:
        for file in location.glob("*.json"):
            metadata = json.loads(file.read_text(encoding="utf-8"))
            raw = file.with_suffix(".log")
            if raw.is_file() and metadata["status"] not in ("host-timeout", "board-command-failed"):
                metadata.update(summarize(raw.read_text(encoding="utf-8", errors="replace")))
            records[file.stem] = metadata
    return records


def recommendation(records):
    valid = []
    for label, info in records.items():
        if info["status"] != "ok" or not info["case"][5]:
            continue
        image = info["image"]
        interval_limit = 2_000_000 / info["actual"][2]
        if (info["fps"] >= 60 and info["interval_p99"] <= interval_limit
                and image["format"] == image["gray_constant"]):
            valid.append((label, info))
    if not valid:
        return None
    return min(valid, key=lambda row: (
        row[1]["case"][3:5] != [640, 360],
        row[1]["case"][6],
        row[1]["interval_p99"],
        -row[1]["fps"],
    ))


def comparisons(records, flag):
    pairs = []
    for case in cases():
        if case[flag]:
            continue
        matching = list(case)
        matching[flag] = True
        baseline = records.get(key(case), {})
        enabled = records.get(key(matching), {})
        if (baseline.get("status") == enabled.get("status") == "ok"
                and baseline["actual"] == enabled["actual"]):
            pairs.append((
                enabled["fps"] - baseline["fps"],
                enabled["interval_p99"] - baseline["interval_p99"],
                enabled["snapshot_p95"] - baseline["snapshot_p95"],
            ))
    return pairs


def write_report(records, markdown, table, supplemental=None):
    planned = list(cases())
    status = Counter(item["status"] for item in records.values())
    complete = len(records) == len(planned)
    answer = recommendation(records)
    with table.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("组合","状态","帧数","fps","间隔 p50 μs","间隔 p95 μs",
                         "间隔 p99 μs","最大间隔 μs","snapshot p50 μs",
                         "snapshot p95 μs","snapshot p99 μs","最大 snapshot μs",">1.5×标称间隔次数","原因"))
        fields = ("status", "frames", "fps", "interval_p50", "interval_p95",
                  "interval_p99", "interval_max", "snapshot_p50", "snapshot_p95",
                  "snapshot_p99", "snapshot_max", "detail")
        for case in planned:
            label = key(case)
            item = records.get(label, {"status":"pending"})
            writer.writerow((label, *(item.get(field, "") for field in fields)))

    lines = [
        "# P0-EXP01 摄像头模式、帧率与裁剪",
        "",
        f"组合结果 {len(records)}/{len(planned)}。有效 {status['ok']} 组，不支持 "
        f"{status['unsupported']} 组，其他异常 {len(records)-status['ok']-status['unsupported']} 组。",
        "",
        "固件 CanMV v1.4-a7de1c8，GC2093 CSI2。成功组合各采 1,000 帧。"
        "每组之间休息 5 分钟，镜头保持同一现场场景。分位数按最近秩计算。",
        "",
    ]
    if answer is None:
        lines.append("现有结果中还没有同时满足 CHN1 开启、平均帧率 ≥60 fps 和帧间隔 p99 要求的灰度组合。")
    else:
        label, info = answer
        mark = "结果覆盖的组合中" if complete else "已完成组合中"
        lines += [
            f"{mark}推荐 {label}。实测 {info['fps']:.3f} fps，"
            f"帧间隔 p99 {info['interval_p99']/1000:.3f} ms，"
            f"最大帧间隔 {info['interval_max']/1000:.3f} ms，"
            f"snapshot 调用 p95 {info['snapshot_p95']/1000:.3f} ms。",
            "",
            "选择顺序为满足验收阈值、优先 640×360、优先保留视野，再比较帧间隔 p99。",
        ]
    successful = [item for item in records.values() if item["status"] == "ok"]
    modes = sorted({tuple(item["actual"]) for item in successful})
    if modes:
        lines += ["", "实测成功的传感器模式为 " + "、".join(f"{w}×{h}@{fps}" for w,h,fps in modes) + "。"]
    lines += ["", "## 模式请求与返回", ""]
    for mode in sorted({case[:3] for case in planned}):
        seen = sorted({tuple(item["actual"]) for item in records.values()
                       if tuple(item["case"][:3]) == mode and "actual" in item})
        description = "、".join(f"{w}×{h}@{fps}" for w,h,fps in seen) or "待取得"
        valid_fps = [item["fps"] for item in records.values()
                     if tuple(item["case"][:3]) == mode and item["status"] == "ok"]
        note = (f"有效 {len(valid_fps)} 组，平均帧率范围 {min(valid_fps):.3f}–{max(valid_fps):.3f} fps。"
                if valid_fps else "")
        lines.append(f"- 请求 {mode[0]}×{mode[1]}@{mode[2]}，板端报告 {description}。{note}")
    lines += ["", "## 开关第二通道和裁剪的对比", ""]
    for title, flag in (("启用 CHN1", 5), ("启用 CHN0 裁剪", 6)):
        differences = comparisons(records, flag)
        if differences:
            dfps, dp99, dsnapshot = map(median, zip(*differences))
            lines.append(
                f"- {title} 有 {len(differences)} 组可配对。"
                f"变化的中位数分别为 {dfps:+.3f} fps、"
                f"帧间隔 p99 {dp99/1000:+.3f} ms、snapshot p95 {dsnapshot/1000:+.3f} ms。"
            )
        else:
            lines.append(f"- {title}尚无可配对的完整数据。")
    cropped = [item for item in successful if item["case"][5] and item["case"][6]]
    lines += [
        "",
        f"CHN1 同时启用时，已验证 {len(cropped)} 组 CHN0 裁剪配置与帧采集。"
        "第二通道按 1280×720 YUV420SP 配置，采样期间仅从第一通道取帧。",
        "",
        "固定输出尺寸时，裁剪改变输入视野，返回图的像素数仍由输出尺寸决定。"
        "snapshot 经常等待新帧，耗时不能直接视为曝光到取帧的物理延迟。"
        "P0-EXP03 按测得的视野和安装高度评估是否裁剪。",
        "",
        "## 异常帧间隔",
        "",
        "逐组超过 1.5 倍标称间隔的次数写入 CSV。采样时只存数组，"
        "CSV 原始行在相机停止后才经 USB 输出。",
    ]
    outlier_cases = [(label, item) for label, item in records.items() if item.get("worst_outlier")]
    if outlier_cases:
        label, item = max(outlier_cases, key=lambda row: row[1]["worst_outlier"]["interval"])
        worst = item["worst_outlier"]
        total = sum(case["over_1_5_nominal"] for _, case in outlier_cases)
        intervals = sum(item["frames"] - 1 for item in successful)
        lines.append(
            f"有效组 {intervals:,} 个间隔中共有 {total} 个长间隔（{100*total/intervals:.3f}%）。最大值 {worst['interval']/1000:.3f} ms"
            f" 出现在 {label} 的第 {worst['index']} 个样本。"
            f"其中 snapshot 内 {worst['snapshot']/1000:.3f} ms，"
            f"两次调用之间 {worst['between_calls']/1000:.3f} ms。"
        )
        lines.append("长间隔按偶发跳帧记录。主矩阵没有逐帧记录 GC 事件，"
                     "无法确定板端自动 GC 是否发生在 snapshot 内。")
        lines.append("P3-T01 跟踪器按真实帧时间计算 dt，补测单次 dt 为 33 ms 的情况。")
    if supplemental:
        lines += ["", "## 补测", ""]
        for label, item in supplemental.items():
            if item["status"] != "ok":
                lines.append(f"- {label} {item['status']}。{item.get('detail','')}")
                continue
            lines.append(
                f"- {label} 共 {item['frames']} 帧，{item['fps']:.3f} fps；"
                f"帧间隔 p99 {item['interval_p99']/1000:.3f} ms，"
                f"最大 {item['interval_max']/1000:.3f} ms。"
            )
            if "heap_increase_during_snapshot" in item:
                lines.append(
                    f"同次补测中 {item['heap_increase_during_snapshot']} 帧在 snapshot 前后观察到堆空闲量增加，"
                    f"其中 {item['delayed_with_heap_increase']} 帧同时超过间隔阈值。"
                    "堆变化只能辅助定位，无法单独证明 GC 是原因。"
                )
    lines += ["", f"完整分位数及失败原因见 {table.name}。原始时序由 exp01_camera.py 的解析函数复算。"]
    markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--retry", type=Path, action="append", default=[])
    parser.add_argument("--supplement", type=Path, action="append", default=[])
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--table", type=Path, required=True)
    args = parser.parse_args()
    supplement = load_records(args.supplement[0], args.supplement[1:]) if args.supplement else None
    write_report(load_records(args.capture, args.retry), args.report, args.table, supplement)


if __name__ == "__main__":
    main()
