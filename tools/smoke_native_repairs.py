"""Saved repair recipes in real Slint preview/export callbacks; no mouse tool yet."""
import argparse,json,os,subprocess,tempfile,time
from pathlib import Path
import numpy as np
import tifffile
from PIL import Image
from smoke_native import digest,memory,wait_json
from smoke_native_skin import loaded_modules


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('exe','probe','out'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--raw',type=Path,action='append',default=[])
    parser.add_argument('--dll',type=Path)
    args=parser.parse_args()
    if args.out.exists():parser.error('Choose a new report')
    if bool(args.raw)!=bool(args.dll):parser.error('--raw and --dll must be used together')
    root=Path(tempfile.mkdtemp(prefix='repairs-window-',dir=Path(__file__).resolve().parents[1]/'.development'))
    yy,xx=np.mgrid[:192,:256];pixels=np.stack((80+xx//3,60+yy//4,70+(xx+yy)%19),axis=-1).astype(np.uint8);pixels[90:104,120:136]=[12,7,4]
    photo=root/'source.png';Image.fromarray(pixels).save(photo);original=digest(photo)
    data=root/'data';subprocess.run([str(args.probe.resolve()),str(photo),str(data)],check=True,capture_output=True)
    report=dict(stage='5E2a repair recipes and rendering',checks=[],exe_sha256=digest(args.exe),qa_directory=str(root),cloud_requests=0,mouse_repair_tool=False)
    active=[]
    def check(name,value):
        if not value:raise AssertionError(name)
        report['checks'].append(name)
    def launch(data,number):
        control=root/f'commands-{number}';control.mkdir();ready=root/f'ready-{number}.json';stop=root/f'stop-{number}'
        env=os.environ|{'Z30_NATIVE_DATA_DIR':str(data)}
        for key in ('Z30_LIBRAW_DLL','Z30_VISION_DIR','Z30_SKIN_DIR'):env.pop(key,None)
        startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=4
        log=(root/f'process-{number}.txt').open('w',encoding='utf8')
        process=subprocess.Popen([str(args.exe.resolve()),'--qa-ready',str(ready),'--qa-stop',str(stop),'--qa-control',str(control)],cwd=root,env=env,stdout=log,stderr=log,startupinfo=startup)
        active.append((process,stop,log));marker=wait_json(ready,process);sequence=0
        def command(action='snapshot',**values):
            nonlocal sequence
            sequence+=1;path=control/f'{sequence:06}.json';pending=path.with_suffix('.pending')
            pending.write_text(json.dumps(dict(action=action,**values)),encoding='utf8');pending.rename(path)
            return wait_json(path.with_suffix('.result.json'),process)
        def until(predicate,timeout=30):
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                state=command()
                if predicate(state):return state
                time.sleep(.02)
            raise TimeoutError('Repair window did not settle')
        return process,stop,command,until,marker
    try:
        process,stop,command,until,marker=launch(data,1)
        state=until(lambda s:s['preview'] and not s['busy']);recipe=state['session']['photos'][0]['recipe'];saved=recipe['repairs']
        check('v7 source-bound repair recipe loads in actual window',state['session']['version']==7 and len(saved['points'])==1)
        check('repair-only restoration does not load inference DLL','onnxruntime.dll' not in loaded_modules(process.pid))
        def export(name,expected):
            target=root/name;command('export',destination=str(target),format=2);until(lambda s:target.exists() and not s['export_busy'])
            return digest(target)==digest(data/expected)
        check('actual full-size TIFF16 matches core repair recipe',export('first.tif','expected.tif'))
        command('adjust',parameter='exposure',value=.3);state=until(lambda s:not s['busy'])
        check('global edit keeps original repair coordinates',state['session']['photos'][0]['recipe']['repairs']==saved)
        command('undo');until(lambda s:not s['busy']);check('undo returns to exact repaired TIFF16',export('undo.tif','expected.tif'))
        command('reset');state=until(lambda s:not s['busy']);check('reset removes repair recipe',state['session']['photos'][0]['recipe'].get('repairs') is None)
        check('reset exports exact neutral TIFF16',export('reset.tif','default.tif'))
        command('undo');until(lambda s:not s['busy']);check('undo reset restores exact repair TIFF16',export('restored.tif','expected.tif'))
        output=root/'batch';output.mkdir();command('batch_start',directory=str(output),format=2,mode=0)
        state=until(lambda s:len(s['batch']['jobs'])==1 and s['batch']['jobs'][0]['state']=='done')
        check('frozen batch uses same source-bound repair recipe',digest(Path(state['batch']['jobs'][0]['destination']))==digest(data/'expected.tif'))
        report['memory_mib']=memory(process.pid);stop.write_text('stop');check('repair process closes cleanly',process.wait(timeout=10)==0)
        process,stop,command,until,_=launch(data,2);state=until(lambda s:s['preview'] and not s['busy'])
        check('restart restores exact immutable repair records',state['session']['photos'][0]['recipe']['repairs']==saved)
        check('restart export remains exact',export('restart.tif','expected.tif'))
        stop.write_text('stop');check('restored process closes cleanly',process.wait(timeout=10)==0)
        heavy=root/'heavy.png';Image.fromarray(np.tile(pixels,(7,7,1))[:1200,:1800]).save(heavy)
        heavy_data=root/'heavy-data';subprocess.run([str(args.probe.resolve()),str(heavy),str(heavy_data),'heavy'],check=True,capture_output=True)
        process,stop,command,until,_=launch(heavy_data,3);until(lambda s:s['busy'])
        started=time.monotonic();command('cancel_preview');command('reset');until(lambda s:s['preview'] and not s['busy'])
        report['cancel_to_default_preview_seconds']=time.monotonic()-started
        check('cancelled heavy repair yields to new neutral preview',report['cancel_to_default_preview_seconds']<10)
        stop.write_text('stop');check('cancelled repair process closes cleanly',process.wait(timeout=10)==0)
        report['raw_cases']=[]
        for index,source in enumerate(args.raw):
            source=source.resolve();source_hash=digest(source);raw_data=root/f'raw-{index}'
            subprocess.run([str(args.probe.resolve()),str(source),str(raw_data),str(args.dll.resolve())],check=True,capture_output=True)
            check(f'RAW {index} repair changes full-size TIFF pixels',digest(raw_data/'expected.tif')!=digest(raw_data/'default.tif'))
            process,stop,command,until,_=launch(raw_data,4+index);until(lambda s:s['preview'] and not s['busy'])
            target=root/f'raw-{index}.tif';command('export',destination=str(target),format=2)
            until(lambda s:target.exists() and not s['export_busy'],90)
            check(f'RAW {index} window TIFF16 matches float repair recipe',digest(target)==digest(raw_data/'expected.tif'))
            array=tifffile.imread(target)
            check(f'RAW {index} repaired output retains true RGB16 detail',array.dtype==np.uint16 and bool(np.any(array%257)))
            del array
            report['raw_cases'].append(dict(source_sha256=source_hash,output_sha256=digest(target),memory_mib=memory(process.pid)))
            stop.write_text('stop');check(f'RAW {index} process closes cleanly',process.wait(timeout=10)==0)
            check(f'RAW {index} original NEF SHA unchanged',digest(source)==source_hash)
        check('original synthetic source was never overwritten',digest(photo)==original)
        report.update(success=True,source_commit=marker['source_commit'],source_dirty=marker['source_dirty'])
        args.out.write_text(json.dumps(report,indent=2),encoding='utf8');print(json.dumps(dict(checks=len(report['checks']),success=True)))
    finally:
        for process,stop,log in active:
            if process.poll() is None:
                stop.write_text('stop')
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            log.close()


if __name__=='__main__':main()
