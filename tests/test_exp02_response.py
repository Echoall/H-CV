from tools.analysis.exp02_response import summarize


def test_plot_data_requires_a_measurable_light_step():
    lines = ["HCV_LIGHT 0 off 0"]
    lines += [f"HCV_LUMA,0,{time},20" for time in range(0, 3000, 50)]
    lines.append("HCV_LIGHT 1 on 3000")
    lines += [f"HCV_LUMA,1,{time},{20 if time < 3300 else 100}"
              for time in range(3000, 6000, 50)]
    result = list(summarize("\n".join(lines)))
    assert result == [
        (0, "off", 60, None, "no-measurable-step"),
        (1, "on", 60, 300, "ok"),
    ]
