"""Exercise the extracted EXE, not source imports; no cloud inference or original writes."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

import numpy as np
from PIL import Image, ImageCms
import tifffile


class MockVision(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_POST(self):
        payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.calls.append(payload)
        controls=dict(exposure=.2,contrast=0,highlights=0,shadows=0,temperature=0,tint=0,saturation=0,vibrance=0,sharpen=0,wb='camera')
        result=dict(title='本地接口验收',summary='模拟服务，无云端调用',observations=['只用于协议验收'],limitations='未验证 DeepSeek 云端效果',adjustments=controls)
        body=json.dumps(dict(choices=[dict(message=dict(content=json.dumps(result)),finish_reason='stop')])).encode()
        self.send_response(200);self.end_headers();self.wfile.write(body)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--zip',type=Path,required=True)
    p.add_argument('--samples',type=Path)
    p.add_argument('--deepseek-network-check',action='store_true',help='GET official models with a deliberately invalid key; no images/inference')
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    report={'model_requests':0,'raw':[]}
    with tempfile.TemporaryDirectory(prefix='z30-便携 验收-') as folder:
        root=Path(folder)
        with zipfile.ZipFile(args.zip) as archive:archive.extractall(root)
        exe=root/'Z30-Studio'/'Z30-Studio.exe'
        data=root/'独立 数据';cwd=root/'任意 工作目录';cwd.mkdir()
        env=dict(os.environ,Z30_DATA_DIR=str(data),PATH=os.path.join(os.environ['SystemRoot'],'System32'))
        for key in ('PYTHONPATH','PYTHONHOME','PYTHONSTARTUP'):env.pop(key,None)
        owner=secrets.token_hex(16)
        process=None;stop=None;url=None
        def start(index):
            nonlocal process,stop,url
            ready=root/f'ready{index}.json';stop=root/f'stop{index}.signal'
            process=subprocess.Popen([str(exe),'--headless','--no-browser','--port','0','--ready-file',str(ready),'--stop-file',str(stop)],cwd=cwd,env=env)
            deadline=time.monotonic()+60
            while not ready.exists():
                if process.poll() is not None:raise RuntimeError('EXE exited: '+str(process.returncode))
                if time.monotonic()>deadline:raise TimeoutError('EXE startup timed out')
                time.sleep(.1)
            url=json.loads(ready.read_text(encoding='utf8'))['url']
        def end():
            if process and process.poll() is None:
                stop.write_text('stop',encoding='ascii')
                try:assert process.wait(timeout=30)==0
                except subprocess.TimeoutExpired:process.kill();process.wait();raise
        def get(route):
            request=urllib.request.Request(url+route,headers={'X-Z30-Client':owner})
            with urllib.request.urlopen(request,timeout=120) as response:return response.read()
        def post(route,value,binary=False,name='fixture.png'):
            body=value if binary else json.dumps(value).encode()
            request=urllib.request.Request(url+route,data=body,headers={'X-Z30-Request':'1','X-Z30-Client':owner,'X-Filename':urllib.parse.quote(name)})
            with urllib.request.urlopen(request,timeout=180) as response:return response.read()
        try:
            start(1)
            report['health']=json.loads(get('/api/health'))
            assert b'Z30 Studio' in get('/')
            for path in ('/app.js','/model-settings.js','/history.js','/batch-grade.js','/auto-grade.js','/style.css','/assistant.css'):
                assert len(get(path))>100
            report['static_assets']=True
            report['codex_status']=json.loads(get('/api/codex/status'))
            duplicate=subprocess.run([str(exe),'--headless','--no-browser'],cwd=cwd,env=env,timeout=20)
            assert duplicate.returncode==2;report['single_instance']=True
            b=io.BytesIO();Image.new('RGB',(320,240),(140,100,70)).save(b,'PNG',icc_profile=ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes())
            photo=json.loads(post('/api/open',b.getvalue(),True,'验收 RGB.png'))
            for fmt in ('JPEG','PNG','TIFF16'):
                output=post('/api/export',dict(id=photo['id'],format=fmt,settings={'exposure':.2}))
                if fmt=='TIFF16':assert tifffile.imread(io.BytesIO(output)).dtype==np.uint16
                else:assert Image.open(io.BytesIO(output)).size==(320,240)
            report['raster_exports']=['JPEG','PNG','TIFF16']
            job=json.loads(post('/api/exports/create',dict(ids=[photo['id']],format='PNG',settings={},protect=True)))['job']
            deadline=time.monotonic()+30
            while json.loads(get('/api/exports/'+job))['state'] in ('queued','running'):
                if time.monotonic()>deadline:raise TimeoutError('Background export')
                time.sleep(.1)
            with zipfile.ZipFile(io.BytesIO(get('/api/exports/'+job+'/download'))) as z:assert any(n.endswith('.png') for n in z.namelist())
            assert (data/'exports').is_dir()
            mock=ThreadingHTTPServer(('127.0.0.1',0),MockVision);mock.calls=[]
            thread=threading.Thread(target=mock.serve_forever,daemon=True);thread.start()
            try:
                config=json.loads(post('/api/ai/config',dict(provider='compatible',base_url=f'http://127.0.0.1:{mock.server_port}',model='vision-test',api_key='local-smoke-not-a-secret')))
                job_ai=json.loads(post('/api/codex/auto',dict(id=photo['id'],settings={},backend_revision=config['revision'],request_id=secrets.token_hex(16))))['job']
                deadline=time.monotonic()+20
                while True:
                    state=json.loads(get('/api/codex/jobs/'+job_ai))
                    if state['state']!='running':break
                    if time.monotonic()>deadline:raise TimeoutError('Mock compatible backend')
                    time.sleep(.1)
                assert state['state']=='done' and state['result']['quality']['native_preview_checked']
                assert len(mock.calls)==1 and len(mock.calls[0]['messages'][0]['content'])==3
                assert 'local-smoke-not-a-secret' not in (data/'ai-provider.json').read_text()
                report['compatible_mock_requests']=1;report['real_cloud_inference_verified']=False
            finally:mock.shutdown();mock.server_close();thread.join()
            if args.deepseek_network_check:
                post('/api/ai/config',dict(provider='deepseek',base_url='https://api.deepseek.com',model='deepseek-flash',api_key='z30-public-connectivity-test-not-a-key'))
                try:post('/api/ai/test',{})
                except urllib.error.HTTPError as error:
                    message=json.loads(error.read())['error'];assert 'HTTP 401' in message
                    report['official_https_expected_unauthorized']=True
                else:raise AssertionError('Deliberately invalid key unexpectedly accepted')
            if args.samples:
                originals=[];recipes={}
                for depth in (12,14):
                    source=args.samples/f'Z30-{depth}bit.NEF';digest=hashlib.sha256(source.read_bytes()).hexdigest()
                    opened=json.loads(post('/api/open',source.read_bytes(),True,source.name));key=opened['id'];originals.append(key)
                    settings=dict(exposure=.1,denoise_chroma=10,lens_k1=.005,masks=[dict(type='radial',exposure=.2)])
                    preview=json.loads(post('/api/preview',dict(id=key,settings=settings)))
                    native=json.loads(post('/api/inspect',dict(id=key,settings=settings,width=128,height=96)))
                    assert (native['full_width'],native['full_height'])==(3712,5568)
                    assert Image.open(io.BytesIO(__import__('base64').b64decode(native['image'].split(',')[1]))).size==(128,96)
                    if depth==14:
                        output=post('/api/export',dict(id=key,format='TIFF16',settings=settings))
                        with tifffile.TiffFile(io.BytesIO(output)) as t:
                            a=t.asarray();assert a.dtype==np.uint16 and a.shape==(5568,3712,3)
                            assert t.pages[0].tags[34675].value and np.any(a[::25,::25]%257!=0)
                    assert hashlib.sha256(source.read_bytes()).hexdigest()==digest
                    recipes[key]=dict(settings=settings,history=dict(index=0,states=[dict(settings=settings,preset='custom')]),undo=dict(settings={},preset='neutral'))
                    report['raw'].append(dict(bits=depth,source_sha256=digest,preview=[preview['width'],preview['height']],native=[native['full_width'],native['full_height']],original_unchanged=True))
                ticket=json.loads(post('/api/projects/prepare',dict(ids=originals,recipes=recipes)))
                project=get(ticket['url'])
                with zipfile.ZipFile(io.BytesIO(project)) as z:
                    doc=json.loads(z.read('project.json'))
                    for item in doc['photos']:assert hashlib.sha256(z.read(item['file'])).hexdigest()==item['sha256']
                for key in originals:post('/api/close',dict(id=key))
                opened=json.loads(post('/api/projects/open',project,True))
                assert len(opened['photos'])==2
                assert opened['photos'][0]['undo']['settings']['exposure']==0
                assert len(opened['photos'][0]['settings']['masks'])==1
                report['project_roundtrip']=True
            end();report['graceful_exit']=True
            start(2)
            assert not json.loads(get('/api/ai/config'))['has_key'];report['key_not_persisted_after_restart']=True
            assert json.loads(get('/api/exports/'+job))['state']=='done'
            assert get('/api/exports/'+job+'/files/0').startswith(b'\x89PNG')
            report['durable_export_after_restart']=True
            end()
            report['python_removed_from_PATH']=True
            report['unicode_and_space_paths']=True
            report['exe_sha256']=hashlib.sha256(exe.read_bytes()).hexdigest()
        except Exception:
            args.out.parent.mkdir(parents=True,exist_ok=True)
            for name in ('launcher.log','shutdown-stacks.log'):
                log=data/'logs'/name
                if log.exists():args.out.with_name(args.out.stem+'-'+name).write_bytes(log.read_bytes())
            raise
        finally:end()
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
