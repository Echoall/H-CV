"""Record each transmitted BALL packet and its matching camera region."""

import sys

from image import ALLOC_REF, AREA

sys.path.insert(0, "/sdcard/hcv")
for name in list(sys.modules):
    if name == "main_hcv" or name == "hcv" or name.startswith("hcv."):
        del sys.modules[name]

from hcv.config import load_config
from hcv.guard import start, tick
from main_hcv import run


PACKET_SIZE = 19
SECONDS = globals().get("CAPTURE_SECONDS", 8)
SIGNAL_FRAME = globals().get("SIGNAL_FRAME", 0)
FRAMES_PER_BLOCK = 64
EVIDENCE = "/sdcard/hcv/m1-ball-insertion.bin"


def record():
    start(5)
    config = load_config("/sdcard/hcv")
    roi = config["roi"]
    bounds = (roi["x"], roi["y"], roi["w"], roi["h"])
    # A quarter-size preview keeps eight seconds of frames within the board heap.
    width, height = bounds[2] // 4, bounds[3] // 4
    if bounds[2] % 4 or bounds[3] % 4 or min(width, height) == 0:
        raise ValueError("recording requires ROI dimensions divisible by four")
    image_bytes = width * height
    record_bytes = PACKET_SIZE + image_bytes
    limit = SECONDS * config["sensor"]["fps"]
    blocks = []
    for _ in range((limit + FRAMES_PER_BLOCK - 1) // FRAMES_PER_BLOCK):
        tick()
        blocks.append(memoryview(bytearray(FRAMES_PER_BLOCK * record_bytes)))
    count = 0

    def observe(frame, packet):
        nonlocal count
        block, slot = divmod(count, FRAMES_PER_BLOCK)
        offset = slot * record_bytes
        buffer = blocks[block]
        buffer[offset:offset + PACKET_SIZE] = packet
        frame.crop(
            0.25, 0.25, roi=bounds, hint=AREA, alloc=ALLOC_REF,
            data=buffer[offset + PACKET_SIZE:offset + record_bytes],
        )
        count += 1
        if count == SIGNAL_FRAME:
            print("HCV_FIELD_CUE,frame=%d" % (count - 1))

    run(max_runtime_s=SECONDS, max_frames=limit, observe_ball=observe)
    start(12)
    with open(EVIDENCE, "wb") as output:
        output.write(("HCV1 %d %d %d\n" % (width, height, count)).encode())
        remaining = count
        for block in blocks:
            tick()
            frames = min(remaining, FRAMES_PER_BLOCK)
            if frames == 0:
                break
            output.write(block[:frames * record_bytes])
            remaining -= frames
    print("HCV_M1_BALL_EVIDENCE,frames=%d,width=%d,height=%d" % (count, width, height))


record()
