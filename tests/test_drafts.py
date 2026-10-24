import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image
import drafts
import diagnostics
import projects
import server


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.source = self.root/'original.png'; self.source.write_bytes(b'original data')
        self.store = drafts.DraftStore(self.root/'drafts')
        self.asset = self.store.add_asset(self.source)
        self.document = dict(photos=[dict(asset=self.asset, settings={'exposure':.2})], selected=0)
        self.id = 'a'*32

    def tearDown(self):
        self.temp.cleanup()

    def test_reopen_deduplicates_originals_and_keeps_versions(self):
        self.assertEqual(self.store.add_asset(self.source), self.asset)
        self.assertEqual(len(list(self.store.blobs.iterdir())), 1)
        self.store.save(self.id, self.document, 0, '1'*32)
        changed = dict(photos=[dict(asset=self.asset, settings={'exposure':.8})], selected=0)
        self.store.save(self.id, changed, 1, '2'*32, snapshot=True)
        reopened = drafts.DraftStore(self.root/'drafts')
        self.assertEqual(reopened.load(self.id)['document'], changed)
        self.assertEqual(reopened.load(self.id, 1)['document'], self.document)
        self.assertEqual(reopened.asset_path(self.asset, verify=True).read_bytes(), self.source.read_bytes())

    def test_uncertain_retry_is_idempotent_and_stale_tab_cannot_overwrite(self):
        self.assertEqual(self.store.save(self.id, self.document, 0, '1'*32), 1)
        self.assertEqual(self.store.save(self.id, self.document, 0, '1'*32), 1)
        with self.assertRaises(drafts.Conflict):
            self.store.save(self.id, self.document, 0, '2'*32)
        with patch.object(self.store, 'asset_path', side_effect=OSError('disk failed')):
            with self.assertRaises(OSError):
                self.store.save(self.id, self.document, 1, '3'*32)
        self.assertEqual(self.store.load(self.id)['revision'], 1)

    def test_crash_during_sqlite_transaction_preserves_acknowledged_edit(self):
        self.store.save(self.id, self.document, 0, '1'*32)
        code = ('import sqlite3,sys,os; db=sqlite3.connect(sys.argv[1]); '
                'db.execute("BEGIN IMMEDIATE"); '
                'db.execute("UPDATE drafts SET document=?", ("broken",)); os._exit(7)')
        result = subprocess.run([sys.executable,'-c',code,str(self.store.root/'drafts.sqlite3')])
        self.assertEqual(result.returncode, 7)
        self.assertEqual(drafts.DraftStore(self.store.root).load(self.id)['document'], self.document)

    def test_corrupt_or_missing_original_never_becomes_a_valid_recovery(self):
        path = self.store.asset_path(self.asset)
        path.write_bytes(b'changed data!')
        with self.assertRaises(ValueError): self.store.asset_path(self.asset, verify=True)
        with self.assertRaises(ValueError): self.store.asset_path('../original.png')
        self.assertEqual(self.source.read_bytes(), b'original data')

    def test_storage_quota_delete_and_live_source_protection(self):
        self.store.save(self.id, self.document, 0, '1'*32)
        self.store.delete(self.id, protected=[self.asset])
        self.assertTrue(self.store.asset_path(self.asset).exists())
        self.store.delete(self.id)
        self.assertFalse((self.store.blobs/self.asset).exists())
        limited = drafts.DraftStore(self.root/'limited', max_bytes=2)
        with self.assertRaises(ValueError): limited.add_asset(self.source)
        self.assertFalse(list(limited.blobs.glob('*.part')))

    def test_256_photo_portable_project_roundtrip_exceeds_old_count_limit(self):
        sources = [dict(path=self.source, name=f'photo-{i}.png', meta={'id':str(i)}) for i in range(256)]
        document = projects.manifest(sources, {}, 255, server.settings)
        output = self.root/'large.z30proj'
        projects.write_project(output, sources, document)
        with projects.read_project(output, self.root, server.settings) as reopened:
            self.assertEqual(len(reopened['photos']), 256)
            self.assertEqual(reopened['selected'], 255)
            self.assertTrue(all(p['staged'].read_bytes() == b'original data' for p in reopened['photos']))
        self.assertGreaterEqual(projects.PROJECT_LIMIT, 8 * 1024**3)

    def test_diagnostics_allowlist_does_not_bundle_photos_keys_or_configuration(self):
        logs = self.root/'logs'; logs.mkdir()
        (logs/'launcher.log').write_text('Authorization: Bearer example-private-token\napi_key="secret-token"\nsk-123456789abcdef', encoding='utf8')
        for file in ('auth.json', 'ai-provider.json', 'original.nef', 'other.log'):
            (logs/file).write_text('private')
        with zipfile.ZipFile(io.BytesIO(diagnostics.bundle(self.root, 'test'))) as archive:
            self.assertEqual(set(archive.namelist()), {'summary.json','launcher.log'})
            text = archive.read('launcher.log').decode()
            self.assertNotIn('example-private-token', text); self.assertNotIn('secret-token', text)
            self.assertNotIn('sk-123456789abcdef', text)


class DraftSessionTests(unittest.TestCase):
    def test_restore_is_lazy_and_closing_a_session_never_deletes_durable_original(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server,'data_root',return_value=Path(folder)):
            store = server.get_drafts(); original = Path(folder)/'original.png'
            image=Image.new('RGB',(16,12),(150,100,80));image.save(original)
            asset=store.add_asset(original)
            photo=dict(asset=asset, meta=dict(name='original.png',width=16,height=12,camera='test',raw=False,bytes=original.stat().st_size),
                       settings=server.settings({'exposure':.3}), history=None, undo=None, analysis=None)
            store.save('d'*32,dict(photos=[photo]*100,selected=54),0,'1'*32)
            with patch.object(server,'decode',side_effect=AssertionError('restore must not decode the library')):
                result=server.restore_draft({'draft_id':'d'*32},'c'*32)
            self.assertEqual(len(result['photos']),100);self.assertEqual(result['selected'],54)
            keys=[p['id'] for p in result['photos']]
            try:
                preview=server.preview_result(server.SESSIONS[keys[54]],photo['settings'],True)
                self.assertEqual((preview['width'],preview['height']),(16,12))
            finally:
                for key in keys:server.release_photo(key)
                server.STORES.pop(Path(folder)/'drafts',None)
            self.assertTrue(store.asset_path(asset,verify=True).exists())
            self.assertEqual(original.read_bytes(),store.asset_path(asset).read_bytes())


if __name__=='__main__':unittest.main()
