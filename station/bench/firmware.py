"""Operator-controlled comparison using the spare Hiwonder v1.8 card."""
import argparse
from pathlib import Path
import subprocess

from station.bench import bench, hubei, power
from tools.board.board import image_matches


def prepare(run, ask):
    ports = {'1': '电脑 USB-A', '2': '电脑 USB-C', '3': '带供电的 hub', '4': '不带供电的 hub', '5': '未知'}
    kind = ask('用哪个口供电？1 电脑 USB-A / 2 电脑 USB-C / 3 带供电的 hub / 4 不带供电的 hub / 5 未知 > ').strip()
    location = ask('接口的位置（例如电脑左侧靠屏幕） > ').strip()
    cable = ask('数据线多长？估计值请带单位，不清楚填「未知」 > ').strip()
    if kind not in ports or not location or not cable:
        raise RuntimeError('供电情况未填写，本轮未开始。')
    run.summary['power_connection'] = dict(port=ports[kind], location=location, cable_length=cable,
                                           observed_5v_volts=None)
    devices = power.boards()
    if len(devices) != 1:
        raise RuntimeError('准备阶段没有唯一的板子，无法跟踪本轮 USB。')
    run.usb_board = devices[0]
    for relative in ('tools/board/hubei_v18/rtsp_camera_stream.py', 'k230/hcv/guard.py',
                     'k230/hcv/media_session_guard.py'):
        source = bench.ROOT / relative
        code, _, _ = run.command('prepare-' + source.stem, bench.BOARD + [
            'put', str(source), '/sdcard/hcv/fw18/' + source.name, '--hiwonder-v18'], timeout=45)
        if code != 0:
            raise RuntimeError('备用卡文件未准备好。停止并保留本轮日志。')


def valid_failure(run):
    capture = run.summary['capture']
    if capture.get('operator_cancelled') or not capture.get('client_playing_at'):
        return False
    if not run.summary.get('rtsp_ready_at') or capture.get('transport_observed') != ['tcp']:
        return False
    if capture.get('frames', 0) <= 0:
        return False
    if capture.get('client_end_reason') == 'eof':
        return True
    return (capture.get('frame_numbers', {}).get('gap_pct') is not None
            and capture.get('video_track_seconds') is not None
            and capture.get('continuous_display_s') is not None
            and (run.summary['simulation'] or any(run.directory.glob('latency-*.png'))))


def interpretation(results):
    if results and results[-1] == '通过':
        return 'v1.8 在本轮通过。结果支持固件因素；两张卡和镜像同时变化。另开任务评估迁移或双卡。'
    if results == ['失败', '失败']:
        return 'v1.8 两种网络均失败。下一步做受控供电对照，再评估板卡。'
    return '对照未完成，请保留记录。'


def original_card(directory):
    run = bench.Run(directory / (bench.datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '-original-info'),
                    {'id': 'preflight', 'title': '原卡只读检查'}, False)
    code, output, _ = run.command('original-info', bench.BOARD + ['info'], timeout=18)
    ok = code == 0 and image_matches(output, 'v1.4')
    run.summary.update(preflight_passed=ok, original_card_verified=ok)
    run.finish()
    print('已确认 Hiwonder v1.4 原卡。' if ok else '未确认 Hiwonder v1.4 原卡。', flush=True)
    return ok


def main(args, ask=input):
    if args.simulate:
        return hubei.wizard(args, ask)
    choice = '2' if args.check_original else ask('输入 1 测 v1.8 备用卡，2 只读检查换回的 v1.4 原卡，回车退出 > ').strip()
    if choice == '2':
        return int(not original_card(args.runs))
    if choice != '1':
        return 1
    code = hubei.wizard(args, ask)
    if ask('测试已结束。完全断电再换回原卡，插好上电后输入「原卡」做只读检查；回车可稍后检查 > ').strip() == '原卡':
        ok = original_card(args.runs)
        return code if ok else 1
    print('原卡恢复尚未验证。下次打开本向导选择 2。')
    return 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--simulate', type=Path, nargs=2, metavar=('FFMPEG', 'MEDIAMTX'))
    parser.add_argument('--simulate-cut', choices=('ap', 'both'))
    parser.add_argument('--runs', type=Path, default=bench.HERE / 'runs')
    parser.add_argument('--check-original', action='store_true')
    args = parser.parse_args()
    if (args.simulate_cut and not args.simulate) or (args.simulate and args.check_original):
        parser.error('incompatible firmware comparison options')
    args.firmware_compare = True
    try:
        with bench.only_instance(args.runs):
            raise SystemExit(main(args))
    except (OSError, RuntimeError, ValueError, EOFError, KeyboardInterrupt, subprocess.SubprocessError) as error:
        print('向导结束。', str(error) if isinstance(error, RuntimeError) else type(error).__name__)
        raise SystemExit(1)
