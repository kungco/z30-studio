"""Paths and external-library handling for source and Windows bundles."""
import ctypes
import os
from pathlib import Path
import sys

APP_VERSION = '2.0.0'


def resource_root():
    return Path(__file__).resolve().parent


def data_root():
    if os.environ.get('Z30_DATA_DIR'):
        return Path(os.environ['Z30_DATA_DIR']).expanduser().resolve()
    if getattr(sys, 'frozen', False):
        configured = os.environ.get('LOCALAPPDATA')
        local = Path(configured) if configured else Path.home() / 'AppData' / 'Local'
        return local / 'Z30 Studio'
    return resource_root() / '.data'


def prepare_external_programs():
    """After native imports, restore Windows DLL lookup for Codex/browser children."""
    if os.name != 'nt' or not getattr(sys, 'frozen', False):
        return
    if not ctypes.windll.kernel32.SetDllDirectoryW(None):
        raise ctypes.WinError()
    bundle = Path(sys._MEIPASS).resolve()
    paths = []
    for part in os.environ.get('PATH', '').split(os.pathsep):
        if part and not Path(part).resolve().is_relative_to(bundle):
            paths.append(part)
    os.environ['PATH'] = os.pathsep.join(paths)
