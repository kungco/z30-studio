"""Independent gamma-aware warp and forward-map bisection references."""
import argparse,json
from pathlib import Path
import numpy as np
from generate_native_warp_reference import field,pin


def decode(v):return np.where(v<=.04045,v/12.92,np.maximum((v+.055)/1.055,0)**2.4)
def encode(v):return np.where(v<=.0031308,12.92*v,1.055*np.maximum(v,0)**(1/2.4)-.055)
def forward(width,height,pins,at):
    point=np.array(at,dtype=float)
    for p in pins:
        centre=np.array(p['to'])*[width-1,height-1];delta=(np.array(p['to'])-p['from'])*[width-1,height-1];r=p['radius']*min(width,height)
        lo,hi=0.,1.
        for _ in range(64):
            mid=(lo+hi)/2;position=point+delta*mid;t=max(0,1-np.sum((position-centre)**2)/(r*r))
            if mid-t*t<0:lo=mid
            else:hi=mid
        point+=delta*((lo+hi)/2)
    return point.tolist()


def make(name,source,pins):
    h,w,_=source.shape;yy,xx=np.mgrid[:h,:w];sx,sy,jac=field(w,h,pins,xx,yy)
    x0=np.floor(sx).astype(int);y0=np.floor(sy).astype(int);x1=np.minimum(x0+1,w-1);y1=np.minimum(y0+1,h-1);fx=(sx-x0)[...,None];fy=(sy-y0)[...,None]
    linear=decode(source.astype(float))
    interpolated=linear[y0,x0]*(1-fx)*(1-fy)+linear[y0,x1]*fx*(1-fy)+linear[y1,x0]*(1-fx)*fy+linear[y1,x1]*fx*fy
    expected=encode(interpolated).astype(np.float32);identity=(sx==xx)&(sy==yy);expected[identity]=source[identity]
    samples=[];forward_samples=[]
    for p in pins:
        cx,cy=np.array(p['to'])*[w-1,h-1];r=p['radius']*min(w,h)
        for at in [[cx,cy],[cx+r*.4,cy-r*.3],[cx-r,cy]]:
            ax,ay,det=field(w,h,pins,*at);samples.append(dict(at=at,source=[float(ax),float(ay)],determinant=float(det)))
            forward_samples.append(dict(at=at,destination=forward(w,h,pins,at)))
    return dict(name=name,space='srgb',width=w,height=h,pixels=source.reshape(-1,3).tolist(),pins=pins,expected=expected.reshape(-1,3).tolist(),map_samples=samples,forward_samples=forward_samples,minimum_analytic_determinant=float(jac.min()),owned=bool(pins))


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    if args.out.exists() or args.out.suffix!='.json':parser.error('Choose a new frozen JSON file')
    yy,xx=np.mgrid[:33,:33];edge=np.broadcast_to((xx>=16)[...,None].astype(np.float32),(33,33,3)).copy()
    p=pin(33,33,(.5,.5),.2,(.5/(33*.2),0))
    cases=[make('half-pixel-black-white',edge,[p]),make('constant-signed-hdr',np.broadcast_to(np.array([-.2,.23,1.5],np.float32),(33,33,3)).copy(),[p]),
        make('overlap-colour-texture',np.random.default_rng(152).uniform(-.1,1.6,(31,41,3)).astype(np.float32),[pin(41,31,(.48,.5),.2,(.09,0)),pin(41,31,(.52,.5),.2,(0,-.08))]),
        make('encoded-disabled',np.broadcast_to(np.array([.04045,.0031308,.79],np.float32),(33,1,3)).copy(),[])]
    centre=cases[0]['expected'][16*33+16][0];assert abs(centre-.735356983)<1e-7 and centre>.73
    with args.out.open('x',encoding='utf8') as f:json.dump(dict(specification='compact-inverse-warp-v1-srgb',oracle='NumPy float64 sRGB decode -> inverse geometry -> linear interpolation -> encode; independent scalar bisection forward',cases=cases),f,separators=(',',':'))
    print(json.dumps(dict(cases=len(cases),bytes=args.out.stat().st_size,half_pixel=centre)))


if __name__=='__main__':main()
