"""Explicit local mesh review lifecycle on a public photo; not naturalness validation."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

import numpy as np
from PIL import Image
import tifffile
from smoke_native import digest, memory, wait_json
from smoke_native_skin import loaded_modules


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    for key in ('exe', 'image', 'oracle', 'out'):
        ap.add_argument('--'+key, type=Path, required=True)
    args = ap.parse_args()
    if args.out.exists():
        ap.error('Choose a new report')
    root = Path(tempfile.mkdtemp(prefix='contour-window-', dir=Path(__file__).resolve().parents[1]/'.development'))
    photos = [root/name for name in ('public.png', 'other.png', 'external-change.png')]
    for photo in photos:
        shutil.copyfile(args.image, photo)
    black = root/'black.png'
    Image.new('RGB', (512, 512)).save(black)
    photos.append(black)
    originals = {str(p.resolve()): digest(p) for p in (args.image, photos[0], photos[1], black)}
    data = root/'data'
    active = []
    report = dict(stage='5F3 local contour review', checks=[], exe_sha256=digest(args.exe),
                  qa_directory=str(root), cloud_requests=0, real_models=True,
                  naturalness_validation=False, analysis_seconds=[])

    def check(label, result):
        if not result:
            raise AssertionError(label)
        report['checks'].append(label)

    def launch(number, unavailable=False):
        control = root/f'commands-{number}'
        control.mkdir()
        ready, stop = root/f'ready-{number}.json', root/f'stop-{number}'
        env = os.environ | {'Z30_NATIVE_DATA_DIR': str(data)}
        for key in ('Z30_LIBRAW_DLL', 'Z30_VISION_DIR', 'Z30_SKIN_DIR', 'Z30_CONTOUR_DIR'):
            env.pop(key, None)
        if unavailable:
            env.update(Z30_VISION_DIR=str(root/'missing-runtime'), Z30_CONTOUR_DIR=str(root/'missing-model'))
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 4
        log = (root/f'process-{number}.txt').open('w', encoding='utf8')
        command_line = [str(args.exe.resolve()), '--qa-ready', str(ready), '--qa-stop', str(stop), '--qa-control', str(control)]
        if number == 1:
            command_line += list(map(str, photos))
        process = subprocess.Popen(command_line, cwd=root, env=env, stdout=log, stderr=log, startupinfo=startup)
        active.append((process, stop, log))
        marker = wait_json(ready, process)
        seq = 0

        def command(action='snapshot', **values):
            nonlocal seq
            seq += 1
            path = control/f'{seq:06}.json'
            pending = path.with_suffix('.pending')
            pending.write_text(json.dumps(dict(action=action, **values)), encoding='utf8')
            pending.rename(path)
            return wait_json(path.with_suffix('.result.json'), process, 30)

        def until(predicate, timeout=30):
            deadline = time.monotonic()+timeout
            while time.monotonic() < deadline:
                state = command()
                if predicate(state):
                    return state
                time.sleep(.02)
            raise TimeoutError('Contour window did not settle: '+json.dumps(state))
        return process, stop, command, until, marker

    try:
        process, stop, command, until, marker = launch(1)

        def recipe(state):
            return state['session']['photos'][state['session']['selected']]['recipe']

        def settle():
            return until(lambda s: s['preview'] and not s['busy'] and not s['face_busy'] and not s['contour']['busy'])

        def find():
            start = time.monotonic()
            command('contour_find')
            state = settle()
            report['analysis_seconds'].append(time.monotonic()-start)
            return state

        def export(name, verify=False):
            path = root/name
            command('export', destination=str(path), format=2)
            state = until(lambda s: path.exists() and not s['export_busy'])
            if verify:
                saved = root/(name+'.json')
                saved.write_text(json.dumps(state['session']), encoding='utf8')
                expected = root/(name+'.oracle.tif')
                subprocess.run([str(args.oracle.resolve()), str(saved), str(state['session']['selected']), str(expected)], check=True, capture_output=True)
                check(name+' matches independently exported saved recipe', digest(path) == digest(expected))
            return path

        state = settle()
        initial = recipe(state)
        check('component discovered without loading inference at startup', state['contour']['assets_ready'] and 'onnxruntime.dll' not in loaded_modules(process.pid))
        state = find()
        check('analysis without a selected face cannot change edits', state['contour']['count'] == 0 and recipe(state) == initial)
        command('detect_face')
        state = until(lambda s: s['has_face'] and not s['face_busy'] and not s['busy'] and s['contour']['ready'])
        before = recipe(state)
        baseline = export('baseline.tif')
        state = find()
        check('actual local mesh creates four bounded proposals', state['contour']['count'] == 4)
        check('outline exposes 36 finite source-image points', len(state['contour']['points']) == 36 and all(0 <= v <= 1 for p in state['contour']['points'] for v in p))
        check('analysis alone preserves the exact editing recipe', recipe(state) == before)
        check('analysis alone preserves exact TIFF pixels', digest(export('checked.tif')) == digest(baseline))
        # Export is an unrelated operation and must discard pending proposals.
        state = settle()
        check('export invalidates transient proposals', state['contour']['count'] == 0)
        find()
        command('contour_strength', value=0)
        command('contour_apply')
        state = settle()
        check('zero strength creates no hidden warp record', recipe(state) == before and state['contour']['count'] == 4)
        command('contour_strength', value=150)
        check('invalid strength is rejected', command()['contour']['strength'] == 0)
        command('contour_strength', value=60)
        command('contour_apply')
        state = settle()
        saved = recipe(state)
        check('explicit application creates four source-bound controls', len(saved['warp']['pins']) == 4)
        check('application uses the independent chosen amount', abs(saved['warp']['amount']-.6) < 1e-6)
        check('application preserves all other recipe fields', {k:v for k,v in saved.items() if k != 'warp'} == {k:v for k,v in before.items() if k != 'warp'})
        check('application clears temporary outline and candidates', state['contour']['count'] == 0 and not state['contour']['points'])
        applied = export('applied.tif', True)
        check('applied contour changes real output pixels', digest(applied) != digest(baseline))
        pixels = tifffile.imread(applied)
        check('output retains RGB16 data', pixels.dtype == np.uint16 and np.any(pixels % 257))
        command('undo')
        state = settle()
        check('the whole contour application is one undo step', recipe(state) == before)
        check('undo restores exact baseline pixels', digest(export('undo.tif')) == digest(baseline))
        command('redo')
        state = settle()
        check('redo restores exact bound controls', recipe(state) == saved)
        check('redo restores exact applied pixels', digest(export('redo.tif')) == digest(applied))
        state = find()
        check('existing warp is preserved rather than overwritten', recipe(state) == saved and state['contour']['count'] == 0)
        command('adjust', parameter='warp_amount', value=0)
        state = settle()
        zero_warp = recipe(state)
        state = find()
        check('even a disabled warp record cannot be overwritten', recipe(state) == zero_warp and state['contour']['count'] == 0)
        command('undo'); settle()
        command('undo'); settle()
        for action, values, label in [('adjust', dict(parameter='exposure', value=.2), 'edit'), ('about', dict(visible=True), 'modal'), ('compare', dict(enabled=True), 'compare')]:
            check(label+' starts with an actual proposal', find()['contour']['count'] == 4)
            command(action, **values)
            if action == 'about': command('about', visible=False)
            if action == 'compare': command('compare', enabled=False)
            command('contour_apply')
            state = settle()
            check(label+' cannot apply stale proposals', state['contour']['count'] == 0 and recipe(state).get('warp') is None)
            if action == 'adjust': command('undo'); settle()
        command('contour_find'); command('contour_cancel')
        state = settle()
        check('explicit cancellation leaves no late result', state['contour']['count'] == 0 and recipe(state) == before)
        find(); command('select', index=1); command('contour_apply')
        state = settle()
        check('photo switch cannot apply old-face controls', state['contour']['count'] == 0 and recipe(state).get('warp') is None)
        command('select', index=3); settle(); command('detect_face'); state = settle()
        check('blank input does not create a face', not state['has_face'])
        check('blank input cannot create contour proposals', find()['contour']['count'] == 0)
        command('select', index=2); settle(); command('detect_face')
        until(lambda s: s['has_face'] and not s['face_busy'] and not s['busy'] and s['contour']['ready'])
        check('external-change fixture initially has valid proposals', find()['contour']['count'] == 4)
        Image.new('RGB', (512, 512), (20, 30, 40)).save(photos[2])
        command('contour_apply'); state = command()
        check('source replacement rejects old geometry', state['contour']['count'] == 0 and recipe(state).get('warp') is None)
        command('select', index=0); settle()
        damaged = root/'damaged-component'
        shutil.copytree(args.exe.resolve().parent/'contour', damaged)
        model = next(damaged.glob('*.onnx'))
        with model.open('r+b') as stream: stream.write(b'BAD')
        command('contour_assets', directory=str(damaged)); settle()
        state = find()
        check('modified model is rejected without editing', state['contour']['count'] == 0 and recipe(state) == before)
        command('contour_assets', directory=str(args.exe.resolve().parent/'contour')); settle()
        check('valid component can be selected again', find()['contour']['count'] == 4)
        command('contour_apply'); state = settle(); saved = recipe(state)
        final = export('final.tif', True)
        report['memory_mib'] = memory(process.pid)
        stop.write_text('stop')
        check('first process closes with saved edits', process.wait(timeout=10) == 0)
        process, stop, command, until, _ = launch(2, unavailable=True)
        state = settle()
        check('restart restores exact applied contour metadata', recipe(state) == saved)
        check('restart restores no transient job or candidates', state['contour']['count'] == 0 and not state['contour']['busy'])
        check('saved contour requires no model DLL to render', 'onnxruntime.dll' not in loaded_modules(process.pid))
        check('export without any model remains exact', digest(export('restart.tif')) == digest(final))
        stop.write_text('stop'); check('model-free process closes cleanly', process.wait(timeout=10) == 0)
        process, stop, command, until, _ = launch(3)
        settle(); command('adjust', parameter='warp_clear', value=0); settle()
        command('contour_find'); stop.write_text('stop')
        check('close during model analysis is bounded', process.wait(timeout=10) == 0)
        check('original photos were not overwritten', all(digest(Path(p)) == sha for p, sha in originals.items()))
        report.update(success=True, source_commit=marker['source_commit'], source_dirty=marker['source_dirty'])
        args.out.write_text(json.dumps(report, indent=2), encoding='utf8')
        print(json.dumps(dict(success=True, checks=len(report['checks']))))
    finally:
        for process, stop, log in active:
            if process.poll() is None:
                stop.write_text('stop')
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
            log.close()


if __name__ == '__main__':
    main()
