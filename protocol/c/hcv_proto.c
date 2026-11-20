#include "hcv_proto.h"

#include <string.h>

static uint8_t known_payload_length(uint8_t msgid)
{
    switch (msgid) {
    case HCV_MSG_BALL: return 13u;
    case HCV_MSG_STATUS: return 12u;
    case HCV_MSG_RUN_INFO: return 11u;
    case HCV_MSG_CMD: return 3u;
    default: return 0u;
    }
}

uint8_t hcv_crc8(const uint8_t *data, size_t length)
{
    uint8_t crc = 0u;
    size_t i;
    unsigned int bit;
    for (i = 0u; i < length; ++i) {
        crc ^= data[i];
        for (bit = 0u; bit < 8u; ++bit)
            crc = (uint8_t)(((crc & 0x80u) != 0u)
                ? ((((unsigned int)crc << 1) ^ 0x07u) & 0xffu)
                : (((unsigned int)crc << 1) & 0xffu));
    }
    return crc;
}

size_t hcv_encode(uint8_t msgid, uint8_t seq, const uint8_t *payload,
                  uint8_t payload_len, uint8_t *out, size_t out_size)
{
    uint8_t length;
    size_t total;
    if (payload_len > HCV_MAX_PAYLOAD)
        return 0u;
    length = (uint8_t)(payload_len + 2u);
    total = (size_t)length + 4u;
    if (out == NULL || out_size < total)
        return 0u;
    out[0] = HCV_SOF0;
    out[1] = HCV_SOF1;
    out[2] = length;
    out[3] = msgid;
    out[4] = seq;
    if (payload_len != 0u && payload != NULL)
        memcpy(&out[5], payload, payload_len);
    out[total - 1u] = hcv_crc8(&out[2], total - 3u);
    return total;
}

void hcv_parser_init(hcv_parser_t *parser)
{
    memset(parser, 0, sizeof(*parser));
}

static void discard_first(hcv_parser_t *parser)
{
    if (parser->buffer_len != 0u) {
        memmove(parser->buffer, parser->buffer + 1u, parser->buffer_len - 1u);
        parser->buffer_len--;
    }
}

static void find_header(hcv_parser_t *parser)
{
    uint8_t index = 0u;
    while (index + 1u < parser->buffer_len &&
           !(parser->buffer[index] == HCV_SOF0 && parser->buffer[index + 1u] == HCV_SOF1))
        index++;
    if (index != 0u) {
        parser->sync_drops += index;
        memmove(parser->buffer, parser->buffer + index, parser->buffer_len - index);
        parser->buffer_len = (uint8_t)(parser->buffer_len - index);
    }
}

int hcv_parser_feed(hcv_parser_t *parser, uint8_t byte, hcv_frame_t *out)
{
    uint8_t length;
    uint8_t expected;
    uint8_t total;
    uint8_t gap;
    if (parser->buffer_len == sizeof(parser->buffer))
        discard_first(parser);
    parser->buffer[parser->buffer_len++] = byte;
    for (;;) {
        find_header(parser);
        if (parser->buffer_len < 3u)
            return 0;
        length = parser->buffer[2];
        if (length < 2u || length > HCV_MAX_LEN) {
            parser->length_errors++;
            discard_first(parser);
            continue;
        }
        if (parser->buffer_len < 4u)
            return 0;
        expected = known_payload_length(parser->buffer[3]);
        if (expected != 0u && length != (uint8_t)(expected + 2u)) {
            parser->length_errors++;
            discard_first(parser);
            continue;
        }
        total = (uint8_t)(length + 4u);
        if (parser->buffer_len < total)
            return 0;
        if (hcv_crc8(parser->buffer + 2u, (size_t)length + 1u) != parser->buffer[total - 1u]) {
            parser->crc_errors++;
            discard_first(parser);
            continue;
        }
        if (out != NULL) {
            out->msgid = parser->buffer[3];
            out->seq = parser->buffer[4];
            out->payload_len = (uint8_t)(length - 2u);
            if (out->payload_len != 0u)
                memcpy(out->payload, parser->buffer + 5u, out->payload_len);
        }
        parser->frames++;
        if (parser->have_seq[parser->buffer[3]]) {
            gap = (uint8_t)(parser->buffer[4] - parser->last_seq[parser->buffer[3]]);
            if (gap > 1u && gap < 128u)
                parser->lost_frames += (uint32_t)(gap - 1u);
        }
        parser->have_seq[parser->buffer[3]] = 1u;
        parser->last_seq[parser->buffer[3]] = parser->buffer[4];
        memmove(parser->buffer, parser->buffer + total, parser->buffer_len - total);
        parser->buffer_len = (uint8_t)(parser->buffer_len - total);
        return 1;
    }
}

