import errno
import json
from pathlib import Path

import pytest

from k230.hcv.config import (
    calibration_fingerprint,
    format_config_for_log,
    load_config,
    runtime_config_warning,
)


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "k230" / "config.example.json"
CASES = json.loads(Path(__file__).with_name("config_cases.json").read_text(encoding="utf-8"))


def write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def example(tmp_path):
    config = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    config["wifi"]["mode"] = "off"
    write_json(tmp_path / "config.json", config)
    return config


def test_reads_base_config_without_override(tmp_path, example):
    assert load_config(tmp_path) == example
    assert runtime_config_warning(example) == (None, 0)


def test_runtime_config_warns_when_polarity_is_not_calibrated(tmp_path, example):
    example["detector"]["params"]["polarity"] = "both"
    write_json(tmp_path / "config.json", example)
    config = load_config(tmp_path)
    assert runtime_config_warning(config) == (
        "detector.polarity=both; run P2-T03 polarity calibration",
        10,
    )


def test_recursively_overrides_local_fields(tmp_path, example):
    write_json(tmp_path / "config.local.json", {"wifi": {"password": "test-only"}, "status_hz": 4})
    config = load_config(tmp_path)
    assert config["wifi"] == dict(example["wifi"], password="test-only")
    assert config["status_hz"] == 4


@pytest.mark.parametrize(
    ("override", "field"),
    [(item[1], item[2]) for item in CASES],
    ids=[item[0] for item in CASES],
)
def test_icd_v101(tmp_path, example, override, field):
    write_json(tmp_path / "config.local.json", override)
    if field is None:
        load_config(tmp_path)
    else:
        with pytest.raises(ValueError) as caught:
            load_config(tmp_path)
        assert field in str(caught.value)


@pytest.mark.parametrize(("override", "field"), [
    ({"schema": True}, "schema"),
    ({"schema": 2}, "schema"),
    ({"sensor": None}, "sensor"),
    ({"uart": {"baud": 9600}}, "uart.baud"),
    ({"roi": {"x": 500}}, "roi"),
    ({"chn_detect": {"crop": [0, 0, 10, False]}}, "crop"),
    ({"markers": {"x_mm": [0, -110, 110]}}, "markers.x_mm"),
    ({"stream": {"port": 65536}}, "stream.port"),
])
def test_effective_config_regressions(tmp_path, example, override, field):
    write_json(tmp_path / "config.local.json", override)
    with pytest.raises(ValueError) as caught:
        load_config(tmp_path)
    assert field in str(caught.value)


def test_missing_nested_field_is_rejected(tmp_path, example):
    del example["uart"]["baud"]
    write_json(tmp_path / "config.json", example)
    with pytest.raises(ValueError, match=r"uart\.baud"):
        load_config(tmp_path)


def test_malformed_local_config_is_not_skipped(tmp_path, example):
    (tmp_path / "config.local.json").write_text('{"wifi":{"password":"test-only', encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        load_config(tmp_path)
    assert "invalid JSON" in str(caught.value)
    assert "test-only" not in str(caught.value)


def test_missing_base_config_is_an_error(tmp_path):
    with pytest.raises(OSError) as caught:
        load_config(tmp_path)
    assert caught.value.args[0] == errno.ENOENT


def test_redacted_log_preserves_original_password(tmp_path, example):
    write_json(tmp_path / "config.local.json", {"wifi": {"mode": "ap", "password": "test-only"}})
    config = load_config(tmp_path)
    logged = format_config_for_log(config)
    assert "test-only" not in logged
    assert json.loads(logged)["wifi"]["password"] == "***"
    assert config["wifi"]["password"] == "test-only"


def test_invalid_password_error_is_redacted(tmp_path, example):
    write_json(tmp_path / "config.local.json", {"wifi": {"mode": "ap", "password": "secret"}})
    with pytest.raises(ValueError) as caught:
        load_config(tmp_path)
    assert "secret" not in str(caught.value)
    assert "***" in str(caught.value)


def test_fingerprint_matches_canonical_sha256_without_credentials(example):
    assert calibration_fingerprint(example) == "e5aa30a19ada5159"
    example["wifi"]["password"] = "changed-secret"
    assert calibration_fingerprint(example) == "e5aa30a19ada5159"
    example["sensor"]["hmirror"] = True
    example["chn_detect"]["crop"] = [10, 20, 640, 360]
    assert calibration_fingerprint(example) == "c0470b14ff91def9"
