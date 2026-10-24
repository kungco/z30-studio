"""Isolated-process stage timings and OS peak RSS; never modifies input files."""
import argparse
import ctypes
import hashlib
import importlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time


def peak_rss():
    if os.name == 'nt':
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)] + [
                (n, ctypes.c_size_t) for n in ('peak', 'working', 'qpeak', 'quota', 'ppeak', 'paged', 'pagefile', 'peakpagefile')]
        c = Counters(); c.cb = ctypes.sizeof(c)
        current = ctypes.windll.kernel32.GetCurrentProcess
        current.restype = ctypes.c_void_p
        query = ctypes.windll.psapi.GetProcessMemoryInfo
        query.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD]
        if not query(current(), ctypes.byref(c), c.cb):
            raise ctypes.WinError()
        return c.peak / 1048576
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1048576 if sys.platform == 'darwin' else 1024)


def worker(args):
    sys.path.insert(0, str(args.source.resolve()))
    m = importlib.import_module('server')
    import numpy as np
    session = dict(path=args.sample, raw=args.sample.suffix.lower() == '.nef', cache={})
    p = m.DEFAULTS.copy()
    p.update(exposure=.75, highlights=-35, shadows=20, temperature=14, tint=-3,
             saturation=10, contrast=7, sharpen=15)
    if args.single_stage:
        return isolated_stage(args, m, session, p)
    stages = {}
    def measure(name, f):
        start = time.perf_counter(); out = f()
        stages[name] = dict(seconds=time.perf_counter() - start, peak_rss_mib=peak_rss())
        return out
    arr = measure('decode_full', lambda: m.decode(session, 'camera', False))
    small = measure('decode_preview', lambda: m.decode(session, 'camera', True))
    measure('decode_preview_cached', lambda: m.decode(session, 'camera', True))
    image = measure('develop_full', lambda: m.develop(arr, p))
    encoded = measure('encode_jpeg', lambda: m.image_bytes(image))
    measure('preview_pipeline_cached', lambda: m.preview_result(session, p, True))
    a = np.asarray(image)
    if args.artifacts:
        args.artifacts.mkdir(parents=True, exist_ok=True)
        image.resize((round(image.width * 600 / max(image.size)), round(image.height * 600 / max(image.size)))).save(
            args.artifacts / (args.sample.stem + '.png'))
    result = dict(sample=args.sample.name, sha256=hashlib.sha256(args.sample.read_bytes()).hexdigest(),
                  stages=stages, dimensions=[image.width, image.height], dtype=str(arr.dtype),
                  preview_dimensions=[small.shape[1], small.shape[0]], jpeg_bytes=len(encoded),
                  color=dict(channel_means=a.mean(axis=(0, 1)).tolist(),
                             channel_clip_fraction=np.mean(a >= 255, axis=(0, 1)).tolist(),
                             dark_fraction=float(np.mean(np.max(a, axis=2) <= 1))))
    if hasattr(m, 'export_bytes'):
        data = measure('export_tiff16', lambda: m.export_bytes(session, p, 'TIFF16', 95))
        result['tiff_bytes'] = len(data)
    print(json.dumps(result))


def isolated_stage(args, m, session, p):
    import numpy as np
    stage = args.single_stage
    result = {'supported': True}
    if stage == 'export_tiff16' and not hasattr(m, 'export_bytes'):
        print(json.dumps({'supported': False})); return
    if stage in ('develop_full', 'encode_jpeg'):
        arr = m.decode(session, 'camera', False)
    if stage == 'encode_jpeg':
        image = m.develop(arr, p)
    if stage in ('decode_preview_cached', 'preview_pipeline_cached'):
        m.decode(session, 'camera', True)
    if stage == 'preview_pipeline_repeat':
        m.preview_result(session, p, True)
    operations = {
        'decode_full': lambda: m.decode(session, 'camera', False),
        'decode_preview': lambda: m.decode(session, 'camera', True),
        'decode_preview_cached': lambda: m.decode(session, 'camera', True),
        'develop_full': lambda: m.develop(arr, p),
        'encode_jpeg': lambda: m.image_bytes(image),
        'preview_pipeline_cached': lambda: m.preview_result(session, p, True),
        'preview_pipeline_repeat': lambda: m.preview_result(session, p, True),
        'export_tiff16': lambda: m.export_bytes(session, p, 'TIFF16', 95),
    }
    start = time.perf_counter(); out = operations[stage]()
    result.update(seconds=time.perf_counter() - start, peak_rss_mib=peak_rss())
    if stage == 'develop_full':
        a = np.asarray(out)
        result.update(dimensions=list(out.size), dtype=str(arr.dtype),
                      channel_means=a.mean(axis=(0, 1)).tolist(),
                      channel_clip_fraction=np.mean(a >= 255, axis=(0, 1)).tolist())
        if args.artifacts:
            args.artifacts.mkdir(parents=True, exist_ok=True)
            out.resize((round(out.width * 600 / max(out.size)), round(out.height * 600 / max(out.size)))).save(
                args.artifacts / (args.sample.stem + '.png'))
    elif stage == 'decode_preview':
        result['dimensions'] = [out.shape[1], out.shape[0]]
    elif stage in ('encode_jpeg', 'export_tiff16'):
        result['bytes'] = len(out)
    print(json.dumps(result))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument('--sample', type=Path)
    p.add_argument('--samples', type=Path, nargs='+')
    p.add_argument('--out', type=Path)
    p.add_argument('--artifacts', type=Path)
    p.add_argument('--repeat', type=int, default=2)
    p.add_argument('--worker', action='store_true')
    p.add_argument('--single-stage')
    p.add_argument('--isolated', action='store_true')
    args = p.parse_args()
    if args.worker:
        return worker(args)
    results = []
    for sample in args.samples:
        runs = []
        for _ in range(args.repeat):
            cmd = [sys.executable, str(Path(__file__).resolve()), '--worker', '--source', str(args.source.resolve()),
                   '--sample', str(sample.resolve())]
            if args.artifacts:
                cmd += ['--artifacts', str(args.artifacts.resolve())]
            if args.isolated:
                stages = {}
                for stage in ('decode_full', 'decode_preview', 'decode_preview_cached', 'develop_full',
                              'encode_jpeg', 'preview_pipeline_cached', 'preview_pipeline_repeat', 'export_tiff16'):
                    r = subprocess.run(cmd + ['--single-stage', stage], check=True, capture_output=True,
                                       encoding='utf8', timeout=240)
                    data = json.loads(r.stdout)
                    if data.pop('supported'):
                        stages[stage] = data
                runs.append(dict(sample=sample.name, sha256=hashlib.sha256(sample.read_bytes()).hexdigest(), stages=stages))
            else:
                r = subprocess.run(cmd, check=True, capture_output=True, encoding='utf8', timeout=240)
                runs.append(json.loads(r.stdout))
        item = runs[0]
        for stage, metrics in item['stages'].items():
            metrics.update(seconds=statistics.median(x['stages'][stage]['seconds'] for x in runs),
                           peak_rss_mib=max(x['stages'][stage]['peak_rss_mib'] for x in runs))
        results.append(item)
        print(sample.name, json.dumps(item['stages']), flush=True)
    report = dict(platform=platform.platform(), python=platform.python_version(),
                  repeats=args.repeat, memory=('Fresh process per stage including prerequisite decode/render' if args.isolated else
                                               'OS process high-water RSS; stages cumulative within each isolated process'),
                  samples=results)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding='utf8')


if __name__ == '__main__':
    main()
