import base64
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from PIL import Image
import api_backend
import ai_preflight
import automation
import codex_assist
import server
from tests.test_api_backend import Endpoint,advice
from tests import test_http as http_tests

class PreflightTests(unittest.TestCase):
    def test_ticket_binds_actual_images_source_owner_settings_goal_and_backend(self):
        tickets=ai_preflight.Tickets();p=server.settings({'exposure':.2});config=dict(provider='local',base_url=api_backend.LOCAL_URL,model='qwen3-vl:8b',revision='a')
        image=server.image_bytes(Image.new('RGB',(20,30),(90,120,150)))
        result=tickets.issue('source','owner',p,'natural','suggest',None,config,image,image)
        self.assertEqual(result['payload'],api_backend.make_payload(config,image,image,codex_assist.build_prompt(p,'natural',False),codex_assist.SCHEMA))
        for change in ({'owner':'other'},{'p':p|{'exposure':.4}},{'goal':'other'},{'revision':'b'}):
            values=dict(token=result['token'],source='source',owner='owner',p=p,goal='natural',mode='suggest',policy=None,revision='a')|change
            with self.assertRaises(ValueError):tickets.get(**values)
        self.assertEqual(tickets.get(result['token'],'source','owner',p,'natural','suggest',None,'a'),(image,image))
        tickets.drop_source('source')
        with self.assertRaises(ValueError):tickets.get(result['token'],'source','owner',p,'natural','suggest',None,'a')

    def test_cost_unknown_and_explicit_price_scenarios_are_not_a_bill(self):
        images=[dict(width=1600,height=1000)]*2
        unknown=ai_preflight.cost_scenario('goal',images,dict(provider='compatible'))
        self.assertIsNone(ai_preflight.cost_scenario('goal',images,dict(provider='codex'))['output_token_cap']);self.assertIsNone(unknown['cost_scenario']);self.assertIsNone(unknown['model_seconds']['median'])
        known=ai_preflight.cost_scenario('goal',images,dict(provider='compatible',rates=dict(input=1,output=2,currency='CNY')),[10,20,30])
        self.assertGreater(known['cost_scenario']['high'],known['cost_scenario']['low']);self.assertEqual(known['model_seconds']['median'],20)
        with self.assertRaises(ValueError):api_backend.Settings.validate(dict(rates={'input':float('nan')}))

    def test_local_protocol_has_no_auth_or_cloud_secret_and_rejects_remote_hosts(self):
        http=ThreadingHTTPServer(('127.0.0.1',0),Endpoint);http.received=[];http.result=advice();http.status=200
        http.started=threading.Event();http.release=threading.Event();http.release.set();thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as root,patch.dict('os.environ',{'Z30_API_KEY':'must-not-forward'}):
                settings=api_backend.Settings(root);public=settings.save(dict(provider='local',base_url=f'http://127.0.0.1:{http.server_port}/v1',model='vision-model'))
                self.assertFalse(public['has_key']);config=settings.snapshot();self.assertEqual(config['api_key'],'')
                result=api_backend.Request(config).generate(b'one',b'two','prompt',codex_assist.SCHEMA)
                self.assertEqual(result['title'],advice()['title']);self.assertIsNone(http.received[0][1])
                self.assertNotIn('must-not-forward',json.dumps(http.received));self.assertNotIn('must-not-forward',settings.path.read_text())
                for invalid in ({'base_url':'https://example.com'},{'model':'qwen:cloud'},{'api_key':'must-not-forward'}):
                    with self.assertRaises(ValueError):settings.save(config|invalid)
        finally:http.shutdown();http.server_close();thread.join()

class PreflightHttpTests(unittest.TestCase):
    setUp=http_tests.HttpTests.setUp
    tearDown=http_tests.HttpTests.tearDown
    post=http_tests.HttpTests.post
    open_photo=http_tests.HttpTests.open_photo
    def test_preflight_does_not_contact_model_then_sends_exact_frozen_payload(self):
        mock=ThreadingHTTPServer(('127.0.0.1',0),Endpoint);mock.received=[];mock.result=advice();mock.status=200
        mock.started=threading.Event();mock.release=threading.Event();mock.release.set();worker=threading.Thread(target=mock.serve_forever,daemon=True);worker.start()
        self.owner='b'*32;key=self.open_photo()
        try:
            config=server.AI_SETTINGS.save(dict(provider='local',base_url=f'http://127.0.0.1:{mock.server_port}/v1',model='vision-model'))
            body=dict(id=key,settings={'exposure':.1},mode='auto',backend_revision=config['revision'])
            with self.post('/api/ai/preflight',body) as response:preview=json.load(response)
            self.assertEqual(mock.received,[]);self.assertNotIn('test.png',json.dumps(preview['payload']))
            for image in preview['images']:
                with Image.open(io.BytesIO(base64.b64decode(image['data'].split(',')[1]))) as photo:self.assertEqual(len(photo.getexif()),0)
            with self.assertRaises(__import__('urllib.error',fromlist=['HTTPError']).HTTPError) as error:
                self.post('/api/codex/auto',body|{'settings':{'exposure':.2},'preview_token':preview['token']})
            error.exception.close()
            self.assertEqual(mock.received,[])
            with self.post('/api/codex/auto',body|{'preview_token':preview['token'],'request_id':'a'*32}) as response:job=json.load(response)['job']
            deadline=time.monotonic()+5
            while job in server.ADVISOR.workers and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(server.ADVISOR.get(job)['state'],'done');self.assertEqual(len(mock.received),1)
            self.assertEqual(mock.received[0][0],preview['payload']);self.assertIsNone(mock.received[0][1])
        finally:
            self.post('/api/close',dict(id=key)).close();mock.shutdown();mock.server_close();worker.join()

if __name__=='__main__':unittest.main()
