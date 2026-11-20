"""Calculate prompt-to-stability times from a P0-EXP02 brightness log."""

import argparse
import csv
from pathlib import Path
from statistics import median


def summarize(raw):
    phases = {}
    for line in raw.splitlines():
        if line.startswith("HCV_LIGHT "):
            _, phase, state, moment = line.split()
            phases[int(phase)] = [state, int(moment), []]
        elif line.startswith("HCV_LUMA,"):
            _, phase, moment, value = line.split(",")
            phases[int(phase)][2].append((int(moment), float(value)))

    previous = None
    for phase, (state, prompt, samples) in phases.items():
        tail = [value for moment, value in samples if moment - prompt >= 2500]
        if len(tail) < 5:
            yield phase, state, len(samples), None, "insufficient-samples"
            previous = None
            continue
        final = median(tail)
        if previous is None or abs(final - previous) < 5:
            yield phase, state, len(samples), None, "no-measurable-step"
        else:
            tolerance = max(2, 0.05 * abs(final - previous))
            settled = next((moment - prompt for index, (moment, _) in enumerate(samples)
                            if samples[-1][0] - moment >= 300
                            and all(abs(value - final) <= tolerance
                                    for _, value in samples[index:])), None)
            yield phase, state, len(samples), settled, "ok" if settled is not None else "unstable"
        previous = final


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    with args.output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("phase", "lamp", "samples", "prompt_to_stable_ms", "status"))
        writer.writerows(summarize(args.raw.read_text(encoding="utf-8", errors="replace")))


if __name__ == "__main__":
    main()
