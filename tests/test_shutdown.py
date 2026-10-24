import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'nt', 'Windows launcher process')
class LauncherShutdownTests(unittest.TestCase):
    def test_headless_stop_file_exits_with_recorded_shutdown_phases(self):
        with tempfile.TemporaryDirectory(prefix='z30-shutdown-test-') as folder:
            root = Path(folder)
            data, ready, stop = root/'data', root/'ready.json', root/'stop.signal'
            cwd = root/'independent cwd'; cwd.mkdir()
            env = dict(os.environ, Z30_DATA_DIR=str(data))
            for key in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP'):
                env.pop(key, None)
            process = subprocess.Popen(
                [sys.executable, str(ROOT/'desktop.py'), '--headless', '--no-browser',
                 '--port', '0', '--ready-file', str(ready), '--stop-file', str(stop)],
                cwd=str(cwd), env=env)
            try:
                deadline = time.monotonic() + 30
                while not ready.exists():
                    if process.poll() is not None:
                        self.fail(f'launcher exited before ready: {process.returncode}')
                    if time.monotonic() > deadline:
                        self.fail('launcher did not become ready')
                    time.sleep(.05)
                url = json.loads(ready.read_text(encoding='utf8'))['url']
                self.assertTrue(url.startswith('http://127.0.0.1:'))
                started = time.monotonic()
                stop.write_text('stop', encoding='ascii')
                self.assertEqual(process.wait(timeout=20), 0)
                self.assertLess(time.monotonic() - started, 20)
            finally:
                if process.poll() is None:
                    process.kill(); process.wait()
            log = (data/'logs'/'launcher.log').read_text(encoding='utf8')
            self.assertEqual(log.count('Shutdown requested (stop file)'), 1)
            for phase in ('Shutdown requested (stop file)',
                          'Stopping session maintenance',
                          'Closing HTTP listener',
                          'Stopping Codex workers',
                          'Removing temporary photo copies',
                          'Local service stopped',
                          'Shutdown finished in',
                          'Launcher stopped'):
                self.assertIn(phase, log)
            self.assertFalse((data/'instance.json').exists())


if __name__ == '__main__':
    unittest.main()
