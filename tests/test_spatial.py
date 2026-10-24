import unittest
import numpy as np
import processing
import spatial
import server

class SpatialTests(unittest.TestCase):
    def test_disabled_spatial_processing_is_identity_without_copy(self):
        a=np.random.default_rng(1).random((40,50,3),dtype=np.float32)*.3
        self.assertIs(spatial.prepare(a,server.DEFAULTS),a)
        np.testing.assert_array_equal(processing.render_array(a,server.DEFAULTS),processing.render_array(a,{k:v for k,v in server.DEFAULTS.items() if k!='masks'}))

    def test_masks_are_spatial_local_invertible_and_chunk_consistent(self):
        a=np.full((100,120,3),.1,np.float32);original=a.copy()
        for kind in ('linear','radial','brush'):
            m=spatial.masks([dict(type=kind,exposure=1,x0=0,y0=.5,x1=1,y1=.5,points=[[.4,.5],[.6,.5]])])[0]
            field=spatial.mask_field(m,120,100,0,100)
            self.assertGreater(field.max(),.9)
            if kind=='linear':
                t=.5/120
                self.assertAlmostEqual(float(field[50,0]),t*t*(3-2*t),places=7)
            else:self.assertEqual(field.min(),0)
            inverse=spatial.mask_field(m|dict(invert=True),120,100,0,100)
            np.testing.assert_allclose(field+inverse,1,atol=1e-6)
            p=server.DEFAULTS|dict(masks=[m],sharpen=10)
            out=processing.render_array(a,p);small=processing.render_array(a,p,chunk_rows=7)
            np.testing.assert_array_equal(out,small)
            self.assertGreater(float(out.max()),float(out.min()))
        np.testing.assert_array_equal(a,original)

    def test_denoise_reduces_flat_noise_and_keeps_an_edge_and_float_precision(self):
        rng=np.random.default_rng(3);a=np.full((140,160,3),.15,np.float32);a[:,80:]=.65
        a+=rng.normal(0,.015,a.shape).astype(np.float32);before=a.copy()
        out=spatial.denoise(a,{'denoise_luma':90,'denoise_chroma':90})
        self.assertEqual(out.dtype,np.float32)
        self.assertLess(float(out[10:120,10:60].var()),float(a[10:120,10:60].var())*.7)
        self.assertGreater(float(out[:,82:].mean()-out[:,:78].mean()),.45)
        self.assertGreater(np.unique(out[20,:60,0]).size,32)
        np.testing.assert_array_equal(a,before)
        chroma=spatial.denoise(a,{'denoise_luma':0,'denoise_chroma':80})
        np.testing.assert_allclose(chroma@spatial.LUMA,a@spatial.LUMA,atol=2e-6)
        owned=a.copy();same=spatial.denoise(owned,{'denoise_luma':90,'denoise_chroma':90},inplace=True)
        self.assertIs(same,owned);np.testing.assert_allclose(same,out,atol=1e-7)

    def test_manual_optics_changes_radius_and_red_channel_without_mutating_source(self):
        x=np.broadcast_to(np.linspace(.1,.8,100,dtype=np.float32)[None,:,None],(80,100,3)).copy()
        original=x.copy();out=spatial.optical(x,dict(lens_red=6))
        np.testing.assert_allclose(out[10:-10,10:-10,1],x[10:-10,10:-10,1],atol=2e-6)
        self.assertGreater(float(abs(out[...,0]-out[...,1]).max()),.001)
        warped=spatial.optical(x,dict(lens_k1=.1,vignette=30))
        self.assertTrue(np.isfinite(warped).all());self.assertEqual(warped.dtype,np.float32)
        np.testing.assert_array_equal(x,original)

    def test_invalid_masks_and_unbounded_strokes_are_rejected(self):
        for m in ([dict(type='command')],[dict(type='radial',exposure=float('nan'))],[dict(type='brush',points=[[.5,.5]]*513)]):
            with self.assertRaises(ValueError):server.settings({'masks':m})

    def test_high_depth_spatial_pipeline_keeps_more_than_256_levels(self):
        a=np.repeat(np.linspace(.01,.5,600,dtype=np.float32)[None,:,None],3,axis=2)
        a=np.repeat(a,30,axis=0)
        p=server.settings(dict(denoise_chroma=30,masks=[dict(type='linear',exposure=.3)]))
        out=processing.render_array(spatial.prepare(a,p),p,bits=16)
        self.assertEqual(out.dtype,np.uint16);self.assertGreater(np.unique(out).size,256)
        self.assertFalse(np.all(out%257==0))

    def test_separate_brush_strokes_do_not_paint_a_connector(self):
        m=spatial.masks([dict(type='brush',points=[[.2,.2],[.8,.8]],breaks=[1],exposure=.5)])[0]
        field=spatial.mask_field(m,100,100,0,100)
        self.assertGreater(field[20,20],.9);self.assertGreater(field[79,79],.9)
        self.assertEqual(field[50,50],0)
        joined=spatial.mask_field(m|{'breaks':[]},100,100,0,100)
        self.assertGreater(joined[50,50],.9)
        for bad in ([0],[2],[1,1],[True],['1']):
            with self.assertRaises(ValueError):spatial.masks([m|{'breaks':bad}])
