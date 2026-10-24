import base64
import copy
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch

from PIL import Image
import api_backend
import codex_assist
import server


def advice():
    return dict(title='自然调色',summary='保留色彩',observations=['暗部偏暗'],limitations='无法恢复剪切信息',
                adjustments={**{k:0 for k in codex_assist.RANGES},'exposure':.4,'wb':'auto'})


class Endpoint(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        body=json.dumps(dict(data=[dict(id='vision-model')])).encode();self.send_response(200);self.end_headers();self.wfile.write(body)
    def do_POST(self):
        payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.received.append((payload,self.headers.get('Authorization')))
        self.server.started.set();self.server.release.wait(10)
        result=self.server.result
        body=json.dumps(dict(choices=[dict(message=dict(content=json.dumps(result)),finish_reason='stop')])).encode()
        self.send_response(self.server.status);self.end_headers()
        try:self.wfile.write(body)
        except (OSError,ConnectionError):pass


class ApiBackendTests(unittest.TestCase):
    def setUp(self):
        self.http=ThreadingHTTPServer(('127.0.0.1',0),Endpoint)
        self.http.received=[];self.http.result=advice();self.http.status=200
        self.http.started=threading.Event();self.http.release=threading.Event();self.http.release.set()
        self.worker=threading.Thread(target=self.http.serve_forever,daemon=True);self.worker.start()
        self.config=dict(provider='compatible',base_url=f'http://127.0.0.1:{self.http.server_port}',model='vision-model',api_key='fake-test-key',revision='one')
    def tearDown(self):
        self.http.release.set();self.http.shutdown();self.http.server_close();self.worker.join()
    def wait(self,advisor,key):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            job=advisor.get(key)
            if key not in advisor.workers:return job
            time.sleep(.01)
        self.fail('Model worker did not finish')

    def test_images_schema_single_request_and_local_bounds_share_the_cli_pipeline(self):
        p=server.settings(dict(wb='daylight',rotation=1,crop='1:1',masks=[dict(type='radial',exposure=.3)]))
        a=codex_assist.Advisor(lambda source,base,result:result|{'quality':{'native_preview_checked':True}})
        with patch.object(codex_assist,'codex_command',side_effect=AssertionError('Should not launch CLI')):
            key=a.submit('photo',b'jpeg-one',b'jpeg-two',p,'自然',True,mode='auto',request_id='a'*32,
                auto_options=dict(strength=50,exposure_limit=.2,color_limit=10),backend=self.config)
            job=self.wait(a,key)
            self.assertEqual(job['state'],'done');self.assertAlmostEqual(job['result']['settings']['exposure'],.1)
            self.assertEqual(job['result']['settings']['wb'],'daylight');self.assertEqual(job['result']['settings']['masks'],p['masks'])
            self.assertEqual(a.submit('photo',b'x',b'y',p,'自然',True,mode='auto',request_id='a'*32,auto_options=dict(strength=50,exposure_limit=.2,color_limit=10),backend=self.config),key)
        self.assertEqual(len(self.http.received),1)
        payload,auth=self.http.received[0];self.assertEqual(auth,'Bearer fake-test-key')
        self.assertEqual(payload['response_format'],{'type':'json_object'});self.assertNotIn('tools',payload)
        content=payload['messages'][0]['content'];self.assertEqual(len(content),3)
        self.assertEqual(base64.b64decode(content[2]['image_url']['url'].split(',')[1]),b'jpeg-two')
        self.assertNotIn('fake-test-key',json.dumps(job));a.shutdown()

    def test_bad_ranges_and_http_errors_never_publish_or_retry(self):
        for status,result in ((200,advice()|{'adjustments':advice()['adjustments']|{'exposure':99}}),(401,advice())):
            self.http.status=status;self.http.result=result
            a=codex_assist.Advisor();before=len(self.http.received)
            key=a.submit('photo',b'a',b'b',server.settings({}),'自然',False,backend=self.config)
            job=self.wait(a,key);self.assertEqual(job['state'],'error');self.assertNotIn('result',job)
            self.assertEqual(len(self.http.received),before+1);self.assertNotIn('fake-test-key',job['error']);a.shutdown()

    def test_cancel_closes_transport_and_blocks_late_publication(self):
        self.http.release.clear();a=codex_assist.Advisor()
        key=a.submit('photo',b'a',b'b',server.settings({}),'自然',False,backend=self.config)
        self.assertTrue(self.http.started.wait(3));a.cancel(key);self.http.release.set()
        job=self.wait(a,key);self.assertEqual(job['state'],'cancelled');self.assertNotIn('result',job);a.shutdown()

    def test_configuration_never_persists_or_returns_key_and_is_bound_to_endpoint(self):
        with tempfile.TemporaryDirectory() as directory,patch.dict('os.environ',{},clear=True):
            settings=api_backend.Settings(directory)
            public=settings.save(self.config);self.assertTrue(public['has_key']);self.assertNotIn('api_key',public)
            self.assertNotIn('fake-test-key',settings.path.read_text())
            with patch.object(Path,'write_text',side_effect=OSError('disk unavailable')):
                with self.assertRaises(OSError):settings.save(self.config|{'api_key':'other-fake-key'})
            self.assertEqual(settings.snapshot()['api_key'],'fake-test-key')
            fresh=api_backend.Settings(directory);self.assertFalse(fresh.public()['has_key'])
            with self.assertRaises(ValueError):fresh.snapshot()
            old=public['revision'];settings.save(self.config|{'base_url':'https://example.com','api_key':''})
            self.assertFalse(settings.public()['has_key'])
            with self.assertRaises(ValueError):settings.snapshot(old)

    def test_official_deepseek_vision_preset_and_endpoint_validation(self):
        self.assertEqual(api_backend.Settings.validate({'provider':'deepseek'})['model'],'deepseek-flash')
        for value in ('http://example.com/v1','https://user:password@example.com','https://example.com/v1?api_key=x','https://example.com/chat/completions','https://example.com:bad'):
            with self.assertRaises(ValueError):api_backend.normalize_url(value)
        with self.assertRaises(ValueError):api_backend.Settings.validate(dict(provider='deepseek',model='deepseek-v4-pro'))
        with self.assertRaises(ValueError):api_backend.Request(self.config|{'api_key':'fake\nkey'})
        api_backend.Request(self.config|{'provider':'deepseek'}).generate(b'a',b'b','JSON',codex_assist.SCHEMA)
        self.assertEqual(self.http.received[-1][0]['thinking'],{'type':'disabled'})

    def test_connection_check_lists_models_without_an_image_or_inference_request(self):
        checked=api_backend.test_connection(self.config)
        self.assertTrue(checked['model_listed']);self.assertFalse(self.http.received)

    def test_reflected_credentials_and_redirects_are_not_published_or_followed(self):
        for status,result in ((200,advice()|{'summary':'fake-test-key'}),(302,advice())):
            self.http.status=status;self.http.result=result;before=len(self.http.received)
            a=codex_assist.Advisor();key=a.submit('photo',b'a',b'b',server.settings({}),'自然',False,backend=self.config)
            job=self.wait(a,key);self.assertEqual(job['state'],'error');self.assertNotIn('result',job)
            self.assertNotIn('fake-test-key',job['error']);self.assertEqual(len(self.http.received),before+1);a.shutdown()
