"""Measure isolated decode/preview/develop/export stages on public Z30 NEFs.

Each core probe runs in a fresh process; peak RSS includes prerequisite stages.
Numbers are observations, not a general speed guarantee.
"""
import argparse
import json
from pathlib import Path
import tempfile

from native_raw_benchmark import EXPECTED, measured, digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('dll', 'raw12', 'raw14', 'preview-probe', 'export-probe', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists() or args.out.suffix != '.json':
        parser.error('Choose a new JSON report path')
    root = Path(tempfile.mkdtemp(prefix='分阶段基准 ', dir=Path(__file__).resolve().parents[1] / '.development')).resolve()
    parameters = root / 'parameters.json'
    parameters.write_text(json.dumps({'exposure': 0.5}), encoding='utf8')
    sources = [args.raw12.resolve(), args.raw14.resolve()]
    originals = {str(path): digest(path) for path in sources}
    if any(value not in EXPECTED.values() for value in originals.values()):
        parser.error('Only retained public Z30 12/14-bit fixtures may be benchmarked')
    result = dict(stage='native-stage4-performance', inputs=originals,
                  preview_probe_sha256=digest(args.preview_probe),
                  export_probe_sha256=digest(args.export_probe),
                  dll_sha256=digest(args.dll), cases=[],
                  caveat='single observation per fixture; each process peak includes prerequisite steps')
    for index, source in enumerate(sources):
        preview = measured([str(args.preview_probe.resolve()), str(args.dll.resolve()),
                            str(source), str(parameters)])
        export_dir = root / f'export-{index}'
        export = measured([str(args.export_probe.resolve()), str(args.dll.resolve()),
                           str(source), str(export_dir), str(parameters)])
        if not preview['success'] or digest(source) != originals[str(source)]:
            raise AssertionError(f'preview or source integrity failed for {source}')
        result['cases'].append(dict(source=str(source), preview=preview, export=export,
                                    output_sha256={path.name: digest(path) for path in export_dir.iterdir()}))
    result['sources_unchanged'] = all(digest(path) == originals[str(path)] for path in sources)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(dict(report=str(args.out), cases=len(result['cases'])), ensure_ascii=False))


if __name__ == '__main__':
    main()
