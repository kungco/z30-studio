import unittest
from unittest.mock import patch
import numpy as np
import lens_tools
import server
import spatial


class LensTests(unittest.TestCase):
    def test_exact_matching_interpolation_and_unknown_profiles_do_not_guess(self):
        meta=dict(lens_model='NIKKOR Z DX 16-50mm f/3.5-6.3 VR\x00',focal_mm=50,aperture=6.3,sensor_crop=1.531)
        chosen=lens_tools.select(meta,{})
        self.assertEqual(chosen['lens_calibration']['coefficients'],[.003,-.004,.020])
        middle=lens_tools.select(meta,dict(focal=32))['lens_calibration']
        np.testing.assert_allclose(middle['coefficients'],[.011,-.018,.0265],atol=1e-10)
        with self.assertRaises(ValueError):lens_tools.select(meta|dict(lens_model='unknown'),{})
        with self.assertRaises(ValueError):lens_tools.select(meta,{'focal':500})
        with self.assertRaises(ValueError):lens_tools.select(meta,{'distance':0})

    def test_auto_fill_has_no_black_edges_and_calibration_snapshot_needs_no_database(self):
        p=server.settings(lens_tools.select(dict(lens_model='Nikkor Z DX 16-50mm f/3.5-6.3 VR',focal_mm=50,aperture=6.3,sensor_crop=1.531),{}))
        arr=np.full((120,180,3),.25,np.float32);original=arr.copy()
        with patch.object(lens_tools,'database',side_effect=AssertionError('snapshot rendering must not fetch a newer database')):
            out=spatial.optical(arr,p)
        self.assertTrue(np.isfinite(out).all());self.assertGreater(out.min(),.24);self.assertEqual(out.dtype,np.float32)
        np.testing.assert_array_equal(arr,original)
        p['lens_calibration']['tca']=[0,0,.99,0,0,.99]
        out=spatial.optical(arr,p);self.assertGreater(out[...,1].min(),.24)

    def test_radial_polynomials_follow_focal_preserving_calibration_equations(self):
        r=np.array([0,.5,1,1.5],np.float32);a,b,c=.01,-.03,.04;d=1-a-b-c
        expected=1+c/d**2*r+b/d**3*r*r+a/d**4*r*r*r
        np.testing.assert_allclose(lens_tools.radial(r,dict(model='ptlens',coefficients=[a,b,c])),expected)
        np.testing.assert_allclose(lens_tools.radial(r,dict(model='poly5',coefficients=[.1,.01,0])),1+.1*r*r+.01*r**4)

    def test_tca_registration_improves_known_synthetic_channel_misregistration(self):
        h,w=260,360;yy,xx=np.mgrid[:h,:w];base=(.35+.13*np.sin(xx*.12)+.1*np.cos(yy*.15)+.08*np.sin((xx+yy)*.2)).astype(np.float32)
        cx,cy=(w-1)/2,(h-1)/2;x=xx.astype(np.float32)-cx;y=yy.astype(np.float32)-cy
        arr=np.stack([spatial._sample(base,cx+x*1.002,cy+y*1.002),base,spatial._sample(base,cx+x*.9985,cy+y*.9985)],axis=2)
        p=server.settings({});result=lens_tools.estimate_tca(arr,p)
        self.assertTrue(result['applied']);self.assertLess(result['settings']['lens_red'],0);self.assertGreater(result['settings']['lens_blue'],0)
        out=spatial.optical(arr,p|result['settings']);region=np.s_[30:-30,30:-30]
        before=np.mean(abs(arr[region][...,0]-arr[region][...,1]));after=np.mean(abs(out[region][...,0]-out[region][...,1]))
        self.assertLess(after,before*.25)
        self.assertFalse(lens_tools.estimate_tca(np.full_like(arr,.3),p)['applied'])

    def test_malformed_calibration_and_switches_are_rejected(self):
        for value in ({'lens_auto':True},{'lens_auto':'true'}, {'lens_calibration':{'model':'command'}}):
            with self.assertRaises(ValueError):server.settings(value)
