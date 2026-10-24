import hashlib
import io
import json
from pathlib import Path
from unittest.mock import patch
import tempfile
import threading
import time
import unittest
import zipfile

from export_jobs import ExportQueue, write_zip


class ExportQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input = self.root / 'original.png'; self.input.write_bytes(b'original source bytes')
        self.queues = []

    def tearDown(self):
        for q in self.queues:
            q.shutdown()
        self.temp.cleanup()

    def queue(self, exporter, **kw):
        q = ExportQueue(self.root / 'jobs', exporter, **kw); self.queues.append(q); return q

    def source(self):
        return dict(path=self.input, name='same-name.png')

    def wait(self, q, key):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            j = q.get(key)
            if j['state'] not in ('queued', 'running') and not j.get('worker_active'):
                return j
            time.sleep(.01)
        self.fail('background worker did not reach a terminal state')

    def test_batch_snapshot_zip_and_original_preservation(self):
        def exporter(path, raw, p, fmt, quality, protect):
            self.assertEqual(path.read_bytes(), b'original source bytes')
            return b'converted PNG', p, None
        q = self.queue(exporter)
        before = self.input.read_bytes()
        key = q.submit([self.source(), self.source()], {'exposure': .5}, 'PNG', 95, False)
        self.assertEqual(self.wait(q, key)['state'], 'done')
        self.assertEqual(self.input.read_bytes(), before)
        buffer = io.BytesIO()
        with q.downloads(key) as (entries, report):
            write_zip(buffer, entries, report)
        with zipfile.ZipFile(buffer) as archive:
            self.assertEqual(set(archive.namelist()), {'01-same-name-Z30.png', '02-same-name-Z30.png', 'export-report.json'})
        q.delete(key)
        self.assertFalse((q.root / key).exists())
        self.assertEqual(self.input.read_bytes(), before)

    def test_partial_failure_retry_only_failed_items_after_restart(self):
        def initial(path, *args):
            if 'input-1' in path.name:
                raise OSError('simulated disk failure')
            return b'done once', args[1], None
        q = self.queue(initial)
        key = q.submit([self.source(), self.source()], {}, 'PNG', 95, False)
        self.assertEqual(self.wait(q, key)['state'], 'partial')
        first = (q.root / key / 'output-0.png').stat().st_mtime_ns
        q.shutdown()
        calls = []
        def retry(path, raw, p, *args):
            calls.append(path.name); return b'retry result', p, None
        recovered = self.queue(retry); recovered.retry(key)
        self.assertEqual(self.wait(recovered, key)['state'], 'done')
        self.assertEqual(calls, ['input-1.png'])
        self.assertEqual((q.root / key / 'output-0.png').stat().st_mtime_ns, first)

    def test_low_disk_budget_keeps_all_outputs_downloadable_without_stored_zip(self):
        q = self.queue(lambda path, raw, p, *args: (b'x' * 4096, p, None), max_bytes=12000)
        key = q.submit([self.source(), self.source()], {}, 'PNG', 95, False)
        self.assertEqual(self.wait(q, key)['state'], 'done')
        self.assertFalse((q.root / key / 'results.zip').exists())
        before = q.disk_bytes()
        target = io.BytesIO()
        with q.downloads(key) as (entries, report):
            with self.assertRaises(ValueError):
                q.delete(key)
            write_zip(target, entries, report)
        with zipfile.ZipFile(target) as archive:
            self.assertEqual(archive.read('01-same-name-Z30.png'), b'x' * 4096)
            self.assertEqual(archive.read('02-same-name-Z30.png'), b'x' * 4096)
        self.assertEqual(q.disk_bytes(), before)

    def test_completed_files_survive_job_error_and_download_disconnect(self):
        q = self.queue(lambda path, raw, p, *args: (b'done', p, None))
        key = q.submit([self.source()], {}, 'PNG', 95, False); self.wait(q, key)
        q.jobs[key]['state'] = 'error'  # Legacy packaging failure must not hide completed files.
        with self.assertRaises(BrokenPipeError):
            with q.downloads(key, 0) as (entries, report):
                self.assertEqual(entries[0][0].read_bytes(), b'done')
                raise BrokenPipeError()
        self.assertFalse(q.readers)
        with self.assertRaises(ValueError):
            with q.downloads(key, -1):
                pass
        q.delete(key)

    def test_inflight_manifest_is_interrupted_on_restart(self):
        q = self.queue(lambda path, raw, p, *args: (b'data', p, None))
        key = q.submit([self.source()], {}, 'PNG', 95, False); self.wait(q, key); q.shutdown()
        manifest = q.root / key / 'manifest.json'
        job = json.loads(manifest.read_text(encoding='utf8'))
        job['state'] = 'running'; job['worker_active'] = True; job['items'][0]['state'] = 'running'
        manifest.write_text(json.dumps(job), encoding='utf8')
        recovered = self.queue(lambda path, raw, p, *args: (b'recovered', p, None))
        self.assertEqual(recovered.get(key)['state'], 'interrupted')
        self.assertFalse(recovered.get(key)['worker_active'])
        recovered.retry(key); self.assertEqual(self.wait(recovered, key)['state'], 'done')

    def test_cancellation_prevents_late_output_and_delete_is_safe(self):
        entered, released = threading.Event(), threading.Event()
        def blocked(path, raw, p, *args):
            entered.set(); released.wait(3); return b'late result', p, None
        q = self.queue(blocked); key = q.submit([self.source()], {}, 'PNG', 95, False)
        self.assertTrue(entered.wait(2)); q.cancel(key)
        with self.assertRaises(ValueError):
            q.delete(key)
        with self.assertRaises(ValueError):
            q.retry(key)
        released.set(); self.assertEqual(self.wait(q, key)['state'], 'cancelled')
        self.assertFalse((q.root / key / 'output-0.png').exists())
        q.delete(key); self.assertTrue(self.input.exists())

    def test_bounded_shutdown_interrupts_queued_work_and_never_publishes_late_output(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def blocked(path, raw, p, *args):
            calls.append(path.name); entered.set(); release.wait(3)
            return b'late result', p, None
        q = self.queue(blocked, shutdown_timeout=.2)
        first = q.submit([self.source()], {}, 'PNG', 95, False)
        second = q.submit([self.source()], {}, 'PNG', 95, False)
        self.assertTrue(entered.wait(2))
        started = time.monotonic(); q.shutdown()
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(q.get(first)['state'], 'interrupted')
        self.assertEqual(q.get(second)['state'], 'interrupted')
        release.set()
        self.assertTrue(q.closed.wait(3))
        self.assertEqual(calls, ['input-0.png'])
        self.assertFalse((q.root / first / 'output-0.png').exists())
        self.assertFalse((q.root / second / 'output-0.png').exists())
        self.assertFalse(q.get(first)['worker_active'])

    def test_restart_removes_stale_partial_and_manifest_temporary_files(self):
        q = self.queue(lambda path, raw, p, *args: (b'done', p, None))
        key = q.submit([self.source()], {}, 'PNG', 95, False)
        self.assertEqual(self.wait(q, key)['state'], 'done')
        q.shutdown()
        partial = q.root / key / 'output-0.part'; partial.write_bytes(b'partial')
        temporary = q.root / key / 'manifest.json.tmp'; temporary.write_text('{', encoding='utf8')
        recovered = self.queue(lambda path, raw, p, *args: (b'again', p, None))
        self.assertFalse(partial.exists())
        self.assertFalse(temporary.exists())
        self.assertEqual(recovered.get(key)['state'], 'done')

    def test_same_root_cannot_have_two_live_queue_owners(self):
        self.queue(lambda *args: None)
        with self.assertRaises(ValueError):
            ExportQueue(self.root / 'jobs', lambda *args: None)

    def test_shutdown_deadline_includes_contended_lock_and_slow_manifest_write(self):
        q = self.queue(lambda *args: (b'output',{},None), shutdown_timeout=.1)
        entered,release=threading.Event(),threading.Event()
        def hold_lock():
            with q.lock:entered.set();release.wait(3)
        thread=threading.Thread(target=hold_lock);thread.start();self.assertTrue(entered.wait(2))
        started=time.monotonic();q.shutdown();elapsed=time.monotonic()-started
        self.assertLess(elapsed,.8);self.assertTrue(q.stopped)
        release.set();thread.join(3);self.assertTrue(q.closed.wait(3))

    def test_restart_marks_missing_completed_results_and_damaged_inputs_for_user_action(self):
        q=self.queue(lambda *args:(b'output',{},None))
        key=q.submit([self.source()],{},'PNG',95,False);self.wait(q,key);q.shutdown()
        (q.root/key/'output-0.png').unlink()
        recovered=self.queue(lambda *args:(b'retry',{},None))
        self.assertEqual(recovered.get(key)['state'],'interrupted')
        self.assertEqual(recovered.get(key)['items'][0]['state'],'pending')
        recovered.shutdown();(q.root/key/'input-0.png').unlink()
        damaged=self.queue(lambda *args:self.fail('must not run automatically'))
        self.assertEqual(damaged.get(key)['state'],'error')
        self.assertIn('校验',damaged.get(key)['items'][0]['error'])

    def test_changed_snapshot_is_rejected_on_retry(self):
        q = self.queue(lambda *args: (_ for _ in ()).throw(OSError('retry later')))
        key = q.submit([self.source()], {}, 'PNG', 95, False); self.wait(q, key)
        (q.root / key / 'input-0.png').write_bytes(b'corrupted')
        q.retry(key); job = self.wait(q, key)
        self.assertEqual(job['state'], 'error')
        self.assertIn('校验', job['items'][0]['error'])

    def test_path_and_space_limits_are_enforced(self):
        q = self.queue(lambda *args: None, max_bytes=10)
        with self.assertRaises(ValueError):
            q._folder('../outside')
        with self.assertRaises(ValueError):
            q.submit([self.source()], {}, 'PNG', 95, False)
        self.assertTrue(self.input.exists())


if __name__ == '__main__':
    unittest.main()
