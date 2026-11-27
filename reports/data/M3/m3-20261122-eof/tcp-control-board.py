import network
import socket
import sys
import time
sys.path.insert(0, '/sdcard/hcv')
from hcv.config import load_config
from hcv.guard import start, tick

start(43)
config = load_config('/sdcard/hcv')
sta = network.WLAN(network.STA_IF)
if not sta.isconnected():
    raise RuntimeError('HCV_TCP_CONTROL_STA_DISCONNECTED')
address = sta.ifconfig()[0]
port = config['stream']['port']
block_size = globals().get('HCV_CONTROL_BLOCK_SIZE', 25000)

if 'HCV_CONTROL_HOST' in globals():
    client = socket.socket()
    try:
        client.settimeout(5)
        client.connect((HCV_CONTROL_HOST, port))
        begin = time.ticks_ms()
        count = wrong = 0
        while True:
            tick()
            data = client.recv(8192)
            if not data:
                break
            count += len(data)
            wrong += len(data) - data.count(b'K')
        print('HCV_TCP_CONTROL_RECEIVED,bytes=%d,other=%d,elapsed_ms=%d' % (
            count, wrong, time.ticks_diff(time.ticks_ms(), begin)))
    finally:
        client.close()
else:
    listener = socket.socket()
    try:
        listener.settimeout(6)
        print('HCV_TCP_CONTROL_STAGE,bind')
        listener.bind((address, port))
        print('HCV_TCP_CONTROL_STAGE,listen')
        listener.listen(1)
        print('HCV_CONTROL_PRIVATE,%s,%d' % (address, port))
        client, _ = listener.accept()
        try:
            print('HCV_TCP_CONTROL_CONNECTED')
            client.settimeout(3)
            block = b'K' * block_size
            begin = time.ticks_ms()
            for index in range(320):
                tick()
                client.sendall(block)
                due = time.ticks_add(begin, (index + 1) * 100)
                delay = time.ticks_diff(due, time.ticks_ms())
                if delay > 0:
                    time.sleep_ms(delay)
            print('HCV_TCP_CONTROL_SENT,bytes=%d,elapsed_ms=%d' % (
                320 * len(block), time.ticks_diff(time.ticks_ms(), begin)))
        finally:
            client.close()
    finally:
        listener.close()
