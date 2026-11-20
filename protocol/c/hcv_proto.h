#ifndef HCV_PROTO_H
#define HCV_PROTO_H

#include <stddef.h>
#include <stdint.h>

#define HCV_SOF0 0xA5u
#define HCV_SOF1 0x5Au
#define HCV_MSG_BALL 0x01u
#define HCV_MSG_STATUS 0x02u
#define HCV_MSG_RUN_INFO 0x81u
#define HCV_MSG_CMD 0x82u
#define HCV_INV ((int16_t)-32768)
#define HCV_MAX_PAYLOAD 253u

typedef struct {
    uint8_t msgid;
    uint8_t seq;
    uint8_t payload[HCV_MAX_PAYLOAD];
    uint8_t payload_len;
} hcv_frame_t;

typedef struct {
    uint8_t state;
    uint8_t length;
    uint8_t msgid;
    uint8_t seq;
    uint8_t payload[HCV_MAX_PAYLOAD];
    uint8_t payload_len;
    uint8_t index;
    uint8_t crc;
    uint32_t frames;
    uint32_t crc_errors;
    uint32_t length_errors;
    uint32_t sync_drops;
    uint32_t lost_frames;
    uint8_t have_seq[256];
    uint8_t last_seq[256];
} hcv_parser_t;

typedef struct {
    uint16_t t_cap_ms;
    uint8_t status;
    uint8_t quality;
    int16_t pos_raw;
    int16_t pos_filt;
    int16_t vel;
    uint8_t age_ms;
    int16_t tilt_est;
} hcv_ball_t;

typedef struct {
    uint8_t state;
    uint8_t err;
    uint16_t fps_x10;
    uint16_t proc_p95_x10;
    uint8_t lat_p95_ms;
    uint8_t stream;
    uint16_t calib_id;
    uint16_t fw_ver;
} hcv_status_t;

typedef struct {
    uint8_t test_id;
    uint8_t run_state;
    uint32_t elapsed_ms;
    int16_t target_pos;
    int16_t rod_angle;
    uint8_t car_flags;
} hcv_run_info_t;

typedef struct {
    uint8_t cmd;
    int16_t arg;
} hcv_cmd_t;

uint8_t hcv_crc8(const uint8_t *data, size_t length);
size_t hcv_encode(uint8_t msgid, uint8_t seq, const uint8_t *payload,
                  uint8_t payload_len, uint8_t *out, size_t out_size);
void hcv_parser_init(hcv_parser_t *parser);
int hcv_parser_feed(hcv_parser_t *parser, uint8_t byte, hcv_frame_t *out);
int hcv_decode_ball(const hcv_frame_t *frame, hcv_ball_t *out);
int hcv_decode_status(const hcv_frame_t *frame, hcv_status_t *out);
int hcv_decode_run_info(const hcv_frame_t *frame, hcv_run_info_t *out);
int hcv_decode_cmd(const hcv_frame_t *frame, hcv_cmd_t *out);
size_t hcv_encode_ball(uint8_t seq, const hcv_ball_t *ball, uint8_t *out, size_t out_size);
size_t hcv_encode_status(uint8_t seq, const hcv_status_t *status, uint8_t *out, size_t out_size);
size_t hcv_encode_run_info(uint8_t seq, const hcv_run_info_t *info, uint8_t *out, size_t out_size);
size_t hcv_encode_cmd(uint8_t seq, const hcv_cmd_t *cmd, uint8_t *out, size_t out_size);

#endif
