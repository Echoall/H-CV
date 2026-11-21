#include "hcv_proto.h"

static hcv_parser_t parser;
static hcv_frame_t frame;

void uart_rx_interrupt(uint8_t byte)
{
    if (hcv_parser_feed(&parser, byte, &frame) && frame.msgid == HCV_MSG_BALL) {
        hcv_ball_t ball;
        if (hcv_decode_ball(&frame, &ball) && (ball.status & 0x01u))
            control_update(ball.pos_filt, ball.vel);
    }
}

void protocol_init(void)
{
    hcv_parser_init(&parser);
}
