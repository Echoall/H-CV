"""Bind one K230 camera channel to hardware H.264 and publish RTSP."""

import uctypes

from media.media import MediaManager, VENC_DEV_ID, VIDEO_ENCODE_MOD_ID
from media.sensor import Sensor
from media.vencoder import Encoder
from mpp import venc, venc_def, venc_struct
from multimedia import multi_media_type, rtsp_server


CHANNEL = 0
STREAM_BUFFERS = 8


def _checked(result, action):
    if result not in (None, 0):
        raise OSError("%s returned %s" % (action, result))


def _release_link(link):
    if link is None:
        return
    del link


class H264Stream:
    def __init__(self, config, sensor_channel=1, copy_packets=False):
        self.settings = config["stream"]
        self.camera = config["chn_stream"]
        self.source_fps = config["sensor"]["fps"]
        self.sensor_channel = sensor_channel
        self.packet_mapping = None
        if copy_packets:
            from mpp import sys as packet_mapping
            self.packet_mapping = packet_mapping
        self.encoder = None
        self.server = None
        self.link = None
        self.created = False
        self.encoding = False
        self.serving = False
        self.packets = 0
        self.bytes = 0
        self.last_read_error = 0
        self.last_send_result = None
        self.pack_storage = bytearray(uctypes.sizeof(venc_def.k_venc_pack_desc, uctypes.NATIVE))
        self.stream_storage = bytearray(uctypes.sizeof(venc_def.k_venc_stream_desc, uctypes.NATIVE))
        self.output = uctypes.struct(
            uctypes.addressof(self.stream_storage), venc_def.k_venc_stream_desc, uctypes.NATIVE
        )
        self.output.pack = uctypes.addressof(self.pack_storage)
        self.pack = uctypes.struct(
            uctypes.addressof(self.pack_storage), venc_def.k_venc_pack_desc, uctypes.NATIVE
        )

    def bind(self, sensor):
        width, height = self.camera["width"], self.camera["height"]
        if self.sensor_channel == 0:
            sensor.set_framesize(width=width, height=height, alignment=12)
            sensor.set_pixformat(Sensor.YUV420SP)
        else:
            sensor.set_framesize(width=width, height=height, chn=self.sensor_channel)
            sensor.set_pixformat(Sensor.YUV420SP, chn=self.sensor_channel)
        self.encoder = Encoder()
        self.encoder.SetOutBufs(CHANNEL, STREAM_BUFFERS, width, height)
        if self.sensor_channel == 0:
            source = sensor.bind_info()["src"]
        else:
            source = sensor.bind_info(chn=self.sensor_channel)["src"]
        self.link = MediaManager.link(
            source, (VIDEO_ENCODE_MOD_ID, VENC_DEV_ID, CHANNEL)
        )

    def start_encoder(self):
        width, height = self.camera["width"], self.camera["height"]
        attrs = venc_struct.k_venc_chn_attr()
        attrs.venc_attr.type = Encoder.PAYLOAD_TYPE_H264
        attrs.venc_attr.stream_buf_size = (width * height * 3 // 4 + 4095) & ~4095
        attrs.venc_attr.stream_buf_cnt = STREAM_BUFFERS
        attrs.venc_attr.pic_width = width
        attrs.venc_attr.pic_height = height
        attrs.venc_attr.profile = Encoder.H264_PROFILE_MAIN
        attrs.rc_attr.rc_mode = venc_struct.K_VENC_RC_MODE_CBR
        attrs.rc_attr.cbr.gop = self.settings["gop"]
        attrs.rc_attr.cbr.src_frame_rate = self.source_fps
        attrs.rc_attr.cbr.dst_frame_rate = self.settings["fps"]
        attrs.rc_attr.cbr.bit_rate = self.settings["bitrate_kbps"]
        _checked(venc.kd_mpi_venc_create_chn(CHANNEL, attrs), "create H.264 encoder")
        self.created = True
        self.encoder.Start(CHANNEL)
        self.encoding = True

    def start_session(self):
        self.server = rtsp_server()
        _checked(self.server.rtspserver_init(self.settings["port"]), "start RTSP listener")
        path = self.settings["path"]
        _checked(self.server.rtspserver_createsession(
            path, multi_media_type.media_h264, False
        ), "create RTSP session")
        self.server.rtspserver_start()
        self.serving = True
        return self.server.rtspserver_getrtspurl(path)

    def send_one(self, timeout_ms):
        self.output.pack_cnt = 1
        result = venc.kd_mpi_venc_get_stream(CHANNEL, self.output, timeout_ms)
        if result:
            self.last_read_error = result
            return False
        try:
            size = self.pack.len
            if self.serving:
                path = self.settings["path"]
                timestamp = self.pack.pts // 1000
                if self.packet_mapping is None:
                    self.last_send_result = self.server.rtspserver_sendvideodata_byphyaddr(
                        path, self.pack.phys_addr, size, timestamp
                    )
                else:
                    address = self.packet_mapping.kd_mpi_sys_mmap(self.pack.phys_addr, size)
                    if not address:
                        raise OSError("could not map H.264 packet")
                    try:
                        payload = bytes(uctypes.bytearray_at(address, size))
                    finally:
                        _checked(self.packet_mapping.kd_mpi_sys_munmap(address, size),
                                 "unmap H.264 packet")
                    self.last_send_result = self.server.rtspserver_sendvideodata(
                        path, payload, size, timestamp
                    )
                _checked(self.last_send_result, "send H.264 packet")
            self.packets += 1
            self.bytes += size
            return True
        finally:
            _checked(venc.kd_mpi_venc_release_stream(CHANNEL, self.output), "release H.264 packet")

    def close(self):
        try:
            if self.serving:
                self.server.rtspserver_stop()
        finally:
            try:
                if self.server is not None:
                    self.server.rtspserver_deinit()
            finally:
                try:
                    if self.link is not None:
                        _release_link(self.link)
                        self.link = None
                finally:
                    try:
                        if self.encoding:
                            self.encoder.Stop(CHANNEL)
                    finally:
                        if self.created:
                            self.encoder.Destroy(CHANNEL)