static uint16_t read_u16(const uint8_t *p)
{
    return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

static uint32_t read_u32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static int16_t read_i16(const uint8_t *p)
{
    return (int16_t)read_u16(p);
}

static void write_u16(uint8_t *p, uint16_t value)
{
    p[0] = (uint8_t)value;
    p[1] = (uint8_t)(value >> 8);
}

static void write_u32(uint8_t *p, uint32_t value)
{
    p[0] = (uint8_t)value;
    p[1] = (uint8_t)(value >> 8);
    p[2] = (uint8_t)(value >> 16);
    p[3] = (uint8_t)(value >> 24);
}

static void write_i16(uint8_t *p, int16_t value)
{
    write_u16(p, (uint16_t)value);
}

int hcv_decode_ball(const hcv_frame_t *frame, hcv_ball_t *out)
{
    const uint8_t *p;
    if (frame == NULL || out == NULL || frame->msgid != HCV_MSG_BALL || frame->payload_len != 13u)
        return 0;
    p = frame->payload;
    out->t_cap_ms = read_u16(p);
    out->status = p[2];
    out->quality = p[3];
    out->pos_raw = read_i16(p + 4);
    out->pos_filt = read_i16(p + 6);
    out->vel = read_i16(p + 8);
    out->age_ms = p[10];
    out->tilt_est = read_i16(p + 11);
    return 1;
}

int hcv_decode_status(const hcv_frame_t *frame, hcv_status_t *out)
{
    const uint8_t *p;
    if (frame == NULL || out == NULL || frame->msgid != HCV_MSG_STATUS || frame->payload_len != 12u)
        return 0;
    p = frame->payload;
    out->state = p[0];
    out->err = p[1];
    out->fps_x10 = read_u16(p + 2);
    out->proc_p95_x10 = read_u16(p + 4);
    out->lat_p95_ms = p[6];
    out->stream = p[7];
    out->calib_id = read_u16(p + 8);
    out->fw_ver = read_u16(p + 10);
    return 1;
}

int hcv_decode_run_info(const hcv_frame_t *frame, hcv_run_info_t *out)
{
    const uint8_t *p;
    if (frame == NULL || out == NULL || frame->msgid != HCV_MSG_RUN_INFO || frame->payload_len != 11u)
        return 0;
    p = frame->payload;
    out->test_id = p[0];
    out->run_state = p[1];
    out->elapsed_ms = read_u32(p + 2);
    out->target_pos = read_i16(p + 6);
    out->rod_angle = read_i16(p + 8);
    out->car_flags = p[10];
    return 1;
}

int hcv_decode_cmd(const hcv_frame_t *frame, hcv_cmd_t *out)
{
    if (frame == NULL || out == NULL || frame->msgid != HCV_MSG_CMD || frame->payload_len != 3u)
        return 0;
    out->cmd = frame->payload[0];
    out->arg = read_i16(frame->payload + 1);
    return 1;
}

size_t hcv_encode_ball(uint8_t seq, const hcv_ball_t *ball, uint8_t *out, size_t out_size)
{
    uint8_t p[13];
    if (ball == NULL) return 0u;
    write_u16(p, ball->t_cap_ms); p[2] = ball->status; p[3] = ball->quality;
    write_i16(p + 4, ball->pos_raw); write_i16(p + 6, ball->pos_filt);
    write_i16(p + 8, ball->vel); p[10] = ball->age_ms; write_i16(p + 11, ball->tilt_est);
    return hcv_encode(HCV_MSG_BALL, seq, p, 13u, out, out_size);
}

size_t hcv_encode_status(uint8_t seq, const hcv_status_t *status, uint8_t *out, size_t out_size)
{
    uint8_t p[12];
    if (status == NULL) return 0u;
    p[0] = status->state; p[1] = status->err; write_u16(p + 2, status->fps_x10);
    write_u16(p + 4, status->proc_p95_x10); p[6] = status->lat_p95_ms; p[7] = status->stream;
    write_u16(p + 8, status->calib_id); write_u16(p + 10, status->fw_ver);
    return hcv_encode(HCV_MSG_STATUS, seq, p, 12u, out, out_size);
}

size_t hcv_encode_run_info(uint8_t seq, const hcv_run_info_t *info, uint8_t *out, size_t out_size)
{
    uint8_t p[11];
    if (info == NULL) return 0u;
    p[0] = info->test_id; p[1] = info->run_state; write_u32(p + 2, info->elapsed_ms);
    write_i16(p + 6, info->target_pos); write_i16(p + 8, info->rod_angle); p[10] = info->car_flags;
    return hcv_encode(HCV_MSG_RUN_INFO, seq, p, 11u, out, out_size);
}

size_t hcv_encode_cmd(uint8_t seq, const hcv_cmd_t *cmd, uint8_t *out, size_t out_size)
{
    uint8_t p[3];
    if (cmd == NULL) return 0u;
    p[0] = cmd->cmd; write_i16(p + 1, cmd->arg);
    return hcv_encode(HCV_MSG_CMD, seq, p, 3u, out, out_size);
}
