"""ICD v1.1.1 binary protocol, compatible with CPython and MicroPython."""

import struct

SOF0 = 0xA5
SOF1 = 0x5A
MSG_BALL = 0x01
MSG_STATUS = 0x02
MSG_RUN_INFO = 0x81
MSG_CMD = 0x82
INV = -32768
MAX_LEN = 32

PAYLOAD_LENGTHS = {
    MSG_BALL: 13,
    MSG_STATUS: 12,
    MSG_RUN_INFO: 11,
    MSG_CMD: 3,
}


class Frame:
    __slots__ = ("msgid", "seq", "payload")

    def __init__(self, msgid, seq, payload):
        self.msgid = msgid
        self.seq = seq
        self.payload = bytes(payload)

    def __repr__(self):
        return "Frame(msgid=0x%02x, seq=%d, payload=%r)" % (
            self.msgid,
            self.seq,
            self.payload,
        )


def crc8(data):
    value = 0
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = ((value << 1) ^ 0x07) & 0xFF if value & 0x80 else (value << 1) & 0xFF
    return value


def encode_frame(msgid, seq, payload):
    payload = bytes(payload)
    if not 0 <= msgid <= 255 or not 0 <= seq <= 255:
        raise ValueError("msgid and seq must fit in one byte")
    length = 2 + len(payload)
    if length > 255:
        raise ValueError("payload is too long")
    body = bytes((length, msgid, seq)) + payload
    return bytes((SOF0, SOF1)) + body + bytes((crc8(body),))


def decode_frame(raw):
    raw = bytes(raw)
    if len(raw) < 5 or raw[0:2] != bytes((SOF0, SOF1)):
        raise ValueError("bad frame header")
    length = raw[2]
    if len(raw) != length + 4:
        raise ValueError("bad frame length")
    if crc8(raw[2:-1]) != raw[-1]:
        raise ValueError("bad frame crc")
    return Frame(raw[3], raw[4], raw[5:-1])


def _check_payload(msgid, payload):
    if len(payload) != PAYLOAD_LENGTHS[msgid]:
        raise ValueError("wrong payload length for msgid 0x%02x" % msgid)


def encode_ball(seq, t_cap_ms, status, quality, pos_raw, pos_filt, vel, age_ms, tilt_est):
    return encode_frame(
        MSG_BALL,
        seq,
        struct.pack("<HBBhhhBh", t_cap_ms & 0xFFFF, status & 0xFF, quality & 0xFF,
                    pos_raw, pos_filt, vel, age_ms & 0xFF, tilt_est),
    )


def decode_ball(frame):
    frame = _as_frame(frame, MSG_BALL)
    _check_payload(MSG_BALL, frame.payload)
    values = struct.unpack("<HBBhhhBh", frame.payload)
    return {
        "seq": frame.seq,
        "t_cap_ms": values[0],
        "status": values[1],
        "quality": values[2],
        "pos_raw": values[3],
        "pos_filt": values[4],
        "vel": values[5],
        "age_ms": values[6],
        "tilt_est": values[7],
    }


def encode_status(seq, state, err, fps_x10, proc_p95_x10, lat_p95_ms, stream, calib_id, fw_ver):
    return encode_frame(
        MSG_STATUS,
        seq,
        struct.pack("<BBHHBBHH", state & 0xFF, err & 0xFF, fps_x10 & 0xFFFF,
                    proc_p95_x10 & 0xFFFF, lat_p95_ms & 0xFF, stream & 0xFF,
                    calib_id & 0xFFFF, fw_ver & 0xFFFF),
    )


def decode_status(frame):
    frame = _as_frame(frame, MSG_STATUS)
    _check_payload(MSG_STATUS, frame.payload)
    values = struct.unpack("<BBHHBBHH", frame.payload)
    return {
        "seq": frame.seq,
        "state": values[0],
        "err": values[1],
        "fps_x10": values[2],
        "proc_p95_x10": values[3],
        "lat_p95_ms": values[4],
        "stream": values[5],
        "calib_id": values[6],
        "fw_ver": values[7],
    }


