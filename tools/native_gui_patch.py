"""Verify and ship the application-integrated, pinned Slint repaint backport."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]
PATCH_ID='slint-winit-1.18.1-win32-invalidation-13568'
MANIFEST_SHA256='3eb38a78e78b04d4e6a2e157c677ffc193e5bb974695b1cc4cfb00676bcb7437'


def verify_source(directory):
    directory=Path(directory)
    entries=[directory,*directory.rglob('*')]
    if any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in entries):
        raise ValueError('GUI patch source may not contain links or junctions')
    manifest=directory/'SOURCE.json'
    payload=manifest.read_bytes()
    if hashlib.sha256(payload).hexdigest()!=MANIFEST_SHA256:
        raise ValueError('Changed GUI patch source manifest')
    data=json.loads(payload)
    if data['patch_id']!=PATCH_ID or data['modified_files']!=['winitwindowadapter.rs']:
        raise ValueError('Unexpected GUI patch identity')
    files={str(p.relative_to(directory)).replace('\\','/') for p in entries[1:] if p.is_file()}
    if files!=set(data['files'])|{'SOURCE.json'}:
        raise ValueError('Missing or extra GUI patch source files')
    root=directory.resolve()
    for name,expected in data['files'].items():
        path=directory/name
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('GUI patch source may not escape its directory')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            raise ValueError('Changed GUI patch source: '+name)
    return dict(patch_id=PATCH_ID,manifest_sha256=MANIFEST_SHA256,files=len(files))


def install_source(source,stage):
    verify_source(source)
    destination=Path(stage)/'sources/slint-winit'
    if destination.exists():raise ValueError('GUI source destination already exists')
    destination.parent.mkdir(parents=True,exist_ok=True)
    shutil.copytree(source,destination)
    return verify_source(destination)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=ROOT/'native/vendor/slint-winit')
    parser.add_argument('--install-to-stage',type=Path)
    args=parser.parse_args()
    result=install_source(args.source,args.install_to_stage) if args.install_to_stage else verify_source(args.source)
    print(json.dumps(result))


if __name__=='__main__':main()
