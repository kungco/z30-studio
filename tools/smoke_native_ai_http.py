"""Exercise the native compatible-image backend against a loopback fake server.

No paid requests. The fake credential is tested in the HTTP header and must not
appear in the request body or temporary files. Uses production Slint callbacks.
"""
import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time

from PIL import Image
from smoke_native_ai import memory, sha, wait_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe',required=True,type=Path)
    parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--raw',type=Path)
    parser.add_argument('--dll',type=Path)
    args=parser.parse_args()
    if not args.exe.is_absolute() or not args.exe.is_file() or args.out.exists() or args.out.suffix!='.json':
        parser.error('use an existing absolute executable and a new JSON report')
    if bool(args.raw)!=bool(args.dll) or any(not p.is_absolute() or not p.is_file() for p in (args.raw,args.dll) if p):
        parser.error('RAW fixture requires an existing absolute source and DLL')
    root=Path(tempfile.mkdtemp(prefix='HTTP 回归 ',dir=Path(__file__).resolve().parents[1]/'.development/native-ai')).resolve()
    photo=root/'不上传的原图.png'
    Image.new('RGB',(720,480),(58,80,109)).save(photo)
    originals={str(photo):sha(photo)}
    if args.raw: originals[str(args.raw)]=sha(args.raw)
    report={'stage':'native-ai-compatible-loopback','exe_sha256':sha(args.exe),'qa_directory':str(root),
            'real_cloud_inference_requests':0,'checks':[],'timings':{},'memory_mib':{},'temp_audits':{}}
    secret='sk-local-test-only'
    def check(label,condition):
        if not condition: raise AssertionError(label)
        report['checks'].append(label)
    try:
        for case in ['auto','invalid','cancel','stale','exit']+(['raw'] if args.raw else []):
            source=args.raw if case=='raw' else photo
            folder=root/case;folder.mkdir();control=folder/'control';control.mkdir();tmp=folder/'tmp';tmp.mkdir()
            ready=folder/'ready.json';stop=folder/'stop.signal';captured={};entered=threading.Event();release=threading.Event()
            class Handler(BaseHTTPRequestHandler):
                def log_message(self,*_):pass
                def do_POST(self,case=case,captured=captured,entered=entered,release=release):
                    size=int(self.headers.get('Content-Length','0'))
                    if size>8_000_000 or self.path!='/v1/chat/completions':
                        self.send_error(400);return
                    body=self.rfile.read(size)
                    captured.update(body=body,authorization=self.headers.get('Authorization'),path=self.path)
                    entered.set()
                    # Keep the request alive at a stable inspection boundary.
                    # On Windows curl exclusively locks its response output;
                    # racing that write/cleanup cannot verify request secrecy.
                    if not release.wait(10):return
                    if case in ('cancel','stale','exit'):time.sleep(5)
                    exposure=9.0 if case=='invalid' else 0.7
                    advice={'title':'本机模拟建议','summary':'保持自然层次','observations':['画面可略提亮'],
                            'adjustments':{'exposure':exposure,'contrast':7,'highlights':-8,'shadows':8,
                                           'temperature':0,'tint':0,'saturation':0,'vibrance':0,
                                           'sharpen':0,'wb':'camera'},'limitations':'仅本机模拟，没有付费推理'}
                    response=json.dumps({'choices':[{'message':{'content':json.dumps(advice)}}]}).encode()
                    try:
                        self.send_response(200);self.send_header('Content-Type','application/json')
                        self.send_header('Content-Length',str(len(response)));self.end_headers();self.wfile.write(response)
                    except (BrokenPipeError,ConnectionResetError):pass
            server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
            server.daemon_threads=True
            serving=threading.Thread(target=server.serve_forever,daemon=True);serving.start()
            env=os.environ.copy();env.update({'Z30_NATIVE_DATA_DIR':str(folder/'data'),'TEMP':str(tmp),'TMP':str(tmp)})
            if args.dll:env['Z30_LIBRAW_DLL']=str(args.dll)
            startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=4
            with (folder/'process.log').open('w',encoding='utf8') as log:
                process=subprocess.Popen([str(args.exe),'--qa-ready',str(ready),'--qa-stop',str(stop),
                    '--qa-control',str(control),str(source)],cwd=folder,env=env,stdout=log,stderr=log,startupinfo=startup)
                seq=0
                def command(action='snapshot',**kwargs):
                    nonlocal seq
                    seq+=1;path=control/f'{seq:06}.json';staging=path.with_suffix('.pending')
                    staging.write_text(json.dumps({'action':action,**kwargs}),encoding='utf8');staging.rename(path)
                    return wait_json(path.with_suffix('.result.json'),process,20)
                def until(predicate,seconds=15):
                    deadline=time.monotonic()+seconds;last=None
                    while time.monotonic()<deadline:
                        last=command()
                        if predicate(last):return last
                        time.sleep(.06)
                    raise TimeoutError(f'{case}: {last}')
                try:
                    wait_json(ready,process)
                    baseline=until(lambda s:s['preview'] and not s['busy'])
                    report['memory_mib'][case+'_ready']=memory(process.pid)
                    check(f'{case}: original referenced unchanged',sha(source)==originals[str(source)])
                    started=time.monotonic()
                    first=command('ai_start',provider=2,base_url=f'http://127.0.0.1:{server.server_port}/v1',
                        model='vision-test',key=secret,goal='自然调色',strength=100,mode=0)
                    check(f'{case}: HTTP job starts',first['ai_busy'])
                    if not entered.wait(10):raise TimeoutError('HTTP mock did not receive request')
                    check(f'{case}: fake API key only in header',captured['authorization']==f'Bearer {secret}'
                        and secret.encode() not in captured['body'])
                    body=json.loads(captured['body'])
                    parts=body['messages'][0]['content']
                    images=[p['image_url']['url'] for p in parts if p['type']=='image_url']
                    dimensions=[]
                    for item in images:
                        if not item.startswith('data:image/jpeg;base64,'):raise AssertionError('non-JPEG image')
                        jpeg=base64.b64decode(item.split(',',1)[1],validate=True)
                        im=Image.open(BytesIO(jpeg));dimensions.append(im.size)
                        if b'Exif' in jpeg or b'GPS' in jpeg:raise AssertionError('preview metadata')
                    check(f'{case}: two bounded images without original path or metadata',len(images)==2
                        and all(max(size)<=1024 for size in dimensions)
                        and source.stem.encode('utf8') not in captured['body'])
                    audited=[p for d in tmp.glob('z30-ai-http-*') for p in d.rglob('*') if p.is_file()]
                    report['temp_audits'][case]=[p.name for p in audited]
                    check(f'{case}: model and JSON schema are sent without a temp-file secret',body['model']=='vision-test'
                        and body['response_format']=={'type':'json_object'} and body['max_tokens']==4096
                        and '"required"' in parts[0]['text']
                        and any(p.name=='request.json' for p in audited)
                        and all(secret.encode() not in p.read_bytes() for p in audited))
                    release.set()
                    if case=='cancel':
                        command('ai_cancel');result=until(lambda s:not s['ai_busy'])
                        check('cancel: no recipe applied',result['exposure']==0)
                    elif case=='stale':
                        command('adjust',parameter='exposure',value=0.2)
                        result=until(lambda s:not s['ai_busy'])
                        check('stale: later edit is retained',abs(result['exposure']-.2)<1e-5)
                    elif case=='exit':
                        stop.write_text('stop');check('exit: network process cancelled before window closes',process.wait(timeout=12)==0)
                    else:
                        result=until(lambda s:not s['ai_busy'])
                        if case=='invalid':check('invalid: model range check rejects bad content',result['exposure']==0 and '失败' in result['ai_status'])
                        else:
                            check(f'{case}: advice applied and undoable',abs(result['exposure']-.7)<1e-5 and result['undo'])
                            command('undo');undone=until(lambda s:not s['busy'])
                            check(f'{case}: undo restores preview',undone['exposure']==0 and undone['preview_fingerprint']==baseline['preview_fingerprint'])
                    report['timings'][case+'_seconds']=round(time.monotonic()-started,3)
                    if process.poll() is None:
                        report['memory_mib'][case+'_after']=memory(process.pid)
                        stop.write_text('stop');check(f'{case}: native process shutdown',process.wait(timeout=12)==0)
                    check(f'{case}: original bytes unchanged',sha(source)==originals[str(source)])
                    check(f'{case}: HTTP request files cleaned',not list(tmp.glob('z30-ai-http-*')))
                finally:
                    release.set()
                    if process.poll() is None:
                        try:stop.write_text('stop');process.wait(timeout=12)
                        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
                    server.shutdown();server.server_close()
        report['success']=True
    except BaseException as error:
        report.update(success=False,error=f'{type(error).__name__}: {error}')
        raise
    finally:
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
