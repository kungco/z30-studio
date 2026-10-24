import base64
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
import codex_assist
import local_vision
import processing
import server
import spatial


class LocalEditTests(unittest.TestCase):
    def test_luminance_colour_and_bitmap_ranges_are_spatial_and_chunk_consistent(self):
        arr=np.ones((70,120,3),np.float32)*.05;arr[:,60:]=[.4,.05,.03]
        m=spatial.masks([dict(type='luminance',range_min=.2,range_max=.3,range_softness=.02)])[0]
        field=spatial.mask_field(m,120,70,0,70,arr);self.assertGreater(field[:,20].mean(),field[:,100].mean())
        m=spatial.masks([dict(type='color',range_color=[.665,.248,.19],color_distance=.05)])[0]
        field=spatial.mask_field(m,120,70,0,70,arr);self.assertGreater(field[:,90].mean(),field[:,20].mean())
        bitmap=local_vision.bitmap(np.pad(np.ones((32,16),np.float32),((0,0),(0,16))))
        m=spatial.masks([dict(type='semantic',bitmap=bitmap,exposure=.7)])[0]
        p=server.settings(dict(masks=[m],sharpen=30))
        a=processing.render_array(arr,p,bits=16,chunk_rows=11);b=processing.render_array(arr,p,bits=16,chunk_rows=70)
        np.testing.assert_array_equal(a,b)
        self.assertGreater(spatial.mask_field(m,120,70,0,70)[:,10].mean(),.9)
        self.assertLess(spatial.mask_field(m,120,70,0,70)[:,-10].mean(),.01)

    def test_raised_limits_are_real_and_invalid_bitmap_or_strokes_still_reject(self):
        self.assertEqual(len(spatial.masks([dict(type='radial')]*32)),32)
        self.assertEqual(len(spatial.masks([dict(type='brush',points=[[.5,.5]]*8192)])[0]['points']),8192)
        for bad in ([dict(type='radial')]*33,[dict(type='brush',points=[[.5,.5]]*8193)],[dict(type='semantic',bitmap='not-a-PNG')]):
            with self.assertRaises(ValueError):spatial.masks(bad)
        b=io.BytesIO();Image.new('RGB',(16,16)).save(b,'PNG')
        with self.assertRaises(ValueError):spatial.bitmap_field(base64.b64encode(b.getvalue()).decode())

    def test_clone_and_heal_remove_a_spot_without_mutating_or_quantizing_the_original(self):
        x=np.linspace(.15,.3,160,dtype=np.float32);arr=np.repeat(np.broadcast_to(x[None,:,None],(100,160,1)),3,axis=2).copy()
        arr[47:53,77:83]=0;original=arr.copy()
        sx,sy=spatial.choose_donor(arr,.5,.5,.06)
        edits=spatial.repairs([dict(mode='heal',x=.5,y=.5,sx=sx,sy=sy,radius=.06)])
        out=spatial.retouch(arr,{'repairs':edits});self.assertGreater(out[50,80].mean(),.1)
        self.assertEqual(out.dtype,np.float32);np.testing.assert_array_equal(arr,original)
        self.assertGreater(len(np.unique(out)),160)
        clone=spatial.retouch(arr,{'repairs':spatial.repairs([dict(mode='clone',x=.5,y=.5,sx=.2,sy=.2,radius=.06)])})
        self.assertGreater(clone[50,80].mean(),.1)
        with self.assertRaises(ValueError):spatial.repairs([dict(mode='command')])

    def test_skin_smoothing_requires_person_region_and_preserves_unmasked_pixels(self):
        rng=np.random.default_rng(5);arr=np.ones((60,90,3),np.float32)*np.array([.3,.17,.1],np.float32)
        arr+=rng.normal(0,.008,arr.shape).astype(np.float32);original=arr.copy()
        mask=local_vision.bitmap(np.pad(np.ones((32,16),np.float32),((0,0),(0,16))))
        out=spatial.retouch(arr,{'skin_smooth':90,'skin_mask':mask})
        self.assertLess(out[10:50,5:30].var(axis=(0,1)).mean(),arr[10:50,5:30].var(axis=(0,1)).mean())
        np.testing.assert_allclose(out[:,-10:],arr[:,-10:],atol=1e-7);np.testing.assert_array_equal(arr,original)
        with self.assertRaises(ValueError):spatial.retouch(arr,{'skin_smooth':50})

    def test_prompt_never_transmits_bitmap_calibration_names_or_repair_metadata(self):
        p=server.settings(dict(lens_profile='PRIVATE LENS NAME',wb_calibration='PRIVATE GRAY CARD',
                              repairs=[dict(mode='clone')],skin_mask=local_vision.bitmap(np.ones((16,16),np.float32))))
        prompt=codex_assist.build_prompt(p,'自然',True)
        for private in ('PRIVATE LENS NAME','PRIVATE GRAY CARD','skin_mask','repairs',p['skin_mask']):self.assertNotIn(private,prompt)
        self.assertIn('exposure',prompt)

    def test_failed_model_download_keeps_previous_weights_and_no_partial_file(self):
        with tempfile.TemporaryDirectory() as folder:
            vision=local_vision.LocalVision(folder);vision.path.write_bytes(b'old-good-model')
            with patch.object(local_vision.urllib.request,'urlopen',return_value=io.BytesIO(b'invalid')):
                with self.assertRaises(ValueError):vision.install()
            self.assertEqual(vision.path.read_bytes(),b'old-good-model');self.assertFalse(list(Path(folder).glob('*.part')))
            with self.assertRaises(ValueError):vision.verify()

    def test_model_contract_generates_distinct_local_person_and_sky_masks(self):
        class Session:
            def get_inputs(self):return [type('Input',(),dict(name='pixel_values'))()]
            def run(self,_,data):
                self.input=data['pixel_values'];logits=np.full((1,150,16,16),-20,np.float32);logits[:,0]=0
                logits[:,2,:4,:]=20;logits[:,12,7:15,5:11]=20;return [logits]
        with tempfile.TemporaryDirectory() as folder:
            v=local_vision.LocalVision(folder);v.session=Session()
            person=v.segment(Image.new('RGB',(64,64)),'person');sky=v.segment(Image.new('RGB',(64,64)),'sky')
            self.assertTrue(person['local_only']);self.assertNotEqual(person['bitmap'],sky['bitmap'])
            self.assertEqual(v.session.input.shape,(1,3,512,512))
            self.assertGreater(person['coverage'],.05);self.assertGreater(sky['coverage'],.05)
