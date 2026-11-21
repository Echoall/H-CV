#include "hcv_proto.h"

#define BALL_VALID (1u << 0)
#define BALL_PREDICTED (1u << 1)
#define BALL_CALIB_OK (1u << 2)
#define BALL_FAULT (1u << 7)

/* Implement this in the control task. Inputs are 0.1 mm, 0.1 mm/s and ms. */
extern void control_accept_ball(int16_t position, int16_t velocity, uint16_t t_cap_ms);

static hcv_parser_t parser;
static hcv_frame_t frame;

void protocol_init(void)
{
    hcv_parser_init(&parser);
}

/* Call for each received byte. In an ISR the callback must publish and return. */
void uart_rx_interrupt(uint8_t byte)
{
    hcv_ball_t ball;
    const unsigned required = BALL_VALID | BALL_CALIB_OK;
    const unsigned checked = required | BALL_PREDICTED | BALL_FAULT;

    if (!hcv_parser_feed(&parser, byte, &frame) || !hcv_decode_ball(&frame, &ball))
        return;
    if ((ball.status & checked) != required || ball.pos_raw == HCV_INV ||
        ball.pos_filt == HCV_INV || ball.vel == HCV_INV)
        return;

    control_accept_ball(ball.pos_filt, ball.vel, ball.t_cap_ms);
}
