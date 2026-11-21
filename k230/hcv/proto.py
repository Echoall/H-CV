"""Allocation-light ICD v1.1.1 encoders and a small RX parser for CanMV."""

import struct


SOF0 = 0xA5
SOF1 = 0x5A
MSG_BALL = 0x01
MSG_STATUS = 0x02
MSG_RUN_INFO = 0x81
MSG_CMD = 0x82
INV = -32768


def crc8(data):
    value = 0
    for byte in data:
        value ^= byte
        for _ in range(8):
            if value & 0x80:
                value = ((value << 1) ^ 0x07) & 0xFF
            else:
                value = (value << 1) & 0xFF
    return value


def pack_ball_into(out, seq, t_cap_ms, status, quality, pos_raw, pos_filt,
                   vel, age_ms, tilt_est):
    if len(out) < 19:
        raise ValueError("BALL buffer must hold 19 bytes")
    out[0] = SOF0
    out[1] = SOF1
    out[2] = 15
    out[3] = MSG_BALL
    out[4] = seq & 0xFF
    struct.pack_into(
        "<HBBhhhBh",
        out,
        5,
        t_cap_ms & 0xFFFF,
        status & 0xFF,
        quality & 0xFF,
        int(pos_raw),
        int(pos_filt),
        int(vel),
        age_ms & 0xFF,
        int(tilt_est),
    )
    out[18] = crc8(out[2:18])
    return out


def pack_status_into(out, seq, state, err, fps_x10, proc_p95_x10,
                     lat_p95_ms, stream, calib_id, fw_ver):
    if len(out) < 18:
        raise ValueError("STATUS buffer must hold 18 bytes")
    out[0] = SOF0
    out[1] = SOF1
    out[2] = 14
    out[3] = MSG_STATUS
    out[4] = seq & 0xFF
    struct.pack_into(
        "<BBHHBBHH",
        out,
        5,
        state & 0xFF,
        err & 0xFF,
        fps_x10 & 0xFFFF,
        proc_p95_x10 & 0xFFFF,
        lat_p95_ms & 0xFF,
        stream & 0xFF,
        calib_id & 0xFFFF,
        fw_ver & 0xFFFF,
    )
    out[17] = crc8(out[2:17])
    return out


class RxParser:
    """Incremental parser for the two MCU-to-K230 message types."""

    def __init__(self):
        self.buffer = bytearray()
        self.crc_errors = 0
        self.length_errors = 0

    def feed(self, data):
        if data:
            self.buffer.extend(data)
        messages = []
        while True:
            if len(self.buffer) < 3:
                break
            if self.buffer[0] != SOF0 or self.buffer[1] != SOF1:
                del self.buffer[0]
                continue
            length = self.buffer[2]
            if length < 2 or length > 32:
                self.length_errors += 1
                del self.buffer[0]
                continue
            total = length + 4
            if len(self.buffer) < total:
                break
            if crc8(self.buffer[2:total - 1]) != self.buffer[total - 1]:
                self.crc_errors += 1
                del self.buffer[0]
                continue
            msgid = self.buffer[3]
            seq = self.buffer[4]
            payload = bytes(self.buffer[5:total - 1])
            del self.buffer[:total]
            if msgid == MSG_BALL and len(payload) == 13:
                messages.append((msgid, seq))
            elif msgid == MSG_RUN_INFO and len(payload) == 11:
                values = struct.unpack("<BBIhhB", payload)
                messages.append((
                    msgid,
                    seq,
                    values[0],
                    values[1],
                    values[2],
                    values[3],
                    values[4],
                    values[5],
                ))
            elif msgid == MSG_CMD and len(payload) == 3:
                command, argument = struct.unpack("<Bh", payload)
                messages.append((msgid, seq, command, argument))
        return messages
