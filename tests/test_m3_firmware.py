"""Exercise the spare-card gates without a serial device or radio."""
from hashlib import sha1
from pathlib import Path

import pytest

from station.bench import bench, firmware, hubei
from tools.board import board


def info(version, machine='k230_canmv_hiwonder'):
    return f"(release='{version}-abcdef0', version='{version}-0-gabcdef0 on 2026-07-03', machine='{machine}')"


@pytest.mark.parametrize('detected,expected,ok', [('v1.8', 'v1.8', True), ('v1.4', 'v1.8', False),
                                                  ('v1.8', 'v1.4', False), ('v1.4', 'v1.4', True),
                                                  ('v1.80', 'v1.8', False)])
def test_identify(detected, expected, ok):
    assert board.image_matches(info(detected), expected) == ok
    assert not board.image_matches(info(detected, 'another_board'), expected)
    assert not board.image_matches(f"machine='k230_canmv_hiwonder'", expected)


def test_upstream_media_file_is_pinned():
    source = (bench.ROOT / 'tools/board/hubei_v18/rtsp_camera_stream.py').read_bytes().replace(b'\r\n', b'\n')
    assert sha1(b'blob ' + str(len(source)).encode() + b'\0' + source).hexdigest() == 'b17ea2a5f20da9f9862ea59d9f0f00b285e0785e'


@pytest.mark.parametrize('detected,machine,code', [('v1.4', 'k230_canmv_hiwonder', 0),
                                                   ('v1.8', 'another_board', 0),
                                                   ('v1.8', 'k230_canmv_hiwonder', None)])
def test_wrong_card_stops_before_upload_or_camera(tmp_path, monkeypatch, detected, machine, code):
    run = bench.Run(tmp_path / 'ap', dict(hubei.plan('ap'), firmware_compare=True), False)
    calls = []
    def command(stage, args, **kwargs):
        calls.append(stage)
        return code, info(detected, machine), ''
    monkeypatch.setattr(run, 'command', command)
    monkeypatch.setattr(hubei.power, 'reconnect', lambda ask: None)
    monkeypatch.setattr(bench, 'config_wifi', lambda: dict(ssid='example', password='dummyvalue'))
    with pytest.raises(RuntimeError):
        hubei.physical(run, 'ap', lambda _: pytest.fail('no capture prompt'), ('wifi','orig','daily'), 'vlc')
    assert calls == ['inventory']


def test_confirm_and_prepare_empty_card(tmp_path, monkeypatch):
    run = bench.Run(tmp_path / 'ap', dict(hubei.plan('ap'), firmware_compare=True), False)
    uploaded = {}
    def command(stage, args, **kwargs):
        if stage == 'inventory':
            return 0, info('v1.8'), ''
        if stage == 'hubei-api':
            return 0, 'HCV_HUBEI_API v1.8-abcdef0 1', ''
        assert args[-1] == '--hiwonder-v18'
        uploaded[args[-2]] = Path(args[-3]).read_bytes()
        return 0, '', ''
    monkeypatch.setattr(run, 'command', command)
    monkeypatch.setattr(hubei.power, 'reconnect', lambda ask: None)
    monkeypatch.setattr(bench, 'config_wifi', lambda: dict(ssid='example', password='dummyvalue'))
    monkeypatch.setattr(firmware.power, 'boards', lambda: ['device'])
    responses = iter(['1', '左侧', '约 50 cm', '取消'])
    with pytest.raises(RuntimeError, match='取消'):
        hubei.physical(run, 'ap', lambda _: next(responses), ('wifi','orig','daily'), 'vlc')
    assert sorted(uploaded) == ['/sdcard/hcv/fw18/guard.py', '/sdcard/hcv/fw18/media_session_guard.py',
                                '/sdcard/hcv/fw18/rtsp_camera_stream.py']
    assert run.summary['verified_image'] == 'v1.8'
    assert run.summary['power_connection'] == {'port': '电脑 USB-A', 'location': '左侧',
                                               'cable_length': '约 50 cm', 'observed_5v_volts': None}


