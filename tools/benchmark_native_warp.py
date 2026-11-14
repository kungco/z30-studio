"""Synthetic 20MP compact warp CPU/peak-memory observations; no photos/models."""
import argparse,json,statistics
from pathlib import Path
from native_raw_benchmark import digest,measured


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('probe','out'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--repeat',type=int,default=3);args=parser.parse_args()
    if args.out.exists() or args.out.suffix!='.json' or not 1<=args.repeat<=5:parser.error('Choose a new JSON report and 1–5 repeats')
    runs=[]
    for repeat in range(args.repeat):
        for n in ([0,1,4] if repeat%2==0 else [4,1,0]):
            row=measured([str(args.probe.resolve()),'5472','3648',str(n)])
            assert row['finite'] and row['owned_output_created']==bool(n) and row['changed']==bool(n)
            row.update(repeat=repeat);runs.append(row)
    summary={str(n):dict(median_seconds=statistics.median(r['seconds'] for r in runs if r['count']==n),peak_working_set_max_mib=max(r['peak_working_set_mib'] for r in runs if r['count']==n)) for n in (0,1,4)}
    report=dict(stage='5F1 synthetic float warp CPU',passed=True,synthetic=True,probe_sha256=digest(args.probe),repeat=args.repeat,summary=summary,runs=runs,limitations=['20MP generated linear RGB; not RAW decode, face inference, photographic quality or GPU acceptance','Timing excludes source generation and output verification; peak memory is the whole process','Bilinear sampling may soften texture; no anti-aliasing or natural face-quality claim'])
    with args.out.open('x',encoding='utf8') as f:json.dump(report,f,indent=2)
    print(json.dumps(summary))


if __name__=='__main__':main()
