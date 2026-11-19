from tools.analysis.p0_t03_results import summarize


def test_uart_acceptance_requires_10000_matching_frames_at_every_rate():
    result = summarize(
        "HCV_UART,115200,100,1020,1020,0,0,0\n"
        "HCV_UART,115200,10000,102000,102000,0,0,0\n"
        "HCV_UART,460800,10000,102000,102000,1,0,0\n"
    )
    assert result[115200]["passed"]
    assert not result[460800]["passed"]
    assert result[921600]["reason"] == "no data"