def test_new_card_refused_by_original_check(tmp_path, monkeypatch):
    calls = []
    def command(self, stage, args, **kwargs):
        calls.append(args[-1])
        return 0, info('v1.8'), ''
    monkeypatch.setattr(bench.Run, 'command', command)
    assert not firmware.original_card(tmp_path)
    assert calls == ['info']


def test_cancelled_capture_is_inconclusive(tmp_path):
    run = bench.Run(tmp_path / 'run', hubei.plan('ap'), False)
    run.summary.update(rtsp_ready_at='ready', capture=dict(client_playing_at='playing', frames=20,
                       client_end_reason='eof', transport_observed=['tcp'], operator_cancelled=True))
    assert not firmware.valid_failure(run)


def test_presence_poll_retains_first_loss_and_recovers(tmp_path, monkeypatch):
    import json
    from threading import Event
    from station.bench import usb_session
    done = Event()
    observations = iter([['board'], OSError('unreadable'), [], ['board']])
    def devices():
        value = next(observations, ['board'])
        if isinstance(value, OSError):
            raise value
        if len(calls) == 4:
            done.set()
        return value
    calls = []
    def sample():
        calls.append(True)
        return devices()
    monkeypatch.setattr(usb_session.power, 'boards', sample)
    monkeypatch.setattr(usb_session, 'removals', lambda *_: ['host-time'])
    run = bench.Run(tmp_path / 'ap', hubei.plan('ap'), False)
    with usb_session.monitor(run, 'board'):
        assert done.wait(3)
    rows = [json.loads(line) for line in (run.directory / 'usb-presence.jsonl').read_text().splitlines()]
    assert [r['present'] for r in rows[:4]] == [True, None, False, True]
    status = run.summary['usb_presence']
    assert status['first_absent_at'] == rows[2]['at']
    assert status['unknown'] == 1 and status['pnp_1010_at'] == ['host-time']
    assert status['poller_stopped'] and status['sample_period_s'] == 0.5


def test_empty_card_creates_project_and_nested_directory(tmp_path):
    from ast import literal_eval
    from hashlib import sha256
    from types import SimpleNamespace
    directories = ['/sdcard']
    files = {}
    def mkdir(path):
        assert path.rsplit('/', 1)[0] in directories
        directories.append(path)
    def write(path, data, chunk_size):
        assert path.rsplit('/', 1)[0] in directories
        files[path] = data
    def execute(source):
        old, new = literal_eval(source.split('os.rename', 1)[-1])
        files[new] = files.pop(old)
    transport = SimpleNamespace(fs_isdir=directories.__contains__, fs_mkdir=mkdir, fs_exists=files.__contains__,
                                fs_writefile=write, fs_hashfile=lambda path, _: sha256(files[path]).digest(), exec=execute)
    source = tmp_path / 'guard.py'
    source.write_bytes(b'example')
    board.upload(transport, source, '/sdcard/hcv/fw18/guard.py')
    assert directories == ['/sdcard', '/sdcard/hcv', '/sdcard/hcv/fw18']
    assert files == {'/sdcard/hcv/fw18/guard.py': b'example'}


def test_ambiguous_cdc_reports_only_usb_ids(monkeypatch):
    from types import SimpleNamespace
    from station.bench import power
    port = SimpleNamespace(vid=0x1209, pid=0xABD1, serial_number='private-serial', location='private-port')
    monkeypatch.setattr(power.list_ports, 'comports', lambda: [port, port])
    with pytest.raises(RuntimeError) as error:
        power.reconnect(lambda _: pytest.fail('ambiguous devices must stop before user prompts'))
    assert '1209:ABD1, 1209:ABD1' in str(error.value)
    assert 'private' not in str(error.value)
    monkeypatch.setattr(power.list_ports, 'comports', lambda: [])
    assert '无 USB 串口' in power.usb_listing()
