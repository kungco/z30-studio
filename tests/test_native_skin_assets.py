import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import prepare_native_skin as skin


class SkinAssetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.vendor=self.root/'vendor';self.vendor.mkdir()
        self.model=self.root/'source.onnx';self.model.write_bytes(b'pinned test model')
        self.manifest={'model':{'name':'test.onnx','bytes':self.model.stat().st_size,
                               'sha256':hashlib.sha256(self.model.read_bytes()).hexdigest()}}
        (self.vendor/'manifest.json').write_text(json.dumps(self.manifest),encoding='utf8')
        (self.vendor/'LICENSE.txt').write_text('fixture license',encoding='utf8')
        (self.vendor/'NOTICE.txt').write_text('fixture attribution',encoding='utf8')
        override=patch.object(skin,'VENDOR',self.vendor);override.start();self.addCleanup(override.stop)

    def test_explicit_preparation_verifies_and_keeps_source_and_existing_output(self):
        output=self.root/'component'
        info=skin.prepare(self.model,output)
        self.assertEqual(info,skin.verify_component(output))
        self.assertFalse(info['desktop_integration'])
        self.assertFalse(info['runtime_bundled'])
        self.assertTrue(info['external_vc_runtime_required'])
        before={p.name:p.read_bytes() for p in output.iterdir()}
        with self.assertRaises(ValueError): skin.prepare(self.model,output)
        self.assertEqual(before,{p.name:p.read_bytes() for p in output.iterdir()})
        self.assertEqual(self.model.read_bytes(),b'pinned test model')

    def test_same_length_corruption_is_not_trusted(self):
        output=self.root/'component';skin.prepare(self.model,output)
        (output/'test.onnx').write_bytes(b'pinned test modeX')
        with self.assertRaises(ValueError): skin.verify_component(output)
        self.model.write_bytes(b'pinned test modeX')
        with self.assertRaises(ValueError): skin.prepare(self.model,self.root/'bad')
        self.assertFalse((self.root/'bad').exists())

    def test_manifest_license_notice_and_additional_files_cannot_be_changed(self):
        for name in ['manifest.json','LICENSE.txt','NOTICE.txt']:
            with self.subTest(name=name):
                output=self.root/name.replace('.','-');skin.prepare(self.model,output)
                path=output/name;data=path.read_bytes()
                path.write_bytes(bytes([data[0]^1])+data[1:])
                with self.assertRaises(ValueError): skin.verify_component(output)
        output=self.root/'extra';skin.prepare(self.model,output)
        (output/'unknown.dll').write_bytes(b'not accepted')
        with self.assertRaises(ValueError): skin.verify_component(output)

    def test_missing_files_or_directory_in_place_of_model_fail_closed(self):
        output=self.root/'component';skin.prepare(self.model,output)
        (output/'test.onnx').unlink();(output/'test.onnx').mkdir()
        with self.assertRaises(ValueError): skin.verify_component(output)
        (output/'NOTICE.txt').unlink()
        with self.assertRaises(ValueError): skin.verify_component(output)


if __name__=='__main__': unittest.main()
