from pathlib import Path
from types import SimpleNamespace

import pytest

from station.bench import bench, m1_v18


def test_legacy_measurement_replays_without_inventing_capture_span():
    result = m1_v18.assess(bench.ROOT / "reports/data/M1/pool-ref-longrun.log")
    assert result["summary"]["frames"] == 54769
    assert result["duration_dropped_including_gc"] == 107
    assert result["active_duration_dropped"] == 0
    assert result["max_sample_process_p95_us"] == 1827
    assert result["wall_duration_dropped"] is None
    assert not result["checks"]["capture_span_complete"]


@pytest.mark.parametrize("process_us,span,expected", [(6000, 600_000_000, True),
                                                      (6001, 600_000_000, False),
                                                      (6000, 600_020_000, False)])
def test_ten_minute_assessment_boundaries(tmp_path, process_us, span, expected):
    path = tmp_path / "capture.log"
    lines = ["find sensor gc2093_csi2, type 32, output 1280x720@90",
             "HCV_M1_BEGIN,mode=1280x720x90,detector_fast=1,roi=0:150:640:48"]
    for i in range(20):
        for _ in range(60):
            lines.append(f"HCV_M1_SAMPLE,frames=45,process=100/{process_us}/6200/6300")
        final = ",final=1" if i == 19 else ""
        lines.append(f"HCV_M1_ROUND,round={i},frames=2700,active_us=30000000,gc_us=0,gc_dropped_est=0{final}")
    lines.extend([f"HCV_M1_CAPTURE_SPAN_US,{span}",
                  "HCV_M1_SUMMARY,frames=54000,elapsed_us=601000000,rx_crc=0,rx_len=0,valid=0"])
    path.write_text("\n".join(lines), encoding="utf-8")
    args = SimpleNamespace(replay=path, runs=tmp_path)
    assert m1_v18.main(args, lambda _: pytest.fail("offline replay asked for user input")) == int(not expected)
    [result] = tmp_path.glob("*/summary.md")
    assert "历史日志回放" in result.read_text(encoding="utf-8")
    if expected:
        path.write_text(path.read_text(encoding="utf-8").replace("rx_len=0,valid=0", "rx_len=0,valid=7"),
                        encoding="utf-8")
        assert m1_v18.main(args) == 1
        args.timing_only = True
        assert m1_v18.main(args) == 0


@pytest.mark.parametrize("skip_modes", [True, False])
def test_field_cancellation_never_reaches_board_camera(monkeypatch, tmp_path, skip_modes):
    run = bench.Run(tmp_path / "field", {"id": "m1-v18"}, False)
    commands = []

    def command(stage, arguments, **kwargs):
        commands.append(stage)
        if stage == "sensor-modes":
            return 0, "HCV_M1_SENSOR_MODE v1.8-build gc2093_csi2 1280x720@90 1", ""
        assert arguments[-1] == "--hiwonder-v18"
        assert Path(arguments[-3]).is_file()
        return 0, "", ""

    monkeypatch.setattr(run, "command", command)
    monkeypatch.setattr(m1_v18.hubei, "verify_power", lambda r, a, version: commands.append("power-" + version))
    with pytest.raises(RuntimeError, match="取消"):
        m1_v18.field(run, lambda _: "取消", skip_mode_list=skip_modes)
    assert commands[0] == "power-v1.8"
    assert ("sensor-modes" in commands) == (not skip_modes)
    assert run.summary["mode_list_skipped"] is skip_modes
    assert "upload-main_hcv.py" in commands
    assert len(set(commands)) == len(commands)
    assert "m1" not in commands
