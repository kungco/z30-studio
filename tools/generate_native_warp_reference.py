"""Freeze independent NumPy geometry/analytic-Jacobian and linear-RGB references.

New compact inverse-warp specification, not a port of an old face slim model.
No Rust executable, model, personal photo or 8-bit image is used as oracle.
"""
import argparse,json
from pathlib import Path
import numpy as np


def pin(width,height,to,radius,shift):
    r=radius*min(width,height)
    return dict(to=list(to),radius=radius,**{'from':[to[0]-shift[0]*r/(width-1),to[1]-shift[1]*r/(height-1)]})


def field(width,height,pins,x,y):
    x=np.array(x,dtype=np.float64,copy=True);y=np.array(y,dtype=np.float64,copy=True)
    jac=np.broadcast_to(np.eye(2),x.shape+(2,2)).copy()
    for p in reversed(pins):
        centre=np.array(p['to'])*[width-1,height-1];delta=(np.array(p['to'])-p['from'])*[width-1,height-1]
        r=p['radius']*min(width,height);dx=x-centre[0];dy=y-centre[1]
        t=np.maximum(0,1-(dx*dx+dy*dy)/(r*r))
        grad=np.stack((-4*dx*t/(r*r),-4*dy*t/(r*r)),axis=-1)
        local=np.eye(2)-delta[:,None]*grad[...,None,:]
        jac=local@jac
        x-=delta[0]*t*t;y-=delta[1]*t*t
    return x,y,np.linalg.det(jac)


def reference(name,width,height,mode,pins):
    yy,xx=np.mgrid[:height,:width]
    if mode=='constant':source=np.broadcast_to(np.array([-.25,.23,2.5],np.float32),(height,width,3)).copy()
    elif mode=='texture':source=np.random.default_rng(143).uniform(-.2,2,(height,width,3)).astype(np.float32)
    else:source=np.stack((xx/max(1,width-1),yy/max(1,height-1),.2+xx*.01-yy*.005),axis=-1).astype(np.float32)
    if mode=='hdr':source=source*4-.3
    sx,sy,jac=field(width,height,pins,xx,yy)
    assert np.min(jac)>.5 and (sx>=0).all() and (sy>=0).all() and (sx<=width-1).all() and (sy<=height-1).all()
    x0=np.floor(sx).astype(int);y0=np.floor(sy).astype(int);x1=np.minimum(x0+1,width-1);y1=np.minimum(y0+1,height-1)
    fx=(sx-x0)[...,None];fy=(sy-y0)[...,None]
    expected=(source[y0,x0].astype(float)*(1-fx)*(1-fy)+source[y0,x1].astype(float)*fx*(1-fy)+source[y1,x0].astype(float)*(1-fx)*fy+source[y1,x1].astype(float)*fx*fy).astype(np.float32)
    samples=[]
    for p in pins:
        cx,cy=np.array(p['to'])*[width-1,height-1];r=p['radius']*min(width,height)
        for px,py in [(cx,cy),(cx+r,cy),(cx-r/np.sqrt(3),cy),(cx,cy+r*.4)]:
            ax,ay,det=field(width,height,pins,px,py);samples.append(dict(at=[px,py],source=[float(ax),float(ay)],determinant=float(det)))
    return dict(name=name,width=width,height=height,pixels=source.reshape(-1,3).tolist(),pins=pins,expected=expected.reshape(-1,3).tolist(),map_samples=samples,minimum_analytic_determinant=float(jac.min()),owned=any(p['from']!=p['to'] for p in pins))


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    if args.out.exists() or args.out.suffix!='.json':parser.error('Choose a new frozen JSON reference path')
    w,h=41,31
    cases=[reference('disabled-gradient',w,h,'gradient',[]),reference('single-max-shift',w,h,'gradient',[pin(w,h,(.52,.5),.2,(.1,0))]),
           reference('diagonal-hdr',w,h,'hdr',[pin(w,h,(.5,.5),.18,(.06,-.07))]),
           reference('mirrored-pair',w,h,'texture',[pin(w,h,(.3,.55),.18,(.08,0)),pin(w,h,(.7,.55),.18,(-.08,0))]),
           reference('four-overlapping',w,h,'texture',[pin(w,h,(.48,.5),.2,(.1,0)),pin(w,h,(.5,.5),.2,(0,-.1)),pin(w,h,(.52,.5),.2,(-.08,0)),pin(w,h,(.5,.52),.2,(0,.09))]),
           reference('portrait-texture',27,45,'texture',[pin(27,45,(.35,.4),.18,(.07,.04)),pin(27,45,(.65,.6),.18,(-.07,.04))]),
           reference('constant-signed-hdr',w,h,'constant',[pin(w,h,(.5,.5),.2,(.1,0))]),reference('one-pixel-wide-disabled',1,33,'hdr',[])]
    data=dict(specification='compact-inverse-warp-v1',oracle='NumPy vectorized inverse geometry + analytic Jacobian + float64 bilinear -> float32',legacy_port=False,maximum_shift_radius=.1,rgb_tolerance=2e-6,coordinate_tolerance=1e-9,cases=cases)
    with args.out.open('x',encoding='utf8') as f:json.dump(data,f,separators=(',',':'))
    print(json.dumps(dict(cases=len(cases),bytes=args.out.stat().st_size,minimum_determinant=min(c['minimum_analytic_determinant'] for c in cases))))


if __name__=='__main__':main()
