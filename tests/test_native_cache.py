import io
import tempfile
import threading
import time
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
import tifffile
import native_cache
import server
from export_jobs import ExportQueue

class NativeTests(unittest.TestCase):
    def test_immutable_leases_pinning_eviction_and_changed_file_rebuild(self):
        with tempfile.TemporaryDirectory() as root:
            cache=native_cache.NativeCache(root,max_bytes=2000);calls=[]
            def build(path):
                calls.append(1)
                with native_cache.writer(path,(8,8,3)) as write:write(0,8,np.full((8,8,3),.25,np.float32))
            try:
                with cache.lease('a',build) as a:
                    self.assertFalse(a.flags.writeable);cache.clear();self.assertEqual(cache.stats()['frames'],1)
                    with cache.lease('b',build) as b:self.assertEqual(a[0,0,0],b[0,0,0])
                with cache.lease('a',build):pass
                self.assertEqual(len(calls),2)
                (cache.root/'a.npy').write_bytes(b'damaged')
                with cache.lease('a',build) as a:self.assertEqual(a[0,0,0],.25)
                with cache.lease('c',build):pass
                self.assertLessEqual(cache.stats()['bytes'],2000);self.assertEqual(cache.stats()['frames'],2)
            finally:cache.close()
            self.assertFalse(cache.root.exists())

    def test_one_builder_for_concurrent_readers_and_incomplete_output_never_published(self):
        with tempfile.TemporaryDirectory() as root:
            cache=native_cache.NativeCache(root);calls=[];errors=[]
            def build(path):
                calls.append(1);time.sleep(.03)
                with native_cache.writer(path,(8,8,3)) as write:write(0,8,np.zeros((8,8,3),np.float32))
            def use():
                try:
                    with cache.lease('same',build) as a:self.assertEqual(a.shape,(8,8,3))
                except Exception as exc:errors.append(exc)
            threads=[threading.Thread(target=use) for _ in range(4)]
            for t in threads:t.start()
            for t in threads:t.join(2)
            self.assertEqual(errors,[]);self.assertEqual(len(calls),1)
            def fail(path):
                with native_cache.writer(path,(8,8,3)) as write:write(0,4,np.zeros((4,8,3),np.float32))
            with self.assertRaises(ValueError):
                with cache.lease('failed',fail):pass
            self.assertFalse((cache.root/'failed.npy').exists());self.assertEqual(list(cache.root.glob('*.part.npy')),[]);cache.close()

    def test_scratch_native_matches_memory_pipeline_and_export_reuses_render(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'source.png';Image.fromarray(np.random.default_rng(2).integers(0,240,(197,251,3),dtype=np.uint8)).save(path)
            session=dict(path=path,raw=False,cache={});p=server.settings(dict(rotation=1,straighten=4,crop='free',crop_rect=[.1,.1,.8,.85],
                denoise_luma=20,lens_k1=.04,clarity=15,sharpen=30,repairs=[dict(mode='clone',x=.5,y=.5,sx=.7,sy=.6,radius=.04)]))
            expected=np.asarray(server.develop(server.decode(session,'camera',False),p))
            cache=native_cache.NativeCache(Path(root)/'cache')
            try:
                with patch.object(server,'get_native_cache',return_value=cache):
                    with server.native_pixels(session,p) as pixels:actual=np.rint(pixels*255).astype(np.uint8)
                    np.testing.assert_array_equal(actual,expected)
                    with patch.object(server,'decode',side_effect=AssertionError('must reuse full render')):
                        data=server.export_bytes(session,p,'TIFF16')
                    output=tifffile.imread(io.BytesIO(data));self.assertTrue(np.any(output%257));self.assertGreater(cache.stats()['hits'],0)
                self.assertEqual(list(cache.root.glob('scratch-*')),[])
            finally:cache.close()

    def test_two_workers_progress_eta_and_memory_guard(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'source.png';path.write_bytes(b'original');release=threading.Event();started=threading.Event();active=0;peak=0;guard=threading.Lock()
            def exporter(source,raw,p,fmt,quality,protect,options,progress=None):
                nonlocal active,peak
                with guard:
                    active+=1;peak=max(peak,active)
                    if active==2:started.set()
                progress('render',.5);release.wait(2)
                with guard:active-=1
                return b'output',p,None
            q=ExportQueue(Path(root)/'jobs',exporter)
            try:
                with patch('native_cache.available_memory',return_value=8*1024**3):
                    key=q.submit([dict(path=path,name='p.png')]*2,{},'PNG',95,False,options={'workers':2})
                    self.assertTrue(started.wait(2));status=q.list()[0]
                    self.assertEqual(status['workers'],2);self.assertEqual(status['progress'],.5);self.assertIsNotNone(status['eta_seconds']);release.set()
                    deadline=time.monotonic()+3
                    while q.get(key).get('worker_active') and time.monotonic()<deadline:time.sleep(.01)
                    self.assertEqual(q.get(key)['state'],'done');self.assertEqual(peak,2)
                with patch('native_cache.available_memory',return_value=1024**3):
                    key=q.submit([dict(path=path,name='p.png')]*2,{},'PNG',95,False,options={'workers':2});deadline=time.monotonic()+3
                    while q.get(key)['state'] in ('queued','running') and time.monotonic()<deadline:time.sleep(.01)
                    self.assertEqual(q.get(key)['workers'],1)
            finally:release.set();q.shutdown()

if __name__=='__main__':unittest.main()
