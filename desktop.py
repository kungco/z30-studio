"""Windows launcher; the existing loopback web editor remains the workbench."""
import argparse
import ctypes
import faulthandler
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import multiprocessing
import os
from pathlib import Path
import sys
import threading
import time
import webbrowser
import shutdown_watchdog

from app_runtime import APP_VERSION, data_root, prepare_external_programs


class InstanceLock:
    def __init__(self, directory):
        self.handle = None
        if os.name != 'nt':
            return
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        self.kernel.CreateMutexW.restype = ctypes.c_void_p
        self.kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        token = hashlib.sha256(str(directory.resolve()).casefold().encode('utf8')).hexdigest()[:24]
        ctypes.set_last_error(0)
        self.handle = self.kernel.CreateMutexW(None, False, 'Local\\Z30Studio-' + token)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == 183:
            self.close()
            raise FileExistsError('Z30 Studio 已在运行')

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class LogStream:
    def write(self, value):
        if value.strip():
            logging.info(value.rstrip())
        return len(value)

    def flush(self):
        pass


def configure_logs(directory):
    directory.mkdir(parents=True, exist_ok=True)
    log = directory / 'launcher.log'
    handler = RotatingFileHandler(log, maxBytes=1024*1024, backupCount=2, encoding='utf8')
    logging.basicConfig(level=logging.INFO, handlers=[handler], format='%(asctime)s %(message)s', force=True)
    if getattr(sys, 'frozen', False):
        sys.stdout = sys.stderr = LogStream()
    return log


def write_ready(path, url, watchdog_pid=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + '.' + str(os.getpid()) + '.tmp')
    part.write_text(json.dumps(dict(pid=os.getpid(), url=url, data_directory=str(data_root()), watchdog_pid=watchdog_pid), ensure_ascii=False), encoding='utf8')
    part.replace(path)


