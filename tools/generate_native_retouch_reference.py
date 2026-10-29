"""Generate a NEW repair reference file from the retained Python baseline.

The existing committed corpus is never overwritten by this developer tool.
"""
import argparse,hashlib,json,subprocess,sys
from pathlib import Path
import numpy as np
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--out',type=Path,required=True)
args=parser.parse_args()
if args.out.exists() or args.out.suffix!='.json':parser.error('Choose a new JSON output')
baseline='a2a2e089d760221a52c8774cca8a2e42f0a82269'
subprocess.run(['git','diff','--exit-code',baseline,'--','spatial.py'],cwd=root,check=True,stdout=subprocess.DEVNULL)
import spatial
rng=np.random.default_rng(5321)
height,width=13,19
yy,xx=np.mgrid[:height,:width]
source=np.stack((.18+.012*xx+.003*yy,.13+.008*xx+.005*yy,.08+.006*xx+.004*yy),axis=2).astype(np.float32)
source+=rng.uniform(-.008,.008,source.shape).astype(np.float32)
source[5:8,8:11]*=.2
cases=[]
def add(name,array,edits,choose=None):
    edits=spatial.repairs(edits)
    before=array.copy();out=spatial.retouch(array,{'repairs':edits})
    assert np.array_equal(array,before)
    row=dict(name=name,width=array.shape[1],height=array.shape[0],pixels=array.reshape(-1,3).tolist(),edits=edits,
             expected=out.reshape(-1,3).tolist())
    if choose:
        row['choose']=dict(x=choose[0],y=choose[1],radius=choose[2]);row['expected_donor']=list(spatial.choose_donor(array,*choose))
    cases.append(row)
base=dict(x=.49,y=.52,sx=.23,sy=.31,radius=.19,opacity=.85,feather=.55)
add('clone-centre',source,[dict(base,mode='clone')],(.49,.52,.08))
add('heal-centre',source,[dict(base,mode='heal')])
add('edge-invalid-donor',source,[dict(base,mode='heal',x=.06,y=.05,sx=0.,sy=0.,radius=.2)])
add('overlap-original-donors',source,[dict(base,mode='clone',sx=.75,sy=.75),dict(base,mode='heal',x=.7,y=.7,sx=.49,sy=.52)])
add('partial-zero-opacity',source,[dict(base,mode='clone',opacity=0.),dict(base,mode='heal',opacity=.2)])
add('hdr-linear',source*8,[dict(base,mode='heal')])
add('transposed-geometry',source.transpose(1,0,2).copy(),[dict(base,mode='clone',x=.3,y=.75)])
add('empty-passthrough',source,[])
add('signed-finite-linear',source-.3,[dict(base,mode='clone'),dict(base,mode='heal',x=.7)])
add('tiny-heal-no-ring',np.array([[[-.1,.4,1.2]]],dtype=np.float32),[dict(mode='heal')])
metadata=dict(baseline=baseline,oracle='spatial.repairs / retouch / choose_donor',
              source_sha256=hashlib.sha256((root/'spatial.py').read_bytes()).hexdigest(),
              numpy=np.__version__,rgb_abs_tolerance=2e-6,donor_abs_tolerance=1e-8)
corpus=dict(metadata=metadata,cases=cases)
path=args.out
path.write_text(json.dumps(corpus,separators=(',',':'))+'\n',encoding='utf8')
print(json.dumps(dict(cases=len(cases),bytes=path.stat().st_size,source_unmodified=True)))
