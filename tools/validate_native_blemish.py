"""Compare the developer probe against independent frozen candidate metadata."""
import argparse,json,subprocess,hashlib
from pathlib import Path
def main():
    ap=argparse.ArgumentParser(description=__doc__)
    for k in ('probe','out'):ap.add_argument('--'+k,type=Path,required=True)
    args=ap.parse_args();r=Path(__file__).resolve().parents[1];corpus=r/'native/crates/core/tests/fixtures/blemish.json'
    if args.out.exists():ap.error('Choose a new report')
    source=json.loads(corpus.read_text());output=json.loads(subprocess.check_output([str(args.probe.resolve()),str(corpus)],text=True));records=[]
    for c,row in zip(source['cases'],output,strict=True):
        assert c['name']==row['name'] and len(c['expected'])==len(row['points']),(c['name'],len(c['expected']),len(row['points']))
        errors={k:max([abs(a[k]-b[k]) for a,b in zip(c['expected'],row['points'],strict=True)]+[0.]) for k in ('x','y','radius','contrast')}
        assert all(v<=(1e-4 if k=='contrast' else 1e-6) for k,v in errors.items()),(c['name'],errors)
        records.append(dict(case=c['name'],candidates=len(row['points']),max_errors=errors))
    report=dict(passed=True,synthetic=True,real_skin_validation=False,corpus_sha256=hashlib.sha256(corpus.read_bytes()).hexdigest(),probe_sha256=hashlib.sha256(args.probe.read_bytes()).hexdigest(),cases=records)
    args.out.write_text(json.dumps(report,indent=2),encoding='utf8');print(json.dumps(dict(cases=len(records),passed=True)))
if __name__=='__main__':main()
