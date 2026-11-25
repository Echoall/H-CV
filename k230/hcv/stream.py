"""Bind one K230 camera channel to hardware H.264 and publish RTSP."""

import time
import uctypes

from media.media import MediaManager, VENC_DEV_ID, VENC_PACK_CNT_MAX, VIDEO_ENCODE_MOD_ID
from media.sensor import Sensor
from media.vencoder import ChnAttrStr, Encoder
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


def _contains_idr(payload):
    start = payload.find(b"\x00\x00\x01")
    while start >= 0 and start + 3 < len(payload):
        if payload[start + 3] & 31 == 5:
            return True
        start = payload.find(b"\x00\x00\x01", start + 3)
    return False


class H264Stream:
    def __init__(self, config, sensor_channel=1, copy_packets=False, trace_frames=False):
        self.settings = config["stream"]
        self.camera = config["chn_stream"]
        self.source_fps = config["sensor"]["fps"]
        self.sensor_channel = sensor_channel
        self.copy_packets = copy_packets
        self.trace_frames = trace_frames
        self.packet_mapping = None
        if copy_packets or trace_frames:
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
        self.last_packet_pts_ms = 0
        self.first_idr_pts_ms = None
        self.last_read_error = 0
        self.last_send_result = None
        pack_size = uctypes.sizeof(venc_def.k_venc_pack_desc, uctypes.NATIVE)
        self.pack_storage = bytearray(pack_size * VENC_PACK_CNT_MAX)
        self.stream_storage = bytearray(uctypes.sizeof(venc_def.k_venc_stream_desc, uctypes.NATIVE))
        self.output = uctypes.struct(
            uctypes.addressof(self.stream_storage), venc_def.k_venc_stream_desc, uctypes.NATIVE
        )
        self.pack_address = uctypes.addressof(self.pack_storage)
        self.output.pack = self.pack_address
        self.pack_descriptors = tuple(
            uctypes.struct(self.pack_address + index * pack_size, venc_def.k_venc_pack_desc, uctypes.NATIVE)
            for index in range(VENC_PACK_CNT_MAX)
        )
        self.status = venc_struct.k_venc_chn_status()

    def bind(self, sensor, source_fps=None):
        width, height = self.camera["width"], self.camera["height"]
        if self.sensor_channel == 0:
            sensor.set_framesize(width=width, height=height, alignment=12)
            sensor.set_pixformat(Sensor.YUV420SP)
        else:
            sensor.set_framesize(width=width, height=height, chn=self.sensor_channel)
            sensor.set_pixformat(Sensor.YUV420SP, chn=self.sensor_channel)
        if source_fps is not None:
            sensor._set_chn_fps(chn=self.sensor_channel, fps=source_fps)
            self.source_fps = source_fps
        self.encoder = Encoder()
        self.encoder.SetOutBufs(CHANNEL, STREAM_BUFFERS, width, height)
        if self.sensor_channel == 0:
            source = sensor.bind_info()["src"]
        else:
            source = sensor.bind_info(chn=self.sensor_channel)["src"]
        self.link = MediaManager.link(
            source, (VIDEO_ENCODE_MOD_ID, VENC_DEV_ID, CHANNEL)
        )

    def start_encoder(self, use_firmware_encoder=False):
        width, height = self.camera["width"], self.camera["height"]
        if use_firmware_encoder:
            attrs = ChnAttrStr(Encoder.PAYLOAD_TYPE_H264, Encoder.H264_PROFILE_MAIN, width, height)
            attrs.src_frame_rate = self.source_fps
            attrs.dst_frame_rate = self.settings["fps"]
            attrs.bit_rate = self.settings["bitrate_kbps"]
            attrs.gop_len = self.settings["gop"]
            try:
                self.encoder.Create(CHANNEL, attrs)
            except OSError as error:
                # This firmware can create the channel, then fail while enabling IDR.
                if str(error) == "mpi venc enable idr failed.":
                    self.created = True
                raise
            self.created = True
            print("HCV_VENC_FIRMWARE_RATES,%d,%d" % (attrs.src_frame_rate, attrs.dst_frame_rate))
        else:
            self._create_encoder(width, height)
        self.encoder.Start(CHANNEL)
        self.encoding = True

    def _create_encoder(self, width, height):
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

    def _available_packs(self, timeout_ms):
        deadline = time.ticks_add(time.ticks_ms(), timeout_ms) if timeout_ms else None
        while True:
            result = venc.kd_mpi_venc_query_status(CHANNEL, self.status)
            _checked(result, "query H.264 stream")
            count = self.status.cur_packs
            if count:
                return count
            if deadline is None or time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                return 0
            time.sleep_ms(1)

    def _send_pack(self, pack, inspect_idr, capture):
        size, address = pack.len, pack.phys_addr
        timestamp = pack.pts // 1000
        idr = False
        path = self.settings["path"]
        if self.copy_packets or inspect_idr or capture is not None:
            mapped = self.packet_mapping.kd_mpi_sys_mmap(address, size)
            if not mapped:
                raise OSError("could not map H.264 packet")
            try:
                data = uctypes.bytearray_at(mapped, size)
                if capture is not None:
                    capture.append(data)
                if self.copy_packets or inspect_idr:
                    payload = bytes(data)
            finally:
                _checked(self.packet_mapping.kd_mpi_sys_munmap(mapped, size), "unmap H.264 packet")
            if inspect_idr:
                idr = _contains_idr(payload)
        if self.copy_packets:
            self.last_send_result = self.server.rtspserver_sendvideodata(path, payload, size, timestamp)
        else:
            self.last_send_result = self.server.rtspserver_sendvideodata_byphyaddr(
                path, address, size, timestamp
            )
        _checked(self.last_send_result, "send H.264 packet")
        self.last_packet_pts_ms = timestamp
        return idr

    def send_one(self, timeout_ms, capture=None):
        """Submit all packets in one encoder output batch."""
        expected = self._available_packs(timeout_ms)
        if not expected:
            return False
        if expected > VENC_PACK_CNT_MAX:
            raise OSError("H.264 stream needs %d pack descriptors" % expected)
        self.output.pack = self.pack_address
        self.output.pack_cnt = expected
        result = venc.kd_mpi_venc_get_stream(CHANNEL, self.output, 0)
        if result:
            self.last_read_error = result
            return False
        self.last_read_error = 0
        i_packs = p_packs = headers = 0
        idr_pts = None
        try:
            count = self.output.pack_cnt
            if not 0 < count <= expected:
                raise OSError("H.264 stream returned %d packs after querying %d" % (count, expected))
            for index in range(count):
                pack = self.pack_descriptors[index]
                inspect_idr = False
                if self.trace_frames:
                    kind = pack.type
                    i_packs += kind == venc_struct.K_VENC_I_FRAME
                    p_packs += kind == venc_struct.K_VENC_P_FRAME
                    headers += kind == venc_struct.K_VENC_HEADER
                    inspect_idr = idr_pts is None and kind != venc_struct.K_VENC_P_FRAME
                if self.serving and self._send_pack(pack, inspect_idr, capture):
                    idr_pts = self.last_packet_pts_ms
                self.packets += 1
                self.bytes += pack.len
        finally:
            _checked(venc.kd_mpi_venc_release_stream(CHANNEL, self.output), "release H.264 stream")
        if self.trace_frames:
            print("HCV_VENC_PACKS,%d,%d,%d,%d,%d,%d,%d" % (
                self.last_packet_pts_ms, expected, count, i_packs, p_packs, headers, idr_pts is not None,
            ))
            if self.first_idr_pts_ms is None and idr_pts is not None:
                self.first_idr_pts_ms = idr_pts
        return True

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
