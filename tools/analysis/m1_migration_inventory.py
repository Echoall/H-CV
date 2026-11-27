"""Compare an extracted /sdcard/hcv with tracked sources without exporting private settings."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    data = path.read_bytes()
    if path.suffix.lower() in (".py", ".json"):
        data = data.replace(b"\r\n", b"\n")
    return sha256(data).digest()


def inventory(archive):
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    by_digest = {}
    for raw in tracked.split(b"\0"):
        if raw:
            path = ROOT / raw.decode("utf-8")
            if path.is_file():
                by_digest.setdefault(digest(path), []).append(path.relative_to(ROOT).as_posix())
    files = sorted(p for p in archive.rglob("*") if p.is_file())
    names = {p.relative_to(archive).as_posix().casefold(): p for p in files}
    modules = {}
    identical = 0
    for name, path in names.items():
        matches = by_digest.get(digest(path), [])
        identical += bool(matches)
        if name == "main_hcv.py" or (name.startswith("hcv/") and name.endswith(".py")):
            modules[name] = "matches" if "k230/" + name in matches else "differs"
    ref = json.loads((ROOT / "reports/data/M1/m1-config.json").read_bytes())
    base = json.loads(names["config.json"].read_bytes())
    import sys
    sys.path.insert(0, str(ROOT / "k230"))
    from hcv.config import load_config
    effective = load_config(archive)
    return {
        "files": len(files), "bytes": sum(p.stat().st_size for p in files),
        "matching_any_tracked_file": identical,
        "modules": modules, "base_config_matches_m1": base == ref,
        "effective_config_different_sections": sorted(k for k in effective.keys() | ref.keys()
                                                     if effective.get(k) != ref.get(k)),
        "has_active_calib_json": "calib.json" in names,
        "reference_files": {k: by_digest.get(digest(names[k]), [])
                            for k in ("m2-reference.json", "m2-calibration-fixture.json")},
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    print(json.dumps(inventory(parser.parse_args().archive), indent=2, ensure_ascii=False))