class LauncherWindow:
    def __init__(self, url, http, worker, log, begin_shutdown=None):
        import tkinter as tk
        self.http, self.worker = http, worker
        self.begin_shutdown = begin_shutdown
        self.stopping = False
        self.root = tk.Tk()
        self.root.title('Z30 Studio · 本地 RAW 暗房')
        self.root.geometry('540x330')
        self.root.resizable(False, False)
        self.root.configure(bg='#191b1d')
        tk.Label(self.root, text='Z30 STUDIO', fg='#efcb68', bg='#191b1d', font=('Segoe UI', 24, 'bold')).pack(pady=(24, 6))
        tk.Label(self.root, text=f'本地 RAW 暗房 · Windows 便携版 {APP_VERSION}', fg='#ededed', bg='#191b1d', font=('Microsoft YaHei UI', 11)).pack()
        self.status = tk.StringVar(value='工作台已就绪  ' + url)
        tk.Label(self.root, textvariable=self.status, fg='#b9c8b9', bg='#191b1d', wraplength=500).pack(pady=17)
        self.open = tk.Button(self.root, text='打开照片工作台', command=lambda: webbrowser.open(url), width=24, bg='#efcb68', font=('Microsoft YaHei UI', 11))
        self.open.pack()
        actions = tk.Frame(self.root, bg='#191b1d')
        actions.pack(pady=13)
        tk.Button(actions, text='查看运行日志', command=lambda: os.startfile(str(log))).pack(side='left', padx=8)
        self.quit = tk.Button(actions, text='停止服务并退出', command=self.stop)
        self.quit.pack(side='left', padx=8)
        tk.Label(self.root, text='关闭此窗口会停止服务；请先在工作台保存项目。\n手动处理无需联网；AI 可选 Codex CLI 或兼容接口。', fg='#a3a3a3', bg='#191b1d', justify='center').pack()
        self.root.protocol('WM_DELETE_WINDOW', self.stop)
        self.root.after(200, self.poll)

    def stop(self):
        if self.stopping:
            return
        self.stopping = True
        self.status.set('正在停止服务并保存后台任务状态…')
        self.open.configure(state='disabled')
        self.quit.configure(state='disabled')
        if self.begin_shutdown:
            self.begin_shutdown('launcher window')
        threading.Thread(target=self.http.shutdown, daemon=True).start()

    def poll(self):
        if not self.worker.is_alive():
            self.root.destroy()
            return
        self.root.after(200, self.poll)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Z30 Studio Windows launcher')
    parser.add_argument('--port', type=int, default=8730)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--headless', action='store_true', help='Run without the launcher window')
    parser.add_argument('--ready-file', type=Path, help='Diagnostic startup status JSON')
    parser.add_argument('--stop-file', type=Path, help='Diagnostic shutdown signal (must not already exist)')
    parser.add_argument('--shutdown-grace', type=float, default=20., help='Independent watchdog total seconds')
    parser.add_argument('--shutdown-phase-timeout', type=float, default=8., help='Independent watchdog phase seconds')
    args = parser.parse_args(argv)
    if not args.headless:
        from tkinter import messagebox
    directory = data_root()
    lock = None
    worker = http = None
    trace_file = None
    shutdown_started = None
    started = time.monotonic()
    instance = directory / 'instance.json'
    watchdog = None
    service_done=threading.Event()
    observer_done=threading.Event()
    shutdown_guard=threading.Lock()

    def phase(name):
        if watchdog:
            try:
                shutdown_watchdog.checkpoint(watchdog[0], name)
            except OSError:
                logging.exception('Could not persist shutdown phase')

    def begin_shutdown(reason):
        nonlocal trace_file, shutdown_started
        with shutdown_guard:
            if shutdown_started is not None:return
            shutdown_started=time.monotonic()
        # Arm native traceback timing before phase/log I/O can block.
        if trace_file:faulthandler.dump_traceback_later(min(2.,args.shutdown_phase_timeout/2),repeat=True,file=trace_file)
        shutdown_watchdog.signal(watchdog)
        phase('shutdown requested: ' + reason)
        logging.info('Shutdown requested (%s), uptime %.1fs', reason, shutdown_started - started)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        try:
            lock = InstanceLock(directory)
        except FileExistsError:
            if not args.headless:
                messagebox.showinfo('Z30 Studio', '软件已在运行。请在已有的 Z30 Studio 启动窗口打开工作台。')
            return 2
        if args.stop_file and args.stop_file.exists():
            raise ValueError('停止信号文件已存在，请使用新的诊断路径')
        if args.ready_file and (args.ready_file.suffix.lower() != '.json' or args.ready_file.exists()):
            raise ValueError('诊断状态需要新的 .json 文件，不覆盖已有文件')
        log = configure_logs(directory / 'logs')
        trace_file=(directory/'logs'/f'shutdown-stacks-{os.getpid()}.log').open('w',encoding='utf8')
        faulthandler.enable(file=trace_file,all_threads=True)
        if not 0 < args.shutdown_phase_timeout <= args.shutdown_grace <= 120:
            raise ValueError('关停超时需要满足 0 < 阶段超时 ≤ 总超时 ≤ 120 秒')
        watchdog = shutdown_watchdog.launch(directory/'logs', args.stop_file, args.shutdown_grace, args.shutdown_phase_timeout)
        import server
        http = server.create_server(args.port)
        url = f'http://127.0.0.1:{http.server_port}'
        def run():
            try:
                server.serve(http, phase)
            except Exception:
                logging.exception('Local service failed')
            finally:service_done.set()
        worker = threading.Thread(target=run, name='z30-service')
        worker.start()
        if args.stop_file:
            def observe_stop():
                while not observer_done.wait(.1):
                    if args.stop_file.exists():
                        begin_shutdown('stop file');http.shutdown();return
            threading.Thread(target=observe_stop,name='z30-stop-observer',daemon=True).start()
        window = None if args.headless else LauncherWindow(url, http, worker, log, begin_shutdown)
        # Native imaging/Tk libraries have loaded before restoring child DLL lookup.
        prepare_external_programs()
        write_ready(instance, url, watchdog[1].pid if watchdog else None)
        if args.ready_file:
            write_ready(args.ready_file, url, watchdog[1].pid if watchdog else None)
        logging.info('Z30 Studio ready: %s', url)
        if not args.no_browser:
            webbrowser.open(url)
        if window:
            if args.stop_file:
                def check_stop():
                    if args.stop_file.exists():
                        window.stop()
                    elif worker.is_alive():
                        window.root.after(200, check_stop)
                window.root.after(200, check_stop)
            window.root.mainloop()
        else:
            while not service_done.wait(.2):
                if args.stop_file and args.stop_file.exists():
                    begin_shutdown('stop file')
                    http.shutdown()
    except KeyboardInterrupt:
        begin_shutdown('keyboard interrupt')
    except Exception as exc:
        logging.exception('Launcher failed')
        if not args.headless:
            messagebox.showerror('Z30 Studio 启动失败', str(exc) + '\n请查看本机数据目录中的 logs/launcher.log。')
        return 1
    finally:
        observer_done.set()
        if watchdog:
            begin_shutdown('launcher cleanup')
        if worker and worker.is_alive():
            begin_shutdown('launcher cleanup')
            http.shutdown()
            worker.join(15)
        if lock:
            try:
                if json.loads(instance.read_text(encoding='utf8')).get('pid') == os.getpid():
                    instance.unlink()
            except (OSError, ValueError):
                pass
            lock.close()
        if trace_file:
            faulthandler.cancel_dump_traceback_later()
            faulthandler.disable()
            trace_file.close()
        if shutdown_started is not None:
            logging.info('Shutdown finished in %.2fs', time.monotonic() - shutdown_started)
        logging.info('Launcher stopped')
        logging.shutdown()
        phase('complete')
    return 0


if __name__ == '__main__':
    multiprocessing.freeze_support()
    if len(sys.argv) > 1 and sys.argv[1] == '--watchdog':
        shutdown_watchdog.main(sys.argv[2:])
        raise SystemExit(0)
    raise SystemExit(main())
