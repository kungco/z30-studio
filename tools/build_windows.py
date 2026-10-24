"""Reproducible Windows x64 portable folder + ZIP; ships no photos or credentials."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ('rawpy', 'numpy', 'Pillow', 'tifffile', 'pyinstaller')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--output', type=Path, default=ROOT.parent)
    args = p.parse_args()
    if sys.platform != 'win32' or struct.calcsize('P') != 8:
        p.error('Build with 64-bit Python on Windows')
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', str(ROOT/'Z30-Studio.spec')], cwd=ROOT, check=True)
    bundle = ROOT/'dist'/'Z30-Studio'
    licenses = bundle/'licenses';licenses.mkdir(exist_ok=True)
    for name in PACKAGES:
        dist = importlib.metadata.distribution(name)
        target = licenses/name;target.mkdir(exist_ok=True)
        for item in dist.files or []:
            if '.dist-info/' in str(item).replace('\\','/') and any(x in str(item).lower() for x in ('license','copying','notice')):
                source = Path(dist.locate_file(item))
                if source.is_file():
                    relative = Path(*item.parts[item.parts.index(next(x for x in item.parts if x.endswith('.dist-info')))+1:])
                    dest = target/relative;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,dest)
    python_license = Path(sys.base_prefix)/'LICENSE.txt'
    if python_license.is_file():shutil.copyfile(python_license,licenses/'Python-LICENSE.txt')
    # Tcl/Tk runtime hooks include their own license. Keep a visible companion copy.
    for name in ('_tcl_data','_tk_data'):
        for file in (bundle/'_internal'/name).glob('*license*'):
            if file.is_file():shutil.copyfile(file,licenses/(name+'-'+file.name))
    shutil.copyfile(ROOT/'docs'/'windows-portable.md',bundle/'使用说明.md')
    shutil.copyfile(ROOT/'docs'/'third-party-notices.md',bundle/'第三方声明.md')
    versions={name:importlib.metadata.version(name) for name in PACKAGES}
    import rawpy
    manifest=dict(app='Z30 Studio',platform='Windows x64',python=platform.python_version(),packages=versions,
                  libraw='.'.join(map(str,rawpy.libraw_version)),
                  source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                  source_tree_dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip()),
                  exe_sha256=hashlib.sha256((bundle/'Z30-Studio.exe').read_bytes()).hexdigest())
    (bundle/'build-info.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
    args.output.mkdir(parents=True,exist_ok=True)
    archive=args.output/'Z30-Studio-Windows-x64.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for file in sorted(bundle.rglob('*')):
            if file.is_file():
                relative=file.relative_to(bundle)
                if any(x in relative.parts for x in ('.data','.venv','.git','__pycache__')) or file.suffix.lower() in ('.nef','.z30proj') or file.name in ('auth.json','.env'):
                    raise ValueError('Unexpected private/runtime file: '+str(relative))
                z.write(file,Path('Z30-Studio')/relative)
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(digest+'  '+archive.name+'\n',encoding='ascii')
    print(json.dumps(dict(zip=str(archive),bytes=archive.stat().st_size,sha256=digest),indent=2))


if __name__=='__main__':main()
