"""Offline suggestion/proof/apply acceptance. No paid service or user photo writes."""
import argparse, json, os, struct, subprocess, tempfile, time, zlib
from pathlib import Path
import numpy as np
from PIL import Image
import tifffile
from smoke_native import digest, wait_json

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    for key in ('exe','fake-codex','out'):ap.add_argument('--'+key,type=Path,required=True)
    ap.add_argument('--raw',type=Path,action='append',default=[])
    ap.add_argument('--dll',type=Path)
    a=ap.parse_args()
    if a.out.exists():ap.error('Choose a new report')
    root=Path(tempfile.mkdtemp(prefix='ai-trial-',dir=Path(__file__).resolve().parents[1]/'.development'))
    photo=root/'photo.png';Image.new('RGB',(480,320),(45,78,104)).save(photo)
    jpg=root/'photo.jpg';Image.new('RGB',(480,320),(45,78,104)).save(jpg)
    wide=root/'rgb16.png'
    # Author standard RGB16 PNG bytes independently of the native decoder.
    def chunk(kind,payload):
        return struct.pack('>I',len(payload))+kind+payload+struct.pack('>I',zlib.crc32(kind+payload)&0xffffffff)
    pixels=np.tile(np.array([12001,20003,28007],dtype='>u2'),(80,120,1))
    scan=b''.join(b'\0'+row.tobytes() for row in pixels)
    wide.write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',120,80,16,2,0,0,0))+chunk(b'IDAT',zlib.compress(scan))+chunk(b'IEND',b''))
    originals={str(p):digest(p) for p in [photo,jpg,wide,*a.raw]}
    active=[];report=dict(checks=[],exe_sha256=digest(a.exe),cloud_requests=0,real_model_quality=False,qa_directory=str(root))
    def check(label,value):
        if not value:raise AssertionError(label)
        report['checks'].append(label)
    def launch(number,inputs):
        folder=root/str(number);folder.mkdir();control=folder/'commands';control.mkdir()
        ready=folder/'ready.json';stop=folder/'stop';count=folder/'requests.txt';data=folder/'data'
        env=os.environ|{'Z30_NATIVE_DATA_DIR':str(data),'Z30_CODEX_PATH':str(a.fake_codex.resolve()),'Z30_FAKE_COUNTER':str(count)}
        env.pop('Z30_FAKE_MODE',None)
        for key in ('Z30_VISION_DIR','Z30_SKIN_DIR','Z30_CONTOUR_DIR'):env.pop(key,None)
        if a.dll:env['Z30_LIBRAW_DLL']=str(a.dll.resolve())
        startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=4
        log=(folder/'process.txt').open('w',encoding='utf8')
        p=subprocess.Popen([str(a.exe.resolve()),'--qa-ready',str(ready),'--qa-stop',str(stop),'--qa-control',str(control),*map(str,inputs)],env=env,cwd=folder,stdout=log,stderr=log,startupinfo=startup)
        active.append((p,stop,log));marker=wait_json(ready,p);seq=0
        def command(action='snapshot',**values):
            nonlocal seq
            seq+=1;path=control/f'{seq:06}.json';tmp=path.with_suffix('.pending');tmp.write_text(json.dumps(dict(action=action,**values)),encoding='utf8');tmp.rename(path)
            return wait_json(path.with_suffix('.result.json'),p,60)
        def until(predicate,timeout=90):
            end=time.monotonic()+timeout
            while time.monotonic()<end:
                s=command()
                if predicate(s):return s
                time.sleep(.02)
            raise TimeoutError(json.dumps(s))
        def calls():return len(count.read_text().splitlines()) if count.exists() else 0
        return p,stop,command,until,calls,data,marker
    try:
        p,stop,command,until,calls,data,marker=launch(1,[photo,jpg])
        def settle():return until(lambda s:s['preview'] and not s['busy'] and not s['ai_busy'] and not s['ai_trial_busy'])
        def recipe(s):return s['session']['photos'][s['session']['selected']]['recipe']
        def export(name):
            dest=root/name;command('export',destination=str(dest),format=2)
            until(lambda s:dest.exists() and not s['export_busy']);return digest(dest)
        def start():
            command('ai_start',goal='自然通透，保留原有色彩',strength=100)
            return until(lambda s:not s['ai_busy'] and s['ai_suggestion_ready'])
        def trial(value):
            command('ai_trial',strength=value)
            return until(lambda s:not s['ai_trial_busy'] and s['ai_trial_ready'])
        settle();command('adjust',parameter='contrast',value=5)
        base=settle();before=recipe(base);fingerprint=base['preview_fingerprint'];baseline=export('baseline.tif')
        check('new UI defaults to suggestions rather than automatic edits',base['ai_mode']==1)
        command('ai_review');s=command()
        check('sending review opens without any model request',s['ai_send_visible'] and calls()==0)
        # Starting via the QA production callback bypasses the modal; ordinary UI
        # users must confirm it. Opening review itself never starts inference.
        s=start();check('one explicit analysis makes exactly one request',calls()==1)
        check('suggestion shows a proof without changing saved parameters',s['ai_trial_ready'] and recipe(s)==before and s['exposure']==0)
        original_proof=s['ai_trial_fingerprint']
        check('trial pixels differ while base preview remains exact',original_proof!=fingerprint and s['preview_fingerprint']==fingerprint)
        check('trial is not serialized in the session draft',json.loads((data/'session.json').read_text())['photos'][0]['recipe']==before)
        check('export before application uses confirmed parameters',export('unapplied.tif')==baseline)
        zero=trial(0);check('zero proof is exactly the saved edit',zero['ai_trial_fingerprint']==fingerprint and recipe(zero)==before)
        half=trial(50);halfprint=half['ai_trial_fingerprint']
        full=trial(100);check('local strength changes actual pixels',halfprint!=full['ai_trial_fingerprint'] and full['ai_trial_fingerprint']!=fingerprint)
        check('returning to full strength uses original advice without cumulative edits',full['ai_trial_fingerprint']==original_proof)
        check('local strength never calls model again',calls()==1)
        command('ai_trial',strength=150);s=command();check('invalid proof strength is rejected',s['ai_trial_strength']==100)
        for strength in (12,83,7,25):command('ai_trial',strength=strength)
        s=until(lambda s:not s['ai_trial_busy'] and s['ai_trial_ready'] and s['ai_trial_strength']==25)
        proof=s['ai_trial_fingerprint'];check('rapid changes coalesce and keep session unchanged',recipe(s)==before and calls()==1)
        command('compare',enabled=True);check('comparison keeps the original editing recipe',recipe(command())==before);command('compare',enabled=False)
        command('ai_apply');s=settle();saved=recipe(s)
        check('confirmation applies the latest proof as one undoable edit',abs(s['exposure']-.175)<1e-5 and s['undo'] and s['preview_fingerprint']==proof)
        check('successful apply clears transient proof',not s['ai_suggestion_ready'] and not s['ai_trial_ready'])
        applied=export('applied.tif');check('confirmed proof changes TIFF16 pixels',applied!=baseline)
        arr=tifffile.imread(root/'applied.tif');check('confirmed output is actual RGB16',arr.dtype==np.uint16 and np.any(arr%257))
        command('undo');s=settle();check('one undo restores exact pre-analysis recipe and pixels',recipe(s)==before and s['preview_fingerprint']==fingerprint)
        check('undo does not claim the old AI edit is still applied','AI 已应用' not in s['ai_status'])
        check('undo export is byte identical',export('undo.tif')==baseline)
        command('redo');s=settle();check('redo restores the exact chosen proof',recipe(s)==saved and s['preview_fingerprint']==proof)
        command('undo');settle();start();trial(50);command('ai_dismiss');s=settle()
        check('discard restores base view and preserves saved edit',not s['ai_trial_ready'] and recipe(s)==before and s['preview_fingerprint']==fingerprint)
        start();command('ai_trial',strength=90);command('adjust',parameter='exposure',value=.2);s=settle()
        check('manual edit invalidates an in-flight local proof',not s['ai_suggestion_ready'] and not s['ai_trial_ready'] and abs(s['exposure']-.2)<1e-5)
        command('undo');settle();start();command('ai_trial',strength=60);command('select',index=1);s=settle();command('ai_apply')
        check('photo switch rejects old analysis and proof',not s['ai_suggestion_ready'] and recipe(s)['parameters']['exposure']==0)
        command('select',index=0);settle();start();command('ai_trial',strength=70);stop.write_text('stop')
        check('exit during local proof is bounded',p.wait(timeout=15)==0)
        p,stop,command,until,calls,data,_=launch(2,[photo])
        s=until(lambda s:s['preview'] and not s['busy']);command('ai_start');until(lambda s:not s['ai_busy'] and s['ai_suggestion_ready'])
        # Replace only our own copied fixture, never any supplied source.
        Image.new('RGB',(480,320),(10,20,30)).save(photo);command('ai_trial',strength=50);command('ai_apply');s=command()
        check('external source replacement cannot apply stale proof',s['exposure']==0 and not s['ai_suggestion_ready'])
        stop.write_text('stop');check('source-error process closes',p.wait(timeout=15)==0)
        for index,source in enumerate([jpg,wide,*a.raw],3):
            p,stop,command,until,calls,data,_=launch(index,[source])
            s=until(lambda s:s['preview'] and not s['busy']);r0=recipe(s);f0=s['preview_fingerprint']
            command('ai_start');until(lambda s:not s['ai_busy'] and s['ai_suggestion_ready'])
            command('ai_trial',strength=50);s=until(lambda s:s['ai_trial_ready'] and not s['ai_trial_busy'])
            check(source.name+': actual decode and trial preserve source recipe',recipe(s)==r0 and calls()==1)
            f1=s['ai_trial_fingerprint'];command('ai_apply');s=command();check(source.name+': apply uses exact rendered trial',s['preview_fingerprint']==f1)
            command('undo');s=until(lambda s:not s['busy']);check(source.name+': undo restores exact decoded preview',s['preview_fingerprint']==f0)
            stop.write_text('stop');check(source.name+': bounded shutdown',p.wait(timeout=15)==0)
        check('all supplied originals remain byte identical',all(digest(Path(path))==sha for path,sha in originals.items() if Path(path)!=photo))
        report.update(success=True,source_commit=marker['source_commit'],source_dirty=marker['source_dirty'])
    except BaseException as error:
        report.update(success=False,error=str(error));raise
    finally:
        for p,stop,log in active:
            if p.poll() is None:
                stop.write_text('stop')
                try:p.wait(timeout=15)
                except subprocess.TimeoutExpired:p.kill();p.wait()
            log.close()
        a.out.write_text(json.dumps(report,indent=2),encoding='utf8')
        print(json.dumps(dict(success=report.get('success'),checks=len(report['checks']))))
if __name__=='__main__':main()
