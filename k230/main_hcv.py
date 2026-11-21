"""M1 board runner for the H-CV visual measurement path.

This file is uploaded to ``/sdcard/hcv/main_hcv.py`` and is intentionally
usable without a ``main.py`` autostart hook.  It runs a bounded measurement
loop and always releases the camera, media manager, UART and image views.
"""

import gc
import json
import os
import sys
import time

from media.media import MediaManager
from media.sensor import Sensor

sys.path.append("/sdcard/hcv")
sys.path.append("/sdcard/hcv/hcv")

from hcv.hcv.config import calibration_fingerprint, load_config, runtime_config_warning
from hcv.hcv.detect_profile import ProfileDetector
from hcv.hcv.guard import start as guard_start
from hcv.hcv.guard import tick as guard_tick
from hcv.hcv.health import STAGES, TimingWindow
from hcv.hcv.link_uart import UART1Link
from hcv.hcv.tracker import KalmanTracker
from hcv.hcv.proto import (
    INV,
    MSG_CMD,
    MSG_RUN_INFO,
    RxParser,
    pack_ball_into,
    pack_status_into,
)


STATE_BOOT = 0
STATE_NO_CALIB = 1
STATE_READY = 2
STATE_DEGRADED = 3
STATE_FAULT = 4

STATUS_VALID = 1 << 0
STATUS_PREDICTED = 1 << 1
STATUS_CALIB_OK = 1 << 2
STATUS_LOW_CONF = 1 << 5
STATUS_EXTRAPOLATED = 1 << 6
STATUS_FAULT = 1 << 7

ERR_CAMERA = 1
ERR_FRAME_TIMEOUT = 2
ERR_CALIB_MISSING = 3
ERR_CALIB_MISMATCH = 4
ERR_PROCESS_TIMEOUT = 7
ERR_CONFIG = 10
ERR_UNKNOWN = 255

FW_VER = 0x0100
NOMINAL_MIN_MM = -125.0
NOMINAL_MAX_MM = 125.0


class PixelLUT:
    """M1 nominal mapping with the same callable interface as a calibration LUT."""

    def __init__(self, pixels, physical):
        if len(pixels) != len(physical) or len(pixels) < 2:
            raise ValueError("LUT requires at least two anchors")
        self.pixels = [float(value) for value in pixels]
        self.physical = [float(value) for value in physical]

    def map(self, value):
        value = float(value)
        if value <= self.pixels[0]:
            index = 0
        elif value >= self.pixels[-1]:
            index = len(self.pixels) - 2
        else:
            index = 0
            while value > self.pixels[index + 1]:
                index += 1
        fraction = (value - self.pixels[index]) / (
            self.pixels[index + 1] - self.pixels[index]
        )
        return self.physical[index] + fraction * (
            self.physical[index + 1] - self.physical[index]
        )

    def inverse(self, value):
        value = float(value)
        if value <= self.physical[0]:
            index = 0
        elif value >= self.physical[-1]:
            index = len(self.physical) - 2
        else:
            index = 0
            while value > self.physical[index + 1]:
                index += 1
        fraction = (value - self.physical[index]) / (
            self.physical[index + 1] - self.physical[index]
        )
        return self.pixels[index] + fraction * (
            self.pixels[index + 1] - self.pixels[index]
        )


def _exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def _load_lut(config, width):
    path = "/sdcard/hcv/calib.json"
    nominal = PixelLUT((0.0, float(width - 1)), (NOMINAL_MIN_MM, NOMINAL_MAX_MM))
    if not _exists(path):
        return nominal, False, 0, ERR_CALIB_MISSING
    try:
        with open(path, "r") as stream:
            calibration = json.load(stream)
        expected = calibration_fingerprint(config)
        if calibration.get("fingerprint") != expected:
            return nominal, False, 0, ERR_CALIB_MISMATCH
        anchors = calibration["anchors"]
        pixels = [float(item["u_px"]) for item in anchors]
        physical = [float(item["x_mm"]) for item in anchors]
        lut = PixelLUT(pixels, physical)
        calibration_id = int(calibration.get("id", 0)) & 0xFFFF
        return lut, True, calibration_id, 0
    except (OSError, ValueError, KeyError, TypeError):
        return nominal, False, 0, ERR_CALIB_MISMATCH


