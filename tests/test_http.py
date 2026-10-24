import io
import json
import threading
import tempfile
import time
import zipfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request

from PIL import Image
import tifffile
from http.server import ThreadingHTTPServer
import server
import codex_assist
from export_jobs import ExportQueue


class QuietHandler(server.Handler):
    def log_message(self, *args):
        pass


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.owner = None
        self.advisor = server.ADVISOR
        server.ADVISOR = codex_assist.Advisor(server.filter_advice)
        self.exports = server.EXPORTS
        self.tmp = tempfile.TemporaryDirectory()
        import api_backend
        self.ai_settings=server.AI_SETTINGS
        server.AI_SETTINGS=api_backend.Settings(self.tmp.name)
        server.EXPORTS = ExportQueue(Path(self.tmp.name), server.export_snapshot)
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.http.server_port)

    def tearDown(self):
        self.http.shutdown(); self.http.server_close(); self.thread.join()
        server.ADVISOR.shutdown(); server.ADVISOR = self.advisor
        server.EXPORTS.shutdown(); server.EXPORTS = self.exports
        server.AI_SETTINGS=self.ai_settings
        self.tmp.cleanup()

    def post(self, route, data, binary=False):
        body = data if binary else json.dumps(data).encode()
        headers = {'X-Z30-Request': '1', 'X-Filename': 'test.png'}
        if self.owner:
            headers['X-Z30-Client'] = self.owner
        return urllib.request.urlopen(urllib.request.Request(self.base + route, data=body,
            headers=headers), timeout=20)

    def open_photo(self):
        b = io.BytesIO(); Image.new('RGB', (12, 8), (150, 100, 80)).save(b, 'PNG', icc_profile=server.ICC)
        with self.post('/api/open', b.getvalue(), True) as response:
            return json.load(response)['id']

    def test_actual_http_tiff16_export(self):
        key = self.open_photo()
        with self.post('/api/export', dict(id=key, format='TIFF16', settings=dict(rotation=1))) as response:
            self.assertEqual(response.headers['Content-Type'], 'image/tiff')
            image = tifffile.imread(io.BytesIO(response.read()))
        self.assertEqual(str(image.dtype), 'uint16')
        self.assertEqual(image.shape, (12, 8, 3))
        self.post('/api/close', {'id': key}).close()

    def test_disk_draft_recovers_closed_sessions_with_history_and_rejects_stale_save(self):
        self.owner='f'*32
        with patch.object(server,'data_root',return_value=Path(self.tmp.name)/'durable'):
            key=self.open_photo();body=dict(draft_id='d'*32,expected_version=0,request_id='1'*32,
                ids=[key],recipes={key:dict(settings={'exposure':.4},history=dict(index=0,
                    states=[dict(settings=server.settings({'exposure':.4}),preset='custom')]))},selected=0)
            with self.post('/api/drafts/save',body) as response:
                self.assertEqual(json.load(response)['revision'],1)
            with self.post('/api/drafts/save',body) as response:
                self.assertEqual(json.load(response)['revision'],1)
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.post('/api/drafts/save',body|{'request_id':'2'*32})
            self.assertEqual(cm.exception.code,409);cm.exception.close()
            self.post('/api/close',{'id':key}).close()
            with self.post('/api/drafts/restore',{'draft_id':'d'*32}) as response:
                photo=json.load(response)['photos'][0]
            restored=photo['id']
            try:
                self.assertNotEqual(restored,key);self.assertEqual(photo['settings']['exposure'],.4)
                self.assertEqual(photo['history']['index'],0)
                with self.post('/api/preview',{'id':restored,'settings':photo['settings']}) as response:
                    self.assertEqual(json.load(response)['width'],12)
                with urllib.request.urlopen(self.base+'/api/diagnostics') as response:
                    with zipfile.ZipFile(io.BytesIO(response.read())) as archive:
                        self.assertNotIn('drafts.sqlite3',archive.namelist())
            finally:
                self.post('/api/close',{'id':restored}).close()
                server.STORES.pop(Path(self.tmp.name)/'durable'/'drafts',None)

    def test_manual_sync_validates_ownership_and_preserves_unspecified_groups(self):
        self.owner='e'*32;a=self.open_photo();b=self.open_photo()
        try:
            body=dict(source={'exposure':.8,'rotation':3},groups=['light'],targets=[
                dict(id=a,settings={'exposure':0,'rotation':1}),dict(id=b,settings={'exposure':.1,'rotation':2})])
            with self.post('/api/edits/sync',body) as response:result=json.load(response)['photos']
            self.assertEqual([p['settings']['exposure'] for p in result],[.8,.8])
            self.assertEqual([p['settings']['rotation'] for p in result],[1,2])
            self.owner='f'*32
            with self.assertRaises(urllib.error.HTTPError) as cm:self.post('/api/edits/sync',body)
            self.assertEqual(cm.exception.code,400);cm.exception.close()
        finally:
            self.owner='e'*32
            self.post('/api/close',{'id':a}).close();self.post('/api/close',{'id':b}).close()

    def test_local_semantic_mask_overlay_and_repair_endpoints(self):
        import local_vision
        import numpy as np
        self.owner='9'*32;key=self.open_photo()
        vision=Mock();vision.segment.return_value=dict(bitmap=local_vision.bitmap(np.ones((32,32),np.float32)),
            kind='subject',coverage=1.,local_only=True,limitation='test model contract')
        try:
            with patch.object(server,'get_vision',return_value=vision):
                with self.post('/api/masks/semantic',dict(id=key,settings={},kind='subject')) as response:
                    result=json.load(response)
            self.assertTrue(result['local_only']);self.assertEqual(result['mask']['type'],'semantic')
            self.assertEqual(result['mask']['exposure'],0)
            with self.post('/api/masks/preview',dict(id=key,settings={'masks':[result['mask']]},index=0)) as response:
                overlay=json.load(response)['image']
            import base64
            self.assertEqual(Image.open(io.BytesIO(base64.b64decode(overlay.split(',')[1]))).mode,'RGBA')
            with self.post('/api/repairs/place',dict(id=key,settings={},auto_source=True,repair=dict(mode='heal',x=.5,y=.5,radius=.02))) as response:
                repair=json.load(response)['repair']
            self.assertTrue(0<=repair['sx']<=1);self.assertTrue(0<=repair['sy']<=1)
        finally:self.post('/api/close',{'id':key}).close()

    def test_compatible_backend_uses_actual_http_images_and_quality_guard(self):
        import base64
        import api_backend
        from tests.test_api_backend import Endpoint,advice
        fake=ThreadingHTTPServer(('127.0.0.1',0),Endpoint)
        fake.received=[];fake.result=advice();fake.status=200
        fake.started=threading.Event();fake.release=threading.Event();fake.release.set()
        thread=threading.Thread(target=fake.serve_forever,daemon=True);thread.start()
        try:
            with patch.object(server,'AI_SETTINGS',api_backend.Settings(self.tmp.name)):
                self.owner='e'*32;key=self.open_photo()
                with self.post('/api/ai/config',dict(provider='compatible',base_url=f'http://127.0.0.1:{fake.server_port}',model='vision-model',api_key='fake-test-key')) as response:config=json.load(response)
                self.assertNotIn('api_key',config)
                with self.assertRaises(urllib.error.HTTPError) as missing_revision:
                    self.post('/api/codex/auto',dict(id=key,settings={}))
                self.assertEqual(missing_revision.exception.code,400);missing_revision.exception.close()
                self.assertFalse(fake.received)
                with self.post('/api/codex/auto',dict(id=key,settings={},backend_revision=config['revision'],request_id='c'*32)) as response:job=json.load(response)['job']
                deadline=time.monotonic()+5
                while server.ADVISOR.get(job)['state']=='running' and time.monotonic()<deadline:time.sleep(.02)
                result=server.ADVISOR.get(job)
                self.assertEqual(result['state'],'done');self.assertTrue(result['result']['quality']['native_preview_checked'])
                content=fake.received[0][0]['messages'][0]['content']
                for part in content[1:]:
                    image=Image.open(io.BytesIO(base64.b64decode(part['image_url']['url'].split(',')[1])))
                    self.assertFalse(image.getexif());self.assertLessEqual(max(image.size),1600)
                self.assertNotIn('test.png',json.dumps(content));self.assertEqual(len(fake.received),1)
                for path in ('/model-settings.js','/batch-grade.js'):
                    with urllib.request.urlopen(self.base+path) as response:self.assertGreater(len(response.read()),100)
                self.post('/api/close',dict(id=key)).close()
        finally:fake.release.set();fake.shutdown();fake.server_close();thread.join()

    def test_project_download_reopen_restores_original_and_history(self):
        self.owner='d'*32; key=self.open_photo()
        original=server.SESSIONS[key]['path'].read_bytes()
        history=dict(index=1,states=[dict(settings=server.DEFAULTS,preset='neutral'),
            dict(settings=server.DEFAULTS|dict(exposure=.5),preset='custom')])
        with self.post('/api/projects/prepare',dict(ids=[key],recipes={key:dict(settings={'exposure':.5},history=history,undo=dict(settings={'exposure':0},preset='neutral'))})) as r:
            url=json.load(r)['url']
        with self.assertRaises(urllib.error.HTTPError) as cm:self.post('/api/close',{'id':key})
        self.assertEqual(cm.exception.code,400);cm.exception.close()
        with urllib.request.urlopen(self.base+url) as response:blob=response.read()
        self.assertFalse(server.PROJECT_READERS)
        self.post('/api/close',{'id':key}).close()
        with self.post('/api/projects/open',blob,True) as response:opened=json.load(response)['photos'][0]
        try:
            self.assertEqual(opened['settings']['exposure'],.5)
            self.assertEqual(opened['history']['index'],1)
            self.assertEqual(opened['undo']['settings']['exposure'],0);self.assertEqual(opened['undo']['preset'],'neutral')
            self.assertEqual(server.SESSIONS[opened['id']]['path'].read_bytes(),original)
        finally:self.post('/api/close',{'id':opened['id']}).close()

    def test_inspection_is_an_exact_crop_of_native_render_with_geometry(self):
        import base64
        import numpy as np
        yy,xx=np.indices((300,420));pixels=np.stack((xx%256,yy%256,(xx+yy)%256),axis=2).astype(np.uint8)
        b=io.BytesIO();Image.fromarray(pixels).save(b,'PNG',icc_profile=server.ICC)
        with self.post('/api/open',b.getvalue(),True) as response:key=json.load(response)['id']
        p=server.settings(dict(rotation=1,crop='1:1',exposure=.5,denoise_chroma=20,lens_k1=.01,masks=[dict(type='radial',exposure=.3)]))
        try:
            expected=np.asarray(server.develop(server.decode(server.SESSIONS[key],'camera',False),p))
            with self.post('/api/inspect',dict(id=key,settings=p,cx=.8,cy=.3,width=96,height=80)) as response:data=json.load(response)
            actual=np.asarray(Image.open(io.BytesIO(base64.b64decode(data['image'].split(',')[1]))))
            self.assertEqual((data['full_width'],data['full_height']),(300,300))
            self.assertEqual((data['x'],data['y'],data['width'],data['height']),(192,50,96,80))
            np.testing.assert_array_equal(actual,expected[50:130,192:288])
            with patch.object(server,'decode',side_effect=AssertionError('native render cache missed')):
                with self.post('/api/inspect',dict(id=key,settings=p,cx=.1,cy=.9,width=96,height=80)) as response:edge=json.load(response)
                tile=np.asarray(Image.open(io.BytesIO(base64.b64decode(edge['image'].split(',')[1]))))
                self.assertEqual((edge['x'],edge['y']),(0,220));np.testing.assert_array_equal(tile,expected[220:300,:96])
        finally:self.post('/api/close',{'id':key}).close()

    def test_protected_export_does_not_drop_manual_masks_or_optical_parameters(self):
        key=self.open_photo();p=server.settings(dict(exposure=.2,lens_red=2,masks=[dict(type='linear',exposure=.3)]))
        try:
            data,applied,metrics=server.export_snapshot(server.SESSIONS[key]['path'],False,p,'TIFF16',95,True)
            self.assertEqual(applied['masks'],p['masks']);self.assertEqual(applied['lens_red'],2)
            with tifffile.TiffFile(io.BytesIO(data)) as t:
                self.assertEqual(t.asarray().dtype.name,'uint16');self.assertEqual(t.pages[0].bitspersample,16)
            self.assertTrue(metrics['native_preview_checked'])
        finally:self.post('/api/close',{'id':key}).close()

    def test_refresh_restores_eight_photos_and_parameters_instead_of_leaking_slots(self):
        self.owner = 'b' * 32
        keys = [self.open_photo() for _ in range(8)]
        try:
            with self.post('/api/sessions/restore', {'recipes': {keys[0]: {'settings': {'exposure': .4, 'rotation': 1, 'crop': '1:1'}, 'undo': {'settings': {'exposure': 0}}}}}) as response:
                data = json.load(response)
            self.assertEqual({p['id'] for p in data['photos']}, set(keys))
            photo = next(p for p in data['photos'] if p['id'] == keys[0])
            self.assertEqual(photo['settings']['exposure'], .4)
            self.assertEqual((photo['width'], photo['height']), (12, 8))
            self.assertEqual((photo['previewWidth'], photo['previewHeight']), (8, 8))
            self.assertEqual(photo['undo']['settings']['exposure'], 0)
            self.post('/api/close', {'id': keys.pop()}).close()
            keys.append(self.open_photo())
        finally:
            for key in keys: self.post('/api/close', {'id': key}).close()

    def test_expiry_reclaims_source_cache_and_jobs_but_heartbeat_keeps_active_tab(self):
        self.owner = 'b' * 32; old = self.open_photo()
        self.owner = 'c' * 32; live = self.open_photo()
        server.SESSIONS[old]['last_seen'] = time.monotonic() - server.SESSION_TTL - 1
        server.ADVISOR.jobs['expired'] = dict(state='done', source_id=old, result={})
        path = server.SESSIONS[old]['path']
        with self.post('/api/sessions/heartbeat', {}) as response:
            self.assertEqual(json.load(response)['ids'], [live])
        self.assertNotIn(old, server.SESSIONS); self.assertFalse(path.exists())
        self.assertEqual(server.ADVISOR.get('expired')['state'], 'cancelled')
        self.assertFalse(any(k[0] == old for k in server.CACHE.items))
        self.owner = 'b' * 32
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post('/api/close', {'id': live})
        self.assertEqual(cm.exception.code, 404); cm.exception.close()
        with self.post('/api/sessions/restore', {}) as response:
            self.assertFalse(json.load(response)['photos'])
        self.owner = 'c' * 32; self.post('/api/close', {'id': live}).close()

    def test_closed_photo_job_cannot_be_retrieved_as_success(self):
        key = self.open_photo()
        server.ADVISOR.jobs['job'] = dict(state='done', source_id=key, result={'settings': {}})
        self.post('/api/close', {'id': key}).close()
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(self.base + '/api/codex/jobs/job')
        self.assertEqual(cm.exception.code, 410)
        cm.exception.close()
        self.assertNotIn('result', server.ADVISOR.get('job'))

    def test_background_batch_survives_closed_photo_sessions_and_downloads_zip(self):
        keys = [self.open_photo(), self.open_photo()]
        with self.post('/api/exports/create', dict(ids=keys, format='PNG', settings=dict(exposure=.2), protect=True)) as response:
            job = json.load(response)['job']
        for key in keys:
            self.post('/api/close', {'id': key}).close()
        deadline = time.monotonic() + 5
        state = None
        while time.monotonic() < deadline:
            with urllib.request.urlopen(self.base + '/api/exports/' + job) as response:
                state = json.load(response)
            if state['state'] not in ('queued', 'running'):
                break
            time.sleep(.02)
        self.assertEqual(state['state'], 'done')
        with urllib.request.urlopen(self.base + '/api/exports/' + job + '/download') as response:
            self.assertEqual(response.headers['Connection'], 'close')
            with zipfile.ZipFile(io.BytesIO(response.read())) as archive:
                self.assertEqual(len([n for n in archive.namelist() if n.endswith('.png')]), 2)
                for name in archive.namelist():
                    if name.endswith('.png'):
                        self.assertEqual(Image.open(io.BytesIO(archive.read(name))).size, (12, 8))
        for index in (0, 1):
            with urllib.request.urlopen(self.base + '/api/exports/' + job + '/files/' + str(index)) as response:
                self.assertEqual(Image.open(io.BytesIO(response.read())).size, (12, 8))
        self.assertFalse(any(cache_key[0] in keys for cache_key in server.CACHE.items))

    def test_missing_photo_batch_is_rejected_without_creating_job(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post('/api/exports/create', dict(ids=['missing'], format='PNG', settings={}))
        self.assertEqual(cm.exception.code, 410); cm.exception.close()
        self.assertFalse(server.EXPORTS.list())

    def test_one_click_auto_uses_existing_cli_then_quality_filter_and_local_preview(self):
        key = self.open_photo()
        path = server.SESSIONS[key]['path']; original = path.read_bytes()
        advice = dict(title='自然通透', summary='温和提亮', observations=['测试画面'], limitations='合成测试',
                      adjustments={**{k: 0 for k in codex_assist.RANGES}, 'exposure': .5, 'wb': 'camera'})
        def launch(cmd, **kwargs):
            Path(cmd[cmd.index('--output-last-message') + 1]).write_text(json.dumps(advice), encoding='utf8')
            return Mock(returncode=0)
        try:
            with patch.object(codex_assist, 'codex_command', return_value=['codex']), patch.object(codex_assist.subprocess, 'Popen', side_effect=launch) as popen:
                body = dict(id=key, settings=dict(rotation=1, crop='1:1'), request_id='a' * 32)
                with self.post('/api/codex/auto', body) as response:
                    self.assertEqual(response.status, 202); start = json.load(response)
                with self.post('/api/codex/auto', body) as response:
                    self.assertEqual(json.load(response)['job'], start['job'])
                with urllib.request.urlopen(self.base + '/api/codex/requests/' + 'a' * 32 + '?source=' + key) as response:
                    self.assertEqual(json.load(response)['job'], start['job'])
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    with urllib.request.urlopen(self.base + '/api/codex/jobs/' + start['job']) as response:
                        job = json.load(response)
                    if job['state'] != 'running': break
                    time.sleep(.01)
                self.assertEqual(job['state'], 'done'); self.assertEqual(job['phase'], 'ready')
                self.assertEqual(job['mode'], 'auto'); self.assertEqual(job['source_id'], key)
                self.assertIn('quality', job['result'])
                self.assertEqual(job['result']['settings']['rotation'], 1)
                self.assertEqual(job['result']['settings']['crop'], '1:1')
                with self.post('/api/preview', dict(id=key, settings=job['result']['settings'], before=True)) as response:
                    preview = json.load(response)
                self.assertEqual((preview['width'], preview['height']), (8, 8))
                self.assertTrue(preview['image'].startswith('data:image/jpeg;base64,'))
                self.assertTrue(start['current_image'].startswith('data:image/jpeg;base64,'))
                popen.assert_called_once()
                self.assertIn('--output-schema', popen.call_args.args[0])
            self.assertEqual(path.read_bytes(), original)
        finally:
            self.post('/api/close', {'id': key}).close()

    def test_cancel_is_scoped_to_source_and_invalidates_completed_auto_result(self):
        key, other = self.open_photo(), self.open_photo()
        try:
            server.ADVISOR.jobs['auto'] = dict(state='done', source_id=key, mode='auto', result={'settings': {}})
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.post('/api/codex/cancel', dict(id=other, job='auto'))
            self.assertEqual(cm.exception.code, 400); cm.exception.close()
            self.assertEqual(server.ADVISOR.get('auto')['state'], 'done')
            self.post('/api/codex/cancel', dict(id=key, job='auto')).close()
            self.assertEqual(server.ADVISOR.get('auto')['state'], 'cancelled')
            self.assertNotIn('result', server.ADVISOR.get('auto'))
        finally:
            for source in (key, other): self.post('/api/close', {'id': source}).close()

    def test_auto_missing_cli_reports_error_without_changing_photo(self):
        key = self.open_photo(); original = server.SESSIONS[key]['path'].read_bytes()
        try:
            with patch.object(codex_assist, 'codex_command', return_value=None), self.assertRaises(urllib.error.HTTPError) as cm:
                self.post('/api/codex/auto', dict(id=key))
            self.assertEqual(cm.exception.code, 400)
            self.assertIn('未找到 Codex', json.load(cm.exception)['error']); cm.exception.close()
            self.assertEqual(server.SESSIONS[key]['path'].read_bytes(), original)
        finally:
            self.post('/api/close', {'id': key}).close()


if __name__ == '__main__':
    unittest.main()
