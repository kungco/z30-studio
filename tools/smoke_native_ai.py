"""Offline end-to-end native AI QA through the real Slint callbacks and a fake CLI.

No model inference or original-photo writes. The fake Codex executable lives in
native/crates/desktop/examples and is never included in the portable package.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from PIL import Image
from smoke_native import memory


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wait_json(path, process, seconds=30):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding='utf8'))
            except json.JSONDecodeError:
                pass
        if process.poll() is not None:
            raise AssertionError(f'native process exited {process.returncode} before {path.name}')
        time.sleep(.04)
    raise TimeoutError(str(path))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe',required=True,type=Path)
    parser.add_argument('--fake-codex',required=True,type=Path)
    parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--raw',action='append',type=Path,default=[],help='Optional public Z30 NEF fixture; may be repeated')
    parser.add_argument('--dll',type=Path,help='Explicit LibRaw DLL when testing RAW')
    args=parser.parse_args()
    if args.out.exists() or args.out.suffix!='.json':
        parser.error('choose a new JSON report path')
    for path in (args.exe,args.fake_codex):
        if not path.is_absolute() or not path.is_file():
            parser.error('both executable paths must be existing absolute files')
    if bool(args.raw)!=bool(args.dll) or any(not path.is_absolute() or not path.is_file() for path in args.raw):
        parser.error('RAW fixtures require existing absolute paths and one --dll')
    workspace=Path(__file__).resolve().parents[1]/'.development/native-ai'
    workspace.mkdir(parents=True,exist_ok=True)
    root=Path(tempfile.mkdtemp(prefix='AI 原生回归 ',dir=workspace)).resolve()
    original=root/'Z30-私密原图.png'
    Image.new('RGB',(720,480),(45,78,104)).save(original)
    gray=root/'Z30-中性灰卡.png'
    Image.new('RGB',(720,480),(130,130,130)).save(gray)
    hashes={str(original):sha(original),str(gray):sha(gray)}
    for raw in args.raw: hashes[str(raw)]=sha(raw)
    report={'stage':'native-ai-offline-callback-process','exe_sha256':sha(args.exe),
        'fake_cli_sha256':sha(args.fake_codex),'qa_directory':str(root),
        'real_cloud_inference_requests':0,'physical_clicks_verified':False,
        'checks':[],'timings':{},'memory_mib':{}}

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        report['checks'].append(name)

    raw_cases={f'raw_{i}':path for i,path in enumerate(args.raw,1)}
    cases=['auto','suggest','invalid','quality','cancel','stale','exit',*raw_cases]
    try:
        for case in cases:
            photo=raw_cases.get(case,gray if case=='quality' else original)
            folder=root/case;folder.mkdir()
            control=folder/'control';control.mkdir()
            tmp=folder/'tmp';tmp.mkdir()
            ready=folder/'ready.json';stop=folder/'stop.signal';trace=folder/'cli-trace.json'
            environment=os.environ.copy()
            environment.update({
                'Z30_NATIVE_DATA_DIR':str(folder/'data'),
                'Z30_CODEX_PATH':str(args.fake_codex),
                'Z30_FAKE_TRACE':str(trace),
                'Z30_FAKE_FORBIDDEN':photo.stem,
                'TEMP':str(tmp),'TMP':str(tmp),
            })
            if args.dll: environment['Z30_LIBRAW_DLL']=str(args.dll)
            if case in ('cancel','stale','exit'):
                environment['Z30_FAKE_MODE']='slow'
            elif case=='invalid':
                environment['Z30_FAKE_MODE']='invalid'
            elif case=='quality':
                environment['Z30_FAKE_MODE']='warm'
            else:
                environment.pop('Z30_FAKE_MODE',None)
            startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow=4
            with (folder/'process.log').open('w',encoding='utf8') as log:
                process=subprocess.Popen([str(args.exe),'--qa-ready',str(ready),
                    '--qa-stop',str(stop),'--qa-control',str(control),str(photo)],
                    cwd=folder,env=environment,stdout=log,stderr=log,startupinfo=startup)
                seq=0
                def command(action='snapshot',**kwargs):
                    nonlocal seq
                    seq+=1
                    path=control/f'{seq:06}.json'
                    staging=path.with_suffix('.pending')
                    staging.write_text(json.dumps(dict(action=action,**kwargs)),encoding='utf8')
                    staging.rename(path)
                    return wait_json(path.with_suffix('.result.json'),process,20)
                def until(predicate,seconds=15):
                    deadline=time.monotonic()+seconds
                    snapshot=None
                    while time.monotonic()<deadline:
                        snapshot=command()
                        if predicate(snapshot):return snapshot
                        time.sleep(.06)
                    raise TimeoutError(f'{case} did not settle: {snapshot}')
                try:
                    wait_json(ready,process)
                    baseline=until(lambda s:s['preview'] and not s['busy'])
                    report['memory_mib'][case+'_ready']=memory(process.pid)
                    check(f'{case}: original image is only referenced',sha(photo)==hashes[str(photo)])
                    mode=1 if case=='suggest' else 0
                    strength=50 if case=='suggest' else 100
                    started=time.monotonic()
                    first=command('ai_start',mode=mode,strength=strength,goal='自然调色')
                    check(f'{case}: AI process started in the native UI',first['ai_busy'])
                    if case in ('cancel','stale','exit'):
                        wait_json(trace,process)
                    if case=='cancel':
                        command('ai_cancel')
                        result=until(lambda s:not s['ai_busy'],8)
                        check('cancel: child stops and no edits are applied',
                            result['exposure']==0 and '取消' in result['ai_status'])
                    elif case=='stale':
                        command('adjust',parameter='exposure',value=0.2)
                        result=until(lambda s:not s['ai_busy'] and abs(s['exposure']-0.2)<1e-5,8)
                        check('stale: an edit cancels old advice without overwriting it',
                            abs(result['session']['photos'][0]['recipe']['parameters']['exposure']-0.2)<1e-5)
                    elif case=='exit':
                        stop.write_text('stop')
                        check('exit: active CLI stops and native process exits',process.wait(timeout=12)==0)
                    else:
                        result=until(lambda s:not s['ai_busy'],20)
                        trace_info=wait_json(trace,process)
                        check(f'{case}: two metadata-free bounded previews and strict schema reach the CLI fixture',
                            trace_info['schema_fields']==10 and len(trace_info['image_dimensions'])==2
                            and all(max(size)<=1024 for size in trace_info['image_dimensions'])
                            and trace_info['read_only'])
                        if case.startswith('raw_'):
                            check(f'{case}: actual NEF preview enters AI and returns a safe edit or quality fallback',
                                result['preview'] and result['engine_ready']
                                and result['neutral_fingerprint']==baseline['neutral_fingerprint']
                                and ('AI 已应用' in result['ai_status'] or '保持当前' in result['ai_status']))
                        elif case=='auto':
                            check('auto: bounded advice is applied and undoable',
                                abs(result['exposure']-0.7)<1e-5 and result['undo']
                                and result['neutral_fingerprint']==baseline['neutral_fingerprint'])
                            undone=command('undo')
                            undone=until(lambda s:s['preview'] and not s['busy'])
                            check('auto: undo restores original recipe and pixels',
                                undone['exposure']==0 and undone['preview_fingerprint']==baseline['preview_fingerprint'])
                        elif case=='suggest':
                            check('suggest: no edit before explicit apply',
                                result['exposure']==0 and result['ai_suggestion_ready'])
                            applied=command('ai_apply')
                            check('suggest: one apply records a single undo step',
                                abs(applied['exposure']-0.35)<1e-5 and applied['undo'])
                        elif case=='quality':
                            check('quality: excessive warmth on a neutral gray image is reduced after pixel inspection',
                                0<=result['temperature']<25 and '回退' in result['ai_status'])
                        elif case=='invalid':
                            check('invalid: out-of-range model data fails closed',
                                result['exposure']==0 and '失败' in result['ai_status']
                                and not result['ai_suggestion_ready'])
                    report['timings'][case+'_seconds']=round(time.monotonic()-started,3)
                    if process.poll() is None:
                        report['memory_mib'][case+'_after']=memory(process.pid)
                    if process.poll() is None:
                        stop.write_text('stop')
                        check(f'{case}: process shutdown',process.wait(timeout=12)==0)
                    check(f'{case}: no original bytes changed',sha(photo)==hashes[str(photo)])
                    check(f'{case}: model temporary JPEGs cleaned',not list(tmp.glob('z30-ai-*')))
                finally:
                    if process.poll() is None:
                        try:
                            stop.write_text('stop')
                            process.wait(timeout=12)
                        except subprocess.TimeoutExpired:
                            process.kill();process.wait(timeout=5)
        report['success']=True
    except BaseException as error:
        report.update(success=False,error=f'{type(error).__name__}: {error}')
        raise
    finally:
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
