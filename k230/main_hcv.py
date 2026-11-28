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
from array import array

from image import ALLOC_REF
from media.media import MediaManager
from media.sensor import Sensor

if sys.path[0] != "/sdcard/hcv":
    sys.path.insert(0, "/sdcard/hcv")

from hcv.calibration import runtime_calibration
from hcv.config import load_config, runtime_config_warning
from hcv.detect_profile import ProfileDetector
from hcv.guard import start as guard_start
from hcv.guard import tick as guard_tick
from hcv.health import STAGES, TimingWindow
from hcv.link_uart import UART1Link
from hcv.tracker import KalmanTracker
from hcv.proto import (
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
ALLOC_PROBE_STAGES = (
    "snapshot",
    "frame_view",
    "detect",
    "map",
    "track",
    "pack",
    "uart",
    "cleanup",
)
ALLOC_DETAIL_STAGES = (
    "prediction",
    "profile_slice",
    "detector_update",
    "fast_copy_profile",
    "fast_residual_setup",
    "fast_peak_search",
    "fast_width_scan",
    "fast_centroid",
    "fast_quality",
    "fast_diag",
)


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


def _load_lut(config, width, path="/sdcard/hcv/calib.json"):
    nominal = PixelLUT((0.0, float(width - 1)), (NOMINAL_MIN_MM, NOMINAL_MAX_MM))
    if not _exists(path):
        return nominal, False, 0, ERR_CALIB_MISSING, None
    try:
        with open(path, "r") as stream:
            calibration = json.load(stream)
        pixels, physical, background = runtime_calibration(calibration, config)
        lut = PixelLUT(pixels, physical)
        calibration_id = int(calibration.get("id", 0)) & 0xFFFF
        return lut, True, calibration_id, 0, background
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return nominal, False, 0, ERR_CALIB_MISMATCH, None


def configure_detector_sensor(sensor, config):
    sensor.reset()
    mode = config["sensor"]
    if mode["hmirror"]:
        sensor.set_hmirror(True)
    if mode["vflip"]:
        sensor.set_vflip(True)
    channel = config["chn_detect"]
    size = {"width": channel["width"], "height": channel["height"], "chn": 0}
    if channel["crop"] is not None:
        size["crop"] = channel["crop"]
    sensor.set_framesize(**size)
    sensor.set_pixformat(Sensor.GRAYSCALE, chn=0)


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


def _write_column_profile(image, bounds, pixels, profile_pixels):
    """Average the ROI into a second buffer while keeping the pixels for shape checks."""
    cropped = image.crop(roi=bounds, alloc=ALLOC_REF, data=pixels)
    cropped.mean_pooled(1, bounds[3], alloc=ALLOC_REF, data=profile_pixels)


def _log_memory(round_index, phase):
    free = []
    for name in ("sys_heap", "sys_page", "sys_mmz"):
        query = getattr(gc, name, None)
        try:
            free.append(query()[2] if query is not None else -1)
        except OSError as error:
            print("HCV_M1_MEM_ERROR,%s,%r" % (name, error))
            free.append(-1)
    print(
        "HCV_M1_MEM,round=%d,phase=%s,mp_free=%d,"
        "sys_heap_free=%d,sys_page_free=%d,sys_mmz_free=%d"
        % (round_index, phase, gc.mem_free(), free[0], free[1], free[2])
    )


def _print_stats(health, dropped, gc_free, run_info, parser):
    stats = health.all_stats()
    snapshot = stats[STAGES.index("snapshot")]
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
        "snapshot=%d/%d/%d/%d,"
        "detect=%d/%d/%d/%d,map=%d/%d/%d/%d,track=%d/%d/%d/%d,"
        "pack=%d/%d/%d/%d,uart=%d/%d/%d/%d,process=%d/%d/%d/%d,"
        "total=%d/%d/%d/%d,gc=%d/%d/%d/%d,gc_free=%d,"
        "rx_crc=%d,rx_len=%d,run_state=%d" % (
            health.frame_count,
            elapsed_us,
            fps,
            dropped,
            snapshot[1],
            snapshot[2],
            snapshot[3],
            snapshot[4],
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


def _allocation_probe(
    sensor,
    link,
    detector,
    tracker,
    tracker_output,
    lut,
    ball_buffer,
    roi_bounds,
    roi_pixels,
    profile_pixels,
    profile_array,
    buffers,
    detail_buffers,
    fast_probe,
    max_frames,
):
    """Measure per-stage free-memory changes with automatic GC disabled."""
    stage_deltas = buffers[:-1]
    frame_drift = buffers[-1]
    frame_count = 0
    detected_frames = 0
    quality_min = 255
    quality_max = 0
    last_cap = None
    ball_seq = 0
    image = None
    image_array = None
    roi_image = None
    roi_profile = None
    gc.collect()
    gc.disable()
    detector._fast_probe = fast_probe
    free_start = gc.mem_free()
    print(
        "HCV_M1_ALLOC_BEGIN,frames=%d,free_start=%d,gc=disabled"
        % (max_frames, free_start)
    )
    while frame_count < max_frames:
        guard_tick()
        frame_start_free = gc.mem_free()

        before = gc.mem_free()
        image = sensor.snapshot(chn=0, timeout=1000)
        t_cap_us = time.ticks_us()
        after = gc.mem_free()
        stage_deltas[0][frame_count] = before - after

        before = gc.mem_free()
        _write_column_profile(image, roi_bounds, roi_pixels, profile_pixels)
        after = gc.mem_free()
        stage_deltas[1][frame_count] = before - after

        before = gc.mem_free()
        predicted_u = _u_prediction(tracker, lut)
        after = gc.mem_free()
        detail_buffers[0][frame_count] = before - after

        before = gc.mem_free()
        roi_profile = profile_array
        after = gc.mem_free()
        detail_buffers[1][frame_count] = before - after

        before = gc.mem_free()
        detector.update(roi_profile, predicted_u=predicted_u, roi_pixels=roi_pixels)
        del roi_profile
        after = gc.mem_free()
        detail_buffers[2][frame_count] = before - after
        detail_buffers[3][frame_count] = (
            fast_probe[0] - fast_probe[1]
        )
        detail_buffers[4][frame_count] = (
            fast_probe[1] - fast_probe[2]
        )
        detail_buffers[5][frame_count] = (
            fast_probe[2] - fast_probe[3]
        )
        detail_buffers[6][frame_count] = (
            fast_probe[3] - fast_probe[4]
        )
        detail_buffers[7][frame_count] = (
            fast_probe[4] - fast_probe[5]
        )
        detail_buffers[8][frame_count] = (
            fast_probe[5] - fast_probe[6]
        )
        detail_buffers[9][frame_count] = (
            fast_probe[6] - fast_probe[7]
        )
        stage_deltas[2][frame_count] = before - after

        found = detector.found
        if found:
            detected_frames += 1
            quality_min = min(quality_min, detector.quality)
            quality_max = max(quality_max, detector.quality)
        before = gc.mem_free()
        position_mm = lut.map(detector.u) if found else None
        after = gc.mem_free()
        stage_deltas[3][frame_count] = before - after

        before = gc.mem_free()
        tracker.step_into(
            tracker_output,
            position_mm,
            t_cap_us,
            quality=detector.quality if found else 0,
        )
        after = gc.mem_free()
        stage_deltas[4][frame_count] = before - after

        if tracker_output[0]:
            pos_filt = _quantize(tracker_output[4])
            vel = _quantize(tracker_output[5])
        else:
            pos_filt = INV
            vel = INV
        before = gc.mem_free()
        pack_ball_into(
            ball_buffer,
            ball_seq,
            t_cap_us // 1000,
            STATUS_VALID if tracker_output[0] else 0,
            detector.quality if found else 0,
            _quantize(position_mm) if found else INV,
            pos_filt,
            vel,
            _age_ms(t_cap_us, time.ticks_us()),
            INV,
        )
        after = gc.mem_free()
        stage_deltas[5][frame_count] = before - after

        before = gc.mem_free()
        link.uart.write(ball_buffer)
        after = gc.mem_free()
        stage_deltas[6][frame_count] = before - after

        before = gc.mem_free()
        image = None
        image_array = None
        roi_image = None
        roi_profile = None
        after = gc.mem_free()
        stage_deltas[7][frame_count] = before - after
        frame_drift[frame_count] = frame_start_free - after

        ball_seq = (ball_seq + 1) & 0xFF
        frame_count += 1
        if last_cap is not None:
            _ = time.ticks_diff(t_cap_us, last_cap)
        last_cap = t_cap_us

    free_end = gc.mem_free()
    print(
        "HCV_M1_ALLOC_END,frames=%d,free_end=%d,drift=%d,"
        "detected_frames=%d,quality_min=%d,quality_max=%d"
        % (frame_count, free_end, free_start - free_end, detected_frames,
           quality_min if detected_frames else 0, quality_max)
    )
    for index, name in enumerate(ALLOC_PROBE_STAGES):
        values = stage_deltas[index]
        minimum = values[0]
        maximum = values[0]
        total = 0
        positive = 0
        for offset in range(frame_count):
            value = int(values[offset])
            minimum = min(minimum, value)
            maximum = max(maximum, value)
            total += value
            if value > 0:
                positive += 1
        print(
            "HCV_M1_ALLOC_STAGE,stage=%s,frames=%d,total=%d,mean=%d,"
            "min=%d,max=%d,positive=%d"
            % (
                name,
                frame_count,
                total,
                int(total / max(1, frame_count)),
                minimum,
                maximum,
                positive,
            )
        )
    for index, name in enumerate(ALLOC_DETAIL_STAGES):
        values = detail_buffers[index]
        minimum = values[0]
        maximum = values[0]
        total = 0
        positive = 0
        for offset in range(frame_count):
            value = int(values[offset])
            minimum = min(minimum, value)
            maximum = max(maximum, value)
            total += value
            if value > 0:
                positive += 1
        print(
            "HCV_M1_ALLOC_DETAIL,stage=%s,frames=%d,total=%d,mean=%d,"
            "min=%d,max=%d,positive=%d"
            % (
                name,
                frame_count,
                total,
                int(total / max(1, frame_count)),
                minimum,
                maximum,
                positive,
            )
        )
    for offset in range(frame_count):
        print(
            "HCV_M1_ALLOC_FRAME,frame=%d,drift=%d,%s"
            % (
                offset + 1,
                frame_drift[offset],
                ",".join(
                    "%s=%d" % (name, int(stage_deltas[index][offset]))
                    for index, name in enumerate(ALLOC_PROBE_STAGES)
                ),
            )
        )
    detector._fast_probe = None
    return frame_count


def run(max_runtime_s=10, max_frames=None, allocation_probe=False, observe_ball=None,
        calibration_path=None, shared=None, config_directory="/sdcard/hcv", report_capture_span=False):
    """Run the bounded M1 loop and print machine-readable evidence lines."""
    config = load_config(config_directory) if shared is None else shared.config
    channel = 0
    if shared is not None:
        channel = 1
        if allocation_probe:
            raise ValueError("allocation probe requires exclusive sensor access")
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
    allocation_buffers = None
    allocation_detail_buffers = None
    allocation_fast_probe = None
    ball_seq = 0
    status_seq = 0
    dropped = 0
    frame_number = 0
    found_count = 0
    valid_count = 0
    predicted_count = 0
    lost_count = 0
    found_streak = 0
    round_index = 0
    round_start_us = None
    pre_gc_log_us = post_gc_log_us = round_stats_us = round_log_us = round_bookkeeping_us = 0
    round_frame_start = 0
    round_dropped_start = 0
    round_valid_start = 0
    run_info = [0, 0, 0, INV, INV, 0]
    last_cap = None
    started_us = None
    next_status_us = None
    state = STATE_BOOT
    err = warning_err
    calib_ok = False
    calib_id = 0
    try:
        guard_start(float(max_runtime_s) + 20.0)
        if shared is None:
            sensor = Sensor(
                width=config["sensor"]["width"],
                height=config["sensor"]["height"],
                fps=config["sensor"]["fps"],
            )
            configure_detector_sensor(sensor, config)
            MediaManager.init()
            media_started = True
            sensor.run()
        else:
            sensor = shared.sensor
        for _ in range(8):
            guard_tick()
            image = sensor.snapshot(chn=channel, timeout=1000)
            image = None
        roi = config["roi"]
        x0, y0, width, height = roi["x"], roi["y"], roi["w"], roi["h"]
        roi_bounds = (x0, y0, width, height)
        roi_pixels = bytearray(width * height)
        profile_pixels = bytearray(width)
        import ulab.numpy as np
        profile_array = np.frombuffer(profile_pixels, dtype=np.uint8)
        calibration = (
            _load_lut(config, width) if calibration_path is None
            else _load_lut(config, width, calibration_path)
        )
        lut, calib_ok, calib_id, lut_err, background = calibration
        if background is None:
            image = sensor.snapshot(chn=channel, timeout=1000)
            _write_column_profile(image, roi_bounds, roi_pixels, profile_pixels)
            background = np.array(profile_array)
        detector = ProfileDetector(
            background,
            config["detector"]["params"],
            width=width,
        )
        if not calib_ok and err == 0:
            err = lut_err
        tracker = KalmanTracker(config["tracker"])
        link = UART1Link(config["uart"]["baud"])
        state = STATE_READY if calib_ok and err == 0 else STATE_NO_CALIB
        if err == ERR_CONFIG:
            state = STATE_FAULT
        if allocation_probe:
            probe_frames = int(max_frames if max_frames is not None else 200)
            allocation_buffers = [
                array("i", [0] * probe_frames)
                for _ in range(len(ALLOC_PROBE_STAGES) + 1)
            ]
            allocation_detail_buffers = [
                array("i", [0] * probe_frames)
                for _ in range(len(ALLOC_DETAIL_STAGES))
            ]
            allocation_fast_probe = array("i", [0] * 8)
        gc.collect()
        gc.disable()
        started_us = time.ticks_us()
        round_start_us = started_us
        next_status_us = started_us
        print(
            "HCV_M1_BEGIN,mode=%dx%dx%d,detect=%dx%d,roi=%d:%d:%d:%d,"
            "calib_ok=%d,calib_id=%d,state=%d,err=%d,detector_fast=%d,"
            "profile=mean_pooled,roi_buffer=preallocated,shape_candidates=3" % (
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
                int(detector.fast),
            )
        )
        if allocation_probe:
            frame_number = _allocation_probe(
                sensor,
                link,
                detector,
                tracker,
                tracker_output,
                lut,
                ball_buffer,
                roi_bounds,
                roi_pixels,
                profile_pixels,
                profile_array,
                allocation_buffers,
                allocation_detail_buffers,
                allocation_fast_probe,
                probe_frames,
            )
            return
        _log_memory(round_index, "start")
        if shared is not None:
            shared.begin(started_us)
        while True:
            guard_tick()
            loop_start = time.ticks_us()
            if time.ticks_diff(loop_start, started_us) >= int(max_runtime_s * 1_000_000):
                break
            if max_frames is not None and frame_number >= int(max_frames):
                break

            snapshot_start = time.ticks_us()
            image = sensor.snapshot(chn=channel, timeout=1000)
            t_cap_us = time.ticks_us()
            health.add("snapshot", time.ticks_diff(t_cap_us, snapshot_start))
            if last_cap is not None:
                interval = time.ticks_diff(t_cap_us, last_cap)
                expected = int(interval / (1_000_000.0 / config["sensor"]["fps"]))
                if expected > 1:
                    dropped += expected - 1
            last_cap = t_cap_us
            process_start = time.ticks_us()

            detect_start = time.ticks_us()
            predicted_u = _u_prediction(tracker, lut)
            _write_column_profile(image, roi_bounds, roi_pixels, profile_pixels)
            detector.update(profile_array, predicted_u=predicted_u, roi_pixels=roi_pixels)
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
            extrapolated = False
            if calib_ok and position_mm is not None:
                if not NOMINAL_MIN_MM <= position_mm <= NOMINAL_MAX_MM:
                    position_mm = None
                else:
                    extrapolated = u_px < lut.pixels[0] or u_px > lut.pixels[-1]
            health.add("map", time.ticks_diff(time.ticks_us(), map_start))

            track_start = time.ticks_us()
            tracker.step_into(
                tracker_output,
                position_mm,
                t_cap_us,
                quality=quality,
            )
            health.add("track", time.ticks_diff(time.ticks_us(), track_start))
            if tracker_output[2]:
                lost_count += 1

            status = 0
            if calib_ok:
                status |= STATUS_CALIB_OK
            if extrapolated:
                status |= STATUS_EXTRAPOLATED
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
            process_us = time.ticks_diff(time.ticks_us(), process_start)
            health.add("process", process_us)

            uart_start = time.ticks_us()
            link.uart.write(ball_buffer)
            health.add("uart", time.ticks_diff(time.ticks_us(), uart_start))
            if observe_ball is not None:
                observe_ball(image, ball_buffer)
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

            round_now_us = time.ticks_us()
            if (
                round_start_us is not None
                and time.ticks_diff(round_now_us, round_start_us) >= 30_000_000
            ):
                _log_memory(round_index, "before_gc")
                gc_start = time.ticks_us()
                gc.collect()
                gc_end = time.ticks_us()
                gc_elapsed_us = time.ticks_diff(gc_end, gc_start)
                _log_memory(round_index, "after_gc")
                if report_capture_span:
                    stats_start = time.ticks_us()
                health.add("gc", gc_elapsed_us)
                gc_dropped_est = int(
                    gc_elapsed_us
                    / (1_000_000.0 / config["sensor"]["fps"])
                )
                if report_capture_span:
                    log_start = time.ticks_us()
                print(
                    "HCV_M1_ROUND,round=%d,frames=%d,dropped=%d,"
                    "active_us=%d,valid=%d,gc_us=%d,gc_dropped_est=%d,free=%d" % (
                        round_index,
                        frame_number - round_frame_start,
                        dropped - round_dropped_start,
                        time.ticks_diff(round_now_us, round_start_us),
                        valid_count - round_valid_start,
                        gc_elapsed_us,
                        gc_dropped_est,
                        gc.mem_free(),
                    )
                )
                if report_capture_span:
                    log_end = time.ticks_us()
                round_index += 1
                if not report_capture_span:
                    round_start_us = time.ticks_us()
                round_frame_start = frame_number
                round_dropped_start = dropped
                round_valid_start = valid_count
                last_cap = None
                if report_capture_span:
                    pre_gc_log_us += time.ticks_diff(gc_start, round_now_us)
                    post_gc_log_us += time.ticks_diff(stats_start, gc_end)
                    round_stats_us += time.ticks_diff(log_start, stats_start)
                    round_log_us += time.ticks_diff(log_end, log_start)
                    round_start_us = time.ticks_us()
                    round_bookkeeping_us += time.ticks_diff(round_start_us, log_end)
            if shared is not None:
                shared.end_frame(frame_number, process_us, t_cap_us)
    finally:
        stopped_us = time.ticks_us()
        if shared is not None and started_us is not None:
            print("HCV_DUAL_END,%d,%d" % (frame_number, time.ticks_diff(stopped_us, started_us)))
        image = None
        image_array = None
        roi_image = None
        roi_profile = None
        if link is not None:
            link.close()
        if sensor is not None and shared is None:
            sensor.stop()
        if media_started:
            MediaManager.deinit()
        gc.enable()
        gc.collect()
        if started_us is not None:
            if shared is not None:
                shared.report(frame_number)
            if report_capture_span:
                if health.frame_count:
                    _print_stats(health, dropped, gc.mem_free(), run_info, parser)
                print("HCV_M1_CAPTURE_SPAN_US,%d" % time.ticks_diff(stopped_us, started_us))
                print(
                    "HCV_M1_BOUNDARY_TIMING,boundaries=%d,pre_gc_log_us=%d,post_gc_log_us=%d,"
                    "round_stats_us=%d,round_log_us=%d,bookkeeping_us=%d" % (
                        round_index, pre_gc_log_us, post_gc_log_us,
                        round_stats_us, round_log_us, round_bookkeeping_us,
                    )
                )
            _log_memory(round_index, "stopped")
            if round_start_us is not None and frame_number > round_frame_start:
                print(
                    "HCV_M1_ROUND,round=%d,final=1,frames=%d,dropped=%d,"
                    "active_us=%d,valid=%d,gc_us=0,gc_dropped_est=0,free=%d" % (
                        round_index,
                        frame_number - round_frame_start,
                        dropped - round_dropped_start,
                        time.ticks_diff(stopped_us, round_start_us),
                        valid_count - round_valid_start,
                        gc.mem_free(),
                    )
                )
            elapsed = time.ticks_diff(time.ticks_us(), started_us)
            print(
                "HCV_M1_SUMMARY,frames=%d,elapsed_us=%d,dropped=%d,"
                "found=%d,valid=%d,predicted=%d,lost=%d,rx_crc=%d,rx_len=%d,"
                "mem_free=%d" % (
                    frame_number,
                    elapsed,
                    dropped,
                    found_count,
                    valid_count,
                    predicted_count,
                    lost_count,
                    parser.crc_errors,
                    parser.length_errors,
                    gc.mem_free(),
                )
            )


if __name__ == "__main__":
    run()
