"""Inspect Python API names before the board's sole media session."""
import _thread
import os
from media.media import ALIGN_UP, MediaManager
from media.sensor import Sensor
from media.vencoder import Encoder
from multimedia import rtsp_server

ready = (ALIGN_UP(640, 16) == 640 and hasattr(_thread, "start_new_thread")
         and hasattr(Sensor, "set_framesize") and hasattr(MediaManager, "link")
         and hasattr(Encoder, "GetStream") and hasattr(Encoder, "ReleaseStream")
         and hasattr(rtsp_server, "rtspserver_sendvideodata_byphyaddr"))
print("HCV_HUBEI_API", os.uname().release, int(ready))
