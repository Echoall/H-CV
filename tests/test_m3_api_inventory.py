from tools.analysis.check_m3_api_inventory import missing_methods, read_inventory


def test_m3_inventory_accepts_required_api(tmp_path):
    log = tmp_path / "api.log"
    lines = []
    for name, methods in (
        ("MediaManager", ("link", "init", "deinit")),
        ("Encoder", ("SetOutBufs", "Create", "Start", "Stop", "Destroy",
                     "GetStream", "ReleaseStream")),
        ("rtsp_server", ("rtspserver_init", "rtspserver_createsession",
                         "rtspserver_start", "rtspserver_stop",
                         "rtspserver_deinit", "rtspserver_getrtspurl",
                         "rtspserver_sendvideodata")),
    ):
        lines.append("HCV_API,%s,%s\n" % (name, ",".join(methods)))
    log.write_text("".join(lines), encoding="utf-8")

    assert missing_methods(read_inventory(log)) == {}


def test_m3_inventory_reports_missing_method(tmp_path):
    log = tmp_path / "api.log"
    log.write_text("HCV_API,MediaManager,link,init\n", encoding="utf-8")

    missing = missing_methods(read_inventory(log))

    assert missing["MediaManager"] == ["deinit"]
    assert "Encoder" in missing
    assert "rtsp_server" in missing
