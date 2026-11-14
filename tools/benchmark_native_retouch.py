"""Windows synthetic clone/heal timing and peak memory; no photos/models read."""
import argparse,json,statistics
from pathlib import Path
from native_raw_benchmark import digest,measured


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--repeat',type=int,default=3)
    args=parser.parse_args()
    if args.out.exists() or args.out.suffix!='.json' or not 1<=args.repeat<=5:
        parser.error('Choose a new JSON report and 1–5 repeats')
    runs=[];modes=[('disabled',0,'heal'),('clone',32,'clone'),('heal',32,'heal')]
    for repeat in range(args.repeat):
        for name,count,mode in modes if repeat%2==0 else list(reversed(modes)):
            row=measured([str(args.probe.resolve()),'5472','3648',str(count),mode])
            assert row['finite'] and row['owned_output_created']==bool(count)
            row.update(name=name,repeat=repeat);runs.append(row)
    summary={}
    for name,_,_ in modes:
        rows=[r for r in runs if r['name']==name]
        summary[name]=dict(median_seconds=statistics.median(r['seconds'] for r in rows),
                           peak_working_set_max_mib=max(r['peak_working_set_mib'] for r in rows))
    report=dict(stage='5E1 synthetic repair CPU',synthetic=True,probe_sha256=digest(args.probe),repeat=args.repeat,
                runs=runs,summary=summary,passed=True,limitations=[
                'Synthetic float linear RGB; no RAW decode, model, desktop or photographic quality validation',
                '32 small normalized-radius .015 edits, not 256 worst-size patches',
                'Windows process peak counter polled every 20 ms; whole process includes source buffer and verification'])
    with args.out.open('x',encoding='utf8') as file:json.dump(report,file,indent=2)
    print(json.dumps(summary))


if __name__=='__main__':main()
