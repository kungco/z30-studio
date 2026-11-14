"""Developer-only Google TFLite/NumPy comparison; explicit local assets only.

Thresholds are set before measurements. Public fixture geometry is not a
subjective portrait-quality validation. Python/TFLite are not app dependencies.
"""
import argparse,hashlib,json,math,pathlib,subprocess,tempfile
import numpy as np
from PIL import Image,ImageOps
from ai_edge_litert.interpreter import Interpreter
import onnx,onnxruntime as ort
from prepare_native_contour import verify_component,VENDOR
from prepare_native_vision import verify_component as verify_runtime,verified

IMAGE_HASH='88431cd9653ccd539741b555fb0a46b61558b301d4110412b5bc28b5e3ea6cb5'
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    ap=argparse.ArgumentParser(description=__doc__)
    for name in ('probe','runtime','component','tflite','image','out'):ap.add_argument('--'+name,type=pathlib.Path,required=True)
    a=ap.parse_args()
    if a.out.exists():ap.error('Choose a new report path')
    verify_component(a.component);verify_runtime(a.runtime);manifest=json.loads((VENDOR/'manifest.json').read_text(encoding='utf8'));ref=manifest['reference'];verified(a.tflite,ref['bytes'],ref['sha256']);assert digest(a.image)==IMAGE_HASH
    graph=onnx.load(a.component/manifest['model']['name']);onnx.checker.check_model(graph)
    assert not graph.functions and all(n.domain in ('','ai.onnx') for n in graph.graph.node) and all(not n.external_data for n in graph.graph.initializer)
    t=Interpreter(model_path=str(a.tflite),num_threads=2);t.allocate_tensors();assert t.get_input_details()[0]['shape'].tolist()==[1,192,192,3]
    ort.disable_telemetry_events();options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
    s=ort.InferenceSession(str(a.component/manifest['model']['name']),options,providers=['CPUExecutionProvider'])
    assert [(x.name,x.shape) for x in s.get_outputs()]==[('landmarks',['batch',468,3]),('score',['batch',1])]
    root=pathlib.Path(tempfile.mkdtemp(prefix='contour-reference-',dir=a.out.resolve().parent));image=Image.open(a.image).convert('RGB');canvas=Image.new('RGB',(768,1024),(25,25,25));canvas.paste(image,(128,128));cases=[]
    for name,im,positive in [('original',image,True),('mirror',ImageOps.mirror(image),True),('portrait-canvas',canvas,True),('black',Image.new('RGB',(512,512)),False)]:
        p=root/(name+'.png');im.save(p);folder=root/name
        result=subprocess.run([str(a.probe.resolve()),str(a.runtime.resolve()),str(a.component.resolve()),str(p),str(folder)],capture_output=True,text=True)
        (root/(name+'.log')).write_text(result.stdout+result.stderr,encoding='utf8');assert result.returncode==0,(name,result.stderr)
        data=json.loads((folder/'result.json').read_text(encoding='utf8'));v=data['result']
        if not positive:assert v['face_count']==0 and v['mesh'] is None;cases.append(dict(name=name,faces=0,correct_no_proposal=True));continue
        assert v['face_count']>=1 and len(v['mesh']['points'])==468 and len(v['pins'])==4
        mesh=v['mesh'];w,h=data['width'],data['height'];f=v['face'];rect=f['rect'];eyes=sorted(f['landmarks'][:2],key=lambda p:p['x']);angle=math.atan2((eyes[1]['y']-eyes[0]['y'])*h,(eyes[1]['x']-eyes[0]['x'])*w)
        side=max(rect[2]*w,rect[3]*h)*1.5;cx=(rect[0]+rect[2]/2)*w;cy=(rect[1]+rect[3]/2)*h
        roi=mesh['roi'];assert max(abs(cx-roi['centre'][0]),abs(cy-roi['centre'][1]),abs(side-roi['side']),abs(angle-roi['rotation']))<1e-10
        neutral=np.fromfile(folder/'neutral.f32',dtype='<f4').reshape(h,w,3);yy,xx=np.mgrid[:192,:192];u=((xx+.5)/192-.5)*side;vv=((yy+.5)/192-.5)*side;c=math.cos(angle);sn=math.sin(angle)
        x=cx+c*u-sn*vv-.5;y=cy+sn*u+c*vv-.5;x0=np.floor(x).astype(np.int64);y0=np.floor(y).astype(np.int64);dx=(x-x0).astype(np.float32)[...,None];dy=(y-y0).astype(np.float32)[...,None]
        def sample(x,y):
            return np.where(((x>=0)&(x<w)&(y>=0)&(y<h))[...,None],neutral[np.clip(y,0,h-1),np.clip(x,0,w-1)],np.float32(0))
        aa=sample(x0,y0);bb=sample(x0+1,y0);cc=sample(x0,y0+1);dd=sample(x0+1,y0+1);top=aa+(bb-aa)*dx;bottom=cc+(dd-cc)*dx;expected=np.clip(top+(bottom-top)*dy,0,1)
        native_input=np.fromfile(folder/'input.f32',dtype='<f4').reshape(3,192,192).transpose(1,2,0);input_error=float(np.abs(expected-native_input).max());assert input_error<=2e-6
        t.set_tensor(t.get_input_details()[0]['index'],expected[None]);t.invoke();lite=t.get_tensor(t.get_output_details()[0]['index']).reshape(468,3)
        out=s.run(None,{'input':native_input.transpose(2,0,1)[None].copy()});model_error=float(np.abs(out[0][0]-lite).max());assert model_error<=.002
        uu=(lite[:,:2].astype(np.float64)/192-.5)*side;points=np.column_stack(((cx+c*uu[:,0]-sn*uu[:,1]-.5)/(w-1),(cy+sn*uu[:,0]+c*uu[:,1]-.5)/(h-1)))
        coordinate_error=float(np.abs(np.asarray(mesh['points'])-points).max());assert coordinate_error<=5e-6
        cases.append(dict(name=name,faces=v['face_count'],points=468,pins=4,max_input_error=input_error,max_model_pixel_error=model_error,max_source_normalized_error=coordinate_error,presence=mesh['presence'],inference_seconds=mesh['inference_seconds']))
    report=dict(passed=True,public_fixture=True,user_z30_portrait=False,subjective_quality=False,gpu=False,probe_sha256=digest(a.probe),component_sha256=manifest['model']['sha256'],tflite_sha256=ref['sha256'],thresholds=dict(input=2e-6,model_pixels=.002,normalized_coordinates=5e-6),cases=cases,directory=str(root))
    a.out.write_text(json.dumps(report,indent=2)+'\n',encoding='utf8');print(json.dumps(report))
if __name__=='__main__':main()
