"""Rebuild numerical reference fixtures; the immutable legacy corpus is untouched.

Run with --write to update developer fixtures after reviewing algorithm changes.
The gamut reference solves cubic channel intersections, independently of the
production binary search, and uses the same documented adaptive RGB guard band.
"""
import argparse
import json
from pathlib import Path
import sys
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import processing
import server


def cubic_reference(lab):
    lab=np.asarray(lab,np.float64)
    a=processing.LAB_LMS64[:,0]*lab[0]
    b=processing.LAB_LMS64[:,1:]@lab[1:]
    coefficient=processing.LMS_RGB64@np.stack([a**3,3*a*a*b,3*a*b*b,b**3],axis=1)
    neutral=coefficient[:,0]
    guard=max(0,min(1e-5,float(neutral.min())*.25,(1-float(neutral.max()))*.25))
    roots=[]
    for row in coefficient:
        for edge in (guard,1-guard):
            shifted=row.copy();shifted[0]-=edge
            roots.extend(float(v.real) for v in np.polynomial.polynomial.polyroots(shifted)
                         if abs(v.imag)<1e-9 and 1e-12<v.real<1)
    stops=sorted([0.,*roots,1.]);limit=1.
    for lo,hi in zip(stops,stops[1:]):
        t=(lo+hi)/2
        rgb=coefficient@np.array([1,t,t*t,t*t*t])
        if rgb.min()<guard-1e-10 or rgb.max()>1-guard+1e-10:
            limit=lo;break
    return np.clip(coefficient@np.array([1,limit,limit**2,limit**3]),0,1).tolist()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--write',action='store_true');args=parser.parse_args()
    folder=ROOT/'native/crates/core/tests/fixtures'
    render=json.loads((folder/'develop-legacy.json').read_text(encoding='utf8'))
    source=np.asarray(render['pixels'],np.float32).reshape(render['height'],render['width'],3)
    for case in render['cases']:
        case['expected']=processing.render_array(source,server.DEFAULTS|case['parameters'],bits=32).reshape(-1,3).tolist()
    render['oracle_base']='Corrected Python: f64 colour and gamut, 24-step continuous inner boundary; original corpus preserved'
    gamut=json.loads((folder/'gamut-boundaries.json').read_text(encoding='utf8'))
    for case in gamut['cases']:case['expected']=cubic_reference(case['lab'])
    gamut['reference']='Independent cubic polynomial roots at adaptive inner RGB boundary; no binary search'
    for name,payload in [('develop.json',render),('gamut-boundaries.json',gamut)]:
        path=folder/name
        if args.write:path.write_text(json.dumps(payload,indent=2)+'\n',encoding='utf8')
        else:
            existing=json.loads(path.read_text(encoding='utf8'))
            for actual,expected in zip(existing['cases'],payload['cases']):
                if not np.allclose(actual['expected'],expected['expected'],rtol=0,atol=1e-7):raise SystemExit('Reference changed: '+name)
    print('Colour reference fixtures verified' if not args.write else 'Colour reference fixtures regenerated; legacy unchanged')


if __name__=='__main__':main()
