"""Independent Windows shutdown supervisor; never discovers or kills other instances.

The child pins a handle to its exact parent before acknowledging readiness. A stop
file also arms it when the parent cannot run Python or write its first shutdown log.
"""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import secrets
import sys
import time


def checkpoint(path, phase):
    path = Path(path)
    part = path.with_suffix('.tmp')
    part.write_text(json.dumps(dict(phase=phase, monotonic=time.monotonic())), encoding='utf8')
    os.replace(part, path)


def launch(directory, stop_file=None, total_timeout=20., phase_timeout=8.):
    if os.name != 'nt':
        return None
    directory.mkdir(parents=True, exist_ok=True)
    state = directory / f'shutdown-{os.getpid()}.json'
    ready = state.with_suffix('.ready')
    event_name = 'Local\\Z30Stop-' + secrets.token_hex(16)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateEventW.restype = ctypes.c_void_p
    handle = kernel.CreateEventW(None, True, False, event_name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    for path in (state, ready):
        path.unlink(missing_ok=True)
    command = [sys.executable]
    if not getattr(sys, 'frozen', False):
        command.append(str(Path(__file__).with_name('desktop.py')))
    command += ['--watchdog', str(os.getpid()), str(state), str(total_timeout), str(phase_timeout), str(stop_file or ''), event_name]
    process = subprocess.Popen(command, creationflags=subprocess.CREATE_NO_WINDOW,
                               cwd=str(Path(sys.executable).parent),
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    until = time.monotonic() + 10
    while not ready.exists():
        if process.poll() is not None or time.monotonic() > until:
            raise RuntimeError('独立退出看门狗未能启动；请查看本机日志')
        time.sleep(.05)
    # Keep the acknowledgement: deleting a just-created file races Windows
    # file-sharing/antivirus handles. It is replaced atomically by the child.
    return state, process, handle


def signal(supervisor):
    if supervisor and len(supervisor) > 2:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.SetEvent.argtypes = [ctypes.c_void_p]
        if not kernel.SetEvent(supervisor[2]):
            raise ctypes.WinError(ctypes.get_last_error())


def wait_for_exit(pid, timeout=5.):
    """Diagnostic harness cleanup; wait only, never terminate a discovered PID."""
    if os.name != 'nt' or not pid:
        return
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x100000, False, int(pid))
    if handle:
        try:
            if kernel.WaitForSingleObject(handle, int(timeout*1000)) == 258:
                raise TimeoutError('Shutdown supervisor did not exit after its parent')
        finally:
            kernel.CloseHandle(handle)


def supervise(pid, state, total_timeout=20., phase_timeout=8., stop_file=None, event_name=None):
    if os.name != 'nt':
        raise RuntimeError('The independent process supervisor is Windows-only')
    state = Path(state)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.OpenEventW.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_wchar_p]
    kernel.OpenEventW.restype = ctypes.c_void_p
    event = kernel.OpenEventW(0x100000, False, event_name) if event_name else None
    if event_name and not event:
        raise ctypes.WinError(ctypes.get_last_error())
    handle = kernel.OpenProcess(0x100000 | 0x0001, False, int(pid))  # SYNCHRONIZE | TERMINATE
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    def log(message):
        try:
            with (state.parent/'shutdown-watchdog.log').open('a', encoding='utf8') as file:
                file.write(f'{time.strftime("%Y-%m-%d %H:%M:%S")} pid={pid} {message}\n'); file.flush()
        except OSError:
            pass  # Disk-full logging must not disable the stop deadline.
    try:
        acknowledgement = state.with_suffix('.ready')
        part = acknowledgement.with_suffix('.ready.tmp')
        part.write_text('ready', encoding='ascii')
        os.replace(part, acknowledgement)
        armed = phase_started = None
        phase = 'waiting for shutdown acknowledgement'
        while kernel.WaitForSingleObject(handle, 100) == 258:  # WAIT_TIMEOUT
            now = time.monotonic()
            document = {}
            try:
                document = json.loads(state.read_text(encoding='utf8'))
            except (OSError, ValueError):
                pass
            stopping = bool(document) or bool(event and kernel.WaitForSingleObject(event, 0) == 0) or bool(stop_file and Path(stop_file).exists())
            if not stopping:
                continue
            if armed is None:
                armed = phase_started = now
                log('stop observed independently')
            if document.get('phase') == 'complete':
                # Keep monitoring actual process exit: non-daemon threads / atexit
                # can still hang after the launcher's cleanup returns.
                pass
            new_phase = document.get('phase', phase)
            if new_phase != phase:
                phase, phase_started = new_phase, now
                log('phase=' + str(phase))
            if now - armed >= total_timeout or now - phase_started >= phase_timeout:
                log(f'deadline exceeded; terminating own parent; phase={phase}; elapsed={now-armed:.2f}s')
                if not kernel.TerminateProcess(handle, 3):
                    log('TerminateProcess failed: ' + str(ctypes.get_last_error()))
                break
    finally:
        kernel.CloseHandle(handle)
        if event:
            kernel.CloseHandle(event)


def main(args):
    pid, state, total, phase, stop, *event = args
    supervise(int(pid), state, float(total), float(phase), stop or None, event[0] if event else None)
