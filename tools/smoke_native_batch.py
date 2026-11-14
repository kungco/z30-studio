"""Exercise the real native batch callbacks, persistence and no-overwrite path.

QA substitutes only the folder dialog. It does not simulate physical mouse use.
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

from smoke_native import digest, wait_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    if args.out.exists() or args.out.suffix != '.json':
        parser.error('Choose a new JSON report path')
    root = Path(tempfile.mkdtemp(prefix='批量验收 ', dir=Path(__file__).resolve().parents[1] / '.development')).resolve()
    photo_dir = root / 'photos'
    output_dir = root / 'exports'
    photo_dir.mkdir()
    output_dir.mkdir()
    y, x = np.mgrid[:1600, :2400]
    base = np.stack(((30+x%170), (20+y%150), (35+(x+y)%130)), axis=-1).astype(np.uint8)
    photos = []
    for index in range(3):
        path = photo_dir / f'素材 {index+1}.png'
        Image.fromarray(np.roll(base, index * 15, axis=2)).save(path)
        photos.append(path)
    del base, x, y
    originals = {str(path): digest(path) for path in photos}
    env = os.environ | {'Z30_NATIVE_DATA_DIR': str(root/'data')}
    report = dict(stage='native-batch-ui', checks=[], source_sha256=originals,
                  physical_clicks_verified=False, real_cloud_inference_requests=0)
    active = []

    def check(label, condition):
        if not condition:
            raise AssertionError(label)
        report['checks'].append(label)

    def launch(number, initial):
        controls = root / f'commands{number}'
        controls.mkdir()
        ready = root / f'ready{number}.json'
        stop = root / f'stop{number}.signal'
        log = (root / f'process{number}.log').open('w', encoding='utf8')
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 4
        process = subprocess.Popen([str(args.exe.resolve()), '--qa-ready', str(ready),
                                    '--qa-stop', str(stop), '--qa-control', str(controls),
                                    *[str(path) for path in initial]], env=env, cwd=root,
                                   stdout=log, stderr=log, startupinfo=startup)
        active.append((process, stop, log))
        wait_json(ready, process, 30)
        wait_json(ready.with_suffix('.preview.json'), process, 60)
        return process, controls, stop

    def controller(process, controls):
        sequence = 0

        def command(action='snapshot', **values):
            nonlocal sequence
            sequence += 1
            path = controls / f'{sequence:06}.json'
            pending = path.with_suffix('.pending')
            pending.write_text(json.dumps(dict(action=action, **values)), encoding='utf8')
            pending.rename(path)
            return wait_json(path.with_suffix('.result.json'), process, 30)

        def until(predicate, timeout=120):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                state = command()
                if predicate(state):
                    return state
                time.sleep(.05)
            raise TimeoutError('batch did not settle: ' + json.dumps(state, ensure_ascii=False))

        return command, until

    def counts(state):
        statuses = [job['state'] for job in state['batch']['jobs']]
        return {name: statuses.count(name) for name in ('queued', 'running', 'done', 'failed', 'interrupted', 'cancelled')}

    try:
        first, control, stop = launch(1, photos)
        command, until = controller(first, control)
        until(lambda s: s['preview'] and not s['busy'])
        command('adjust', parameter='exposure', value=1.0)
        until(lambda s: s['session']['photos'][0]['recipe']['parameters']['exposure'] == 1.0)
        command('batch_start', directory=str(output_dir), format=2, mode=0)
        until(lambda s: counts(s)['running'] == 1)
        check('first job persisted running before background export',
              json.loads((root/'data/batch-jobs.json').read_text(encoding='utf8'))['jobs'][0]['state'] == 'running')
        command('batch_pause_resume')
        paused = until(lambda s: counts(s)['running'] == 0 and s['batch_paused'])
        check('pause waits for active photo and keeps later jobs queued',
              counts(paused)['done'] == 1 and counts(paused)['queued'] == 2)
        stop.write_text('stop')
        check('paused process closes cleanly', first.wait(timeout=30) == 0)

        second, control, stop = launch(2, [])
        command, until = controller(second, control)
        restored = command()
        check('restarted queue remains paused and retains per-photo recipes',
              restored['batch_paused'] and counts(restored)['queued'] == 2
              and restored['batch']['jobs'][0]['recipe']['parameters']['exposure'] == 1.0)
        collision = Path(restored['batch']['jobs'][1]['destination'])
        collision.write_bytes(b'existing output must survive')
        command('batch_pause_resume')
        settled = until(lambda s: counts(s)['queued'] == 0 and counts(s)['running'] == 0)
        check('existing target fails closed while later job completes',
              counts(settled)['failed'] == 1 and counts(settled)['done'] == 2
              and collision.read_bytes() == b'existing output must survive')
        collision.unlink()
        command('batch_retry')
        finished = until(lambda s: counts(s)['done'] == 3 and counts(s)['running'] == 0)
        check('explicit retry completes only the failed item',
              [job['attempts'] for job in finished['batch']['jobs']] == [1, 2, 1])
        pixels = [tifffile.imread(job['destination']) for job in finished['batch']['jobs']]
        check('all TIFF16 outputs have full resolution and high depth',
              all(image.shape == (1600, 2400, 3) and image.dtype == np.uint16 for image in pixels))
        check('individual recipes create distinct visible output',
              float(pixels[0].mean()) > float(pixels[1].mean()) * 1.15)
        check('all originals remain byte identical',
              all(digest(path) == originals[str(path)] for path in photos))
        before = len(finished['batch']['jobs'])
        command('batch_start', directory=str(output_dir), format=2, mode=0)
        rejected = until(lambda s: '未创建批量任务' in s['batch_status'])
        check('new batch cannot reuse already published target names',
              len(rejected['batch']['jobs']) == before and all(path.exists() for path in map(lambda j: Path(j['destination']), rejected['batch']['jobs'])))
        cancel_dir = root / 'cancelled-exports'
        cancel_dir.mkdir()
        command('batch_start', directory=str(cancel_dir), format=2, mode=1)
        shared = until(lambda s: len(s['batch']['jobs']) == before + 3 and s['batch']['jobs'][before]['state'] == 'running')
        check('shared recipe mode freezes the selected photo parameters for every job',
              all(job['recipe']['parameters']['exposure'] == 1.0 for job in shared['batch']['jobs'][before:]))
        command('batch_cancel')
        cancelled = until(lambda s: all(job['state'] != 'running' for job in s['batch']['jobs'][before:]))
        check('cancel keeps pending photos unpublished and marks them durably',
              all(job['state'] == 'cancelled' and not Path(job['destination']).exists()
                  for job in cancelled['batch']['jobs'][before+1:]))
        command('batch_retry')
        retried = until(lambda s: all(job['state'] == 'done' for job in s['batch']['jobs'][before:]))
        check('cancelled photos can be explicitly retried without replacing earlier outputs',
              all(Path(job['destination']).exists() for job in retried['batch']['jobs'][before:])
              and all(digest(path) == originals[str(path)] for path in photos))
        stop.write_text('stop')
        check('completed process closes cleanly', second.wait(timeout=30) == 0)
        report['job_attempts'] = [job['attempts'] for job in finished['batch']['jobs']]
        report['queue_directory'] = str(root)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
        print(json.dumps(dict(checks=len(report['checks']), report=str(args.out)), ensure_ascii=False))
    finally:
        for process, stop, log in active:
            if process.poll() is None:
                stop.write_text('stop')
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            log.close()


if __name__ == '__main__':
    main()