def _quantize(value):
    if value is None:
        return INV
    scaled = int(round(float(value) * 10.0))
    return max(-32767, min(32767, scaled))


def _age_ms(t_cap_us, now_us):
    value = time.ticks_diff(now_us, t_cap_us) // 1000
    return max(0, min(255, int(value)))


def _u_prediction(tracker, lut):
    if not tracker.initialized or tracker.lost:
        return None
    try:
        return lut.inverse(tracker.x)
    except (ValueError, ZeroDivisionError):
        return None


def _uart_pending(link):
    if link is None or link.uart is None:
        return b""
    available = getattr(link.uart, "any", None)
    if available is None:
        return b""
    count = available()
    if not count:
        return b""
    return link.uart.read(min(int(count), 64)) or b""


def _print_stats(health, dropped, gc_free, run_info, parser):
    stats = health.all_stats()
    detect = stats[STAGES.index("detect")]
    map_stats = stats[STAGES.index("map")]
    track = stats[STAGES.index("track")]
    pack = stats[STAGES.index("pack")]
    uart = stats[STAGES.index("uart")]
    gc_stats = stats[STAGES.index("gc")]
    process = stats[STAGES.index("process")]
    total = stats[STAGES.index("total")]
    fps = 0.0
    elapsed_us = 0
    if health.first_us is not None and health.last_us is not None:
        elapsed_us = max(1, time.ticks_diff(health.last_us, health.first_us))
        fps = health.frame_count * 1_000_000.0 / elapsed_us
    print(
        "HCV_M1_SAMPLE,frames=%d,elapsed_us=%d,fps=%.3f,dropped=%d,"
        "detect=%d/%d/%d/%d,map=%d/%d/%d/%d,track=%d/%d/%d/%d,"
        "pack=%d/%d/%d/%d,uart=%d/%d/%d/%d,process=%d/%d/%d/%d,"
        "total=%d/%d/%d/%d,gc=%d/%d/%d/%d,gc_free=%d,"
        "rx_crc=%d,rx_len=%d,run_state=%d" % (
            health.frame_count,
            elapsed_us,
            fps,
            dropped,
            detect[1],
            detect[2],
            detect[3],
            detect[4],
            map_stats[1],
            map_stats[2],
            map_stats[3],
            map_stats[4],
            track[1],
            track[2],
            track[3],
            track[4],
            pack[1],
            pack[2],
            pack[3],
            pack[4],
            uart[1],
            uart[2],
            uart[3],
            uart[4],
            process[1],
            process[2],
            process[3],
            process[4],
            total[1],
            total[2],
            total[3],
            total[4],
            gc_stats[1],
            gc_stats[2],
            gc_stats[3],
            gc_stats[4],
            gc_free,
            parser.crc_errors,
            parser.length_errors,
            run_info[1],
        )
    )


