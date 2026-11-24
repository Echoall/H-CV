"""Read frame-rate API fields without opening the camera or encoder."""

import os

from media.sensor import Sensor
from media.vencoder import ChnAttrStr, Encoder
from mpp import venc, venc_struct


print("HCV_FIRMWARE_RELEASE", os.uname().release)
attributes = ChnAttrStr(Encoder.PAYLOAD_TYPE_H264, Encoder.H264_PROFILE_MAIN, 960, 540)
print("HCV_CHNATTR_DEFAULT", attributes.src_frame_rate, attributes.dst_frame_rate,
      attributes.bit_rate, attributes.gop_len)
attributes.src_frame_rate = 90
attributes.dst_frame_rate = 30
print("HCV_CHNATTR_READBACK", attributes.src_frame_rate, attributes.dst_frame_rate)
cbr = venc_struct.k_venc_chn_attr().rc_attr.cbr
cbr.src_frame_rate = 90
cbr.dst_frame_rate = 30
print("HCV_CBR_READBACK", cbr.src_frame_rate, cbr.dst_frame_rate)
print("HCV_VENC_READBACK_API", hasattr(venc, "kd_mpi_venc_get_chn_attr"),
      hasattr(venc, "kd_mpi_venc_set_chn_attr"))
print("HCV_SENSOR_CHN_FPS_API", hasattr(Sensor, "_set_chn_fps"))
