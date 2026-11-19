"""Create the EXP01 report from recorded camera logs and retry results."""

import argparse
from collections import Counter
import csv
import json
from pathlib import Path

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


def write_report(records, markdown, table):
    planned = list(cases())
    status = Counter(item["status"] for item in records.values())
    complete = len(records) == len(planned)
    answer = recommendation(records)
    with table.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("组合","状态","帧数","fps","间隔 p50 μs","间隔 p95 μs",
                         "间隔 p99 μs","最大间隔 μs","snapshot p50 μs",
                         "snapshot p95 μs","snapshot p99 μs","最大 snapshot μs","原因"))
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
            f"snapshot 调用 p95 {info['snapshot_p95']/1000:.3f} ms。",
            "",
            "选择顺序为满足验收阈值、优先 640×360、优先保留视野，再比较帧间隔 p99。",
        ]
    successful = [item for item in records.values() if item["status"] == "ok"]
    modes = sorted({tuple(item["actual"]) for item in successful})
    if modes:
        lines += ["", "实测成功的传感器模式为 " + "、".join(f"{w}×{h}@{fps}" for w,h,fps in modes) + "。"]
    cropped = [item for item in successful if item["case"][5] and item["case"][6]]
    lines += [
        "",
        f"CHN1 同时启用时，已验证 {len(cropped)} 组 CHN0 裁剪配置与帧采集。"
        "第二通道按 1280×720 YUV420SP 配置，采样期间仅从第一通道取帧。",
        "",
        "snapshot 耗时是调用至返回的时间。曝光时刻到取帧的物理延迟待独立实验。",
        "",
        f"完整分位数及失败原因见 {table.name}。原始时序由 exp01_camera.py 的解析函数复算。",
    ]
    markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--retry", type=Path, action="append", default=[])
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--table", type=Path, required=True)
    args = parser.parse_args()
    write_report(load_records(args.capture, args.retry), args.report, args.table)


if __name__ == "__main__":
    main()
