import io
import json
import tempfile
import unittest
from pathlib import Path
import zipfile

import projects
import server


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.original = self.root/'original.png'; self.original.write_bytes(b'exact original bytes')
        self.sources = [dict(path=self.original, name='original.png', meta={'id':'photo'})]
        self.history = dict(index=1, states=[dict(settings=server.DEFAULTS, preset='neutral'),
                            dict(settings=server.DEFAULTS|{'exposure': .5}, preset='custom')])
        self.doc = projects.manifest(self.sources, {'photo':dict(settings={'exposure':.5}, history=self.history)}, 0, server.settings)

    def tearDown(self): self.temp.cleanup()

    def write(self, document=None, extra=None):
        path=self.root/'test.z30proj'
        projects.write_project(path,self.sources,document or self.doc)
        if extra:
            with zipfile.ZipFile(path,'a') as archive: archive.writestr(*extra)
        return path

    def test_portable_original_history_and_settings_roundtrip(self):
        with projects.read_project(self.write(),self.root,server.settings) as loaded:
            self.assertEqual(loaded['photos'][0]['staged'].read_bytes(),self.original.read_bytes())
            self.assertEqual(loaded['photos'][0]['settings']['exposure'],.5)
            self.assertEqual(loaded['photos'][0]['history']['index'],1)
        self.assertEqual(self.original.read_bytes(),b'exact original bytes')

    def test_corrupt_source_and_unlisted_paths_are_rejected(self):
        bad=json.loads(json.dumps(self.doc));bad['photos'][0]['sha256']='0'*64
        with self.assertRaises(ValueError):
            with projects.read_project(self.write(bad),self.root,server.settings): pass
        with self.assertRaises(ValueError):
            with projects.read_project(self.write(extra=('../escape',b'bad')),self.root,server.settings): pass
        self.assertFalse((self.root.parent/'escape').exists())

    def test_inconsistent_history_or_future_version_is_rejected(self):
        for key in ('history','version'):
            bad=json.loads(json.dumps(self.doc))
            if key=='history':bad['photos'][0]['history']['index']=0
            else:bad['version']=999
            with self.assertRaises(ValueError):
                with projects.read_project(self.write(bad),self.root,server.settings): pass
