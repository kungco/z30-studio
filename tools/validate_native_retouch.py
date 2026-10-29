"""Validate the native float repair probe against the frozen Python corpus.

Synthetic data only. This validates the engine, not a desktop repair tool or
automatic blemish recognition. The absolute float tolerance is fixed at 2e-6.
"""
import argparse
import json
from pathlib import Path
import tempfile

import numpy as np
from native_raw_benchmark import digest, measured


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists() or args.out.suffix != '.json':
        parser.error('Choose a new JSON output')
    root = Path(__file__).resolve().parents[1]
    corpus_path = root / 'native/crates/core/tests/fixtures/retouch.json'
    original_hash = digest(corpus_path)
    corpus = json.loads(corpus_path.read_text(encoding='utf8'))
    folder = Path(tempfile.mkdtemp(prefix='retouch-reference-', dir=root / '.development'))
    output = folder / 'native'
    native = measured([str(args.probe.resolve()), str(corpus_path), str(output)])
    records = []
    for case, receipt in zip(corpus['cases'], native['cases'], strict=True):
        assert receipt['case'] == case['name']
        expected = np.array(case['expected'], np.float32)
        source = np.array(case['pixels'], np.float32)
        actual = np.fromfile(output / (case['name'] + '.f32'), dtype='<f4').reshape(expected.shape)
        excluded = np.all(expected == source, axis=1)
        error = float(np.abs(actual - expected).max())
        exact = bool(np.array_equal(actual[excluded], source[excluded]))
        finite = bool(np.isfinite(actual).all())
        donor_error = None
        if 'expected_donor' in case:
            donor_error = max(abs(receipt['donor'][k] - v) for k, v in zip(('x', 'y'), case['expected_donor']))
        assert error <= 2e-6 and exact and finite, (case['name'], error, exact, finite)
        assert donor_error is None or donor_error <= 1e-8
        records.append(dict(case=case['name'],max_rgb_error=error,excluded_pixels_exact=exact,
                            finite=finite,donor_error=donor_error,hdr_retained=bool(actual.max()>1)))
    assert digest(corpus_path) == original_hash
    report = dict(stage='5E1 float repair core',baseline=corpus['metadata']['baseline'],
                  corpus_sha256=original_hash,probe_sha256=digest(args.probe),rgb_abs_tolerance=2e-6,
                  donor_abs_tolerance=1e-8,cases=records,passed=True,synthetic=True,
                  desktop_integration=False,automatic_blemish_detection=False,cloud_requests=0,
                  scratch=str(folder))
    with args.out.open('x',encoding='utf8') as file:
        json.dump(report,file,indent=2)
    print(json.dumps(dict(cases=len(records),passed=True,max_rgb_error=max(r['max_rgb_error'] for r in records))))


if __name__ == '__main__':
    main()