def encode_run_info(seq, test_id, run_state, elapsed_ms, target_pos, rod_angle, car_flags):
    return encode_frame(
        MSG_RUN_INFO,
        seq,
        struct.pack("<BBIhhB", test_id & 0xFF, run_state & 0xFF, elapsed_ms & 0xFFFFFFFF,
                    target_pos, rod_angle, car_flags & 0xFF),
    )


def decode_run_info(frame):
    frame = _as_frame(frame, MSG_RUN_INFO)
    _check_payload(MSG_RUN_INFO, frame.payload)
    values = struct.unpack("<BBIhhB", frame.payload)
    return {
        "seq": frame.seq,
        "test_id": values[0],
        "run_state": values[1],
        "elapsed_ms": values[2],
        "target_pos": values[3],
        "rod_angle": values[4],
        "car_flags": values[5],
    }


def encode_cmd(seq, cmd, arg):
    return encode_frame(MSG_CMD, seq, struct.pack("<Bh", cmd & 0xFF, arg))


def decode_cmd(frame):
    frame = _as_frame(frame, MSG_CMD)
    _check_payload(MSG_CMD, frame.payload)
    cmd, arg = struct.unpack("<Bh", frame.payload)
    return {"seq": frame.seq, "cmd": cmd, "arg": arg}


def _as_frame(value, msgid):
    frame = value if isinstance(value, Frame) else decode_frame(value)
    if frame.msgid != msgid:
        raise ValueError("unexpected msgid 0x%02x" % frame.msgid)
    return frame


class StreamParser:
    """Incremental parser with error and sequence-loss counters."""

    def __init__(self):
        self.buffer = bytearray()
        self.frames = 0
        self.crc_errors = 0
        self.length_errors = 0
        self.sync_drops = 0
        self.lost_frames = 0
        self._last_seq = {}

    def feed(self, data):
        self.buffer.extend(bytes(data))
        output = []
        while True:
            if len(self.buffer) < 2:
                break
            if self.buffer[0] != SOF0 or self.buffer[1] != SOF1:
                index = self.buffer.find(bytes((SOF0, SOF1)))
                if index < 0:
                    self.sync_drops += len(self.buffer) - (1 if self.buffer[-1] == SOF0 else 0)
                    if self.buffer[-1] == SOF0:
                        self.buffer[:] = self.buffer[-1:]
                    else:
                        self.buffer.clear()
                else:
                    self.sync_drops += index
                    del self.buffer[:index]
                continue
            if len(self.buffer) < 3:
                break
            length = self.buffer[2]
            if length < 2 or length > MAX_LEN:
                self.length_errors += 1
                del self.buffer[0]
                continue
            if len(self.buffer) >= 4:
                msgid = self.buffer[3]
                if msgid in PAYLOAD_LENGTHS and length != PAYLOAD_LENGTHS[msgid] + 2:
                    self.length_errors += 1
                    del self.buffer[0]
                    continue
            total = length + 4
            if len(self.buffer) < total:
                break
            msgid = self.buffer[3]
            candidate = bytes(self.buffer[:total])
            if crc8(candidate[2:-1]) != candidate[-1]:
                self.crc_errors += 1
                del self.buffer[0]
                continue
            frame = Frame(msgid, candidate[4], candidate[5:-1])
            output.append(frame)
            self.frames += 1
            previous = self._last_seq.get(msgid)
            if previous is not None:
                gap = (frame.seq - previous) & 0xFF
                if 1 < gap < 128:
                    self.lost_frames += gap - 1
            self._last_seq[msgid] = frame.seq
            del self.buffer[:total]
        return output

    def flush(self):
        leftover = len(self.buffer)
        self.buffer.clear()
        return leftover
