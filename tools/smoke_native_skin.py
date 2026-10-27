"""Real Slint callbacks: semantic skin persistence, export and failure lifecycle.

Public image copies only. File dialogs use the existing QA mechanism. This is
not physical-click, diverse-skin-tone, real-cloud or clean-Windows validation.
"""
import argparse,json,os,shutil,subprocess,tempfile,time,ctypes
from pathlib import Path
import numpy as np
from PIL import Image
import tifffile
from smoke_native import digest,memory,wait_json


def loaded_modules(pid):
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.K32EnumProcessModulesEx.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.HMODULE),wintypes.DWORD,ctypes.POINTER(wintypes.DWORD),wintypes.DWORD]
    kernel.K32GetModuleFileNameExW.argtypes=[wintypes.HANDLE,wintypes.HMODULE,wintypes.LPWSTR,wintypes.DWORD]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.OpenProcess(0x0400|0x0010,False,pid)
    if not handle:raise ctypes.WinError(ctypes.get_last_error())
    try:
        modules=(wintypes.HMODULE*512)();needed=wintypes.DWORD()
        if not kernel.K32EnumProcessModulesEx(handle,modules,ctypes.sizeof(modules),ctypes.byref(needed),3):raise ctypes.WinError(ctypes.get_last_error())
        if needed.value>ctypes.sizeof(modules):raise RuntimeError('Module inventory exceeds bound')
        names=[]
        for module in modules[:needed.value//ctypes.sizeof(wintypes.HMODULE)]:
            name=ctypes.create_unicode_buffer(32768)
            if not kernel.K32GetModuleFileNameExW(handle,module,name,len(name)):raise ctypes.WinError(ctypes.get_last_error())
            names.append(Path(name.value).name.lower())
        return names
    finally:kernel.CloseHandle(handle)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('exe','assets','skin','image','out'):parser.add_argument('--'+key,type=Path,required=True)
    parser.add_argument('--bundled',action='store_true')
    args=parser.parse_args()
    if args.out.exists():parser.error('Report already exists')
    root=Path(tempfile.mkdtemp(prefix='皮肤窗口 ',dir=Path(__file__).resolve().parents[1]/'.development')).resolve()
    first=root/'人像.png';second=root/'背景.png';shutil.copyfile(args.image,first);Image.new('RGB',(512,320),(70,90,110)).save(second)
    originals={str(p):digest(p) for p in (args.image,first,second)}
    data=root/'data';data.mkdir()
    old=json.dumps(dict(version=3,photos=[dict(path=str(p),recipe={}) for p in (first,second)],selected=0),ensure_ascii=False).encode('utf8')
    (data/'session.json').write_bytes(old)
    env=os.environ|{'Z30_NATIVE_DATA_DIR':str(data)}
    env.pop('Z30_SKIN_DIR',None);env.pop('Z30_VISION_DIR',None)
    base=root/'no-model-app';base.mkdir();shutil.copyfile(args.exe,base/'Z30Studio.exe')
    report=dict(stage='5C2-native-skin-ui',checks=[],exe_sha256=digest(args.exe),qa_directory=str(root),cloud_requests=0,physical_clicks_verified=False)
    children=[]
    def check(label,condition):
        if not condition:raise AssertionError(label)
        report['checks'].append(label)
    def launch(number,base_app=False):
        control=root/f'commands-{number}';control.mkdir();ready=root/f'ready-{number}.json';stop=root/f'stop-{number}'
        log=(root/f'process-{number}.log').open('w',encoding='utf8');exe=base/'Z30Studio.exe' if base_app else args.exe.resolve()
        startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=4
        process=subprocess.Popen([str(exe),'--qa-ready',str(ready),'--qa-stop',str(stop),'--qa-control',str(control)],cwd=root,env=env,stdout=log,stderr=log,startupinfo=startup)
        children.append((process,stop,log));marker=wait_json(ready,process);sequence=0
        def command(action='snapshot',**values):
            nonlocal sequence
            sequence+=1;path=control/f'{sequence:06}.json';pending=path.with_suffix('.pending')
            pending.write_text(json.dumps(dict(action=action,**values)),encoding='utf8');pending.rename(path)
            return wait_json(path.with_suffix('.result.json'),process,30)
        def until(predicate,timeout=45):
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                state=command()
                if predicate(state):return state
                time.sleep(.05)
            raise TimeoutError(json.dumps({k:v for k,v in state.items() if k not in ('session','batch')},ensure_ascii=False))
        until(lambda s:s['preview'] and not s['busy'])
        report.setdefault('builds',[]).append(marker)
        return process,stop,command,until
    def assets(command,until,bundled=False):
        if not bundled:
            command('face_assets',directory=str(args.assets.resolve()));until(lambda s:not s['face_busy'])
            command('skin_assets',directory=str(args.skin.resolve()))
        return until(lambda s:not s['face_busy'] and s['skin_assets_ready'] and s['face_assets_ready'])
    try:
        process,stop,command,until=launch(1)
        initial=command();baseline=initial['preview_fingerprint']
        check('v3 draft migrates to v4 and backs up original bytes',initial['session']['version']==4 and any(p.read_bytes()==old for p in data.glob('session-v3-backup-*.json')))
        assets(command,until,args.bundled);command('detect_face');until(lambda s:not s['face_busy'] and s['has_face'])
        start=time.monotonic();command('segment_skin');masked=until(lambda s:not s['face_busy'] and not s['busy'] and s['has_skin_mask'])
        report['first_segmentation_seconds']=time.monotonic()-start
        mask=masked['session']['photos'][0]['recipe']['skin']
        check('explicit segmentation saves a bounded mask without applying strength',0<len(mask['weights'])<=87384 and masked['skin_smooth']==0 and masked['preview_fingerprint']==baseline)
        check('mask overlay is available with neutral strength',masked['show_skin_mask'])
        command('adjust',parameter='skin_smooth',value=90);smoothed=until(lambda s:not s['busy'])
        check('semantic smoothing changes actual float-rendered preview pixels',smoothed['preview_fingerprint']!=baseline)
        command('undo');zero=until(lambda s:not s['busy'])
        check('strength undo retains accepted skin area',zero['has_skin_mask'] and zero['skin_smooth']==0)
        command('undo');removed=until(lambda s:not s['busy'])
        check('one further undo removes segmentation and retains face',not removed['has_skin_mask'] and removed['has_face'])
        command('redo');until(lambda s:not s['busy']);command('redo');restored=until(lambda s:not s['busy'])
        check('redo restores identical mask and smoothed pixels',restored['preview_fingerprint']==smoothed['preview_fingerprint'] and restored['session']['photos'][0]['recipe']['skin']==mask)
        command('show_skin_mask',visible=False);hidden=command();command('show_skin_mask',visible=True);shown=command()
        check('mask visibility does not bake overlay into rendered pixels',hidden['preview_fingerprint']==shown['preview_fingerprint'])
        command('compare',enabled=True);compared=command();command('compare',enabled=False)
        check('before comparison still uses neutral image',compared['compare'] and compared['neutral_fingerprint']!=compared['preview_fingerprint'])
        output=root/'batch';output.mkdir();command('batch_start',directory=str(output),format=2,mode=1)
        batch=until(lambda s:len(s['batch']['jobs'])==2 and all(j['state']=='done' for j in s['batch']['jobs']))
        check('shared batch captures first photo semantic mask',batch['batch']['jobs'][0]['recipe']['skin']==mask)
        check('shared batch never transfers skin mask to another photo',batch['batch']['jobs'][1]['recipe'].get('skin') is None)
        exported=Path(batch['batch']['jobs'][0]['destination']);pixels=tifffile.imread(exported)
        check('semantic batch writes full-size real 16-bit TIFF',pixels.shape==(512,512,3) and pixels.dtype==np.uint16 and np.any(pixels%257))
        report['memory_mib']=memory(process.pid);stop.write_text('stop');check('first skin process closes cleanly',process.wait(timeout=30)==0)
        process,stop,command,until=launch(2,True);saved=command()
        check('restart without model folders restores identical skin effect',saved['has_skin_mask'] and not saved['skin_assets_ready'] and not saved['face_assets_ready'] and saved['preview_fingerprint']==smoothed['preview_fingerprint'])
        modules=loaded_modules(process.pid);check('restoring saved mask does not load ONNX runtime','onnxruntime.dll' not in modules)
        report['restored_without_runtime_modules']=modules
        destination=root/'restored.tif';command('export',destination=str(destination),format=2)
        until(lambda s:not s['export_busy'] and destination.exists())
        check('model-free restored export matches prior semantic batch pixels',np.array_equal(tifffile.imread(destination),pixels))
        command('reset');until(lambda s:not s['busy']);command('undo');recovered=until(lambda s:not s['busy'])
        check('reset and undo restore persisted semantic mask',recovered['session']['photos'][0]['recipe']['skin']==mask)
        assets(command,until)
        bad=root/'bad-model';bad.mkdir();blob=bytearray((args.skin/'selfie_multiclass_256x256.onnx').read_bytes());blob[-1]^=1;(bad/'selfie_multiclass_256x256.onnx').write_bytes(blob)
        command('skin_assets',directory=str(bad));until(lambda s:not s['face_busy']);command('segment_skin')
        rejected=until(lambda s:not s['face_busy'] and 'SHA-256' in s['face_status'])
        check('same-length corrupted model is rejected and prior mask preserved',rejected['session']['photos'][0]['recipe']['skin']==mask)
        command('skin_assets',directory=str(args.skin.resolve()));until(lambda s:not s['face_busy']);command('segment_skin');command('select',index=1)
        switched=until(lambda s:s['selected']==1 and not s['face_busy'] and not s['busy'])
        check('switching photos cannot apply old skin result to next photo',not switched['has_skin_mask'] and switched['session']['photos'][1]['recipe'].get('skin') is None)
        command('select',index=0);until(lambda s:not s['busy']);command('segment_skin');command('cancel_face')
        cancelled=until(lambda s:not s['face_busy'] and not s['busy'])
        check('cancellation preserves saved mask and source binding',cancelled['session']['photos'][0]['recipe']['skin']==mask)
        command('segment_skin');start=time.monotonic();stop.write_text('stop');check('close with segmentation requested exits cleanly',process.wait(timeout=30)==0)
        report['close_seconds']=time.monotonic()-start
        check('all public input and test source copies stay byte-identical',all(digest(Path(path))==value for path,value in originals.items()))
        check('successful exports leave no part files',not list(output.glob('*.part')) and not list(root.glob('*.part')))
        report['success']=True
    except BaseException as error:
        report.update(success=False,error=f'{type(error).__name__}: {error}');raise
    finally:
        for process,stop,log in children:
            if process.poll() is None:
                stop.write_text('stop')
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            log.close()
        args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        print(json.dumps(dict(success=report.get('success'),checks=len(report['checks']),report=str(args.out))))


if __name__=='__main__':main()
