"""Windows CPU/peak-memory measurements on synthetic float skin-tone buffers.

No input photos/models are read. This measures only the optional tone pass,
not decode, smoothing, full export, or real Z30 portrait quality/performance.
"""
import argparse
import json
from pathlib import Path
import statistics
import tempfile

from native_raw_benchmark import digest, measured


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--repeat', type=int, default=3)
    args = parser.parse_args()
    if args.out.exists() or args.out.suffix != '.json' or not 1 <= args.repeat <= 5:
        parser.error('Choose a new JSON report and 1–5 repeats')
    root = Path(tempfile.mkdtemp(prefix='tone-perf-', dir=Path(__file__).resolve().parents[1] / '.development'))
    modes = [('disabled', 0, 0), ('uniform', 100, 0), ('lighten', 0, 100), ('both', 100, 100)]
    report = dict(stage='5D synthetic tone CPU', synthetic=True, repeat=args.repeat,
                  probe_sha256=digest(args.probe), cases=[],
                  limitations=['Synthetic float display-sRGB; no photograph decode or model inference',
                               'Single CPU/machine; no GPU, real Z30 portrait or multi-skin-tone validation',
                               'Face covers 80% of each dimension; larger than public astronaut face',
                               'Peak working set includes source float buffer and verification; not incremental allocation'])
    for width, height in [(512, 512), (5472, 3648)]:
        case = dict(width=width, height=height, runs=[], summary={},
                    memory_sampling_may_miss_short_lived_peak=(width == 512))
        for repeat in range(args.repeat):
            for name, uniformity, lighten in modes if repeat % 2 == 0 else list(reversed(modes)):
                result = measured([str(args.probe.resolve()), str(width), str(height),
                                   str(uniformity), str(lighten), str(root / f'{width}-{repeat}-{name}.anchor')])
                if not result['finite_in_gamut']:
                    raise AssertionError('Invalid full-size tone output')
                result.update(mode=name, repeat=repeat)
                case['runs'].append(result)
        for name, _, _ in modes:
            runs = [r for r in case['runs'] if r['mode'] == name]
            case['summary'][name] = dict(tone_median_seconds=statistics.median(r['tone_seconds'] for r in runs),
                                        peak_working_set_max_mib=max(r['peak_working_set_mib'] for r in runs))
        report['cases'].append(case)
    report['passed'] = True
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x', encoding='utf8') as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
    print(json.dumps(dict(report=str(args.out), passed=True)))


if __name__ == '__main__':
    main()
