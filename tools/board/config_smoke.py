"""Run the shared MicroPython config cases on a connected K230."""

import argparse
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
CLI = Path(__file__).with_name("board.py")
REMOTE = "/sdcard/hcv/p0_exp00/config-check"
FILES = {
    "k230/hcv/config.py": "config.py",
    "k230/config.example.json": "config.example.json",
    "tests/config_cases.json": "config_cases.json",
    "tests/config_micropython.py": "config_micropython.py",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="auto")
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()

    command = [sys.executable, str(CLI), "--port", args.port]
    args.log.parent.mkdir(parents=True, exist_ok=True)
    for source, name in FILES.items():
        result = subprocess.run([*command, "put", str(ROOT / source), REMOTE + "/" + name])
        if result.returncode:
            return result.returncode

    with tempfile.TemporaryDirectory(prefix="hcv-config-") as directory:
        setup = Path(directory) / "verify.py"
        setup.write_text(
            f"root = {REMOTE!r}\n"
            "scope = {'__name__': 'hcv_config_check'}\n"
            "with open(root + '/config_micropython.py') as source:\n"
            "    exec(source.read(), scope)\n"
            "scope['verify'](root, root + '/config.py', "
            "root + '/config.example.json', root + '/config_cases.json')\n",
            encoding="utf-8",
        )
        result = subprocess.run([
            *command, "run", str(setup), "--timeout", "30", "--stream", "--log", str(args.log),
        ])
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