def run(max_runtime_s=10, max_frames=None):
    """Run the bounded M1 loop and print machine-readable evidence lines."""
    config = load_config("/sdcard/hcv")
    warning, warning_err = runtime_config_warning(config)
    if warning:
        print("HCV_CONFIG_WARNING," + warning)

    sensor = None
    media_started = False
    link = None
    image = None
    image_array = None
    roi_image = None
    roi_profile = None
    parser = RxParser()
    health = TimingWindow(128)
    tracker = None
    detector = None
    lut = None
    tracker_output = [False, False, False, False, 0.0, 0.0, None]
    ball_buffer = bytearray(19)
    status_buffer = bytearray(18)
    ball_seq = 0
    status_seq = 0
    dropped = 0
    frame_number = 0
    found_count = 0
    valid_count = 0
    predicted_count = 0
    found_streak = 0
    run_info = [0, 0, 0, INV, INV, 0]
    last_cap = None
    started_us = None
    next_status_us = None
    next_gc_frame = 45
    state = STATE_BOOT
    err = warning_err
    calib_ok = False
    calib_id = 0
    try:
        guard_start(float(max_runtime_s) + 20.0)
        sensor = Sensor(
            width=config["sensor"]["width"],
            height=config["sensor"]["height"],
            fps=config["sensor"]["fps"],
        )
        sensor.reset()
        frame_size = {
            "width": config["chn_detect"]["width"],
            "height": config["chn_detect"]["height"],
            "chn": 0,
        }
        if config["chn_detect"]["crop"] is not None:
            frame_size["crop"] = config["chn_detect"]["crop"]
        sensor.set_framesize(**frame_size)
        sensor.set_pixformat(Sensor.GRAYSCALE, chn=0)
        MediaManager.init()
        media_started = True
        sensor.run()
        for _ in range(8):
            guard_tick()
            image = sensor.snapshot(chn=0, timeout=1000)
            image = None
        image = sensor.snapshot(chn=0, timeout=1000)
        image_array = image.to_numpy_ref()
        roi = config["roi"]
        x0, y0, width, height = roi["x"], roi["y"], roi["w"], roi["h"]
        roi_image = image_array[y0:y0 + height, x0:x0 + width]
        import ulab.numpy as np
        background = np.array(roi_image[height // 2, :])
        detector = ProfileDetector(
            background,
            config["detector"]["params"],
            width=width,
        )
        lut, calib_ok, calib_id, lut_err = _load_lut(config, width)
        if not calib_ok and err == 0:
            err = lut_err
        tracker = KalmanTracker(config["tracker"])
        link = UART1Link(config["uart"]["baud"])
        state = STATE_READY if calib_ok and err == 0 else STATE_NO_CALIB
        if err == ERR_CONFIG:
            state = STATE_FAULT
        gc.collect()
        gc.disable()
        started_us = time.ticks_us()
        next_status_us = started_us
        print(
            "HCV_M1_BEGIN,mode=%dx%dx%d,detect=%dx%d,roi=%d:%d:%d:%d,"
            "calib_ok=%d,calib_id=%d,state=%d,err=%d" % (
                config["sensor"]["width"],
                config["sensor"]["height"],
                config["sensor"]["fps"],
                config["chn_detect"]["width"],
                config["chn_detect"]["height"],
                x0,
                y0,
                width,
                height,
                int(calib_ok),
                calib_id,
                state,
                err,
            )
        )
        while True:
            guard_tick()
            loop_start = time.ticks_us()
            if time.ticks_diff(loop_start, started_us) >= int(max_runtime_s * 1_000_000):
                break
            if max_frames is not None and frame_number >= int(max_frames):
                break

            snapshot_start = time.ticks_us()
            image = sensor.snapshot(chn=0, timeout=1000)
            t_cap_us = time.ticks_us()
            health.add("snapshot", time.ticks_diff(t_cap_us, snapshot_start))
            if last_cap is not None:
                interval = time.ticks_diff(t_cap_us, last_cap)
                expected = int(interval / (1_000_000.0 / config["sensor"]["fps"]))
                if expected > 1:
                    dropped += expected - 1
            last_cap = t_cap_us
            image_array = image.to_numpy_ref()
            roi_image = image_array[y0:y0 + height, x0:x0 + width]
            process_start = time.ticks_us()

            detect_start = time.ticks_us()
            predicted_u = _u_prediction(tracker, lut)
            roi_profile = roi_image[height // 2, :]
            detector.update(roi_profile, predicted_u=predicted_u)
            found = detector.found
            quality = detector.quality if found else 0
            u_px = detector.u if found else None
            if found:
                found_count += 1
                found_streak += 1
            else:
                found_streak = 0
            measurement_found = found_streak >= 2
            health.add("detect", time.ticks_diff(time.ticks_us(), detect_start))

            map_start = time.ticks_us()
            position_mm = lut.map(u_px) if measurement_found else None
            health.add("map", time.ticks_diff(time.ticks_us(), map_start))

            track_start = time.ticks_us()
            tracker.step_into(
                tracker_output,
                position_mm,
                t_cap_us,
                quality=quality,
            )
            health.add("track", time.ticks_diff(time.ticks_us(), track_start))

            status = 0
            if calib_ok:
                status |= STATUS_CALIB_OK
            if tracker_output[1]:
                status |= STATUS_PREDICTED
                predicted_count += 1
            if tracker_output[1] and tracker_output[0]:
                pos_filt = _quantize(tracker_output[4])
                vel = _quantize(tracker_output[5])
            elif tracker_output[0] and not tracker_output[1]:
                status |= STATUS_VALID
                valid_count += 1
                pos_filt = _quantize(tracker_output[4])
                vel = _quantize(tracker_output[5])
            else:
                pos_filt = INV
                vel = INV
            if measurement_found and not tracker_output[1] and tracker_output[0]:
                pos_raw = _quantize(position_mm)
            else:
                pos_raw = INV
            if quality < 80 and (measurement_found or tracker_output[1]):
                status |= STATUS_LOW_CONF
            if state == STATE_FAULT or err in (
                ERR_CAMERA,
                ERR_FRAME_TIMEOUT,
                ERR_PROCESS_TIMEOUT,
                ERR_CONFIG,
                ERR_UNKNOWN,
            ):
                status |= STATUS_FAULT
            if not tracker_output[0] and not tracker_output[1]:
                pos_filt = INV
                vel = INV
            pack_start = time.ticks_us()
            pack_ball_into(
                ball_buffer,
                ball_seq,
                t_cap_us // 1000,
                status,
                quality,
                pos_raw,
                pos_filt,
                vel,
                _age_ms(t_cap_us, time.ticks_us()),
                INV,
            )
            health.add("pack", time.ticks_diff(time.ticks_us(), pack_start))
            health.add("process", time.ticks_diff(time.ticks_us(), process_start))

            uart_start = time.ticks_us()
            link.uart.write(ball_buffer)
            health.add("uart", time.ticks_diff(time.ticks_us(), uart_start))
            health.add("total", time.ticks_diff(time.ticks_us(), loop_start))
            health.frame(t_cap_us)
            ball_seq = (ball_seq + 1) & 0xFF
            frame_number += 1

            incoming = _uart_pending(link)
            if incoming:
                for message in parser.feed(incoming):
                    if message[0] == MSG_RUN_INFO:
                        run_info[0] = message[2]
                        run_info[1] = message[3]
                        run_info[2] = message[4]
                        run_info[3] = message[5]
                        run_info[4] = message[6]
                        run_info[5] = message[7]
                    elif message[0] == MSG_CMD and message[2] == 1:
                        pack_status_into(
                            status_buffer,
                            status_seq,
                            state,
                            err,
                            0,
                            0,
                            config["latency_const_ms"],
                            0,
                            calib_id,
                            FW_VER,
                        )
                        link.uart.write(status_buffer)
                        status_seq = (status_seq + 1) & 0xFF

            now_us = time.ticks_us()
            if time.ticks_diff(now_us, next_status_us) >= 0:
                elapsed = time.ticks_diff(now_us, started_us)
                stats = health.stats(STAGES.index("process"))
                fps_x10 = int(
                    health.frame_count * 10_000_000 / max(1, elapsed)
                )
                proc_p95_x10 = int(stats[2] / 100)
                pack_status_into(
                    status_buffer,
                    status_seq,
                    state,
                    err,
                    fps_x10,
                    proc_p95_x10,
                    config["latency_const_ms"],
                    0,
                    calib_id,
                    FW_VER,
                )
                link.uart.write(status_buffer)
                status_seq = (status_seq + 1) & 0xFF
                _print_stats(health, dropped, gc.mem_free(), run_info, parser)
                health.reset()
                next_status_us = now_us + int(1_000_000 / config["status_hz"])

            image = None
            image_array = None
            roi_image = None
            roi_profile = None

            if frame_number >= next_gc_frame:
                gc_start = time.ticks_us()
                gc.collect()
                health.add("gc", time.ticks_diff(time.ticks_us(), gc_start))
                next_gc_frame += 45
    finally:
        image = None
        image_array = None
        roi_image = None
        roi_profile = None
        if link is not None:
            link.close()
        if sensor is not None:
            sensor.stop()
        if media_started:
            MediaManager.deinit()
        gc.enable()
        gc.collect()
        if started_us is not None:
            elapsed = time.ticks_diff(time.ticks_us(), started_us)
            print(
                "HCV_M1_SUMMARY,frames=%d,elapsed_us=%d,dropped=%d,"
                "found=%d,valid=%d,predicted=%d,rx_crc=%d,rx_len=%d,"
                "mem_free=%d" % (
                    frame_number,
                    elapsed,
                    dropped,
                    found_count,
                    valid_count,
                    predicted_count,
                    parser.crc_errors,
                    parser.length_errors,
                    gc.mem_free(),
                )
            )


if __name__ == "__main__":
    run()
