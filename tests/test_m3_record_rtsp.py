import pytest

from station.record_rtsp import vlc_command


def test_vlc_displays_and_records_same_rtsp_source(tmp_path):
    output = tmp_path / "test folder" / "test.mp4"
    command = vlc_command("vlc", "rtsp://192.168.0.1:8554/hcv", output)
    assert command[-1] == "rtsp://192.168.0.1:8554/hcv"
    assert "--network-caching=200" in command
    assert "--no-one-instance" in command
    assert command[command.index("--sout") + 1] == (
        '#duplicate{dst=display,dst=std{access=file,mux=mp4,dst="'
        + output.resolve().as_posix() + '"}}'
    )
    with pytest.raises(ValueError, match="RTSP"):
        vlc_command("vlc", "http://192.168.0.1/hcv", output)
