"""Freeze an independent NumPy-window/moment reference. Never reads Rust output."""
import argparse,json
from pathlib import Path
import numpy as np

def pixels(c):
    yy,xx=np.mgrid[:c['height'],:c['width']];v=.3+.0002*xx+.0001*yy
    for cx,cy,r in c['spots']:
        d=(xx-cx)**2+(yy-cy)**2
        v*=1-.55*np.exp(-d/(2*r*r)) if c['kind']=='gaussian' else np.where(d<=r*r,.35,1.)
    if c['kind']=='line':v*=np.where((xx>=60)&(xx<63)&(yy>=25)&(yy<100),.2,1.)
    return (v*c['gain']).astype(np.float32)

def reference(c):
    # Equal-channel data; apply exactly the specified linear luminance weights.
    v=pixels(c).astype(np.float64);v=np.log2(np.maximum(.2126*v+.7152*v+.0722*v,1e-6));h,w=v.shape
    def box(a,r):return np.lib.stride_tricks.sliding_window_view(np.pad(a,r,mode='edge'),(2*r+1,2*r+1)).mean(axis=(-2,-1),dtype=np.float64)
    bg=box(v,10);fg=box(v,1);std=np.sqrt(np.maximum(box(v*v,10)-bg*bg,0.));contrast=bg-fg
    flags=(contrast>=np.maximum(.13,1.5*std))&(fg-v>=.018);flags[:10]=False;flags[-10:]=False;flags[:,:10]=False;flags[:,-10:]=False
    if c['blocked']:flags[:]=False
    points=[];limit=np.clip(min(w,h)*.05,5,12)
    for y,x in np.argwhere(flags):
        if not flags[y,x]:continue
        queue=[(int(y),int(x))];flags[y,x]=False;head=0
        while head<len(queue):
            cy,cx=queue[head];head+=1
            for dy in (-1,0,1):
                for dx in (-1,0,1):
                    ny,nx=cy+dy,cx+dx
                    if 0<=ny<h and 0<=nx<w and flags[ny,nx]:flags[ny,nx]=False;queue.append((ny,nx))
        a=np.array(queue,dtype=np.float64);span=np.ptp(a,axis=0)+1
        if len(a)<3 or span.max()>limit:continue
        centre=a.mean(axis=0);b=a-centre;cov=b.T@b/len(b);e=np.linalg.eigvalsh(cov)
        if e.sum()<=0 or e[0]/max(e[1],1e-12)<.35:continue
        points.append(dict(x=float(centre[1]/(w-1)),y=float(centre[0]/(h-1)),radius=float(np.clip((span.max()*.5+2)/min(w,h),.002,.02)),contrast=float(np.array([contrast[int(y),int(x)] for y,x in queue],dtype=np.float32).max())))
    points.sort(key=lambda p:(-p['contrast'],p['y'],p['x']));points=points[:16];selected=[]
    for p in points:
        if all(np.hypot((p['x']-q['x'])*(w-1),(p['y']-q['y'])*(h-1))>(p['radius']+q['radius'])*min(w,h)*1.5 for q in selected):selected.append(p)
    return selected

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--out',type=Path,required=True);args=ap.parse_args()
    if args.out.exists():ap.error('Choose a new corpus')
    base=dict(width=160,height=128,gain=1.,kind='disks',spots=[],blocked=False);cases=[]
    for name,changes in [('flat',{}),('two-spots',dict(spots=[[55,64,2.2],[100,70,2.2]])),('dark-skin-exposure',dict(spots=[[55,64,2.2]],gain=.02)),('hdr',dict(spots=[[55,64,2.2]],gain=16.)),('blocked-skin',dict(spots=[[55,64,2.2]],blocked=True)),('elongated-line',dict(kind='line')),('broad-shadow',dict(kind='gaussian',spots=[[80,64,15.]])),('bounded-dense',dict(width=240,height=180,spots=[[x,y,1.5] for y in (35,65,95,125) for x in (35,70,105,140,175)]))]:
        c=base|changes|dict(name=name);c['expected']=reference(c);cases.append(c)
    payload=dict(specification='skin-spot-log-contrast-v1',oracle='NumPy sliding windows, float64 variance, eigvalsh moments and Python8-connectivity; not Rust output',cases=cases)
    args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(payload,indent=2),encoding='utf8');print(json.dumps({c['name']:len(c['expected']) for c in cases}))
if __name__=='__main__':main()
