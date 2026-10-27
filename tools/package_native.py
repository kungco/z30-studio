"""Install a verified RAW runtime or create/verify a clean native ZIP.

Packaging refuses dirty builds, missing corresponding source, non-system DLL
imports, changed binaries and existing destinations. Hashes detect corruption;
they are not a digital signature or a clean-Windows certification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import tempfile
import zipfile

if __package__:
    from .build_native_libraw import SDK_HASH, SDK_NAME, check_system_imports, sha256
    from .prepare_native_vision import verify_component
    from .prepare_native_skin import verify_component as verify_skin_component
else:
    from build_native_libraw import SDK_HASH, SDK_NAME, check_system_imports, sha256
    from prepare_native_vision import verify_component
    from prepare_native_skin import verify_component as verify_skin_component

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_LICENSES = {'LibRaw-COPYRIGHT.txt', 'LibRaw-CDDL-1.0.txt', 'LibRaw-LGPL-2.1.txt',
                     'GCC-COPYING3.txt', 'GCC-RUNTIME-EXCEPTION.txt', 'MinGW-w64-runtime.txt'}
APP_SYSTEM_DLLS = {'advapi32.dll', 'api-ms-win-core-synch-l1-2-0.dll',
                  'api-ms-win-core-winrt-error-l1-1-0.dll', 'bcryptprimitives.dll',
                  'combase.dll', 'comctl32.dll', 'dwmapi.dll', 'dwrite.dll', 'gdi32.dll',
                  'imm32.dll', 'kernel32.dll', 'msvcrt.dll', 'ntdll.dll', 'ole32.dll',
                  'oleaut32.dll', 'shell32.dll', 'shlwapi.dll', 'uiautomationcore.dll',
                  'user32.dll', 'uxtheme.dll'}


def pe_imports(path: Path) -> list[str]:
    """Read PE32+ imports without loading or executing the inspected DLL."""
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError('Unexpected PE size')
    data = path.read_bytes()
    def number(fmt: str, offset: int) -> int:
        if offset < 0 or offset + struct.calcsize(fmt) > len(data):
            raise ValueError('Truncated PE')
        return struct.unpack_from(fmt, data, offset)[0]
    if data[:2] != b'MZ':
        raise ValueError('Not a PE file')
    pe = number('<I', 0x3c)
    if data[pe:pe+4] != b'PE\0\0' or number('<H', pe+4) != 0x8664:
        raise ValueError('Expected x64 PE')
    opt = pe+24
    opt_size = number('<H', pe+20)
    count = number('<H', pe+6)
    if number('<H', opt) != 0x20b or opt_size < 224 or not 1 <= count <= 96:
        raise ValueError('Invalid PE32+ optional header')
    if number('<I', opt+108) < 14:
        raise ValueError('Missing PE data directories')
    if number('<I', opt+112+13*8):
        raise ValueError('Delay imports require a separate dependency audit')
    sections = []
    for index in range(count):
        s = opt+opt_size+index*40
        sections.append((number('<I', s+12), number('<I', s+16), number('<I', s+20)))
    def offset(rva: int, size: int) -> int:
        for base, length, start in sections:
            if base <= rva and rva+size <= base+length and start+rva-base+size <= len(data):
                return start+rva-base
        raise ValueError('PE RVA is outside file-backed sections')
    table = number('<I', opt+120)
    length = number('<I', opt+124)
    if not table or not 20 <= length <= 65536:
        raise ValueError('Missing or invalid import directory')
    result = []
    for index in range(min(length//20, 512)):
        descriptor = offset(table+index*20, 20)
        if not any(data[descriptor:descriptor+20]):
            return sorted(set(result), key=str.lower)
        name_rva = number('<I', descriptor+12)
        name = bytearray()
        for i in range(256):
            value = data[offset(name_rva+i, 1)]
            if value == 0:
                break
            name.append(value)
        else:
            raise ValueError('Unterminated DLL name')
        result.append(name.decode('ascii'))
    raise ValueError('Unterminated import directory')


def verify_runtime(directory: Path, current_recipe: bool = False) -> dict:
    manifest = json.loads((directory/'raw/runtime.json').read_text(encoding='utf-8'))
    if (manifest.get('schema_version'), manifest.get('version'), manifest.get('license'),
        manifest.get('source_modified')) != (1, '0.22.2', 'CDDL-1.0', False):
        raise ValueError('Unexpected RAW runtime contract')
    if manifest.get('archive_sha256') != SDK_HASH or sha256(directory/'sources'/SDK_NAME) != SDK_HASH:
        raise ValueError('Missing or changed corresponding LibRaw source')
    recipe = directory/'sources/build_native_libraw.py'
    if sha256(recipe) != manifest.get('build_recipe_sha256'):
        raise ValueError('Changed RAW build recipe')
    if current_recipe and sha256(recipe) != sha256(ROOT/'tools/build_native_libraw.py'):
        raise ValueError('Rebuild RAW runtime using the current build recipe')
    dll = directory/'raw/libraw.dll'
    if sha256(dll) != manifest.get('dll_sha256') or dll.stat().st_size != manifest.get('dll_bytes'):
        raise ValueError('Changed RAW DLL')
    imports = pe_imports(dll)
    check_system_imports(imports)
    if sorted(map(str.lower, imports)) != sorted(map(str.lower, manifest.get('imports', []))):
        raise ValueError('RAW dependency manifest mismatch')
    licenses = manifest.get('license_sha256', {})
    if set(licenses) != REQUIRED_LICENSES:
        raise ValueError('Incomplete RAW/runtime license notices')
    for name, expected in licenses.items():
        if sha256(directory/'licenses'/name) != expected:
            raise ValueError(f'Changed license notice: {name}')
    return manifest


def install_runtime(runtime: Path, stage: Path) -> None:
    verify_runtime(runtime, current_recipe=True)
    if not (stage/'Z30Studio.exe').is_file():
        raise ValueError('Expected an existing native staging directory')
    for name in ('raw', 'sources'):
        if (stage/name).exists():
            raise FileExistsError(stage/name)
    for name in REQUIRED_LICENSES:
        target = stage/'licenses'/name
        if target.exists() and sha256(target) != sha256(runtime/'licenses'/name):
            raise ValueError(f'Conflicting existing notice: {name}')
    for name in ('raw', 'sources'):
        shutil.copytree(runtime/name, stage/name)
    (stage/'licenses').mkdir(exist_ok=True)
    for name in REQUIRED_LICENSES:
        shutil.copyfile(runtime/'licenses'/name, stage/'licenses'/name)


def verify_stage(stage: Path) -> dict:
    info = json.loads((stage/'build-info.json').read_text(encoding='utf-8'))
    if info.get('source_tree_dirty') is not False or info.get('profile') != 'release':
        raise ValueError('Only a clean Release build can be packaged')
    if info.get('raw_bundled') is not True:
        raise ValueError('RAW runtime is not marked as bundled')
    exe = stage/'Z30Studio.exe'
    if sha256(exe) != info.get('exe_sha256') or exe.stat().st_size != info.get('exe_bytes'):
        raise ValueError('Changed application executable')
    imports = pe_imports(exe)
    if not imports or any(name.lower() not in APP_SYSTEM_DLLS for name in imports):
        raise ValueError(f'Application has unaudited runtime imports: {imports}')
    verify_runtime(stage)
    if info.get('vision_bundled') is True:
        if info.get('models_bundled') is not True or info.get('vision_external_vc_runtime') is not True:
            raise ValueError('Bundled vision must declare model weights and external VC++ dependency')
        verify_component(stage/'vision')
    elif (stage/'vision').exists() or info.get('models_bundled') is True:
        raise ValueError('Undeclared optional model/runtime directory')
    if info.get('skin_bundled') is True:
        if info.get('vision_bundled') is not True:
            raise ValueError('Bundled skin requires the audited vision runtime')
        verify_skin_component(stage/'skin')
    elif (stage/'skin').exists():
        raise ValueError('Undeclared optional skin directory')
    return info


def package(stage: Path, output: Path) -> dict:
    info = verify_stage(stage)
    if output.suffix.lower() != '.zip' or output.exists() or output.is_relative_to(stage):
        raise ValueError('Choose a new .zip path outside the staging directory')
    files = {}
    for path in sorted(stage.rglob('*')):
        if path.is_symlink() or not path.resolve().is_relative_to(stage):
            raise ValueError('Staging directory must not contain external links')
        if path.is_file():
            name = path.relative_to(stage).as_posix()
            if name == 'package-manifest.json':
                raise ValueError('Stage already contains a package manifest')
            files[name] = {'sha256': sha256(path), 'bytes': path.stat().st_size}
    manifest = {'schema_version': 1, 'source_commit': info['source_commit'],
                'exe_imports': pe_imports(stage/'Z30Studio.exe'), 'files': files}
    # Publish without overwriting an existing ZIP, including a destination race.
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix='.part', delete=False) as temporary:
        temp = Path(temporary.name)
    try:
        with zipfile.ZipFile(temp, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for name in files:
                archive.write(stage/name, name)
            archive.writestr('package-manifest.json', json.dumps(manifest, indent=2)+'\n')
        with zipfile.ZipFile(temp) as archive:
            for name, expected in files.items():
                with archive.open(name) as stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() != expected['sha256']:
                        raise ValueError(f'Stage changed while packaging: {name}')
        os.link(temp, output)
    finally:
        temp.unlink(missing_ok=True)
    return {'zip': str(output), 'bytes': output.stat().st_size, 'sha256': sha256(output),
            'source_commit': info['source_commit'], 'files': len(files)}


def verify_package(stage: Path) -> None:
    verify_stage(stage)
    manifest = json.loads((stage/'package-manifest.json').read_text(encoding='utf-8'))
    actual = {p.relative_to(stage).as_posix() for p in stage.rglob('*') if p.is_file()}
    if actual != set(manifest['files']) | {'package-manifest.json'}:
        raise ValueError('Package file list changed')
    for name, expected in manifest['files'].items():
        path = (stage/name).resolve()
        if not path.is_relative_to(stage) or sha256(path) != expected['sha256'] or path.stat().st_size != expected['bytes']:
            raise ValueError(f'Package file changed: {name}')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('install-runtime', 'pack', 'verify'))
    parser.add_argument('--stage', required=True, type=Path)
    parser.add_argument('--runtime', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    stage = args.stage.resolve(strict=True)
    if args.action == 'install-runtime':
        if not args.runtime:
            parser.error('install-runtime requires --runtime')
        install_runtime(args.runtime.resolve(strict=True), stage)
        print('RAW runtime installed and verified')
    elif args.action == 'pack':
        if not args.out:
            parser.error('pack requires --out')
        print(json.dumps(package(stage, args.out.resolve()), indent=2))
    else:
        verify_package(stage)
        print('Package file hashes and RAW dependencies verified')


if __name__ == '__main__':
    main()
