"""Compare bundled GNU LibRaw against the pinned official DLL on public Z30 NEFs."""
import argparse
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.native_raw_benchmark import EXPECTED, compare, digest, measured
from tools.package_native import verify_runtime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--official-dll', type=Path, required=True)
    parser.add_argument('--raw12', type=Path, required=True)
    parser.add_argument('--raw14', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error('Report must be a new file')
    runtime = args.runtime.resolve(strict=True)
    manifest = verify_runtime(runtime)
    official = args.official_dll.resolve(strict=True)
    if digest(official) != '6a459c22039abf0eac4d263673337c8ed5f223acbd372fcf77610debf80ac8cd':
        raise ValueError('Unexpected official baseline DLL')
    scratch = Path(tempfile.mkdtemp(prefix='libraw-comparison-', dir=ROOT/'.development'))
    probe = ROOT/'native/target/release/examples/raw_probe.exe'
    report = dict(stage='native-stage2g-raw-runtime', probe_sha256=digest(probe),
        official_dll_sha256=digest(official), bundled_dll_sha256=manifest['dll_sha256'],
        cases=[], original_sources_unchanged=False, exact_pixels=False)
    sources = {12: args.raw12.resolve(strict=True), 14: args.raw14.resolve(strict=True)}
    for bits, source in sources.items():
        if digest(source) != EXPECTED[bits]:
            raise ValueError('Expected the pinned public fixture, not a user original')
        for half in (False, True):
            outputs, results = [], []
            for label, dll in [('official', official), ('bundled', runtime/'raw/libraw.dll')]:
                output = scratch/f'{bits}-{half}-{label}.f32'
                command = [str(probe), str(dll), str(source), str(output)]
                if half:
                    command.append('half')
                results.append(measured(command))
                outputs.append(output)
            before, after = results
            if (before['width'], before['height'], before['metadata']) != (after['width'], after['height'], after['metadata']):
                raise ValueError('RAW dimensions or metadata changed')
            error = compare(*outputs, before['width'], before['height'])
            report['cases'].append(dict(bits=bits, half=half, official=before, bundled=after, error=error))
    report['original_sources_unchanged'] = all(digest(p) == EXPECTED[b] for b, p in sources.items())
    report['exact_pixels'] = all(c['error']['max_abs_error'] == 0 and c['error']['nonfinite_channels'] == 0 for c in report['cases'])
    with args.out.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'report': str(args.out), 'exact_pixels': report['exact_pixels'],
                      'original_sources_unchanged': report['original_sources_unchanged']}))
    if not report['exact_pixels'] or not report['original_sources_unchanged']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
