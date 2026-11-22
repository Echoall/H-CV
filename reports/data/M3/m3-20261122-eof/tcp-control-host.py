import argparse
from contextlib import ExitStack
from ipaddress import IPv4Address
from pathlib import Path
import re
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time


def receive(address, port, expected):
    count = wrong = 0
    began = time.monotonic()
    try:
        with socket.create_connection((address, port), timeout=5) as conn:
            while (data := conn.recv(65536)):
                count += len(data)
                wrong += len(data) - data.count(b'K')
                if time.monotonic() - began > 37:
                    raise TimeoutError('control stream exceeded deadline')
    except OSError as error:
        print('HCV_TCP_CONTROL_CLIENT_ERROR,%s,errno=%s' % (
              type(error).__name__, error.errno), flush=True)
    print('HCV_TCP_CONTROL_RECEIVED,bytes=%d,other=%d,elapsed_s=%.3f' % (
          count, wrong, time.monotonic() - began), flush=True)
    return count == expected and wrong == 0


def send(listener, block_size):
    stage = 'accept'
    try:
        conn, _ = listener.accept()
        print('HCV_TCP_CONTROL_CONNECTED', flush=True)
        with conn:
            stage = 'send'
            conn.settimeout(3)
            block = b'K' * block_size
            began = time.monotonic()
            for index in range(320):
                if time.monotonic() - began > 37:
                    raise TimeoutError('control stream exceeded deadline')
                conn.sendall(block)
                time.sleep(max(0, began + (index + 1) * 0.1 - time.monotonic()))
        print('HCV_TCP_CONTROL_SENT,bytes=%d,elapsed_s=%.3f' % (
              320 * len(block), time.monotonic() - began), flush=True)
    except OSError as error:
        print('HCV_TCP_CONTROL_HOST_ERROR,%s,%s,winerror=%s,errno=%s' % (
              stage, type(error).__name__, getattr(error, 'winerror', None), error.errno), flush=True)
        return False
    return True


def run(reverse, block_size):
    entry = Path(__file__).with_name('tcp-control-board.py')
    expected = 320 * block_size
    with ExitStack() as resources:
        listener = None
        options = f'HCV_CONTROL_BLOCK_SIZE = {block_size}\n'
        if reverse:
            listener = resources.enter_context(socket.socket())
            listener.bind((str(reverse), 8554))
            listener.listen(1)
            listener.settimeout(10)
            options += f'HCV_CONTROL_HOST = {str(reverse)!r}\n'
        temp = Path(resources.enter_context(TemporaryDirectory())) / entry.name
        temp.write_bytes(options.encode() + entry.read_bytes())
        entry = temp
        cmd = [sys.executable, 'tools/board/board.py', '--port', 'auto', 'run',
               str(entry), '--stream', '--timeout', '47']
        board = resources.enter_context(subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        ))
        sent = send(listener, block_size) if listener else True
        received = False
        for line in board.stdout:
            if line.startswith('HCV_CONTROL_PRIVATE,'):
                _, address, port = line.strip().split(',')
                received = receive(str(IPv4Address(address)), int(port), expected)
            elif line.startswith('HCV_TCP_CONTROL_'):
                print(line.strip(), flush=True)
                received |= line.startswith(f'HCV_TCP_CONTROL_RECEIVED,bytes={expected},other=0,')
            elif error := re.match(r'\s*(\w*Error):\s*(?:\[Errno (\d+)\])?', line):
                print('HCV_TCP_CONTROL_BOARD_ERROR,%s,errno=%s' % (
                      error[1], error[2]), flush=True)
        status = board.wait()
        print('HCV_TCP_CONTROL_BOARD_EXIT,%d' % status, flush=True)
    return status or int(not (sent and received))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--reverse', type=IPv4Address, metavar='HOST_IP',
                        help='make the computer the server and sender on TCP 8554')
    parser.add_argument('--block-size', type=int, choices=(10000, 25000), default=25000,
                        help='TCP payload bytes per 100 ms')
    args = parser.parse_args()
    raise SystemExit(run(args.reverse, args.block_size))
