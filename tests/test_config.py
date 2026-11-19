from copy import deepcopy
import json
from pathlib import Path

import pytest

from k230.hcv.config import load_config


EXAMPLE = Path(__file__).resolve().parents[1] / "k230" / "config.example.json"


def write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def example(tmp_path):
    config = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    write_json(tmp_path / "config.json", config)
    return config


def test_reads_base_config_without_override(tmp_path, example):
    assert load_config(tmp_path) == example


def test_recursively_overrides_local_fields(tmp_path, example):
    write_json(tmp_path / "config.local.json", {"wifi": {"password": "test-only"}, "status_hz": 4})
    config = load_config(tmp_path)
    assert config["wifi"] == dict(example["wifi"], password="test-only")
    assert config["status_hz"] == 4


@pytest.mark.parametrize(("override", "message"), [
    ({"schema": True}, "schema"),
    ({"schema": 2}, "schema"),
    ({"sensor": None}, "sensor"),
    ({"uart": {"baud": 9600}}, "uart.baud"),
    ({"roi": {"x": 500}}, "roi"),
    ({"chn_detect": {"crop": [0, 0, 10, False]}}, "crop"),
    ({"markers": {"x_mm": [0, -110, 110]}}, "markers.x_mm"),
    ({"stream": {"port": 65536}}, "stream.port"),
])
def test_validates_effective_config(tmp_path, example, override, message):
    write_json(tmp_path / "config.local.json", override)
    with pytest.raises(ValueError, match=message):
        load_config(tmp_path)


def test_missing_nested_field_is_rejected(tmp_path, example):
    config = deepcopy(example)
    del config["uart"]["baud"]
    write_json(tmp_path / "config.json", config)
    with pytest.raises(ValueError, match=r"uart\.baud"):
        load_config(tmp_path)


def test_malformed_local_config_is_not_skipped(tmp_path, example):
    (tmp_path / "config.local.json").write_text("{", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(tmp_path)


def test_missing_base_config_is_an_error(tmp_path):
    with pytest.raises(OSError):
        load_config(tmp_path)
