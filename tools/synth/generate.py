import argparse
import json
from pathlib import Path

import numpy as np


def project_s_to_u(s_mm, theta_deg=0.0, height_mm=300.0, scale_px_per_mm=2.0,
                   center_u=320.0, pivot_mm=-125.0):
    k = np.tan(np.deg2rad(theta_deg)) / height_mm
    distance = np.asarray(s_mm, dtype=float) - pivot_mm
    return center_u + scale_px_per_mm * distance / (1.0 + k * distance)


def inverse_u_to_s(u_px, theta_deg=0.0, height_mm=300.0, scale_px_per_mm=2.0,
                   center_u=320.0, pivot_mm=-125.0):
    k = np.tan(np.deg2rad(theta_deg)) / height_mm
    ratio = (np.asarray(u_px, dtype=float) - center_u) / scale_px_per_mm
    return pivot_mm + ratio / (1.0 - k * ratio)


def fit_projective(s_mm, u_px):
    s_mm = np.asarray(s_mm, dtype=float)
    u_px = np.asarray(u_px, dtype=float)
    matrix = np.column_stack((s_mm, np.ones_like(s_mm), -u_px * s_mm))
    coefficients = np.linalg.lstsq(matrix, u_px, rcond=None)[0]
    return tuple(float(value) for value in coefficients)


def apply_projective(s_mm, coefficients):
    a, b, c = coefficients
    s_mm = np.asarray(s_mm, dtype=float)
    return (a * s_mm + b) / (c * s_mm + 1.0)


def _ball_coverage(xx, yy, center_x, center_y, radius, supersample=4):
    coverage = np.zeros(xx.shape, dtype=float)
    offsets = (np.arange(supersample, dtype=float) + 0.5) / supersample - 0.5
    for offset_y in offsets:
        for offset_x in offsets:
            distance = ((xx + offset_x - center_x) ** 2 +
                        (yy + offset_y - center_y) ** 2)
            coverage += distance <= radius ** 2
    return coverage / float(supersample * supersample)


def generate_scene(
    width=640,
    height=360,
    ball_x=320.0,
    ball_y=190.0,
    ball_s_mm=None,
    theta_deg=0.0,
    height_mm=300.0,
    scale_px_per_mm=2.0,
    pivot_mm=-125.0,
    marker_positions_mm=(-100.0, 0.0, 100.0),
    ball_radius=10.0,
    polarity="dark",
    background=150.0,
    gradient=0.08,
    noise=2.0,
    highlight=True,
    highlight_radius=2.0,
    scratch=False,
    magnet_shadow=True,
    vel_px_per_s=0.0,
    exposure_us=0.0,
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
    if ball_s_mm is not None:
        ball_x = float(project_s_to_u(
            ball_s_mm, theta_deg, height_mm, scale_px_per_mm, width / 2.0, pivot_mm
        ))
    blur_px = abs(float(vel_px_per_s)) * float(exposure_us) / 1_000_000.0
    if blur_px == 0.0:
        blur_px = abs(float(motion_blur))
    centers = [ball_x] if blur_px == 0.0 else np.linspace(
        ball_x - blur_px / 2.0, ball_x + blur_px / 2.0, 9
    )
    coverage = np.zeros(image.shape, dtype=float)
    for center in centers:
        coverage += _ball_coverage(xx, yy, center, ball_y, ball_radius)
    coverage /= len(centers)
    if polarity == "dark":
        image = image - 65.0 * coverage
    elif polarity == "bright":
        image = image + 65.0 * coverage
    else:
        raise ValueError("polarity must be dark or bright")
    if highlight:
        glint = np.exp(-((xx - ball_x + ball_radius * 0.35) ** 2 +
                         (yy - ball_y + ball_radius * 0.35) ** 2) /
                        (2.0 * highlight_radius ** 2))
        image = image + 110.0 * glint * coverage
    if magnet_shadow:
        shadow = np.exp(-((xx - ball_x) ** 2 / (2.0 * (ball_radius * 1.7) ** 2) +
                          (yy - ball_y - ball_radius * 1.4) ** 2 /
                          (2.0 * (ball_radius * 0.7) ** 2)))
        image = image - 18.0 * shadow
    if scratch:
        image[int(slot_y), :] -= 22.0
    marker_truth = []
    for position in marker_positions_mm:
        marker_u = float(project_s_to_u(
            position, theta_deg, height_mm, scale_px_per_mm, width / 2.0, pivot_mm
        ))
        marker_v = float(slot_y - slot_height / 2.0 - 8.0)
        marker_mask = (np.abs(xx - marker_u) <= 3.0) & (np.abs(yy - marker_v) <= 3.0)
        image = np.where(marker_mask, 30.0, image)
        marker_truth.append({"s_mm": float(position), "u_px": marker_u, "v_px": marker_v})
    image = image + rng.normal(0.0, noise, size=image.shape)
    image = np.clip(image, 0, 255).astype(np.uint8)
    truth = {
        "u_px": float(ball_x),
        "v_px": float(ball_y),
        "s_mm": None if ball_s_mm is None else float(ball_s_mm),
        "ball_radius_px": float(ball_radius),
        "polarity": polarity,
        "theta_deg": float(theta_deg),
        "markers": marker_truth,
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
            "truth_x_mm": truth["s_mm"],
            "markers": truth["markers"],
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
    parser.add_argument("--theta-deg", type=float, default=0.0)
    parser.add_argument("--ball-s-mm", type=float)
    parser.add_argument("--vel-px-per-s", type=float, default=0.0)
    parser.add_argument("--exposure-us", type=float, default=0.0)
    parser.add_argument("--motion-blur", type=float, default=0.0)
    args = parser.parse_args()
    write_scene(
        args.output,
        count=args.count,
        polarity=args.polarity,
        theta_deg=args.theta_deg,
        ball_s_mm=args.ball_s_mm,
        vel_px_per_s=args.vel_px_per_s,
        exposure_us=args.exposure_us,
        motion_blur=args.motion_blur,
    )


if __name__ == "__main__":
    main()
