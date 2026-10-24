import io
import tempfile
import time
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np
import tifffile
from PIL import Image, ImageCms
import colour_management as colour
import export_tools
import server
from export_jobs import ExportQueue
from imaging import raster_linear
from processing import display_chunk,render_array
from tests.color_profiles import wide_rgb_profile,A98_TO_SRGB

class OutputTests(unittest.TestCase):
    def test_generated_profiles_match_independent_lcms_conversion(self):
        # Compare our float matrix/TRC transform with LCMS reading the actual ICC.
        a=np.array([[[60,90,110],[120,120,120],[180,140,110],[12,15,18]]],np.uint8)
        for space in ('adobe-rgb','prophoto'):
            blob=colour.profile(space);src=ImageCms.ImageCmsProfile(io.BytesIO(blob))
            image=Image.fromarray(a);image.info['icc_profile']=blob
            expected=np.asarray(colour.as_srgb(image)).astype(float)
            actual=colour.encode(colour.to_working(colour.decode(a.astype(np.float32)/255,space),space),'srgb')*255
            np.testing.assert_allclose(actual,expected,atol=1.5)
            self.assertEqual(src.profile.xcolor_space.strip(),'RGB')

    def test_wide_icc_input_retains_out_of_srgb_colours(self):
        image=Image.new('RGB',(3,2),(0,220,0));image.info['icc_profile']=wide_rgb_profile()
        with io.BytesIO() as b:
            image.save(b,'PNG',icc_profile=image.info['icc_profile']);b.seek(0)
            with Image.open(b) as source:a,policy,warnings=raster_linear(source,wide=True)
        expected=np.array([0,(220/255)**(563/256),0])@A98_TO_SRGB.T
        np.testing.assert_allclose(a[0,0],expected,atol=1e-4)
        self.assertLess(a[0,0,0],0);self.assertIn('浮点',policy)
        for space in colour.SPACES:
            rgb=display_chunk(a,server.settings({'output_space':space,'saturation':55,'grading':{'shadows':{'hue':40,'saturation':10}}}))
            self.assertTrue(np.isfinite(rgb).all());self.assertGreaterEqual(rgb.min(),0);self.assertLessEqual(rgb.max(),1)

    def test_float_output_resize_sharpen_watermark_and_tiff_precision(self):
        ramp=np.linspace(.01,.7,1001,dtype=np.float32);a=np.repeat(np.repeat(ramp[None,:,None],39,axis=0),3,axis=2)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'original.png';Image.new('RGB',(5,3)).save(path);before=path.read_bytes()
            p=server.settings({'output_space':'prophoto','exposure':.2});encoded=render_array(a,p,bits=32)
            data=export_tools.encode(encoded,'prophoto','TIFF16',95,path,{'width':600,'output_sharpen':30,'watermark':'Z30','watermark_size':5})
            with tifffile.TiffFile(io.BytesIO(data)) as tif:
                output=tif.asarray();self.assertEqual(output.dtype,np.uint16);self.assertEqual(output.shape[1],600)
                self.assertTrue(np.any(output%257));self.assertEqual(tif.pages[0].tags[34675].value,colour.profile('prophoto'))
            self.assertEqual(path.read_bytes(),before)

    def test_metadata_policy_strips_location_and_normalizes_orientation(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source.jpg';exif=Image.Exif();exif[271]='Test Camera';exif[274]=6;exif[315]='Author';exif[34853]={1:'N',2:(1.,2.,3.)}
            Image.new('RGB',(40,30)).save(source,exif=exif);pixels=np.full((30,40,3),.4,np.float32)
            for fmt in ('JPEG','PNG','TIFF16'):
                kept=export_tools.encode(pixels,'adobe-rgb',fmt,95,source,{'metadata':'basic'})
                cleared=export_tools.encode(pixels,'adobe-rgb',fmt,95,source,{'metadata':'clear'})
                if fmt=='TIFF16':
                    with tifffile.TiffFile(io.BytesIO(kept)) as t:self.assertEqual(t.pages[0].tags[271].value,'Test Camera');self.assertNotIn(34853,t.pages[0].tags)
                    with tifffile.TiffFile(io.BytesIO(cleared)) as t:self.assertNotIn(271,t.pages[0].tags)
                else:
                    with Image.open(io.BytesIO(kept)) as im:self.assertEqual(im.getexif()[271],'Test Camera');self.assertEqual(im.getexif()[274],1);self.assertNotIn(34853,im.getexif())
                    with Image.open(io.BytesIO(cleared)) as im:self.assertEqual(len(im.getexif()),0)

    def test_display_proof_is_preview_only_and_invalid_icc_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=colour.Profiles(Path(tmp)/'icc');info=store.add(colour.profile('adobe-rgb'))
            image=Image.new('RGB',(6,4),(50,150,90));image.info['icc_profile']=colour.profile('srgb')
            p=server.settings(dict(proof_profile=info['id'],proof_enabled=True,display_profile=info['id'],proof_gamut=True))
            proof=store.preview(image,p);self.assertEqual(proof.info['icc_profile'],colour.profile('adobe-rgb'))
            path=Path(tmp)/'original.png';image.save(path);session=dict(path=path,raw=False)
            with patch.object(server,'get_profiles',side_effect=AssertionError('export must not proof')):
                a=server.export_bytes(session,p,'TIFF16',options={});b=server.export_bytes(session,server.DEFAULTS,'TIFF16',options={})
            np.testing.assert_array_equal(tifffile.imread(io.BytesIO(a)),tifffile.imread(io.BytesIO(b)))
            with self.assertRaises(ValueError):store.add(b'bad ICC')
            with self.assertRaises(ValueError):store.read('../outside')

    def test_frozen_per_photo_export_recipes_options_and_safe_unique_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'original.png';path.write_bytes(b'original');release=__import__('threading').Event();seen=[]
            def exporter(source,raw,p,fmt,quality,protect,options):
                release.wait(2);seen.append((p['exposure'],options['long_edge']));return b'export',p,None
            q=ExportQueue(Path(tmp)/'jobs',exporter)
            try:
                edits=[{'exposure':.2},{'exposure':1.2}];opts={'long_edge':1200,'name_template':'{name}'}
                key=q.submit([dict(path=path,name='same.png')]*2,{},'PNG',90,False,options=opts,per_photo=edits)
                edits[0]['exposure']=3;opts['long_edge']=10;release.set();deadline=time.monotonic()+3
                while q.get(key)['state'] in ('queued','running') and time.monotonic()<deadline:time.sleep(.01)
                self.assertEqual(q.get(key)['state'],'done');self.assertEqual(seen,[(.2,1200),(1.2,1200)])
                with q.downloads(key) as (entries,report):self.assertEqual([e[1] for e in entries],['same.png','same-2.png'])
                self.assertEqual(path.read_bytes(),b'original')
            finally:q.shutdown()
        for invalid in ('../{name}','{name.__class__}','{index!r}','CON/{index}'):
            with self.assertRaises(ValueError):export_tools.normalize({'name_template':invalid})
        self.assertEqual(export_tools.dimensions((6000,4000),export_tools.normalize({'width':1200,'height':1200})),(1200,800))

if __name__=='__main__':unittest.main()
