"""Developer-only CPU skin acceptance against original Google TFLite + NumPy.

Explicit local assets only; no downloads, cloud requests, or user-photo claims.
The original model's metadata and logits are checked independently of the
community ONNX conversion. Thresholds are fixed before measurements.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time
import zipfile

import numpy as np
from PIL import Image, ImageOps

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.prepare_native_skin import verify_component
from tools.prepare_native_vision import verified
from tools.smoke_native import memory

IMAGE_HASH='88431cd9653ccd539741b555fb0a46b61558b301d4110412b5bc28b5e3ea6cb5'
MAX_ERROR=2e-4
MEAN_ERROR=1e-5
CLASS_AGREEMENT=.9998
SIDE=256


def digest(path):
    with path.open('rb') as stream: return hashlib.file_digest(stream,'sha256').hexdigest()


def metadata_contract(path):
    from ai_edge_litert import schema_py_generated as schema
    from flatbuffers.table import Table
    from flatbuffers.number_types import Float32Flags
    # Field offsets from the upstream TFLite metadata schema M001:
    # ModelMetadata.subgraph_metadata=3, SubGraph.input_tensor_metadata=2,
    # TensorMetadata.process_units=4, ProcessUnit.options union payload=1.
    model=schema.Model.GetRootAsModel(path.read_bytes(),0)
    data=next(bytes(model.Buffers(model.Metadata(i).Buffer()).DataAsNumpy())
              for i in range(model.MetadataLength()) if model.Metadata(i).Name()==b'TFLITE_METADATA')
    assert data[4:8]==b'M001'
    def child(table,field,vector=False):
        offset=table.Offset(4+field*2)
        assert offset
        position=table.Vector(offset) if vector else table.Pos+offset
        return Table(table.Bytes,table.Indirect(position))
    meta=Table(data,struct.unpack_from('<I',data)[0])
    sub=child(meta,3,True)
    tensor=child(sub,2,True)
    unit=child(tensor,4,True)
    assert unit.Bytes[unit.Pos+unit.Offset(4)]==1  # NormalizationOptions
    norm=child(unit,1)
    mean,std=[norm.GetVectorAsNumpy(Float32Flags,norm.Offset(4+i*2)).tolist() for i in range(2)]
    assert mean==[127.5] and std==[127.5]
    # CustomMetadata[0].data: SegmenterOptions V001, Activation.SOFTMAX=2.
    custom=child(sub,9,True)
    data_offset=custom.Offset(6)
    segment_data=custom.Bytes[custom.Vector(data_offset):custom.Vector(data_offset)+custom.VectorLen(data_offset)]
    assert segment_data[4:8]==b'V001'
    options=Table(segment_data,struct.unpack_from('<I',segment_data)[0])
    assert options.Bytes[options.Pos+options.Offset(4)]==2
    with zipfile.ZipFile(path) as archive: labels=archive.read('labels.txt').decode().splitlines()
    assert labels==['background','hair','body-skin','face-skin','clothes','others']
    return dict(mean=mean,std=std,activation='softmax',labels=labels)


def resized(pixels,width=SIDE,height=SIDE):
    h,w=pixels.shape[:2]
    xs=np.clip((np.arange(width,dtype=np.float64)+.5)*w/width-.5,0,w-1)
    ys=np.clip((np.arange(height,dtype=np.float64)+.5)*h/height-.5,0,h-1)
    x0,y0=xs.astype(int),ys.astype(int)
    x1,y1=np.minimum(x0+1,w-1),np.minimum(y0+1,h-1)
    dx,dy=(xs-x0).astype(np.float32)[None,:,None],(ys-y0).astype(np.float32)[:,None,None]
    top=pixels[y0[:,None],x0]+(pixels[y0[:,None],x1]-pixels[y0[:,None],x0])*dx
    bottom=pixels[y1[:,None],x0]+(pixels[y1[:,None],x1]-pixels[y1[:,None],x0])*dx
    return top+(bottom-top)*dy


def softmax(logits):
    values=np.exp(logits-logits.max(axis=-1,keepdims=True))
    return values/values.sum(axis=-1,keepdims=True)


def reference_weights(probabilities):
    p=probabilities[...,3]
    t=np.clip((p-.55)/.30,0,1)
    raw=np.floor(t*t*(3-2*t)*255+.5).astype(np.uint8)
    raw[probabilities.argmax(-1)!=3]=0
    padded=np.pad(raw,1,mode='edge')
    return np.minimum.reduce([padded[y:y+SIDE,x:x+SIDE] for y in range(3) for x in range(3)])


def compare(actual,expected):
    assert actual.shape==expected.shape and np.isfinite(actual).all() and np.isfinite(expected).all()
    delta=np.abs(actual-expected)
    return dict(max_error=float(delta.max()),mean_error=float(delta.mean()))


def measured(command):
    # File-backed pipes avoid deadlock from the model's known Resize warnings.
    with tempfile.TemporaryFile() as stdout,tempfile.TemporaryFile() as stderr:
        start=time.perf_counter();peak=0
        process=subprocess.Popen(command,stdout=stdout,stderr=stderr,cwd=ROOT)
        try:
            while process.poll() is None:
                if time.perf_counter()-start>90: raise TimeoutError('Skin probe exceeded 90 seconds')
                try:
                    counters=memory(process.pid)
                    peak=max(peak,counters['working_set'],counters['peak_working_set'])
                except OSError:
                    if process.poll() is None: raise
                time.sleep(.01)
            stdout.seek(0);stderr.seek(0)
            out=stdout.read(200001);errors=stderr.read(200001)
            if process.returncode or len(out)>200000: raise RuntimeError(errors.decode('utf8',errors='replace')[-3000:])
            result=json.loads(out)
            result.update(wall_seconds=time.perf_counter()-start,peak_working_set_mib=peak,stderr_bytes=len(errors))
            return result
        finally:
            if process.poll() is None: process.kill();process.wait(timeout=10)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('probe','runtime','model','tflite','image','out'): parser.add_argument('--'+key,type=Path,required=True)
    args=parser.parse_args()
    for key in ('probe','runtime','model','tflite','image','out'): setattr(args,key,getattr(args,key).resolve())
    if args.out.exists(): raise ValueError('Report already exists')
    verify_component(args.model)
    manifest=json.loads((ROOT/'native/vendor/skin/manifest.json').read_text(encoding='utf8'))
    ref=manifest['reference']; verified(args.tflite,ref['bytes'],ref['sha256'])
    assert digest(args.image)==IMAGE_HASH,'Use the pinned public-domain image'
    contract=metadata_contract(args.tflite)
    import onnx,onnxruntime as ort
    from ai_edge_litert.interpreter import Interpreter
    graph=onnx.load(args.model/manifest['model']['name'],load_external_data=False)
    onnx.checker.check_model(graph)
    assert not graph.functions and not any(t.external_data for t in graph.graph.initializer)
    assert all(n.domain=='' for n in graph.graph.node),'Custom operations are not accepted'
    ort.disable_telemetry_events();ort.set_default_logger_severity(3)
    opts=ort.SessionOptions();opts.intra_op_num_threads=2;opts.inter_op_num_threads=1
    onnx_session=ort.InferenceSession(str(args.model/manifest['model']['name']),opts,providers=['CPUExecutionProvider'])
    lite=Interpreter(model_path=str(args.tflite),num_threads=2);lite.allocate_tensors()
    original=Image.open(args.image).convert('RGB')
    records=[]; failures=[]
    scratch=Path(tempfile.mkdtemp(prefix='skin-validation-',dir=ROOT/'.development'))
    variants={'original':original,'wide':ImageOps.pad(original,(800,512)),
              'tall':ImageOps.pad(original,(512,800)),'mirror':ImageOps.mirror(original),
              'rotate90':original.transpose(Image.Transpose.ROTATE_90),
              'rotate180':original.transpose(Image.Transpose.ROTATE_180),
              'rotate270':original.transpose(Image.Transpose.ROTATE_270),
              'face-crop':original.crop((150,30,300,210)),
              'blank':Image.new('RGB',(512,512),(0,0,0))}
    for name,image in variants.items():
        path=scratch/(name+'.png');image.save(path)
        output=scratch/name
        result=measured([str(args.probe),str(args.runtime),str(args.model),str(path),str(output)])
        width,height=image.size
        pixels=np.fromfile(output/'neutral.f32',dtype='<f4').reshape(height,width,3)
        inputs=np.clip(resized(pixels),0,1)[None]*np.float32(2)-np.float32(1)
        lite.set_tensor(lite.get_input_details()[0]['index'],inputs);lite.invoke()
        official=softmax(lite.get_tensor(lite.get_output_details()[0]['index']))[0]
        converted=softmax(onnx_session.run(None,{'input_29':inputs})[0])[0]
        actual=np.fromfile(output/'face-probabilities.f32',dtype='<f4').reshape(SIDE,SIDE)
        weights=np.fromfile(output/'weights.u8',dtype=np.uint8).reshape(SIDE,SIDE)
        categories=np.fromfile(output/'categories.u8',dtype=np.uint8).reshape(SIDE,SIDE)
        metrics=dict(native_vs_onnx=compare(actual,converted[...,3]),
                     conversion_vs_tflite=compare(converted,official),
                     native_vs_tflite=compare(actual,official[...,3]),
                     native_class_agreement=float(np.mean(categories==converted.argmax(-1))),
                     conversion_class_agreement=float(np.mean(converted.argmax(-1)==official.argmax(-1))),
                     gate_max_byte_error=int(np.abs(weights.astype(int)-reference_weights(converted).astype(int)).max()))
        for key in ('native_vs_onnx','conversion_vs_tflite','native_vs_tflite'):
            if metrics[key]['max_error']>MAX_ERROR or metrics[key]['mean_error']>MEAN_ERROR: failures.append(name+': '+key)
        if min(metrics['native_class_agreement'],metrics['conversion_class_agreement'])<CLASS_AGREEMENT: failures.append(name+': categories')
        if metrics['gate_max_byte_error']>1: failures.append(name+': gate')
        if name=='blank' and result['first']['accepted_pixels']!=0: failures.append('blank gate must be empty')
        if result['faces']:
            before=np.fromfile(output/'geometry.f32',dtype='<f4').reshape(height,width,3)
            after=np.fromfile(output/'semantic.f32',dtype='<f4').reshape(height,width,3)
            gate=resized(weights[...,None].astype(np.float32)/255,width,height)[...,0]
            excluded=gate==0
            metrics['excluded_pixels_unchanged']=bool(np.array_equal(after[excluded],pixels[excluded]))
            metrics['geometry_changed_excluded_pixels']=int(np.count_nonzero(np.any(before[excluded]!=pixels[excluded],axis=-1)))
            metrics['semantic_changed_pixels']=int(np.count_nonzero(np.any(after!=pixels,axis=-1)))
            metrics['finite_in_gamut']=bool(np.isfinite(after).all() and after.min()>=0 and after.max()<=1)
            if not metrics['excluded_pixels_unchanged'] or not metrics['finite_in_gamut']: failures.append(name+': pixel safety')
            Image.fromarray(np.round(np.clip(after,0,1)*255).astype(np.uint8)).save(output/'semantic.png')
        Image.fromarray(weights).save(output/'skin-mask.png')
        records.append(dict(name=name,metrics=metrics,native=result))
    # Same-length corruption must be rejected by the native loader, not just by
    # Python's preparer; retain its failure instead of falling back to geometry.
    bad=scratch/'corrupt-model';bad.mkdir()
    data=bytearray((args.model/manifest['model']['name']).read_bytes());data[len(data)//2]^=1
    (bad/manifest['model']['name']).write_bytes(data)
    negative=subprocess.run([str(args.probe),str(args.runtime),str(bad),str(args.image),str(scratch/'rejected')],capture_output=True,timeout=30)
    rejected=negative.returncode!=0 and b'SHA-256' in negative.stderr and not (scratch/'rejected/weights.u8').exists()
    if not rejected: failures.append('tampered model accepted')
    report=dict(stage='5C1 skin engine; no desktop integration',source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                source_tree_dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT)),probe_sha256=digest(args.probe),
                model=manifest['model'],reference=ref,metadata_contract=contract,public_image_sha256=IMAGE_HASH,
                scratch=str(scratch),thresholds=dict(max_probability_error=MAX_ERROR,mean_error=MEAN_ERROR,class_agreement=CLASS_AGREEMENT,max_gate_byte_error=1),
                records=records,tampered_model_rejected=rejected,original_image_unchanged=digest(args.image)==IMAGE_HASH,
                user_photos_validated=False,cloud_requests=0,failures=failures,passed=not failures)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    with args.out.open('x',encoding='utf8') as stream: json.dump(report,stream,ensure_ascii=False,indent=2)
    print(json.dumps(dict(passed=not failures,cases=len(records),failures=failures,report=str(args.out))))
    if failures: raise SystemExit(1)


if __name__=='__main__': main()
