import json
from pathlib import Path
from subprocess import CompletedProcess
import sys
from types import SimpleNamespace

import pytest

from station.bench import bench, wlan


def baseline():
    path = bench.HERE / "plans/p1-ap-baseline.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_all_plans_validate_and_required_fields():
    for path in (bench.HERE / "plans").glob("*.json"):
        plan = bench.validate_plan(json.loads(path.read_text(encoding="utf-8")))
        assert plan["id"] == path.stem
    plan = baseline()
    del plan["criteria"]
    with pytest.raises(ValueError):
        bench.validate_plan(plan)


@pytest.mark.parametrize("field,value", [
    ("fps", 31), ("bitrate_kbps", 499), ("height", 541), ("gop", 61),
    ("transport", "other"), ("caching_ms", 99), ("seconds", 61), ("version", True),
])
def test_invalid_boundaries(field, value):
    with pytest.raises(ValueError):
        bench.validate_plan({**baseline(), field: value})


def test_free_mode_uses_same_bounds(capsys):
    answers = iter(["2", "2", "31", "", "", "", "", "5",
                    "2", "2", "15", "500", "10", "100", "5"])
    result = bench.free_plan(baseline(), lambda _: next(answers))
    assert result["width"] == 640
    assert result["transport"] == "udp"
    assert result["fps"] == 15
    assert result["seconds"] == 5
    assert "重新填写" in capsys.readouterr().out


def test_redaction_before_persistence(tmp_path):
    run = bench.Run(tmp_path / "run", baseline(), False)
    ssid, password = "测试&网络", "p&!<PRIVATE>123"
    run.private.update((ssid, password, "HomeNet-A", "fps"))
    text = (f"{ssid} 测试&amp;网络 {password} HomeNet-A 192.0.2.12 "
            "fe80::6140:1234:5678:42%15 2001:db8::1 AA:BB:CC:11:22:33 "
            "66-77-88-00-AA-BB")
    run.line("example.log", text)
    run.summary["observations"]["备注"] = text
    run.finish()
    for name in ("example.log", "summary.json", "summary.md"):
        document = (run.directory / name).read_text(encoding="utf-8")
        for secret in (ssid, "测试&amp;网络", password, "HomeNet-A", "192.0.2.12", "fe80:",
                       "2001:db8", "AA:BB", "66-77-88"):
            assert secret not in document
    assert json.loads((run.directory / "summary.json").read_text(encoding="utf-8"))["plan"]["fps"] == 30


def test_wlan_connect_restore_and_private_profile_cleanup():
    current = ["Home Synthetic", "Home Saved Profile"]
    commands = []
    profiles = []

    def command(*args):
        commands.append(args)
        if args[0] == "show":
            details = f"\n    Name : Wi-Fi\n    SSID : {current[0]}\n    Profile : {current[1]}\n"
            return CompletedProcess(args, 0, details)
        if args[0] == "add":
            profile = Path(args[2].partition("=")[2])
            assert "example&amp;pass" in profile.read_text(encoding="utf-8")
            profiles.append(profile)
        elif args[0] == "connect":
            current[:] = [args[2].partition("=")[2], args[1].partition("=")[2]]
        return CompletedProcess(args, 0, "")

    original = wlan.snapshot(command)
    assert original == ("Wi-Fi", "Home Synthetic", "Home Saved Profile")
    assert wlan.connect(original, "Board Synthetic", "example&pass", command=command)
    assert all(not path.exists() for path in profiles)
    assert wlan.restore(original, command=command)
    assert commands[-2][1] == "name=Home Saved Profile"


def test_wlan_failure_gives_manual_instructions(capsys, monkeypatch, tmp_path):
    def access_denied(*args):
        return CompletedProcess(args, 1, "")

    assert wlan.snapshot(access_denied) is None
    monkeypatch.setattr(wlan, "connect", lambda *_: False)
    run = bench.Run(tmp_path / "run", baseline(), False)
    wifi = {"ssid": "Bench-Test", "password": "dummy-pass"}
    assert not bench.connect_board(run, ("Wi-Fi", "original", "saved"), wifi)
    bench.show_board_network(wifi)
    assert "右下角网络图标" in capsys.readouterr().out
    assert run.summary["network"]["automatic_switch"] is False


def test_preflight_rejects_used_boot_before_writes(tmp_path, monkeypatch):
    run = bench.Run(tmp_path / "run", baseline(), False)

    def inventory(name, *args, **kwargs):
        assert name == "board-inventory"
        return 0, "HCV_BENCH_USED 1\n", ""

    monkeypatch.setattr(run, "command", inventory)
    with pytest.raises(RuntimeError, match="全灭 10 秒"):
        bench.preflight(run)


def test_runtime_plan_reaches_board_probe_without_saving_credentials(monkeypatch):
    from k230.hcv import config

    board_config = json.loads((bench.ROOT / "k230/config.example.json").read_text(encoding="utf-8"))
    board_config["wifi"]["mode"] = "off"
    received = []
    monkeypatch.setitem(sys.modules, "hcv.config", SimpleNamespace(
        load_config=lambda directory: board_config, _merge=config._merge, _validate=config._validate,
    ))
    monkeypatch.setitem(sys.modules, "hcv.ch0_configurable_probe",
                        SimpleNamespace(run=lambda **values: received.append(values)))
    monkeypatch.setitem(sys.modules, "network", SimpleNamespace(
        AP_IF=1, WLAN=lambda mode: SimpleNamespace(
            config=lambda **values: received.append(values), ifconfig=lambda: ("192.0.2.8",),
        ),
    ))
    monkeypatch.setattr(sys, "path", sys.path.copy())
    plan = bench.validate_plan({**baseline(), "width": 640, "height": 360, "fps": 20,
                                "gop": 20, "bitrate_kbps": 850})
    wifi = {"mode": "ap", "ssid": "example-board", "password": "example-key", "channel": 6}
    exec(bench.board_entry(plan, wifi), {})
    assert received[0] == {"ssid": wifi["ssid"], "key": wifi["password"], "channel": 6}
    params = received[1]["config"]
    assert params["chn_stream"]["width"] == 640
    assert params["stream"]["gop"] == 20
    assert params["stream"]["fps"] == 20
    assert params["stream"]["bitrate_kbps"] == 850
