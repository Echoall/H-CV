"""Observe completed operations on the normal RTSP worker without opening another connection."""
import time


class EncoderCounter:
    def __init__(self, encoder):
        self.encoder = encoder
        self.acquired = 0
        self.released = 0
        self.sent = 0
        self.last = time.ticks_ms()

    def __getattr__(self, name):
        return getattr(self.encoder, name)

    def GetStream(self, channel, data):
        result = self.encoder.GetStream(channel, data)
        if result == 0:
            self.acquired += 1
        return result

    def ReleaseStream(self, channel, data):
        result = self.encoder.ReleaseStream(channel, data)
        self.released += 1
        now = time.ticks_ms()
        if time.ticks_diff(now, self.last) >= 1000:
            self.last = now
            print("HCV_VIDEO_ENCODER,%d,%d,%d" % (self.acquired, self.sent, self.released))
        return result


class SenderCounter:
    def __init__(self, server, counters):
        self.server = server
        self.counters = counters

    def __getattr__(self, name):
        return getattr(self.server, name)

    def rtspserver_sendvideodata_byphyaddr(self, name, address, size, timeout):
        result = self.server.rtspserver_sendvideodata_byphyaddr(name, address, size, timeout)
        self.counters.sent += 1
        return result


def observed_server(base):
    class ObservedServer(base):
        def _stream_loop(self):
            self.encoder = EncoderCounter(self.encoder)
            self.server = SenderCounter(self.server, self.encoder)
            super()._stream_loop()

    return ObservedServer()


class MainHeartbeat:
    def __init__(self):
        self.last = time.ticks_ms()
        self.count = 0

    def tick(self):
        now = time.ticks_ms()
        self.count += 1
        if time.ticks_diff(now, self.last) >= 1000:
            self.last = now
            print("HCV_VIDEO_MAIN,%d" % self.count)
