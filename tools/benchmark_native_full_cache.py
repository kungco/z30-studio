"""Compare identical repeated RAW exports with and without the bounded cache.

Runs alternate fresh child processes, checks every TIFF byte hash and preserves
the public source hashes. Windows process counters measure peak working set.
"""
import argparse
import json
from pathlib import Path
import statistics
import tempfile

from native_raw_benchmark import EXPECTED, measured, digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('dll', 'raw12', 'raw14', 'probe', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--repeat', type=int, default=3)
    args = parser.parse_args()
    if args.out.exists() or args.out.suffix != '.json' or not 1 <= args.repeat <= 5:
        parser.error('Choose a new JSON report path and 1–5 repeats')
    root = Path(tempfile.mkdtemp(prefix='全尺寸缓存基准 ', dir=Path(__file__).resolve().parents[1] / '.development')).resolve()
    report = dict(stage='native-full-frame-cache', repeat=args.repeat,
                  probe_sha256=digest(args.probe), dll_sha256=digest(args.dll), cases=[],
                  limitations=['Same public photo exported twice; distinct-photo batches do not benefit',
                               'Windows file cache is warm on repeated reads; alternate mode order',
                               'Cache retains one float frame and can increase peak/idle memory'])
    for source in (args.raw12.resolve(), args.raw14.resolve()):
        source_hash = digest(source)
        if source_hash not in EXPECTED.values():
            parser.error('Use the retained public Z30 12/14-bit fixtures')
        case = dict(source=str(source), source_sha256=source_hash, runs=[], summary={})
        expected_output_hash = None
        for repeat in range(args.repeat):
            modes = ('uncached', 'cached') if repeat % 2 == 0 else ('cached', 'uncached')
            for mode in modes:
                directory = root / f'{source.stem}-{repeat}-{mode}'
                result = measured([str(args.probe.resolve()), str(args.dll.resolve()),
                                   str(source), str(directory), mode])
                hashes = [digest(directory/name) for name in ('first.tif', 'second.tif')]
                expected_output_hash = expected_output_hash or hashes[0]
                if any(value != expected_output_hash for value in hashes):
                    raise AssertionError('Cache changed rendered output bytes')
                if [row['reused_decode'] for row in result['receipts']] != [False, mode == 'cached']:
                    raise AssertionError('Unexpected cache hit behavior')
                result['output_sha256'] = hashes
                result['repeat'] = repeat
                case['runs'].append(result)
        for mode in ('uncached', 'cached'):
            runs = [run for run in case['runs'] if run['mode'] == mode]
            case['summary'][mode] = dict(
                second_export_median_seconds=statistics.median(run['receipts'][1]['seconds'] for run in runs),
                second_decode_median_seconds=statistics.median(run['receipts'][1]['decode_seconds'] for run in runs),
                peak_working_set_max_mib=max(run['peak_working_set_mib'] for run in runs))
        if digest(source) != source_hash:
            raise AssertionError('Source changed during benchmark')
        case['source_unchanged'] = True
        report['cases'].append(case)
    report['success'] = True
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(dict(report=str(args.out), success=True), ensure_ascii=False))


if __name__ == '__main__':
    main()
