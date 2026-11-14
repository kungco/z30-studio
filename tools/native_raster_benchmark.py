"""Read-only synthetic raster acceptance and Windows worker memory comparison.

The native app has no Python dependency. This is a developer measurement tool.
Small analytic fixtures gate quality. Original Python comparisons report their
8-bit ICC boundary separately rather than relabeling it as float equivalence.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def rgb8_delta(old,new,width,height):
    import numpy as np
    a=np.memmap(old,dtype='<f4',mode='r',shape=(height,width,3));b=np.memmap(new,dtype='<f4',mode='r',shape=a.shape)
    maximum=0
    for y in range(0,height,64):
        def encoded(values):
            return np.rint(np.where(values<=.0031308,values*12.92,1.055*np.maximum(values,0)**(1/2.4)-.055)*255).astype(np.int32)
        maximum=max(maximum,int(np.abs(encoded(a[y:y+64])-encoded(b[y:y+64])).max()))
    return dict(max_rgb8_code_difference=maximum,within_jpeg_decode_tolerance=maximum<=1)


def oracle(source, output):
    output=Path(output)
    output.with_suffix('.pid.json').write_text(json.dumps(dict(pid=os.getpid())),encoding='utf8')
    from tools.frozen_reference import baseline_directory
    sys.path.insert(0,str(baseline_directory()))
    from PIL import Image
    import numpy as np
    from imaging import raster_linear
    started=time.perf_counter()
    with Image.open(source) as image:frame,policy,warnings=raster_linear(image)
    elapsed=time.perf_counter()-started
    with output.open('xb') as stream:frame.astype('<f4',copy=False).tofile(stream)
    print(json.dumps(dict(width=frame.shape[1],height=frame.shape[0],decode_seconds=elapsed,policy=policy,warnings=warnings)),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe',type=Path,default=ROOT/'native/target/release/examples/raster_probe.exe')
    parser.add_argument('--full-size',action='store_true')
    parser.add_argument('--oracle-child',nargs=2)
    args=parser.parse_args()
    if args.oracle_child:oracle(*args.oracle_child);return
    import numpy as np
    from PIL import Image
    from tools.native_raw_benchmark import measured,compare
    from tools.generate_raster_reference import raw_png,srgb
    from tools.frozen_reference import BASE,baseline_directory
    baseline_directory()
    folder=ROOT/'.development/native-raster'/('run-'+uuid.uuid4().hex);folder.mkdir(parents=True)
    corpus=json.loads((ROOT/'native/crates/core/tests/fixtures/raster.json').read_text())
    report=dict(stage='2C-raster-icc',full_migration_complete=False,probe_sha256=digest(args.probe),
                baseline_commit=BASE,native_base_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                source_sha256={name:digest(ROOT/name) for name in ['native/crates/core/src/raster.rs','native/crates/core/src/colour.rs','native/crates/core/src/frame.rs']},cases=[],performance=[])
    for index,case in enumerate(corpus['cases']):
        data=bytes.fromhex(case['hex']);source=folder/(f'fixture-{index}'+('.jpg' if data[:2]==b'\xff\xd8' else '.png'));source.write_bytes(data)
        before=digest(source);output=folder/f'fixture-{index}.f32'
        process=subprocess.run([str(args.probe.resolve()),str(source),str(output)],capture_output=True,timeout=30)
        result=dict(name=case['name'],source_unchanged=digest(source)==before)
        if 'error_contains' in case:
            result['expected_rejection']=process.returncode!=0 and case['error_contains'] in process.stderr.decode('utf8',errors='replace')
            result['success']=result['expected_rejection'] and result['source_unchanged']
        else:
            if process.returncode:raise RuntimeError(process.stderr.decode('utf8',errors='replace'))
            metadata=json.loads(process.stdout)
            actual=np.fromfile(output,dtype='<f4').reshape(-1,3);expected=np.array(case['expected'],np.float64)
            result.update(max_abs_error=float(np.abs(actual-expected).max()),tolerance=case['tolerance'],source_bits=metadata['source_bits'])
            result['success']=(metadata['width'],metadata['height'])==(case['width'],case['height']) and np.isfinite(actual).all().item() and result['max_abs_error']<=case['tolerance'] and result['source_unchanged']
        report['cases'].append(result)
    if args.full_size:
        width,height=5568,3712
        xx=np.arange(width,dtype=np.uint32)[None,:];yy=np.arange(height,dtype=np.uint32)[:,None]
        image=np.empty((height,width,3),np.uint8)
        image[:,:,0]=(xx*255//(width-1));image[:,:,1]=(yy*255//(height-1));image[:,:,2]=((xx+yy)*255//(width+height-2))
        icc=(ROOT/'native/crates/core/assets/srgb.icc').read_bytes()
        sources=[]
        for label,options in [('jpeg20mp-untagged',{}),('jpeg20mp-icc',{'icc_profile':icc}),('jpeg20mp-420',{'subsampling':2})]:
            path=folder/(label+'.jpg');Image.fromarray(image).save(path,**({'quality':95,'subsampling':0}|options));sources.append((label,path))
        del image
        wide=np.empty((height,width,3),np.uint16)
        wide[:,:,0]=xx*65535//(width-1);wide[:,:,1]=yy*65535//(height-1);wide[:,:,2]=(xx+yy)*65535//(width+height-2)
        path=folder/'png16-20mp.png';path.write_bytes(raw_png(wide));sources.append(('png16-20mp',path))
        del wide
        for label,source in sources:
            print('Measuring '+label,flush=True)
            output=folder/(label+'-native.f32');before=digest(source)
            native=measured([str(args.probe.resolve()),str(source),str(output)])
            result=dict(name=label,native=native,source_unchanged=digest(source)==before)
            if label.startswith('jpeg'):
                old=folder/(label+'-baseline.f32')
                baseline=measured([sys.executable,str(Path(__file__).resolve()),'--oracle-child',str(source),str(old)],old.with_suffix('.pid.json'))
                result.update(baseline=baseline,old_output_comparison=compare(old,output,width,height))
                result['old_output_comparison'].update(rgb8_delta(old,output,width,height))
            else:
                # Analytic original uint16 coordinate ramp, independently of PNG/CMS.
                frame=np.memmap(output,dtype='<f4',mode='r',shape=(height,width,3));maximum=0.
                for y in range(0,height,64):
                    ycoord=np.arange(y,min(y+64,height),dtype=np.uint32)[:,None]
                    encoded=np.empty((len(ycoord),width,3),np.float64)
                    encoded[:,:,0]=(xx*65535//(width-1))/65535
                    encoded[:,:,1]=(ycoord*65535//(height-1))/65535
                    encoded[:,:,2]=((xx+ycoord)*65535//(width+height-2))/65535
                    maximum=max(maximum,float(np.abs(frame[y:y+64]-srgb(encoded)).max()))
                result.update(analytic_max_abs_error=maximum,analytic_tolerance=2e-6,
                              baseline='Original Python explicitly rejects RGB16 PNG; no fabricated timing/equivalence')
            report['performance'].append(result)
    report['success']=all(v['success'] for v in report['cases']) and all(v['source_unchanged'] and v.get('analytic_max_abs_error',0)<=v.get('analytic_tolerance',0) and v.get('old_output_comparison',{}).get('within_jpeg_decode_tolerance',True) for v in report['performance'])
    report['limitations']=['One measurement per synthetic case; no statistical speed claim','ICC float path deliberately avoids original Python 8-bit CMS intermediate','CMYK/YCCK and cICP/HDR are explicitly rejected pending separate implementation','sRGB display output; monitor ICC and visual/print calibration unverified']
    path=folder/'report.json';path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(dict(report=str(path),success=report['success'])),flush=True)
    if not report['success']:raise SystemExit(1)


if __name__=='__main__':main()
