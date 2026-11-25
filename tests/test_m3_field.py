import json
from types import SimpleNamespace

import pytest

from station.bench import field


def test_missing_backup_is_reported_as_local_setup():
    run = SimpleNamespace(command=lambda *args, **kwargs: (
        1, "", "board: run the board.py backup command before put, run or verify"))
    with pytest.raises(RuntimeError, match="缺少本机原有的板卡备份清单"):
        field.uptime(run)


@pytest.mark.parametrize("pc_gap,matched,board_gap,raw,round_count,expected", [
    (1, 1, 0, True, 1, "通过"),
    (1.1, 3, 0, True, 2, "通过"),
    (2, 0, 0, True, 1, "证据不足"),
    (2, 2, 1, True, 1, "板端原始短片已有缺口"),
    (0, 0, 0, False, 1, "证据不足"),
])
def test_operator_field_routes(tmp_path, monkeypatch, pc_gap, matched, board_gap, raw, round_count, expected):
    ffmpeg = tmp_path / "ffmpeg"
    ffmpeg.touch()
    args = SimpleNamespace(runs=tmp_path, simulate=[ffmpeg, ffmpeg], field=True)
    monkeypatch.setattr(field.bench, "tools", lambda *_: None)
    monkeypatch.setattr(field.bench, "find_ffprobe", lambda: "probe")
    plans = []

    def capture(args, ask, selected_plan):
        plan = field.bench.validate_plan(selected_plan)
        plans.append(plan)
        directory = args.runs / ("001-" + plan["id"])
        directory.mkdir()
        data = dict(recording_passed=True, network={"restored": True},
                    capture=dict(overlap_ready=True, latency_screenshots=7, video_track_seconds=61),
                    board_raw_clip=dict(downloaded=raw, sd_write_ms=248))
        field.bench.write_json(directory / "summary.json", data)

    def numbers(path, _):
        is_board = path.suffix == ".h264"
        return {"skipped": board_gap if is_board else 3,
                "gap_pct": board_gap if is_board else (pc_gap if len(plans) == 1 else 0)}

    monkeypatch.setattr(field.bench, "main", capture)
    monkeypatch.setattr(field, "frame_numbers", numbers)
    monkeypatch.setattr(field, "picture_digests", lambda *_: [])
    monkeypatch.setattr(field, "compare", lambda *_: dict(status="compared", missing_board_pictures=matched))
    field.field(args)
    summary = json.loads((args.runs / "summary.json").read_text(encoding="utf-8"))
    assert summary["result"] == expected and len(plans) == round_count
    if round_count == 2:
        assert [plans[1][key] for key in ("width", "height", "fps", "bitrate_kbps", "gop")] == [
            640, 360, 20, 800, 20]
