"""Validate compact inverse warps against independent frozen NumPy references."""
import argparse,json,tempfile
from pathlib import Path
import numpy as np
from native_raw_benchmark import digest,measured


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('probe','out'):parser.add_argument('--'+key,type=Path,required=True)
    args=parser.parse_args()
    if args.out.exists() or args.out.suffix!='.json':parser.error('Choose a new JSON report')
    repo=Path(__file__).resolve().parents[1];path=repo/'native/crates/core/tests/fixtures/warp.json';before=digest(path)
    corpus=json.loads(path.read_text(encoding='utf8'));folder=Path(tempfile.mkdtemp(prefix='warp-reference-',dir=repo/'.development'));output=folder/'native'
    result=measured([str(args.probe.resolve()),str(path),str(output)]);records=[]
    for case,row in zip(corpus['cases'],result['cases'],strict=True):
        assert row['case']==case['name'] and row['owned']==case['owned']
        expected=np.array(case['expected'],np.float32);source=np.array(case['pixels'],np.float32)
        actual=np.fromfile(output/(case['name']+'.f32'),dtype='<f4').reshape(expected.shape)
        error=float(np.abs(actual-expected).max());assert error<=2e-6 and np.isfinite(actual).all()
        samples=np.array(row['samples']);wanted=np.array([s['source'] for s in case['map_samples']]);map_error=float(np.abs(samples-wanted).max()) if len(samples) else 0.
        assert map_error<=1e-9
        yy,xx=np.mgrid[:case['height'],:case['width']];outside=np.ones(xx.shape,bool)
        for p in case['pins']:
            cx,cy=np.array(p['to'])*[case['width']-1,case['height']-1];r=p['radius']*min(case['width'],case['height']);outside&=(xx-cx)**2+(yy-cy)**2>=r*r
        assert np.array_equal(actual[outside.ravel()],source[outside.ravel()])
        records.append(dict(case=case['name'],max_rgb_error=error,max_coordinate_error=map_error,outside_exact=True,owned=row['owned'],signed_preserved=bool(actual.min()<0),hdr_preserved=bool(actual.max()>1),minimum_analytic_determinant=case['minimum_analytic_determinant']))
    assert digest(path)==before
    report=dict(stage='5F1 constrained float warp core',passed=True,synthetic=True,corpus_sha256=before,probe_sha256=digest(args.probe),rgb_abs_tolerance=2e-6,coordinate_tolerance=1e-9,cases=records,desktop_integration=False,face_contour_inference=False,real_portrait=False,gpu=False,scratch=str(folder))
    with args.out.open('x',encoding='utf8') as f:json.dump(report,f,indent=2)
    print(json.dumps(dict(cases=len(records),passed=True,max_rgb_error=max(r['max_rgb_error'] for r in records),max_coordinate_error=max(r['max_coordinate_error'] for r in records))))


if __name__=='__main__':main()
