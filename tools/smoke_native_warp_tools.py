"""Real manual-warp callbacks; generated PNGs and optional read-only public NEFs."""
import argparse,json,os,subprocess,tempfile,time
from pathlib import Path
import numpy as np
import tifffile
from PIL import Image
from smoke_native import digest,memory,wait_json
from smoke_native_skin import loaded_modules

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('exe','oracle','out'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--raw',type=Path,action='append',default=[]);parser.add_argument('--dll',type=Path)
    args=parser.parse_args()
    if args.out.exists() or bool(args.raw)!=bool(args.dll):parser.error('New report required; RAW requires DLL')
    root=Path(tempfile.mkdtemp(prefix='warp-tools-',dir=Path(__file__).resolve().parents[1]/'.development'))
    yy,xx=np.mgrid[:384,:512];pixels=np.stack((65+xx//4,60+yy//4,70+(xx+yy)%29),axis=-1).astype(np.uint8);pixels[180:205,246:270]=[15,7,4]
    first=root/'photo.png';portrait=root/'portrait.png';changed=root/'external-change.png'
    Image.fromarray(pixels).save(first);Image.fromarray(pixels.transpose(1,0,2)).save(portrait);Image.fromarray(pixels).save(changed)
    photos=[first,portrait,changed,*[p.resolve() for p in args.raw]];original={str(p):digest(p) for p in [first,portrait,*photos[3:]]}
    report=dict(stage='5F2b manual warp tools',checks=[],exe_sha256=digest(args.exe),qa_directory=str(root),physical_clicks=False,cloud_requests=0,raw_cases=[],export_timings={},drag_to_preview_seconds=[]);data=root/'data';active=[]
    def check(label,value):
        if not value:raise AssertionError(label)
        report['checks'].append(label)
    def launch(number):
        control=root/f'commands-{number}';control.mkdir();ready=root/f'ready-{number}.json';stop=root/f'stop-{number}'
        env=os.environ|{'Z30_NATIVE_DATA_DIR':str(data)}
        for key in ('Z30_LIBRAW_DLL','Z30_VISION_DIR','Z30_SKIN_DIR'):env.pop(key,None)
        startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=4
        log=(root/f'process-{number}.log').open('w',encoding='utf8');process=subprocess.Popen([str(args.exe.resolve()),'--qa-ready',str(ready),'--qa-stop',str(stop),'--qa-control',str(control),*map(str,photos if number==1 else [])],cwd=root,env=env,stdout=log,stderr=log,startupinfo=startup)
        active.append((process,stop,log));marker=wait_json(ready,process);sequence=0
        def command(action='snapshot',**values):
            nonlocal sequence
            sequence+=1;path=control/f'{sequence:06}.json';pending=path.with_suffix('.pending');pending.write_text(json.dumps(dict(action=action,**values)),encoding='utf8');pending.rename(path)
            return wait_json(path.with_suffix('.result.json'),process,30)
        def until(predicate,timeout=60):
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                state=command()
                if predicate(state):return state
                time.sleep(.02)
            raise TimeoutError('Warp tool did not settle: '+json.dumps(state))
        return process,stop,command,until,marker
    try:
        process,stop,command,until,marker=launch(1)
        def settle():return until(lambda s:s['preview'] and not s['busy'])
        def recipe(s,index=None):return s['session']['photos'][s['session']['selected'] if index is None else index]['recipe']
        def coords(s):
            x,y,w,h=s['view']['image'];return x+w*.5,y+h*.5,w,h
        def drag(dx=.08,dy=.03):
            s=settle();x,y,w,h=coords(s);started=time.monotonic();command('warp_pointer',phase=0,x=x,y=y);command('warp_pointer',phase=1,x=x+w*dx,y=y+h*dy);s=settle();report['drag_to_preview_seconds'].append(time.monotonic()-started);return s
        def export(name,verify=False):
            target=root/name;started=time.monotonic();command('export',destination=str(target),format=2);s=until(lambda s:target.exists() and not s['export_busy']);report['export_timings'][name]=time.monotonic()-started
            if verify:
                snapshot=root/(name+'.json');snapshot.write_text(json.dumps(s['session']),encoding='utf8');expected=root/(name+'.oracle.tif');index=s['session']['selected'];extra=[str(args.dll.resolve())] if index>=3 else []
                subprocess.run([str(args.oracle.resolve()),str(snapshot),str(index),str(expected),*extra],check=True,capture_output=True)
                check(name+' matches saved float recipe',digest(target)==digest(expected))
            return target
        state=settle();neutral=export('neutral.tif');check('new project starts without warp records or tool mode',not state['warp_tool']['mode'] and recipe(state).get('warp') is None)
        command('repair_tool',mode=1);command('warp_tool',enabled=True);state=command();check('warp tool disables repair picking',state['warp_tool']['mode'] and state['repair']['mode']==0)
        x,y,w,h=coords(state);before=recipe(state);pan=state['view']['pan'];command('warp_pointer',phase=0,x=x,y=y);state=command();check('pointer down captures a visible drag',state['warp_tool']['dragging'])
        command('warp_pointer',phase=1,x=x,y=y);state=settle();check('a click never adds a deformation',recipe(state)==before and not state['warp_tool']['dragging'])
        command('warp_pointer',phase=0,x=-10,y=-10);command('warp_pointer',phase=1,x=x,y=y);state=settle();check('outside-photo press never starts an edit',recipe(state)==before)
        state=drag();saved=recipe(state)['warp'];pin=saved['pins'][0];check('drag creates a bound record in displayed pixel-centre coordinates',len(saved['pins'])==1 and abs(pin['from'][0]-.5)<1e-6 and abs(pin['from'][1]-.5)<1e-6)
        bw,bh=saved['binding']['width'],saved['binding']['height'];shift=np.hypot((pin['to'][0]-pin['from'][0])*(bw-1),(pin['to'][1]-pin['from'][1])*(bh-1))
        check('large drag is capped and leaves viewport pan unchanged',shift<=.1*pin['radius']*min(bw,bh) and state['view']['pan']==pan and '10%' in state['warp_tool']['status'])
        warped=export('warped.tif',True);check('manual drag actually changes full-size TIFF pixels',digest(warped)!=digest(neutral))
        command('undo');state=settle();check('one drag is exactly one undo step',recipe(state).get('warp') is None);check('undo synchronizes the visible no-warp status','当前无形变' in state['warp_tool']['status']);check('undo restores exact neutral pixels',digest(export('undo.tif'))==digest(neutral))
        command('redo');state=settle();check('redo restores exact controls',recipe(state)['warp']==saved);check('redo restores exact deformed pixels',digest(export('redo.tif'))==digest(warped))
        # Every interrupted gesture must keep the immutable prior controls.
        for change,values,label in [('view',dict(zoom=1.5,pan_x=20,pan_y=-15),'viewport'),('about',dict(visible=True),'modal'),('adjust',dict(parameter='exposure',value=.3),'global edit'),('repair_tool',dict(mode=1),'tool switch')]:
            command('warp_tool',enabled=True);state=settle();x,y,w,h=coords(state);previous=recipe(state)['warp'];command('warp_pointer',phase=0,x=x,y=y);command(change,**values)
            if change=='about':command('about',visible=False)
            command('warp_pointer',phase=1,x=x+w*.04,y=y);state=settle();check(label+' change invalidates the held drag',recipe(state)['warp']==previous and not state['warp_tool']['dragging'])
            if change=='adjust':command('undo');settle()
        command('warp_tool',enabled=True);state=settle();x,y,w,h=coords(state);command('warp_pointer',phase=0,x=x,y=y);command('select',index=1);command('warp_pointer',phase=1,x=x+w*.04,y=y);state=settle();check('photo switch cannot write old drag into a new photo',recipe(state).get('warp') is None and recipe(state,0)['warp']==saved)
        x,y,w,h=coords(state);left=state['view']['image'][0];command('warp_pointer',phase=0,x=left-2,y=y);command('warp_pointer',phase=1,x=x,y=y);state=settle();check('portrait outside-image press is rejected',recipe(state).get('warp') is None)
        state=drag();check('portrait drag is valid after zoom and pan',state['warp_tool']['count']==1);export('portrait.tif',True)
        command('select',index=2);state=settle();x,y,w,h=coords(state);command('warp_pointer',phase=0,x=x,y=y);Image.new('RGB',(512,384),(11,21,31)).save(changed);command('warp_pointer',phase=1,x=x+w*.04,y=y);state=command();check('external source replacement rejects held coordinates',recipe(state).get('warp') is None and not state['warp_tool']['dragging'])
        command('retry_preview');settle()
        command('select',index=0);settle();command('warp_tool',enabled=True)
        for count in (2,3,4):state=drag();check(f'ordered drag {count} appends without erasing earlier controls',state['warp_tool']['count']==count and recipe(state)['warp']['pins'][0]==saved['pins'][0])
        before=recipe(state);state=drag();check('fifth drag is rejected without changing history',recipe(state)==before and not state['warp_tool']['ready'])
        command('adjust',parameter='warp_clear',value=0);state=settle();check('clear removes every manual control',recipe(state).get('warp') is None);check('clear returns exact neutral pixels',digest(export('clear.tif'))==digest(neutral))
        command('undo');state=settle();check('clear has its own undo step',state['warp_tool']['count']==4);command('redo');settle();state=drag();saved=recipe(state)['warp']
        command('adjust',parameter='warp_amount',value=0);state=settle();check('zero strength has an accurate visible status','强度为0' in state['warp_tool']['status']);before=recipe(state);state=drag();check('zero strength retains records and prevents hidden additions',recipe(state)==before and not state['warp_tool']['ready']);check('zero manual strength remains pixel-exact neutral',digest(export('zero.tif'))==digest(neutral));command('undo');settle()
        for index,source in enumerate(photos[3:],3):
            command('select',index=index);settle();command('warp_tool',enabled=True);base=export(f'raw-{index}-neutral.tif');state=drag();check(f'RAW {index} receives one manual drag',state['warp_tool']['count']==1)
            started=time.monotonic();output=export(f'raw-{index}.tif',True);seconds=time.monotonic()-started
            check(f'RAW {index} manual geometry changes full-size output',digest(output)!=digest(base));array=tifffile.imread(output);check(f'RAW {index} output retains real16bit detail',array.dtype==np.uint16 and bool(np.any(array%257)));del array
            report['raw_cases'].append(dict(source_sha256=original[str(source)],output_sha256=digest(output),export_and_oracle_seconds=seconds,memory_mib=memory(process.pid)))
        command('select',index=0);state=settle();final=export('final.tif',True);records=recipe(state)['warp'];report['memory_mib']=memory(process.pid);stop.write_text('stop');check('tool process closes cleanly',process.wait(timeout=10)==0)
        process,stop,command,until,_=launch(2);state=settle();check('restart restores exact manual control records',recipe(state)['warp']==records);check('drag tool starts safely disabled after restart',not state['warp_tool']['mode'] and not state['warp_tool']['dragging']);check('restored manual export is byte-identical',digest(export('restart.tif'))==digest(final));check('manual tools never load an inference DLL','onnxruntime.dll' not in loaded_modules(process.pid));stop.write_text('stop');check('restored process closes cleanly',process.wait(timeout=10)==0)
        check('original PNG and NEF bytes were never overwritten',all(digest(Path(p))==sha for p,sha in original.items()))
        report.update(success=True,source_commit=marker['source_commit'],source_dirty=marker['source_dirty'],external_mutation_fixture=str(changed));args.out.write_text(json.dumps(report,indent=2),encoding='utf8');print(json.dumps(dict(checks=len(report['checks']),success=True)))
    finally:
        for process,stop,log in active:
            if process.poll() is None:
                stop.write_text('stop')
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            log.close()

if __name__=='__main__':main()
