"""Real public Z30 12/14-bit batch acceptance through the native window callbacks."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

import numpy as np
import tifffile

from smoke_native import digest, memory, wait_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('exe', 'raw12', 'raw14', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists() or args.out.suffix != '.json':
        parser.error('Choose a new JSON report path')
    from native_raw_benchmark import EXPECTED
    sources = [args.raw12.resolve(), args.raw14.resolve()]
    source_hashes = {str(source): digest(source) for source in sources}
    if any(value not in EXPECTED.values() for value in source_hashes.values()):
        parser.error('Both inputs must be the retained public Z30 12/14-bit fixtures')
    root = Path(tempfile.mkdtemp(prefix='RAW 批量 ', dir=Path(__file__).resolve().parents[1] / '.development')).resolve()
    data = root / 'data'
    output = root / 'batch'
    output.mkdir()
    control = root / 'commands'
    control.mkdir()
    ready, stop = root/'ready.json', root/'stop.signal'
    env = os.environ | {'Z30_NATIVE_DATA_DIR': str(data),
                        'PATH': str(Path(os.environ['SystemRoot'])/'System32')+';'+os.environ['SystemRoot']}
    env.pop('Z30_LIBRAW_DLL', None)
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 4
    report = dict(stage='native-batch-real-z30', source_hashes=source_hashes,
                  exe_sha256=digest(args.exe), checks=[], timings={}, bundled_raw=True,
                  raw_environment_override=False, physical_clicks_verified=False)

    def check(label, condition):
        if not condition:
            raise AssertionError(label)
        report['checks'].append(label)

    with (root/'process.log').open('w', encoding='utf8') as log:
        process = subprocess.Popen([str(args.exe.resolve()), '--qa-ready', str(ready),
                                    '--qa-stop', str(stop), '--qa-control', str(control),
                                    *map(str, sources)], env=env, cwd=root,
                                   stdout=log, stderr=log, startupinfo=startup)
        sequence = 0

        def command(action='snapshot', **values):
            nonlocal sequence
            sequence += 1
            path = control/f'{sequence:06}.json'
            pending = path.with_suffix('.pending')
            pending.write_text(json.dumps(dict(action=action, **values)), encoding='utf8')
            pending.rename(path)
            return wait_json(path.with_suffix('.result.json'), process, 30)

        def until(predicate, timeout=150):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                state = command()
                if predicate(state):
                    return state
                time.sleep(.1)
            raise TimeoutError('RAW batch did not settle: ' + json.dumps(state, ensure_ascii=False))

        try:
            wait_json(ready, process, 30)
            wait_json(ready.with_suffix('.preview.json'), process, 90)
            singles = []
            for index, source in enumerate(sources):
                command('select', index=index)
                selected = until(lambda s: s['selected'] == index and s['preview'] and not s['busy'], 90)
                if index == 1:
                    check('switching to another photo evicts the previous full-size cache before preview decode',
                          selected.get('full_cache_bytes') == 0)
                destination = root/f'single-{index}.tif'
                started = time.monotonic()
                command('export', destination=str(destination), format=2)
                until(lambda s: destination.exists() and not s['export_busy'], 150)
                singles.append(destination)
                report['timings'][f'single_{index}_seconds'] = round(time.monotonic()-started, 3)
                if index == 0:
                    repeat_destination = root/'single-repeat.tif'
                    started = time.monotonic()
                    command('export', destination=str(repeat_destination), format=2)
                    repeat = until(lambda s: repeat_destination.exists() and not s['export_busy'], 90)
                    report['timings']['single_repeat_seconds'] = round(time.monotonic()-started, 3)
                    check('real window reuses full-size cache for an immediate repeat export',
                          '复用全尺寸缓存' in repeat['export_status'] and digest(repeat_destination) == digest(destination))
            report['single_export_memory_mib'] = memory(process.pid)
            until(lambda s: s.get('full_cache_bytes') == 0, 40)
            report['single_after_cache_expiry_memory_mib'] = memory(process.pid)
            check('idle full-size cache expires in the running window', command().get('full_cache_bytes') == 0)
            stop.write_text('stop')
            check('single-export process closes cleanly', process.wait(timeout=30) == 0)
            control = root/'batch-commands'
            control.mkdir()
            ready, stop = root/'batch-ready.json', root/'batch-stop.signal'
            sequence = 0
            process = subprocess.Popen([str(args.exe.resolve()), '--qa-ready', str(ready),
                                        '--qa-stop', str(stop), '--qa-control', str(control)],
                                       env=env, cwd=root, stdout=log, stderr=log, startupinfo=startup)
            wait_json(ready, process, 30)
            wait_json(ready.with_suffix('.preview.json'), process, 90)
            until(lambda s: s['preview'] and not s['busy'], 90)
            started = time.monotonic()
            command('batch_start', directory=str(output), format=2, mode=0)
            state = until(lambda s: len(s['batch']['jobs']) == 2
                          and all(job['state'] == 'done' for job in s['batch']['jobs']), 180)
            report['timings']['batch_seconds'] = round(time.monotonic()-started, 3)
            report['memory_mib'] = memory(process.pid)
            check('normal batch does not retain newly decoded full-size frames',
                  state.get('full_cache_bytes') == 0)
            check('two real Z30 jobs complete with no failed status',
                  len(state['batch']['jobs']) == 2 and not state['batch_protected'])
            for index, (source, single, job) in enumerate(zip(sources, singles, state['batch']['jobs'])):
                target = Path(job['destination'])
                single_pixels = tifffile.imread(single)
                batch_pixels = tifffile.imread(target)
                check(f'RAW {index} single/batch TIFF16 pixels identical',
                      batch_pixels.dtype == np.uint16 and batch_pixels.shape == single_pixels.shape
                      and np.array_equal(batch_pixels, single_pixels))
                check(f'RAW {index} source SHA unchanged', digest(source) == source_hashes[str(source)])
                check(f'RAW {index} 16-bit output retains sub-8-bit detail',
                      bool(np.any(batch_pixels % 257)))
            check('no unfinished part remains on successful batch',
                  not any(path.suffix == '.part' for path in output.iterdir()))
            stop.write_text('stop')
            check('batch process closes cleanly', process.wait(timeout=30) == 0)
            report['queue_directory'] = str(root)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
            print(json.dumps(dict(checks=len(report['checks']), report=str(args.out)), ensure_ascii=False))
        finally:
            if process.poll() is None:
                stop.write_text('stop')
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)


if __name__ == '__main__':
    main()
