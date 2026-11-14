"""Small selection, persistent advice, explicit request counts; offline models only."""
import argparse,json,os,subprocess,tempfile,time
from pathlib import Path
from PIL import Image
from smoke_native import digest,wait_json

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    for key in ('exe','fake-codex','out'):ap.add_argument('--'+key,type=Path,required=True)
    ap.add_argument('--raw',type=Path,action='append',default=[])
    ap.add_argument('--dll',type=Path)
    a=ap.parse_args()
    if a.out.exists():ap.error('Choose a new report')
    root=Path(tempfile.mkdtemp(prefix='style-flow-',dir=Path(__file__).resolve().parents[1]/'.development'))
    photos=[]
    for i in range(23):
        p=root/f'{i:02}.png';v={0:100,1:45,2:110,3:220}.get(i,50+i);Image.new('RGB',(240,160),(v,v+1,v+2)).save(p);photos.append(p)
    raw_indices=list(range(len(photos),len(photos)+len(a.raw)));photos.extend(p.resolve() for p in a.raw)
    originals={str(p):digest(p) for p in photos};counter=root/'requests.txt';fail=root/'fail';fail.write_text('fail bright fixture')
    active=[];data=root/'data';report=dict(checks=[],exe_sha256=digest(a.exe),cloud_requests=0,model_quality=False,qa_directory=str(root))
    def check(label,value):
        if not value:raise AssertionError(label)
        report['checks'].append(label)
    def calls():return len(counter.read_text().splitlines()) if counter.exists() else 0
    def book():return json.loads((data/'style-jobs.json').read_text(encoding='utf8'))
    def status(index):return next(s for s in book()['shots'] if Path(s['path']).name==photos[index].name)
    def launch(number):
        folder=root/str(number);folder.mkdir();control=folder/'commands';control.mkdir();ready=folder/'ready.json';stop=folder/'stop'
        env=os.environ|{'Z30_NATIVE_DATA_DIR':str(data),'Z30_CODEX_PATH':str(a.fake_codex.resolve()),'Z30_FAKE_COUNTER':str(counter),'Z30_FAKE_MODE':'style','Z30_FAKE_STYLE_DELAY_MS':'150','Z30_FAKE_FAIL_MARKER':str(fail)}
        if a.dll:env['Z30_LIBRAW_DLL']=str(a.dll.resolve())
        startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=4
        log=(folder/'process.txt').open('w',encoding='utf8');p=subprocess.Popen([str(a.exe.resolve()),'--qa-ready',str(ready),'--qa-stop',str(stop),'--qa-control',str(control),*(map(str,photos) if number==1 else [])],cwd=folder,env=env,stdout=log,stderr=log,startupinfo=startup)
        active.append((p,stop,log));marker=wait_json(ready,p);seq=0
        def command(action='snapshot',**values):
            nonlocal seq
            seq+=1;path=control/f'{seq:06}.json';tmp=path.with_suffix('.pending');tmp.write_text(json.dumps(dict(action=action,**values)),encoding='utf8');tmp.rename(path)
            return wait_json(path.with_suffix('.result.json'),p,30)
        def until(predicate,timeout=90):
            end=time.monotonic()+timeout
            while time.monotonic()<end:
                s=command()
                if predicate(s):return s
                time.sleep(.02)
            raise TimeoutError(json.dumps(s))
        return p,stop,command,until,marker
    try:
        p,stop,command,until,marker=launch(1)
        def settle():return until(lambda s:s['preview'] and not s['busy'] and not s['style_busy'] and not s['ai_trial_busy'])
        def photo(index):command('select',index=index);return settle()
        def recipe(s,index):return s['session']['photos'][index]['recipe']
        def choose(index,value=True):return command('style_choose',index=index,chosen=value)
        def analyze():command('style_review');s=command();assert s['style_confirm'];command('style_start');return until(lambda s:not s['style_running'] and not s['style_busy'])
        s=settle();original=recipe(s,1);command('adjust',parameter='contrast',value=14);command('adjust',parameter='exposure',value=.25);settle();command('style_capture')
        check('reference freezes only parameters and text without a model call',calls()==0 and book()['reference']['parameters']['contrast']==14 and book()['reference']['goal'])
        choose(0);check('reference cannot be selected as a target',command()['style_count']==0)
        choose(1);choose(2);command('style_copy');s=settle()
        check('copy parameters sends zero model requests',calls()==0)
        check('copy applies frozen reference values to both targets',recipe(s,1)['parameters']==book()['reference']['parameters']==recipe(s,2)['parameters'])
        check('copy preserves unrelated recipe fields',all(v==original[k] for k,v in recipe(s,1).items() if k!='parameters'))
        photo(1);command('undo');s=settle();check('first target has its own undo',recipe(s,1)==original and recipe(s,2)['parameters']['contrast']==14)
        photo(2);command('undo');s=settle();check('second target has its own undo',recipe(s,2)==original)
        for i in range(3,21):choose(i)
        choose(21);check('selection cap is enforced without changing library capacity',command()['style_count']==20 and len(s['session']['photos'])==len(photos))
        for i in range(4,21):choose(i,False)
        check('explicit small selection count is three',command()['style_calls']==3)
        command('style_start');check('starting without request review cannot call a provider',calls()==0 and not command()['style_running'])
        command('style_review');s=command();check('request count review performs no inference',s['style_confirm'] and s['style_calls']==3 and calls()==0)
        command('style_start');s=until(lambda s:not s['style_running'] and not s['style_busy'])
        check('one confirmed request per selected target',calls()==3)
        check('failure does not discard successful advice',status(1)['status']==status(2)['status']=='Ready' and status(3)['status']=='Failed')
        check('each image gets independently adapted parameters',status(1)['advice']['adjustments']['exposure']!=status(2)['advice']['adjustments']['exposure'])
        check('analysis alone changes no target recipe',all(recipe(s,i)==original for i in (1,2,3)))
        saved_second=status(2)['advice'];photo(1);base=command()['preview_fingerprint'];command('style_proof');s=until(lambda s:s['ai_suggestion_ready'] and not s['style_busy'])
        check('stored advice becomes a local proof without another request',calls()==3 and s['ai_trial_ready'] and s['exposure']==0)
        command('ai_trial',strength=50);s=until(lambda s:s['ai_trial_ready'] and not s['ai_trial_busy']);fingerprint=s['ai_trial_fingerprint'];command('ai_apply');s=settle()
        check('proof application uses chosen strength and records completion',abs(s['exposure']-.3)<1e-5 and status(1)['status']=='Applied' and s['preview_fingerprint']==fingerprint)
        dest=root/'applied.tif';command('export',destination=str(dest),format=2);until(lambda s:dest.exists() and not s['export_busy'])
        check('chosen target exports actual final pixels',dest.stat().st_size>1000)
        command('undo');s=settle();check('batch advice applies as one independent undo step',recipe(s,1)==original and s['preview_fingerprint']==base)
        command('redo');s=settle();check('redo restores exact chosen proof',s['preview_fingerprint']==fingerprint)
        fail.unlink();check('retry count excludes ready and already confirmed targets',command()['style_calls']==1)
        s=analyze();check('explicit retry requests only the failed photo',calls()==4 and status(3)['status']=='Ready' and status(3)['attempts']==2)
        check('retry preserves prior successful raw advice',status(2)['advice']==saved_second)
        stop.write_text('stop');check('first process closes cleanly',p.wait(timeout=12)==0)
        p,stop,command,until,_=launch(2);s=settle()
        check('restart restores successful suggestions without any model call',calls()==4 and status(2)['advice']==saved_second and not s['style_running'])
        photo(2);command('style_proof');until(lambda s:s['ai_suggestion_ready'] and not s['style_busy']);check('restart can proof saved advice offline',calls()==4)
        command('ai_dismiss');settle();command('style_proof');command('select',index=0);s=settle()
        check('switching photo during proof cannot attach old result',not s['ai_suggestion_ready'] and s['selected']==0)
        command('style_capture');check('new reference preserves the previous batch as an exact backup',bool(list(data.glob('style-backup-*.json'))))
        for i in (4,5,6):choose(i)
        before=calls();command('style_review');command('style_start');command('style_stop',cancel=False);s=until(lambda s:not s['style_running'] and not s['style_busy'])
        check('pause finishes at most the current request',calls()==before+1 and status(5)['status']=='Queued' and status(6)['status']=='Queued')
        choose(6,False);check('resume review excludes unselected pending work',command()['style_calls']==1)
        analyze();check('resume cannot exceed its new confirmed count',calls()==before+2 and status(6)['status']=='Cancelled')
        if raw_indices:
            photo(0);command('style_capture')
            for i in raw_indices:choose(i)
            before=calls();analyze();check('public RAW batch makes only explicitly confirmed calls',calls()==before+len(raw_indices) and all(status(i)['status']=='Ready' for i in raw_indices))
            for i in raw_indices:
                s=photo(i);saved=recipe(s,i);f0=s['preview_fingerprint'];command('style_proof');until(lambda s:s['ai_suggestion_ready'] and not s['style_busy']);command('ai_apply');s=settle()
                check(photos[i].name+': batch advice is an independently undoable real RAW edit',s['undo'] and calls()==before+len(raw_indices))
                dest=root/(photos[i].stem+'.tif');command('export',destination=str(dest),format=2);until(lambda s:dest.exists() and not s['export_busy']);command('undo');s=settle()
                check(photos[i].name+': full RAW export and exact undo',dest.stat().st_size>1000 and recipe(s,i)==saved and s['preview_fingerprint']==f0)
            photo(0)
        command('style_capture');choose(7);choose(8);before=calls();command('style_review');command('style_start');until(lambda s:s['style_busy']);command('style_stop',cancel=True);until(lambda s:not s['style_running'] and not s['style_busy'])
        check('stop cancels queued work and retains an explicit retry state',calls()<=before+1 and all(s['status']=='Cancelled' for s in book()['shots']))
        command('style_capture');choose(9);choose(10);before=calls();command('style_review');command('style_start');until(lambda s:s['style_busy'] and calls()>before)
        p.kill();p.wait(timeout=5);paid_before_restart=calls()
        p,stop,command,until,_=launch(3);s=settle()
        for _ in range(4):command()
        check('crash recovery marks in-flight and pending jobs without restarting requests',calls()==paid_before_restart and all(s['status']=='Cancelled' for s in book()['shots']) and not s['style_running'])
        stop.write_text('stop');check('recovered process closes cleanly',p.wait(timeout=12)==0)
        record=data/'style-jobs.json';record.write_bytes(b'{broken')
        p,stop,command,until,_=launch(4);s=settle();command('style_capture');command('style_start');s=command()
        check('corrupt task records are preserved and cannot trigger inference',record.read_bytes()==b'{broken' and calls()==paid_before_restart and not s['style_running'])
        check('corrupt task history leaves ordinary photo editing available',s['engine_ready'])
        stop.write_text('stop');check('protected process closes cleanly',p.wait(timeout=12)==0)
        check('original input photos are never overwritten',all(digest(Path(p))==sha for p,sha in originals.items()))
        report.update(success=True,source_commit=marker['source_commit'],source_dirty=marker['source_dirty'],model_requests=calls())
    except BaseException as e:report.update(success=False,error=str(e));raise
    finally:
        for p,stop,log in active:
            if p.poll() is None:
                stop.write_text('stop')
                try:p.wait(timeout=12)
                except subprocess.TimeoutExpired:p.kill();p.wait()
            log.close()
        a.out.write_text(json.dumps(report,indent=2),encoding='utf8');print(json.dumps(dict(success=report.get('success'),checks=len(report['checks']))))
if __name__=='__main__':main()
