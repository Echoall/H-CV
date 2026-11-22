"""Print board API names without constructing media objects."""

from media.media import MediaManager
from media.vencoder import Encoder
from multimedia import rtsp_server


def emit(name, value):
    print("HCV_API,%s,%s" % (name, ",".join(sorted(dir(value)))))


emit("MediaManager", MediaManager)
emit("Encoder", Encoder)
emit("rtsp_server", rtsp_server)
