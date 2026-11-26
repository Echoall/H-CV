"""Read API surfaces and the driver's sensor mode selection without starting media."""
import _thread
import os
from media.media import ALIGN_UP, MediaManager
from media.sensor import *
from media.vencoder import Encoder, StreamData
from mpp import k_vicap_probe_config, k_vicap_sensor_info
from mpp.vicap import kd_mpi_sensor_adapt_get
from multimedia import rtsp_server

cfg = k_vicap_probe_config()
cfg.csi = get_default_sensor()
cfg.width, cfg.height, cfg.fps = 1920, 1080, 60
info = k_vicap_sensor_info()
rc = kd_mpi_sensor_adapt_get(cfg, info)
print("HCV_HUBEI_MODE,%d,%d,%d,%d,%d" % (rc, info.width, info.height, info.fps, info.type))
ready = (rc == 0 and (info.width, info.height, info.fps) == (1920, 1080, 60)
         and ALIGN_UP(640, 16) == 640 and hasattr(_thread, "start_new_thread")
         and hasattr(Sensor, "set_framesize") and hasattr(MediaManager, "link")
         and hasattr(StreamData(), "phy_addr") and hasattr(Encoder, "GetStream")
         and hasattr(rtsp_server, "rtspserver_sendvideodata_byphyaddr"))
print("HCV_HUBEI_API", os.uname().release, int(ready))
