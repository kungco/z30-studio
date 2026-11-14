"""Review/apply lifecycle on synthetic face/skin fixtures, NOT real acne validation."""
import argparse,json,os,subprocess,tempfile,time,shutil
from pathlib import Path
import numpy as np
import tifffile
from PIL import Image
from smoke_native import digest,memory,wait_json
from smoke_native_skin import loaded_modules

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    for k in ('exe','probe','oracle','out'):ap.add_argument('--'+k,type=Path,required=True)
    ap.add_argument('--public-image',type=Path)
    args=ap.parse_args()
    if args.out.exists():ap.error('Choose a new report')
    root=Path(tempfile.mkdtemp(prefix='blemish-tools-',dir=Path(__file__).resolve().parents[1]/'.development'));yy,xx=np.mgrid[:192,:256];v=145.+xx*.04+yy*.015
    for x,y in [(82,110),(173,112),(77,58),(128,101),(12,40)]:v*=1-.55*np.exp(-((xx-x)**2+(yy-y)**2)/(2*1.5**2))
    pixels=np.repeat(np.rint(v).astype(np.uint8)[...,None],3,axis=2);photo=root/'synthetic-face.png';second=root/'other.png';changed=root/'external-change.png'
    for p in (photo,second,changed):Image.fromarray(pixels).save(p)
    original={str(p):digest(p) for p in (photo,second)};data=root/'data';other_data=root/'other-data'
    for p,d in [(photo,data),(changed,other_data)]:subprocess.run([str(args.probe.resolve()),str(p),str(d)],check=True,capture_output=True)
    profile=json.loads((data/'session.json').read_text());copy=json.loads(json.dumps(profile['photos'][0]));copy['path']=str(second);copy['recipe']['portrait']={'smooth':0,'face':None,'binding':None};copy['recipe']['skin']=None;profile['photos'].append(copy);profile['photos'].append(json.loads((other_data/'session.json').read_text())['photos'][0])
    if args.public_image:
        public=root/'public-portrait.png';shutil.copyfile(args.public_image,public);extra=json.loads(json.dumps(copy));extra['path']=str(public);profile['photos'].append(extra);original[str(public)]=digest(public);original[str(args.public_image.resolve())]=digest(args.public_image)
    (data/'session.json').write_text(json.dumps(profile),encoding='utf8')
    report=dict(stage='5E3 reviewable skin-spot suggestions',checks=[],synthetic_face_geometry=True,synthetic_skin_mask=True,real_acne_validation=False,exe_sha256=digest(args.exe),qa_directory=str(root),cloud_requests=0,analysis_seconds=[]);active=[]
    def check(label,value):
        if not value:raise AssertionError(label)
        report['checks'].append(label)
    def launch(number):
        control=root/f'commands-{number}';control.mkdir();ready=root/f'ready-{number}.json';stop=root/f'stop-{number}';env=os.environ|{'Z30_NATIVE_DATA_DIR':str(data)}
        for k in ('Z30_LIBRAW_DLL','Z30_VISION_DIR','Z30_SKIN_DIR'):env.pop(k,None)
        startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=4;log=(root/f'process-{number}.txt').open('w',encoding='utf8');process=subprocess.Popen([str(args.exe.resolve()),'--qa-ready',str(ready),'--qa-stop',str(stop),'--qa-control',str(control)],cwd=root,env=env,stdout=log,stderr=log,startupinfo=startup);active.append((process,stop,log));marker=wait_json(ready,process);seq=0
        def command(action='snapshot',**values):
            nonlocal seq
            seq+=1;p=control/f'{seq:06}.json';pending=p.with_suffix('.pending');pending.write_text(json.dumps(dict(action=action,**values)),encoding='utf8');pending.rename(p);return wait_json(p.with_suffix('.result.json'),process,30)
        def until(predicate,timeout=30):
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                s=command()
                if predicate(s):return s
                time.sleep(.02)
            raise TimeoutError('Blemish window did not settle: '+json.dumps(s))
        return process,stop,command,until,marker
    try:
        process,stop,command,until,marker=launch(1)
        def recipe(s,index=0):return s['session']['photos'][index]['recipe']
        def settle():return until(lambda s:s['preview'] and not s['busy'] and not s['blemish']['busy'])
        def find():
            started=time.monotonic();command('blemish_find');s=settle();report['analysis_seconds'].append(time.monotonic()-started);return s
        def export(name,verify=False):
            p=root/name;command('export',destination=str(p),format=2);s=until(lambda s:p.exists() and not s['export_busy'])
            if verify:
                snapshot=root/(name+'.json');snapshot.write_text(json.dumps(s['session']),encoding='utf8');expected=root/(name+'.oracle.tif');subprocess.run([str(args.oracle.resolve()),str(snapshot),str(s['session']['selected']),str(expected)],check=True,capture_output=True);check(name+' matches the saved float recipe',digest(p)==digest(expected))
            return p
        state=settle();before=recipe(state);baseline=export('baseline.tif');check('restored semantic fixture enables review without inference','onnxruntime.dll' not in loaded_modules(process.pid) and state['blemish']['ready'])
        state=find();check('only the two eligible cheek spots become candidates',state['blemish']['count']==2 and state['blemish']['selected']==2)
        check('finding candidates does not edit the recipe or preview',recipe(state)==before and state['preview_fingerprint']==state['neutral_fingerprint'])
        check('candidate markers correspond to the two cheek positions',all(min(np.hypot(m[0]*256-.5-x,m[1]*192-.5-y) for x,y in [(82,110),(173,112)])<2 for m in state['blemish']['marks']))
        command('blemish_select',index=0,selected=False);state=command();check('individual review updates check state and marker',state['blemish']['selected']==1 and not state['blemish']['marks'][0][4]);chosen=state['blemish']['marks'][1]
        command('blemish_apply');state=settle();saved=recipe(state);points=saved['repairs']['points'];check('only the selected suggestion is committed',len(points)==1 and abs(points[0]['x']-(chosen[0]*256-.5)/255)<1e-6 and abs(points[0]['y']-(chosen[1]*192-.5)/191)<1e-6)
        check('default strength is applied in float repair metadata',abs(points[0]['opacity']-.6)<1e-6)
        check('skin and portrait metadata survive application',saved['skin']==before['skin'] and saved['portrait']==before['portrait']);check('successful application removes transient proposals',state['blemish']['count']==0)
        applied=export('applied.tif',True);check('reviewed repair changes actual TIFF pixels',digest(applied)!=digest(baseline));array=tifffile.imread(applied);check('output retains RGB16 intermediate detail',array.dtype==np.uint16 and bool(np.any(array%257)));del array
        command('undo');state=settle();check('one reviewed batch is one undo step',recipe(state)==before);check('undo restores exact baseline TIFF',digest(export('undo.tif'))==digest(baseline));command('redo');state=settle();check('redo restores exact selected repair metadata',recipe(state)==saved);check('redo restores exact TIFF',digest(export('redo.tif'))==digest(applied))
        state=find();check('existing repair targets are excluded from the next analysis',state['blemish']['count']==1)
        command('blemish_strength',value=0);command('blemish_apply');state=settle();check('zero strength cannot commit hidden repairs',recipe(state)==saved and state['blemish']['count']==1)
        command('blemish_strength',value=70);command('blemish_select',index=0,selected=False);command('blemish_apply');state=settle();check('empty selection cannot change the recipe',recipe(state)==saved)
        command('blemish_select',index=0,selected=True);command('blemish_apply');state=settle();check('new batch preserves prior manual controls',len(recipe(state)['repairs']['points'])==2 and recipe(state)['repairs']['points'][0]==points[0]);check('chosen strength is saved for the new point',abs(recipe(state)['repairs']['points'][1]['opacity']-.7)<1e-6)
        command('undo');state=settle();check('second batch has an independent undo step',recipe(state)==saved)
        for action,values,label in [('adjust',dict(parameter='exposure',value=.2),'global edit'),('about',dict(visible=True),'modal')]:
            find();command(action,**values)
            if action=='about':command('about',visible=False)
            command('blemish_apply');state=settle();check(label+' invalidates old candidates',state['blemish']['count']==0 and recipe(state)['repairs']==saved['repairs'])
            if action=='adjust':command('undo');settle()
        command('blemish_find');command('blemish_cancel');state=settle();check('explicit cancel leaves no late proposal or edit',state['blemish']['count']==0 and recipe(state)==saved)
        command('blemish_find');command('select',index=1);command('blemish_apply');state=settle();check('photo switch cannot apply a previous face proposal',recipe(state,1).get('repairs') is None and not state['blemish']['ready'])
        command('select',index=2);settle();state=find();check('separate external-change fixture has initial candidates',state['blemish']['count']==2);Image.new('RGB',(256,192),(20,30,40)).save(changed);command('blemish_apply');state=command();check('external source replacement rejects the pending proposal',recipe(state,2).get('repairs') is None and state['blemish']['count']==0)
        report['synthetic_memory_mib']=memory(process.pid)
        if args.public_image:
            command('select',index=3);settle();command('detect_face');until(lambda s:s['has_face'] and not s['face_busy']);command('segment_skin');state=until(lambda s:s['has_skin_mask'] and not s['face_busy'] and not s['busy']);check('public portrait uses actual face and semantic skin inference',state['has_face'] and state['has_skin_mask']);real_recipe=recipe(state,3);real_baseline=export('public-neutral.tif');state=find();check('public proposals are bounded and do not silently edit',0<=state['blemish']['count']<=16 and recipe(state,3)==real_recipe);check('public checking preserves exact TIFF output',digest(export('public-checked.tif'))==digest(real_baseline));report['public_portrait']=dict(candidates=state['blemish']['count'],real_models=True,acne_ground_truth=False);command('blemish_cancel')
        command('select',index=0);state=settle();find();command('blemish_cancel');state=settle();final=export('final.tif',True);saved=recipe(state);report['memory_mib']=memory(process.pid);stop.write_text('stop');check('process closes and saves applied repairs',process.wait(timeout=10)==0)
        process,stop,command,until,_=launch(2);state=settle();check('restart restores exact applied repairs',recipe(state)==saved);check('transient candidates and busy jobs are not restored',state['blemish']['count']==0 and not state['blemish']['busy']);check('restart needs no inference DLL','onnxruntime.dll' not in loaded_modules(process.pid));check('restart export remains exact',digest(export('restart.tif'))==digest(final))
        command('blemish_find');stop.write_text('stop');check('closing during analysis is bounded and clean',process.wait(timeout=10)==0)
        check('original synthetic PNGs were never overwritten',all(digest(Path(p))==sha for p,sha in original.items()));report.update(success=True,source_commit=marker['source_commit'],source_dirty=marker['source_dirty'],external_mutation_fixture=str(changed));args.out.write_text(json.dumps(report,indent=2),encoding='utf8');print(json.dumps(dict(checks=len(report['checks']),success=True)))
    finally:
        for process,stop,log in active:
            if process.poll() is None:
                stop.write_text('stop')
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            log.close()
if __name__=='__main__':main()
