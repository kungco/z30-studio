# Build on Windows x64: python -m PyInstaller --noconfirm Z30-Studio.spec
from pathlib import Path
from PyInstaller.utils.hooks import collect_dynamic_libs

root = Path(SPECPATH)
a = Analysis([str(root / 'desktop.py')], pathex=[str(root)],
    binaries=collect_dynamic_libs('rawpy'),
    datas=[(str(root / 'web'), 'web')],
    hiddenimports=['tifffile._imagecodecs'],
    excludes=['unittest', 'pytest', 'pip', 'setuptools', 'scipy', 'matplotlib', 'IPython'],
    noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Z30-Studio',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False, version=str(root/'windows-version.txt'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Z30-Studio')
