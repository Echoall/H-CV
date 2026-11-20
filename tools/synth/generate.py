import argparse
import json
from pathlib import Path

import numpy as np


def generate_scene(
    width=640,
    height=360,
    ball_x=320.0,
    ball_y=190.0,
    ball_radius=10.0,
    polarity="dark",
    background=150.0,
    gradient=0.08,
    noise=2.0,
    highlight=True,
    highlight_radius=2.0,
    scratch=False,
    magnet_shadow=True,
    perspective=0.0,
    motion_blur=0.0,
    seed=0,
):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width]
    image = background + gradient * (xx - width / 2.0)
    slot_y = height * 0.53
    slot_height = height * 0.18
    slot = np.abs(yy - slot_y) <= slot_height / 2.0
    image = np.where(slot, image - 12.0, image)
    if perspective:
        image = image + perspective * (yy - slot_y) * (xx - width / 2.0) / width
    distance = np.sqrt((xx - ball_x) ** 2 + (yy - ball_y) ** 2)
    if polarity == "dark":
        image = np.where(distance <= ball_radius, image - 65.0, image)
    elif polarity == "bright":
        image = np.where(distance <= ball_radius, image + 65.0, image)
    else:
        raise ValueError("polarity must be dark or bright")
    if highlight:
        glint = np.exp(-((xx - ball_x + ball_radius * 0.35) ** 2 +
                         (yy - ball_y + ball_radius * 0.35) ** 2) /
                        (2.0 * highlight_radius ** 2))
        image = image + 110.0 * glint
    if magnet_shadow:
        shadow = np.exp(-((xx - ball_x) ** 2 / (2.0 * (ball_radius * 1.7) ** 2) +
                          (yy - ball_y - ball_radius * 1.4) ** 2 /
                          (2.0 * (ball_radius * 0.7) ** 2)))
        image = image - 18.0 * shadow
    if scratch:
        image[int(slot_y), :] -= 22.0
    if motion_blur > 0:
        shift = max(1, int(motion_blur))
        image = (image + np.roll(image, shift, axis=1) + np.roll(image, -shift, axis=1)) / 3.0
    image = image + rng.normal(0.0, noise, size=image.shape)
    image = np.clip(image, 0, 255).astype(np.uint8)
    truth = {
        "u_px": float(ball_x),
        "v_px": float(ball_y),
        "ball_radius_px": float(ball_radius),
        "polarity": polarity,
        "seed": seed,
    }
    return image, truth


def write_scene(output, count=3, **kwargs):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    seed_base = kwargs.pop("seed", 0)
    files = []
    for index in range(count):
        image, truth = generate_scene(seed=seed_base + index, **kwargs)
        image_path = output / ("frame_%06d.npy" % index)
        truth_path = output / ("frame_%06d.json" % index)
        np.save(image_path, image)
        truth_path.write_text(json.dumps({
            "seq": index,
            "t_cap_us": index * 11111,
            "truth_x_px": truth["u_px"],
            "truth_y_px": truth["v_px"],
            "truth": truth,
            "image": image_path.name,
        }, indent=2) + "\n", encoding="utf-8")
        files.extend([image_path.name, truth_path.name])
    return files


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--polarity", choices=("dark", "bright"), default="dark")
    parser.add_argument("--motion-blur", type=float, default=0.0)
    args = parser.parse_args()
    write_scene(args.output, count=args.count, polarity=args.polarity, motion_blur=args.motion_blur)


if __name__ == "__main__":
    main()
