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
    def test_trace_survives_complete_phase_when_interpreter_exit_is_blocked(self):
        from shutdown_watchdog import wait_for_exit
        with tempfile.TemporaryDirectory(prefix='z30-atexit-trace-') as folder:
            root=Path(folder);ready=root/'ready.json';stop=root/'stop.signal'
            code="import atexit,time,sys,desktop;atexit.register(lambda:time.sleep(60));sys.exit(desktop.main(sys.argv[1:]))"
            process=subprocess.Popen([sys.executable,'-c',code,'--headless','--no-browser','--port','0','--ready-file',str(ready),
                '--stop-file',str(stop),'--shutdown-grace','8','--shutdown-phase-timeout','4'],cwd=ROOT,
                env=os.environ|{'Z30_DATA_DIR':str(root/'data')},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            supervisor=None
            try:
                deadline=time.monotonic()+30
                while not ready.exists():
                    self.assertIsNone(process.poll())
                    if time.monotonic()>deadline:self.fail('launcher not ready')
                    time.sleep(.05)
                supervisor=json.loads(ready.read_text(encoding='utf8'))['watchdog_pid'];stop.write_text('stop')
                self.assertEqual(process.wait(12),3)
                traces=''.join(p.read_text(encoding='utf8') for p in (root/'data/logs').glob('shutdown-stacks-*.log'))
                self.assertIn('<lambda>',traces)
                self.assertEqual(json.loads(next((root/'data/logs').glob('shutdown-*.json')).read_text())['phase'],'complete')
            finally:
                if process.poll() is None:process.kill();process.wait()
                wait_for_exit(supervisor)

    def test_unread_inherited_stdout_cannot_stall_requests_or_shutdown(self):
        import socket
        from shutdown_watchdog import wait_for_exit
        with tempfile.TemporaryDirectory(prefix='z30-undrained-pipe-') as folder:
            root=Path(folder);ready=root/'ready.json';stop=root/'stop.signal'
            process=subprocess.Popen([sys.executable,str(ROOT/'desktop.py'),'--headless','--no-browser','--port','0',
                '--ready-file',str(ready),'--stop-file',str(stop)],cwd=root,env=os.environ|{'Z30_DATA_DIR':str(root/'data')},
                stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
            supervisor=None;sock=None
            try:
                deadline=time.monotonic()+30
                while not ready.exists():
                    self.assertIsNone(process.poll())
                    if time.monotonic()>deadline:self.fail('launcher not ready')
                    time.sleep(.05)
                status=json.loads(ready.read_text(encoding='utf8'));supervisor=status['watchdog_pid'];port=int(status['url'].rsplit(':',1)[1])
                sock=socket.create_connection(('127.0.0.1',port),5);sock.settimeout(5)
                request=f'GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n'.encode();sock.sendall(request*1500)
                data=b''
                while data.count(b'HTTP/1.1 200')<1500:
                    chunk=sock.recv(65536)
                    self.assertTrue(chunk,'connection closed before all health responses');data+=chunk
                # Deliberately never read the child's stdout pipe. Old per-request
                # print(flush=True) blocked and finalization required watchdog kill.
                stop.write_text('stop');started=time.monotonic();self.assertEqual(process.wait(10),0)
                self.assertLess(time.monotonic()-started,6)
                self.assertNotIn('deadline exceeded',(root/'data/logs/shutdown-watchdog.log').read_text(encoding='utf8') if (root/'data/logs/shutdown-watchdog.log').exists() else '')
            finally:
                if sock:sock.close()
                if process.poll() is None:process.kill();process.wait()
                process.stdout.close();wait_for_exit(supervisor)

    def test_independent_watchdog_observes_stop_even_without_parent_shutdown_log(self):
        from shutdown_watchdog import wait_for_exit
        with tempfile.TemporaryDirectory(prefix='z30-independent-watchdog-') as folder:
            root=Path(folder);ready=root/'ready.json';stop=root/'stop.signal'
            code=('import sys,time,json,os;from pathlib import Path;import shutdown_watchdog as w;'
                  'state,watch,event=w.launch(Path(sys.argv[1]),Path(sys.argv[2]),4.,2.);'
                  'Path(sys.argv[3]).write_text(json.dumps({"watchdog_pid":watch.pid}));time.sleep(60)')
            process=subprocess.Popen([sys.executable,'-c',code,str(root),str(stop),str(ready)],cwd=ROOT)
            watchdog_pid=None
            try:
                deadline=time.monotonic()+20
                while not ready.exists():
                    self.assertIsNone(process.poll())
                    if time.monotonic()>deadline:self.fail('watchdog was not ready')
                    time.sleep(.05)
                watchdog_pid=json.loads(ready.read_text())['watchdog_pid']
                stop.write_text('stop');started=time.monotonic()
                self.assertEqual(process.wait(10),3)
                self.assertLess(time.monotonic()-started,8)
                text=(root/'shutdown-watchdog.log').read_text(encoding='utf8')
                self.assertIn('stop observed independently',text)
                self.assertIn('waiting for shutdown acknowledgement',text)
            finally:
                if process.poll() is None:process.kill();process.wait()
                wait_for_exit(watchdog_pid)

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
                        log = data/'logs'/'launcher.log'
                        self.fail(f'launcher exited before ready: {process.returncode}\n' + (log.read_text(encoding='utf8') if log.exists() else 'no log'))
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
