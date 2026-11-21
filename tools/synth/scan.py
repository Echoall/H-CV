import argparse
import json
from pathlib import Path

if __package__:
    from .generate import write_scene
else:
    from generate import write_scene


def build_manifest(output, count):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = []
    index = 0
    for polarity in ("dark", "bright"):
        for highlight in (False, True):
            for theta in (0.0, 10.0, 20.0):
                for blur in (0.0, 8.0):
                    scene = "case_%03d" % index
                    manifest.append({
                        "scene": scene,
                        "polarity": polarity,
                        "highlight": highlight,
                        "theta_deg": theta,
                        "blur_px": blur,
                        "positions_mm": [
                            -100.0 + (200.0 * frame / max(1, count - 1))
                            for frame in range(count)
                        ],
                        "seed": index,
                    })
                    if count:
                        write_scene(
                            output / scene,
                            count=count,
                            polarity=polarity,
                            highlight=highlight,
                            theta_deg=theta,
                            vel_px_per_s=blur * 1000.0,
                            exposure_us=1000.0,
                            seed=index,
                        )
                    index += 1
    for kind in ("glare", "local_shadow"):
        for width in (12, 40, 60):
            scene = "interference_%s_%02d" % (kind, width)
            manifest.append({
                "scene": scene,
                "kind": kind,
                "width_px": width,
                "strengths": [25, 40, 65],
                "ball": False,
            })
            if count:
                write_scene(
                    output / scene,
                    count=count,
                    ball_x=-1000,
                    ball_s_mm=None,
                    noise=2,
                    highlight=False,
                    magnet_shadow=False,
                    **{kind: {
                        "x_px": 320,
                        "width_px": width,
                        "strength": 40,
                    }},
                )
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--count", type=int, default=0)
    args = parser.parse_args()
    print(json.dumps({"scenes": len(build_manifest(args.output, args.count))}))


if __name__ == "__main__":
    main()
