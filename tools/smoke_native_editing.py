"""Exercise the real Slint callbacks/process with isolated drafts and export files.

Opt-in QA replaces only file-picker input; it is not physical-click/screenshot QA.
Python/Pillow/tifffile are test dependencies, never native runtime dependencies.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

import numpy as np
from PIL import Image
import tifffile
from smoke_native import digest, memory, wait_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--raw', type=Path)
    parser.add_argument('--dll', type=Path)
    args = parser.parse_args()
    if args.out.exists() or args.out.suffix != '.json':
        parser.error('Choose a new JSON report path')
    if bool(args.raw) != bool(args.dll):
        parser.error('--raw and --dll must be used together')
    workspace = Path(__file__).resolve().parents[1] / '.development/native-editing'
    workspace.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='UI 编辑 ', dir=workspace)).resolve()
    first = root / '源图 一.png'
    second = root / '源图 二.png'
    # The larger first image keeps a cancellation test in an actual render stage.
    y, x = np.mgrid[:1600, :2400]
    pixels = np.stack((30+x%150, 20+y%120, 25+(x+y)%140), axis=-1).astype(np.uint8)
    Image.fromarray(pixels).save(first)
    Image.new('RGB', (512, 320), (70, 100, 140)).save(second)
    del x, y, pixels
    photos = [first, second]
    if args.raw:
        photos.append(args.raw.resolve())
    originals = {str(p): digest(p) for p in photos}
    env = os.environ | {'Z30_NATIVE_DATA_DIR': str(root/'data'),
        'PATH': str(Path(os.environ['SystemRoot'])/'System32')+';'+os.environ['SystemRoot']}
    if args.dll:
        env['Z30_LIBRAW_DLL'] = str(args.dll.resolve())
    else:
        env.pop('Z30_LIBRAW_DLL', None)
    result = dict(stage='native-editing-callback-process', exe_sha256=digest(args.exe),
        qa_directory=str(root), checks=[], timings={}, physical_clicks_verified=False,
        screenshot_visual_acceptance=False, real_cloud_inference_requests=0)
    active = []
    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        result['checks'].append(name)
    try:
        for cycle in (1, 2):
            control = root/f'commands{cycle}'; control.mkdir()
            ready = root/f'ready{cycle}.json'; stop = root/f'stop{cycle}.signal'
            startup = subprocess.STARTUPINFO(); startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 4
            with (root/f'process{cycle}.log').open('w', encoding='utf8') as log:
                process = subprocess.Popen([str(args.exe.resolve()), '--qa-ready', str(ready),
                    '--qa-stop', str(stop), '--qa-control', str(control),
                    *([str(p) for p in photos] if cycle == 1 else [])],
                    env=env, cwd=root, stdout=log, stderr=log, startupinfo=startup)
                active.append((process, stop))
                result[f'build{cycle}'] = wait_json(ready, process)
                wait_json(ready.with_suffix('.preview.json'), process, 40)
                sequence = 0
                def command(action='snapshot', **kwargs):
                    nonlocal sequence
                    sequence += 1
                    path = control/f'{sequence:06}.json'
                    # Publish a complete command; the app only reads the .json name.
                    pending = path.with_suffix('.pending')
                    pending.write_text(json.dumps(dict(action=action, **kwargs)), encoding='utf8')
                    pending.rename(path)
                    return wait_json(path.with_suffix('.result.json'), process, 30)
                def until(predicate, timeout=40):
                    deadline = time.monotonic()+timeout
                    while time.monotonic() < deadline:
                        snapshot = command()
                        if predicate(snapshot):
                            return snapshot
                        time.sleep(.07)
                    raise TimeoutError('Native state did not settle: '+json.dumps(snapshot, ensure_ascii=False))
                def preview():
                    return until(lambda s: s['preview'] and not s['busy'])
                current = preview()
                if cycle == 2:
                    check('per-photo recipe restored after a real process restart',
                        current['session']['photos'][0]['recipe']['parameters']['exposure'] == 1.5
                        and current['session']['photos'][1]['recipe']['parameters']['temperature'] == 30.)
                    check('undo is explicitly in-memory and starts empty on restart', not current['undo'])
                    command('adjust', parameter='exposure', value=.4)
                    stop.write_text('stop')
                    check('exit flushes the most recent slider value before debounce', process.wait(timeout=20) == 0
                        and abs(json.loads((root/'data/session.json').read_text(encoding='utf8'))['photos'][current['selected']]['recipe']['parameters']['exposure'] - .4) < 1e-6)
                    continue
                neutral = current['neutral_fingerprint']; original_preview = current['preview_fingerprint']
                started = time.monotonic()
                for value in (.25, .75, 1.25):
                    command('adjust', parameter='exposure', value=value)
                current = preview()
                result['timings']['three_slider_changes_to_latest_preview_seconds'] = time.monotonic()-started
                check('sliders update actual pixels and preserve neutral comparison',
                    current['exposure'] == 1.25 and current['preview_fingerprint'] != original_preview
                    and current['neutral_fingerprint'] == neutral and '复用解码缓存' in current['status'])
                check('before/after toggle uses a neutral image', command('compare', enabled=True)['compare'])
                command('compare', enabled=False)
                command('undo'); current = preview()
                check('one undo reverses a whole drag gesture', current['exposure'] == 0 and not current['undo']
                    and current['preview_fingerprint'] == original_preview)
                command('redo'); current = preview()
                check('redo restores edited pixels', current['exposure'] == 1.25)
                command('reset'); current = preview()
                check('reset restores neutral pixels', current['preview_fingerprint'] == original_preview)
                command('undo'); preview()
                command('select', index=1); preview()
                command('adjust', parameter='temperature', value=30.); preview()
                command('select', index=0); current = preview()
                check('switching photos restores each set of sliders', current['exposure'] == 1.25 and current['temperature'] == 0
                    and current['session']['photos'][1]['recipe']['parameters']['temperature'] == 30.)
                command('adjust', parameter='exposure', value=2.)
                command('select', index=1); command('select', index=0)
                current = preview()
                check('rapid switching rejects stale preview requests', current['selected'] == 0 and current['exposure'] == 2.)
                command('adjust', parameter='exposure', value=1.5)
                command('cancel_preview'); time.sleep(.5)
                current = command()
                check('cancelled preview cannot reappear and retains recipe', not current['busy'] and not current['preview']
                    and current['session']['photos'][0]['recipe']['parameters']['exposure'] == 1.5)
                command('retry_preview'); preview()
                for index, extension in enumerate(('jpg', 'png', 'tif')):
                    command('select', index=0); preview()
                    destination = root/f'输出 {index}.{extension}'
                    command('export', destination=str(destination), format=index)
                    command('select', index=1)
                    current = until(lambda s: not s['export_busy'])
                    check(f'{extension} exports captured photo despite selection change', destination.exists()
                        and Image.open(destination).size == (2400, 1600) and '已导出' in current['export_status'])
                    if extension == 'tif':
                        data = tifffile.imread(destination)
                        check('real GUI TIFF output has sub-eight-bit precision', data.dtype == np.uint16 and bool(np.any(data % 257)))
                    else:
                        check(f'{extension} output includes ICC', bool(Image.open(destination).info.get('icc_profile')))
                command('select', index=0); preview()
                command('export', destination=str(first), format=1)
                current = until(lambda s: not s['export_busy'])
                check('UI refuses to overwrite an existing original', digest(first) == originals[str(first)] and '目标已存在' in current['export_status'])
                if args.raw:
                    command('select', index=2); current = preview()
                    check('real RAW enters the same editable UI pipeline', current['engine_ready'] and current['selected'] == 2
                        and os.path.samefile(current['session']['photos'][2]['path'], args.raw))
                    command('adjust', parameter='exposure', value=.6); preview()
                    raw_output = root/'实拍全尺寸.tif'
                    started = time.monotonic()
                    command('export', destination=str(raw_output), format=2)
                    command('select', index=1)
                    current = until(lambda s: not s['export_busy'], 90)
                    data = tifffile.imread(raw_output)
                    check('real RAW UI export is full-size 16-bit', data.dtype == np.uint16 and data.shape[:2] == (5568, 3712)
                        and '已导出' in current['export_status'])
                    result['timings']['real_raw_export_seconds'] = time.monotonic()-started
                    command('select', index=2); preview()
                cancelled = root/'取消导出.png'
                command('export', destination=str(cancelled), format=1)
                command('cancel_export')
                current = until(lambda s: not s['export_busy'], 90)
                check('cancellation leaves no final output or temporary parts', not cancelled.exists()
                    and not list(root.glob('.z30-export-*.part')) and '取消' in current['export_status'])
                exiting = root/'退出中断.tif'
                command('export', destination=str(exiting), format=2)
                until(lambda s: s['export_busy'] and '全尺寸显影' in s['export_status'], 45)
                result['memory_mib'] = memory(process.pid)
                started = time.monotonic(); stop.write_text('stop')
                check('close during active rendering cancels and exits cleanly', process.wait(timeout=30) == 0)
                result['timings']['close_during_render_seconds'] = time.monotonic()-started
                check('close does not leave partial exports', not exiting.exists() and not list(root.glob('.z30-export-*.part')))
        check('all original sample hashes unchanged', all(digest(Path(p)) == value for p, value in originals.items()))
        result['success'] = True
    except BaseException as error:
        result.update(success=False, error=f'{type(error).__name__}: {error}')
        raise
    finally:
        for process, stop in active:
            if process.poll() is None:
                try:
                    stop.write_text('stop'); process.wait(timeout=15)
                except (OSError, subprocess.TimeoutExpired):
                    process.kill(); process.wait(timeout=5)  # Only this harness's own child.
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
