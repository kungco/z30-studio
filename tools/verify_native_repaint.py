"""Check OS invalidation on a probe's own static window, including fractional scale.

The explicit baseline mode requires every damaged pixel to remain broken. It
records a reproduced defect; it never counts that executable as fixed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--expect-broken-baseline',action='store_true')
    args=parser.parse_args()
    if args.out.exists() or not args.probe.is_file():parser.error('Use an existing probe and a new report')
    scratch=Path(__file__).resolve().parents[1]/'.development';scratch.mkdir(exist_ok=True)
    root=Path(tempfile.mkdtemp(prefix='window-repaint-',dir=scratch))
    records=[]
    for scale in (1.,1.25,1.5,2.):
        report=root/f'scale-{scale}.json'
        result=subprocess.run([str(args.probe.resolve()),str(report)],cwd=root,
            env=os.environ|{'SLINT_SCALE_FACTOR':str(scale)},capture_output=True,timeout=15)
        (root/f'scale-{scale}.stderr.txt').write_bytes(result.stderr)
        if not report.exists():raise RuntimeError(result.stderr.decode('utf8',errors='replace'))
        data=json.loads(report.read_text(encoding='utf8'))
        assert data['error'] is None and len(data['cases'])==3,data
        assert abs(data['scale_factor']-scale)<1e-6,data
        assert data['own_window_only'] and not data['app_exposed_to_other_window']
        if args.expect_broken_baseline:
            assert result.returncode==1 and data['passed'] is False,data
            assert all(c['actual']==c['damaged'] and not c['restored'] for c in data['cases']),data
        else:
            assert result.returncode==0 and data['passed'] is True,data
            assert all(c['actual']==c['expected'] and c['restored'] for c in data['cases']),data
        records.append(data)
    output=dict(mode='known-broken baseline' if args.expect_broken_baseline else 'fixed candidate',
        baseline_failure_reproduced=args.expect_broken_baseline,fixed=not args.expect_broken_baseline,
        probe_sha256=hashlib.sha256(args.probe.read_bytes()).hexdigest(),cases=12,records=records,
        synthetic_scale_factors=True,multi_monitor_hardware_test=False,own_window_only=True,
        photos_used=False,process_directory=str(root))
    with args.out.open('x',encoding='utf8') as f:json.dump(output,f,indent=2)
    print(json.dumps(dict(mode=output['mode'],fixed=output['fixed'],cases=12)))


if __name__=='__main__':main()
