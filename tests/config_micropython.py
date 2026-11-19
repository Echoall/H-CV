"""Run the shared ICD cases with MicroPython Unix (from the repository root)."""

import json
import sys

def write_json(path, value):
    with open(path, "w") as stream:
        json.dump(value, stream)


def verify(directory, module_file, example_file, cases_file):
    scope = {"__name__": "hcv_config"}
    with open(module_file) as source:
        exec(source.read(), scope)
    load_config = scope["load_config"]
    format_config_for_log = scope["format_config_for_log"]
    with open(example_file) as stream:
        sample = json.load(stream)
    sample["wifi"]["mode"] = "off"
    local = directory + "/config.local.json"
    write_json(directory + "/config.json", sample)
    assert load_config(directory) == sample
    with open(cases_file) as stream:
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
    verify(sys.argv[1], "k230/hcv/config.py", "k230/config.example.json",
           "tests/config_cases.json")
