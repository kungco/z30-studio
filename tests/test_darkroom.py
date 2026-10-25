import io
import base64
import unittest
from unittest.mock import patch
import numpy as np
import tifffile
import darkroom
import processing
import server
from PIL import Image


class DarkroomTests(unittest.TestCase):
    def test_neutral_controls_preserve_existing_pixels_and_do_not_mutate_input(self):
        arr=np.random.default_rng(6).uniform(0,1,(75,91,3)).astype(np.float32);original=arr.copy()
        legacy={k:v for k,v in server.DEFAULTS.items() if k not in darkroom.DEFAULTS}
        np.testing.assert_array_equal(processing.render_array(arr,legacy,bits=16),processing.render_array(arr,server.settings({}),bits=16))
        np.testing.assert_array_equal(arr,original)

    def test_curve_is_monotone_smooth_and_individual_channel_edits_are_isolated(self):
        points=((0,.02),(.2,.12),(.6,.8),(1,.98));table=darkroom.curve_table(points)
        self.assertTrue(np.all(np.diff(table)>=0));self.assertAlmostEqual(float(table[0]),.02,places=6)
        self.assertAlmostEqual(float(table[-1]),.98,places=6)
        self.assertLess(np.max(np.abs(np.diff(table,n=2))),.00002)
        rgb=np.full((4,5,3),.3,np.float32);out=darkroom.color(rgb,dict(curves={'r':[list(p) for p in points]}))
        np.testing.assert_array_equal(out[...,1:],rgb[...,1:]);self.assertFalse(np.array_equal(out[...,0],rgb[...,0]))

    def test_hsl_and_grading_are_finite_bounded_and_preserve_neutral_when_no_toning(self):
        rgb=np.array([[[.8,.25,.18],[.1,.3,.8],[.4,.4,.4]]],np.float32)
        p=server.settings(dict(hsl={'red':dict(hue=20,saturation=50,luminance=10)}))
        out=darkroom.color(rgb,p)
        self.assertTrue(np.isfinite(out).all());self.assertGreaterEqual(out.min(),0);self.assertLessEqual(out.max(),1)
        np.testing.assert_allclose(out[0,2],rgb[0,2],atol=.0001)
        self.assertGreater(np.linalg.norm(out[0,0]-rgb[0,0]),np.linalg.norm(out[0,1]-rgb[0,1]))
        graded=darkroom.color(rgb,server.settings({'grading':{'shadows':dict(hue=240,saturation=20)}}))
        self.assertFalse(np.array_equal(graded,rgb));self.assertTrue(np.isfinite(graded).all())

    def test_grey_card_sample_and_kelvin_gains_are_exposure_normalized(self):
        arr=np.ones((10,12,3),np.float32)*np.array([.3,.2,.1],np.float32)
        gains=darkroom.sample_neutral(arr,.5,.5);neutral=arr[4,5]*gains
        np.testing.assert_allclose(neutral,np.mean(neutral),rtol=1e-6)
        np.testing.assert_allclose(darkroom.kelvin_gains(6500),[1,1,1],atol=1e-6)
        warm=darkroom.kelvin_gains(3200);self.assertGreater(warm[2],warm[0])
        self.assertAlmostEqual(float(warm@processing.LUMA),1,places=6)
        with self.assertRaises(ValueError):darkroom.sample_neutral(np.zeros_like(arr),.5,.5)
        self.assertEqual(server.source_wb({'raw':True},server.settings({'wb_mode':'custom'})),'daylight')

    def test_free_crop_and_straighten_preserve_float_precision_and_have_no_black_triangles(self):
        arr=np.linspace(.1,.9,120*180*3,dtype=np.float32).reshape(120,180,3)
        for rotation in range(4):
            for angle in (-30,0,17):
                p=server.settings(dict(rotation=rotation,straighten=angle,crop='free',crop_rect=[.1,.2,.6,.7]))
                out=server.geometry(arr,p);self.assertGreater(out.min(),0);self.assertEqual(out.dtype,np.float32)
                self.assertLess(out.shape[0]*out.shape[1],120*180)

    def test_detail_and_curves_have_no_strip_seams_and_tiff_keeps_sub8bit_levels(self):
        y,x=np.mgrid[:131,:197];arr=np.stack([.1+x/500,.1+y/350,.15+x/700],axis=-1).astype(np.float32)
        p=server.settings(dict(clarity=70,texture=40,dehaze=10,sharpen=25,
            curves={'rgb':[[0,0],[.3,.35],[.7,.78],[1,1]]},hsl={'orange':{'saturation':15}}))
        a=processing.render_array(arr,p,bits=16,chunk_rows=19);b=processing.render_array(arr,p,bits=16,chunk_rows=131)
        np.testing.assert_allclose(a,b,atol=1)
        with patch.object(server,'decode',return_value=arr):blob=server.export_bytes({},p,'TIFF16')
        out=tifffile.imread(io.BytesIO(blob));self.assertEqual(out.dtype,np.uint16)
        self.assertGreater(len(np.unique(out)),256);self.assertTrue(np.any(out%257))

    def test_invalid_curves_ranges_and_crop_are_rejected(self):
        for data in ({'curves':{'rgb':[[0,0],[.7,.8],[.5,.6],[1,1]]}},
                     {'kelvin':float('nan')},{'wb_gains':[1,False,1]},
                     {'crop_rect':[.5,0,.8,1]},{'hsl':{'red':{'hue':90}}}):
            with self.subTest(data=data),self.assertRaises(ValueError):server.settings(data)

    def test_before_preview_and_cache_keep_the_identical_free_crop_and_straighten(self):
        arr=np.full((128,180,3),.25,np.float32);session={'meta':{'id':'darkroom-composition-test'}}
        try:
            with patch.object(server,'decode',return_value=arr):
                widths=[]
                for width in (.4,.7):
                    p=server.settings(dict(crop='free',crop_rect=[.1,.2,width,.6],straighten=7,exposure=.3))
                    result=server.preview_result(session,p,True)
                    before=Image.open(io.BytesIO(base64.b64decode(result['before'].split(',')[1])))
                    self.assertEqual(before.size,(result['width'],result['height']));widths.append(before.width)
                self.assertNotEqual(*widths)
        finally:server.CACHE.remove_source('darkroom-composition-test')


if __name__=='__main__':unittest.main()
