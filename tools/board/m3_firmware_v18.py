"""Bound the upstream v1.8 Hubei camera session and announce its endpoint."""
import os
import sys
import time
import network


def run(wifi, supervised_stop=False):
    identity = os.uname()
    if (identity.machine != 'k230_canmv_hiwonder' or identity.release.split('-')[0] != 'v1.8'
            or identity.version.split(' on ')[0].split('-')[0] != 'v1.8'):
        raise RuntimeError('Wrong comparison firmware')
    sys.path.insert(0, '/sdcard/hcv/fw18')
    from guard import start, tick
    from media_session_guard import claim
    from rtsp_camera_stream import CameraRtspServer, RTSP_PORT, RTSP_SESSION
    start(170)
    try:
        os.stat('/sdcard/hcv/run')
    except OSError:
        os.mkdir('/sdcard/hcv/run')
    claim()
    ap = wifi['mode'] == 'ap'
    nic = network.WLAN(network.AP_IF if ap else network.STA_IF)
    if ap:
        try:
            nic.config(ssid=wifi['ssid'], key=wifi['password'], channel=wifi['channel'])
        except TypeError:
            nic.config(ssid=wifi['ssid'], key=wifi['password'])
    else:
        nic.connect(wifi['ssid'], wifi['password'])
        began = time.ticks_ms()
        while not nic.isconnected() and time.ticks_diff(time.ticks_ms(), began) < 30000:
            tick()
            time.sleep_ms(250)
        if not nic.isconnected():
            raise RuntimeError('STA association failed')
    address = nic.ifconfig()[0]
    if address == '0.0.0.0':
        raise RuntimeError('No network address')
    print('HCV_RTSP_PRIVATE,%s,%d,%s' % (address, RTSP_PORT, RTSP_SESSION))
    if ap:
        print('HCV_AP_READY')
    server = CameraRtspServer()
    try:
        server.start()
        print('HCV_CH0_CONFIG_READY')
        began = last = time.ticks_ms()
        duration_ms = 48000 if supervised_stop else 125000
        interval_ms = 1000 if supervised_stop else 5000
        while time.ticks_diff(time.ticks_ms(), began) < duration_ms:
            tick()
            time.sleep_ms(250)
            if server.thread_over:
                raise RuntimeError('encoder worker stopped')
            now = time.ticks_ms()
            if time.ticks_diff(now, last) >= interval_ms:
                elapsed = max(1, time.ticks_diff(now, server.started_ms))
                print('HCV_HUBEI_STATUS,elapsed_ms=%d,frames=%d,bytes=%d,fps=%.1f' % (
                    elapsed, server.frame_count, server.byte_count, server.frame_count * 1000 / elapsed))
                last = now
    finally:
        if supervised_stop:
            print('HCV_SERVICE_STOP_BEGIN')
        server.stop()
    if not server.thread_over:
        raise RuntimeError('worker stop not confirmed')
    print('HCV_CH0_CONFIG_CLEANUP')
