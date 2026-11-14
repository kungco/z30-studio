"""Real repair-tool callbacks and viewport geometry. Physical clicks checked separately.

Only generated images and explicitly provided read-only public RAW fixtures.
The third synthetic file is intentionally modified to test stale sampling.
"""
import argparse,json,os,subprocess,tempfile,time
from pathlib import Path
import numpy as np
from PIL import Image
import tifffile
from smoke_native import digest,memory,wait_json
from smoke_native_skin import loaded_modules


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('exe','oracle','out'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--raw',type=Path,action='append',default=[])
    parser.add_argument('--dll',type=Path)
    args=parser.parse_args()
    if args.out.exists() or bool(args.raw)!=bool(args.dll):parser.error('Choose a new report; RAW requires DLL')
    root=Path(tempfile.mkdtemp(prefix='repair-tools-',dir=Path(__file__).resolve().parents[1]/'.development'))
    yy,xx=np.mgrid[:384,:512]
    array=np.stack((80+xx//5,60+yy//5,70+(xx+yy)%19),axis=-1).astype(np.uint8);array[182:202,246:266]=[15,7,4]
    first=root/'photo.png';second=root/'portrait.png';changed=root/'external-change.png'
    Image.fromarray(array).save(first);Image.fromarray(array.transpose(1,0,2)).save(second);Image.fromarray(array).save(changed)
    photos=[first,second,changed,*[p.resolve() for p in args.raw]]
    original={str(p):digest(p) for p in [first,second,*photos[3:]]}
    data=root/'data';report=dict(stage='5E2b repair tools',checks=[],exe_sha256=digest(args.exe),qa_directory=str(root),physical_clicks=False,cloud_requests=0,raw_cases=[],pointer_roundtrip_seconds=[])
    active=[]
    def check(name,value):
        if not value:raise AssertionError(name)
        report['checks'].append(name)
    def launch(number):
        control=root/f'commands-{number}';control.mkdir();ready=root/f'ready-{number}.json';stop=root/f'stop-{number}'
        env=os.environ|{'Z30_NATIVE_DATA_DIR':str(data)}
        for key in ('Z30_LIBRAW_DLL','Z30_VISION_DIR','Z30_SKIN_DIR'):env.pop(key,None)
        startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=4
        log=(root/f'process-{number}.log').open('w',encoding='utf8')
        process=subprocess.Popen([str(args.exe.resolve()),'--qa-ready',str(ready),'--qa-stop',str(stop),'--qa-control',str(control),*map(str,photos if number==1 else [])],cwd=root,env=env,stdout=log,stderr=log,startupinfo=startup)
        active.append((process,stop,log));marker=wait_json(ready,process);sequence=0
        def command(action='snapshot',**values):
            nonlocal sequence
            sequence+=1;path=control/f'{sequence:06}.json';pending=path.with_suffix('.pending')
            pending.write_text(json.dumps(dict(action=action,**values)),encoding='utf8');pending.rename(path)
            return wait_json(path.with_suffix('.result.json'),process,30)
        def until(predicate,timeout=40):
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                state=command()
                if predicate(state):return state
                time.sleep(.02)
            raise TimeoutError('Repair tools did not settle: '+json.dumps(state))
        return process,stop,command,until,marker
    try:
        process,stop,command,until,marker=launch(1)
        def settle(timeout=40):return until(lambda s:s['preview'] and not s['busy'] and not s['repair']['busy'] and s['repair']['ready'],timeout)
        def point(state,index=0,photo_index=0):return state['session']['photos'][photo_index]['recipe']['repairs']['points'][index]
        def click(u,v):
            state=command();x,y,w,h=state['view']['image'];start=time.monotonic();command('repair_canvas',x=x+u*w,y=y+v*h);state=settle()
            report['pointer_roundtrip_seconds'].append(time.monotonic()-start);return state
        def export(name):
            target=root/name;command('export',destination=str(target),format=2);until(lambda s:target.exists() and not s['export_busy'],90);return target
        initial=settle();check('new project starts with repair tool off',initial['repair']['mode']==0 and initial['repair']['count']==0)
        command('repair_tool',mode=1);command('repair_adjust',parameter='radius',value=6)
        state=click(.5,.5);first_point=point(state).copy()
        check('actual repair canvas callback adds a source-bound heal point',state['repair']['count']==1 and first_point['mode']=='heal' and abs(first_point['radius']-.06)<1e-8)
        check('point selector follows selected record after model replacement',state['repair']['choice']==state['repair']['selected']+1==1)
        check('repair tool changes actual preview pixels',state['preview_fingerprint']!=initial['preview_fingerprint'])
        check('selected target and source overlay match displayed pixel centres',np.allclose(state['repair']['marks'][0][:2],[.5,.5],atol=1e-6) and state['repair']['has_source'])
        check('original unedited comparison remains unchanged',state['neutral_fingerprint']==initial['neutral_fingerprint'])
        first_export=export('first.tif');command('repair_marks',visible=False);second_export=export('no-marks.tif')
        check('overlay visibility never changes exported pixels',digest(first_export)==digest(second_export));command('repair_marks',visible=True)
        command('repair_select',index=-1);state=click(.5,.5);check('clicking visible marker selects instead of adding',state['repair']['count']==1 and state['repair']['selected']==0)
        for name,value in [('radius',4),('radius',4.5),('opacity',50),('feather',80)]:command('repair_adjust',parameter=name,value=value)
        state=settle();check('selected point sliders change only their own settings',abs(point(state)['radius']-.045)<1e-7 and point(state)['opacity']==.5 and abs(point(state)['feather']-.8)<1e-7)
        command('undo');state=settle();check('feather undo keeps opacity and radius',point(state)['feather']==.5 and point(state)['opacity']==.5 and abs(point(state)['radius']-.045)<1e-7)
        command('undo');state=settle();check('opacity undo keeps previous radius gesture',point(state)['opacity']==1 and abs(point(state)['radius']-.045)<1e-7)
        command('undo');state=settle();check('same-control radius gesture is one undo step',abs(point(state)['radius']-.06)<1e-7)
        command('repair_select',index=0);command('repair_pick');state=click(.75,.5)
        expected=(.75*512-.5)/511
        check('manual source uses width-minus-one pixel-centre mapping',abs(point(state)['sx']-expected)<1e-6 and np.allclose(state['repair']['source_uv'],[.75,.5],atol=1e-6))
        check('manual resampling leaves target and strength unchanged',point(state)['x']==first_point['x'] and point(state)['y']==first_point['y'] and point(state)['opacity']==1)
        command('repair_suggest');state=settle();check('explicit neighbour suggestion restores deterministic donor',np.allclose([point(state)['sx'],point(state)['sy']],[first_point['sx'],first_point['sy']],atol=1e-8))
        command('view',zoom=1.5,pan_x=-40,pan_y=-30);state=command();cw,ch=state['view']['canvas'];pw,ph=state['view']['pixels'];w=min(cw,ch*pw/ph)*1.5;h=w*ph/pw
        expected_rect=[(cw-w)/2+np.clip(-40,-max(0,(w-cw)/2),max(0,(w-cw)/2)),(ch-h)/2+np.clip(-30,-max(0,(h-ch)/2),max(0,(h-ch)/2)),w,h]
        check('actual Slint zoom and clamped pan use matching geometry',np.allclose(state['view']['image'],expected_rect,atol=.002))
        state=click(.3,.6);p=point(state,1)
        check('zoomed panned clicks map to correct stored coordinates',state['repair']['count']==2 and abs(p['x']-(.3*512-.5)/511)<1e-6 and abs(p['y']-(.6*384-.5)/383)<1e-6)
        check('zoomed target overlay round-trips to same canvas position',np.allclose(state['repair']['marks'][1][:2],[.3,.6],atol=1e-6))
        before=state['session'];command('repair_canvas',x=-1,y=10);state=command();check('coordinates outside the canvas cannot add offscreen edits',state['session']==before)
        command('view',zoom=1,pan_x=0,pan_y=0);state=command();x,y,w,h=state['view']['image']
        if x>1 or y>1:
            before=state['session'];command('repair_canvas',x=x/2 if x>1 else x+w/2,y=y+h/2 if x>1 else y/2)
            check('letterbox clicks are ignored without clamping onto an edge',command()['session']==before)
        before=command()['session'];command('compare',enabled=True);command('repair_canvas',x=x+w*.4,y=y+h*.4)
        check('before comparison cannot mutate repair records',command()['session']==before);command('compare',enabled=False);settle()
        command('about',visible=True);before=command()['session'];command('repair_canvas',x=x+w*.4,y=y+h*.4);command('repair_remove')
        check('modal overlays block canvas and destructive edit callbacks',command()['session']==before);command('about',visible=False);settle()
        command('repair_select',index=1);command('repair_remove');state=settle();check('remove selected point preserves other records',state['repair']['count']==1 and point(state)==first_point)
        command('undo');state=settle();check('remove is one undoable edit',state['repair']['count']==2)
        command('redo');settle();command('repair_clear');state=settle();check('clear removes records without touching original file',state['repair']['count']==0 and digest(first)==original[str(first)])
        command('undo');state=settle();check('clear is independently undoable',state['repair']['count']==1)
        command('repair_tool',mode=2);state=click(.4,.3);check('clone refuses a new target without a source',state['repair']['count']==1)
        command('repair_pick');state=click(.75,.6);check('source picking does not add a target',state['repair']['count']==1 and state['repair']['has_source'])
        state=click(.4,.3);clone=point(state,1);check('clone stores independently chosen source and target',clone['mode']=='clone' and abs(clone['sx']-(.75*512-.5)/511)<1e-6 and abs(clone['x']-(.4*512-.5)/511)<1e-6)
        command('select',index=1);state=settle();check('photo switch clears transient source and selection',state['repair']['count']==0 and not state['repair']['has_source'] and state['repair']['selected']==-1 and state['view']['pan']==[0.,0.])
        state=click(.5,.5);check('clone source never leaks across photographs',state['repair']['count']==0)
        command('repair_tool',mode=1);state=click(.5,.5);check('portrait-aspect image receives independent repair binding',state['repair']['count']==1 and state['session']['photos'][0]['recipe']['repairs']['binding']!=state['session']['photos'][1]['recipe']['repairs']['binding'])
        command('select',index=2);settle();command('repair_tool',mode=2);command('repair_pick');click(.75,.5)
        changed_pixels=np.asarray(Image.open(changed)).copy();changed_pixels[0,0]=[250,240,230];Image.fromarray(changed_pixels).save(changed)
        state=click(.4,.3);check('changed source cannot accept a stale cached donor',state['repair']['count']==0 and '变化' in state['repair']['status'])
        command('retry_preview');state=settle();check('new source binding clears a primed manual source',not state['repair']['has_source'])
        state=click(.4,.3);check('refreshed clone still requires an explicit new source',state['repair']['count']==0)
        for index,source in enumerate(args.raw,start=3):
            command('select',index=index);settle(90);command('repair_tool',mode=1);command('repair_adjust',parameter='radius',value=1.5);state=click(.5,.5)
            check(f'RAW {index-3} canvas creates an original-size-bound point',state['repair']['count']==1 and point(state,0,index)['mode']=='heal')
            target=export(f'raw-{index}.tif');expected_file=root/f'raw-{index}-oracle.tif'
            subprocess.run([str(args.oracle.resolve()),str(data/'session.json'),str(index),str(expected_file),str(args.dll.resolve())],check=True,capture_output=True)
            check(f'RAW {index-3} full-size export matches captured tool recipe',digest(target)==digest(expected_file))
            arr=tifffile.imread(target);check(f'RAW {index-3} tool export retains true 16-bit detail',arr.dtype==np.uint16 and bool(np.any(arr%257)));del arr
            report['raw_cases'].append(dict(source_sha256=digest(source),output_sha256=digest(target),memory_mib=memory(process.pid)))
        if args.raw:
            # A real full-size RAW export holds the shared compute lock. Queue
            # sampling behind it, then change the photo or explicitly cancel.
            # No artificial production delay or test-only planner is involved.
            command('select',index=3);state=settle(90);command('repair_tool',mode=1)
            saved_raw=state['session']['photos'][3]['recipe'];saved_portrait=state['session']['photos'][1]['recipe']
            pending_output=root/'sampling-race.tif';command('export',destination=str(pending_output),format=2)
            x,y,w,h=command()['view']['image'];command('repair_canvas',x=x+.25*w,y=y+.3*h)
            check('sampling actually waits behind the full-size export',command()['repair']['busy'])
            command('select',index=1);state=settle(90);until(lambda s:pending_output.exists() and not s['export_busy'],90)
            check('late sampling cannot apply to previous or newly selected photo',state['session']['photos'][3]['recipe']==saved_raw and state['session']['photos'][1]['recipe']==saved_portrait)
            command('select',index=3);settle(90);command('repair_tool',mode=1)
            cancelled_output=root/'sampling-cancel.tif';command('export',destination=str(cancelled_output),format=2)
            x,y,w,h=command()['view']['image'];command('repair_canvas',x=x+.25*w,y=y+.3*h)
            check('second sampling is pending before explicit cancellation',command()['repair']['busy'])
            command('repair_cancel');state=until(lambda s:cancelled_output.exists() and not s['export_busy'],90)
            check('cancelled sampling cannot append a repair after completion',state['session']['photos'][3]['recipe']==saved_raw and not state['repair']['busy'])
            check('concurrent source selection never changes frozen export pixels',digest(pending_output)==digest(cancelled_output)==digest(root/'raw-3.tif'))
            modal_output=root/'sampling-modal.tif';command('export',destination=str(modal_output),format=2)
            x,y,w,h=command()['view']['image'];command('repair_canvas',x=x+.25*w,y=y+.3*h)
            check('third sampling is pending before a modal opens',command()['repair']['busy'])
            command('about',visible=True);command('about',visible=False)
            state=until(lambda s:modal_output.exists() and not s['export_busy'],90)
            check('opening and closing a modal invalidates queued sampling',state['session']['photos'][3]['recipe']==saved_raw and not state['repair']['busy'])
            check('modal cancellation also preserves the frozen export',digest(modal_output)==digest(cancelled_output))
        command('select',index=0);state=settle();final=export('final.tif');records=state['session']['photos'][0]['recipe']['repairs']
        report['memory_mib']=memory(process.pid);stop.write_text('stop');check('tool process closes and flushes records',process.wait(timeout=10)==0)
        process,stop,command,until,_=launch(2);state=settle();check('restart restores every repair coordinate and parameter',state['session']['photos'][0]['recipe']['repairs']==records and state['repair']['count']==2)
        check('tool is safely off after restart',state['repair']['mode']==0 and not state['repair']['picking'])
        restored=export('restart.tif');check('restart export reproduces exact pixels',digest(restored)==digest(final))
        check('repair tool does not require an inference runtime','onnxruntime.dll' not in loaded_modules(process.pid))
        stop.write_text('stop');check('restored tool process closes cleanly',process.wait(timeout=10)==0)
        check('app never overwrites original PNG or NEF inputs',all(digest(Path(p))==sha for p,sha in original.items()))
        report.update(success=True,source_commit=marker['source_commit'],source_dirty=marker['source_dirty'],external_mutation_fixture=str(changed))
        args.out.write_text(json.dumps(report,indent=2),encoding='utf8');print(json.dumps(dict(checks=len(report['checks']),success=True)))
    finally:
        for process,stop,log in active:
            if process.poll() is None:
                stop.write_text('stop')
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            log.close()


if __name__=='__main__':main()
