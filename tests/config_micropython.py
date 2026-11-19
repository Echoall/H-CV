"""Run the shared ICD cases with MicroPython Unix (from the repository root)."""

import json
import os
import sys

sys.path.insert(0, "k230/hcv")
from config import format_config_for_log, load_config


def write_json(path, value):
    with open(path, "w") as stream:
        json.dump(value, stream)


def verify(directory):
    with open("k230/config.example.json") as stream:
        sample = json.load(stream)
    sample["wifi"]["mode"] = "off"
    local = directory + "/config.local.json"
    write_json(directory + "/config.json", sample)
    assert load_config(directory) == sample
    with open("tests/config_cases.json") as stream:
        cases = json.load(stream)

    for name, overlay, field in cases:
        write_json(local, overlay)
        try:
            load_config(directory)
        except ValueError as error:
            assert field is not None and field in str(error), name
        else:
            assert field is None, name

    write_json(local, {"wifi": {"mode": "ap", "password": "test-only"}})
    config = load_config(directory)
    assert config["wifi"]["password"] == "test-only"
    assert json.loads(format_config_for_log(config))["wifi"]["password"] == "***"
    print(len(cases), "shared ICD cases passed; base read, overlay and redaction passed.")


if __name__ == "__main__":
    verify(sys.argv[1])
