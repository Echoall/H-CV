"""Consume VLC's headless YUV output during the Windows-only localhost simulation."""

import ctypes
from ctypes import wintypes
from hashlib import sha256
from threading import Event, Thread
import uuid


class RenderedFrames:
    """Read the VLC 3 yuv vout stream, including its global header and FRAME lines."""

    def __init__(self, width, height, on_frame=None):
        self.format = f"I420 W{width} H{height} ".encode()
        self.size = width * height * 3 // 2
        self.buffer = bytearray()
        self.started = False
        self.picture = None
        self.frame_count = 0
        self.on_frame = on_frame

    def feed(self, chunk):
        self.buffer.extend(chunk)
        if not self.started:
            line = self.buffer.find(b"\n")
            if line < 0 and len(self.buffer) < 256:
                return
            if line < 0 or not self.buffer[:line].startswith(self.format):
                raise ValueError("unexpected VLC render format")
            del self.buffer[:line + 1]
            self.started = True
        end = len(b"FRAME\n") + self.size
        while len(self.buffer) >= end:
            if not self.buffer.startswith(b"FRAME\n"):
                raise ValueError("invalid VLC frame boundary")
            self.picture = sha256(memoryview(self.buffer)[6:end]).digest()
            if self.on_frame is not None:
                self.on_frame(memoryview(self.buffer)[6:end])
            self.frame_count += 1
            del self.buffer[:end]


class RenderPipe:
    def __init__(self, width, height, on_frame=None):
        self.name = r"\\.\pipe\hcv-sim-" + uuid.uuid4().hex
        self.frames = RenderedFrames(width, height, on_frame)
        self.error = None
        self.stop = Event()
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateNamedPipeW.restype = wintypes.HANDLE
        handle = self.api.CreateNamedPipeW(self.name, 1, 1 | 8, 1, 0, 1024 * 1024, 0, None)
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        self.handle = wintypes.HANDLE(handle)
        self.thread = Thread(target=self.read, daemon=True)
        self.thread.start()

    def read(self):
        buf = ctypes.create_string_buffer(262144)
        view = memoryview(buf).cast("B")
        count = wintypes.DWORD()
        while not self.stop.is_set():
            if self.api.ConnectNamedPipe(self.handle, None) or ctypes.get_last_error() == 535:
                break
            self.stop.wait(0.005)
        while not self.stop.is_set():
            if self.api.ReadFile(self.handle, buf, len(buf), ctypes.byref(count), None) and count.value:
                if self.error is None:
                    try:
                        self.frames.feed(view[:count.value])
                    except ValueError as error:
                        self.error = error
            else:
                if ctypes.get_last_error() == 109:
                    break
                self.stop.wait(0.002)

    def close(self):
        self.stop.set()
        self.thread.join()
        self.api.CloseHandle(self.handle)
