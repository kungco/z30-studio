"""Prepare, verify or explicitly install the optional pinned skin model.

The native engine/probe uses the separate existing vision runtime. A prepared
model is not proof of desktop integration, image quality, or clean-Windows use.
"""
import argparse
import json
from pathlib import Path
import tempfile
import shutil

try:
    from .prepare_native_vision import verified
except ImportError:
    from prepare_native_vision import verified

VENDOR = Path(__file__).resolve().parents[1] / 'native/vendor/skin'


def verify_component(directory: Path):
    manifest = json.loads((VENDOR/'manifest.json').read_text(encoding='utf8'))
    asset = manifest['model']
    names = {asset['name'], 'manifest.json', 'LICENSE.txt', 'NOTICE.txt'}
    if directory.is_symlink() or not directory.is_dir() or {p.name for p in directory.iterdir()} != names:
        raise ValueError('Skin component must contain exactly the pinned files')
    if any((directory/name).is_symlink() or not (directory/name).is_file() for name in names):
        raise ValueError('Skin component must contain regular files')
    verified(directory/asset['name'], asset['bytes'], asset['sha256'])
    for name in ('manifest.json','LICENSE.txt','NOTICE.txt'):
        expected = (VENDOR/name).read_bytes()
        path = directory/name
        if path.stat().st_size != len(expected) or path.read_bytes() != expected:
            raise ValueError('Skin component manifest/license/notice differs from pinned bytes')
    return dict(kind='optional-skin-engine-component', model_sha256=asset['sha256'],
                desktop_integration=True, runtime_bundled=False, external_vc_runtime_required=True)


def prepare(model: Path, output: Path):
    manifest = json.loads((VENDOR/'manifest.json').read_text(encoding='utf8'))
    asset = manifest['model']
    if output.exists() or output.is_symlink():
        raise ValueError('Existing output cannot be replaced')
    if model.is_symlink():
        raise ValueError('A regular local model is required')
    verified(model, asset['bytes'], asset['sha256'])
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='skin-prepare-',dir=output.parent) as temporary:
        stage = Path(temporary)
        shutil.copyfile(model,stage/asset['name'])
        for name in ('manifest.json','LICENSE.txt','NOTICE.txt'):
            shutil.copyfile(VENDOR/name,stage/name)
        info = verify_component(stage)
        # Exclusive reservation prevents replacing even an existing empty dir
        # on POSIX. The incomplete directory is deliberately retained on errors.
        output.mkdir()
        for name in ('LICENSE.txt','NOTICE.txt',asset['name'],'manifest.json'):
            with (output/name).open('xb') as dest, (stage/name).open('rb') as source:
                shutil.copyfileobj(source,dest)
        verify_component(output)
    return info


def install_component(component: Path, stage: Path):
    try:
        from .prepare_native_vision import verify_component as verify_runtime
    except ImportError:
        from prepare_native_vision import verify_component as verify_runtime
    verify_component(component)
    verify_runtime(stage/'vision')
    target=stage/'skin'
    if not stage.is_dir() or target.exists() or target.is_symlink():
        raise ValueError('Existing skin output cannot be replaced')
    shutil.copytree(component,target)
    verify_component(target)
    return dict(installed=str(target),external_vc_runtime_required=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',type=Path)
    parser.add_argument('--out',type=Path)
    parser.add_argument('--verify',type=Path)
    parser.add_argument('--component',type=Path)
    parser.add_argument('--install-to-stage',type=Path)
    args=parser.parse_args()
    if args.install_to_stage:
        if not args.component or args.model or args.out or args.verify: parser.error('Install requires --component and --install-to-stage only')
        result=install_component(args.component.absolute(),args.install_to_stage.absolute())
    elif args.verify:
        if args.model or args.out: parser.error('--verify cannot be mixed with preparation')
        result=verify_component(args.verify.absolute())
    else:
        if not args.model or not args.out: parser.error('--model and --out required')
        result=prepare(args.model.absolute(),args.out.absolute())
    print(json.dumps(result,indent=2))


if __name__=='__main__': main()
