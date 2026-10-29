"""Prepare optional pinned CPU vision assets in a new directory.

No downloads, installers or model inference. The base RAW editor does not need
these files. ONNX Runtime's separate VC++ dependency is recorded explicitly.
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

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'native/vendor/vision'


def verified(path: Path, size: int, expected_hash: str):
    if not path.is_file() or path.stat().st_size != size:
        raise ValueError(f'Asset missing or size mismatch: {path.name}')
    with path.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual != expected_hash:
        raise ValueError(f'Asset SHA-256 mismatch: {path.name}')


def pe_dependencies(path: Path):
    """Bounded PE32+ normal and RVA delay imports; independent from base packer.

    The base packer's refusal of delay imports is intentionally retained.
    """
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('Unexpected optional runtime PE size')
    data = path.read_bytes()

    def number(fmt, offset):
        if offset < 0 or offset + struct.calcsize(fmt) > len(data):
            raise ValueError('Truncated PE')
        values = struct.unpack_from(fmt, data, offset)
        return values[0] if len(values) == 1 else values

    if data[:2] != b'MZ':
        raise ValueError('Not a PE')
    pe = number('<I', 0x3c)
    if data[pe:pe+4] != b'PE\0\0' or number('<H', pe+4) != 0x8664:
        raise ValueError('Expected Windows x64 PE')
    section_count = number('<H', pe+6)
    optional_size = number('<H', pe+20)
    optional = pe + 24
    if number('<H', optional) != 0x20b or optional_size < 240 or not 1 <= section_count <= 96:
        raise ValueError('Invalid PE32+ header')
    sections = []
    for index in range(section_count):
        offset = optional + optional_size + index*40
        virtual_size, rva, raw_size, raw_offset = number('<IIII', offset+8)
        sections.append((rva, max(virtual_size, raw_size), raw_size, raw_offset))

    def mapped(rva, size=1):
        for base, length, raw_size, raw_offset in sections:
            if base <= rva < base+length and rva-base+size <= raw_size:
                result = raw_offset+rva-base
                if result+size <= len(data):
                    return result
        raise ValueError('PE RVA is outside file-backed sections')

    def name(rva):
        offset = mapped(rva)
        end = data.find(b'\0', offset, min(offset+256, len(data)))
        if end < 0:
            raise ValueError('Invalid imported DLL name')
        text = data[offset:end].decode('ascii').lower()
        if not text.endswith('.dll') or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789_.-' for c in text):
            raise ValueError('Unexpected imported DLL path')
        return text

    result = {}
    for label, directory_index, stride, name_offset in [('normal', 1, 20, 12), ('delay', 13, 32, 4)]:
        rva, size = number('<II', optional+112+directory_index*8)
        imports = []
        if rva:
            if size < stride or size > 65536:
                raise ValueError('Invalid import directory size')
            for offset in range(0, size, stride):
                descriptor = mapped(rva+offset, stride)
                if not any(data[descriptor:descriptor+stride]):
                    break
                if label == 'delay' and number('<I', descriptor) != 1:
                    raise ValueError('Only RVA-based delay imports are supported')
                imports.append(name(number('<I', descriptor+name_offset)))
            else:
                raise ValueError('Unterminated import directory')
        result[label] = sorted(set(imports))
    return result


def prepare(model: Path, runtime_archive: Path, output: Path):
    if not output.is_absolute() or output.exists():
        raise ValueError('Choose a new absolute output directory; existing data is never replaced')
    if os.name != 'nt':
        raise ValueError('This optional Windows component preparation currently requires Windows')
    manifest = json.loads((VENDOR/'manifest.json').read_text(encoding='utf8'))
    model_info, runtime_info = manifest['model'], manifest['runtime']
    verified(model, model_info['bytes'], model_info['sha256'])
    verified(runtime_archive, runtime_info['archive_bytes'], runtime_info['archive_sha256'])
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='vision-component-', dir=output.parent) as temporary:
        stage = Path(temporary).resolve()
        if stage.parent != output.parent.resolve():
            raise ValueError('Temporary directory escaped the intended output parent')
        shutil.copyfile(model, stage/model_info['name'])
        verified(stage/model_info['name'], model_info['bytes'], model_info['sha256'])
        with zipfile.ZipFile(runtime_archive) as archive:
            prefix = f"onnxruntime-win-x64-{runtime_info['version']}/"
            member = prefix + 'lib/' + runtime_info['name']
            data = archive.read(member)
            (stage/runtime_info['name']).write_bytes(data)
            for source, target in [('LICENSE', 'ONNXRuntime-LICENSE.txt'), ('ThirdPartyNotices.txt', 'ONNXRuntime-ThirdPartyNotices.txt')]:
                expected = (VENDOR/target).read_bytes()
                if archive.read(prefix+source) != expected:
                    raise ValueError('Runtime license/notice differs from pinned upstream files')
                (stage/target).write_bytes(expected)
        verified(stage/runtime_info['name'], runtime_info['bytes'], runtime_info['sha256'])
        shutil.copyfile(VENDOR/'YuNet-LICENSE.txt', stage/'YuNet-LICENSE.txt')
        shutil.copyfile(VENDOR/'manifest.json', stage/'manifest.json')
        dependencies = pe_dependencies(stage/runtime_info['name'])
        info = dict(kind='optional-yunet-cpu-component', model_sha256=model_info['sha256'],
                    runtime_sha256=runtime_info['sha256'], dependencies=dependencies,
                    dependency_policy='Explicit runtime, dependencies restricted to System32',
                    external_vc_runtime_required=True, clean_windows_verified=False,
                    cpu_inference_validation='Run tools/validate_native_faces.py separately; preparation is not inference acceptance')
        (stage/'install-info.json').write_text(json.dumps(info, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
        # Windows directory rename cannot replace an existing directory.
        if output.exists():
            raise ValueError('Output appeared during preparation; no replacement attempted')
        stage.rename(output)
    return info


def verify_component(directory: Path):
    manifest=json.loads((VENDOR/'manifest.json').read_text(encoding='utf8'))
    names={manifest['model']['name'],manifest['runtime']['name'],'YuNet-LICENSE.txt',
           'ONNXRuntime-LICENSE.txt','ONNXRuntime-ThirdPartyNotices.txt','manifest.json','install-info.json'}
    if not directory.is_dir() or {path.name for path in directory.iterdir()} != names:
        raise ValueError('Optional vision component file list differs from the pinned set')
    if any((directory/name).is_symlink() or not (directory/name).is_file() for name in names):
        raise ValueError('Optional component must contain regular local files')
    for key in ('model','runtime'):
        asset=manifest[key];verified(directory/asset['name'],asset['bytes'],asset['sha256'])
    for name in ('YuNet-LICENSE.txt','ONNXRuntime-LICENSE.txt','ONNXRuntime-ThirdPartyNotices.txt'):
        if (directory/name).read_bytes() != (VENDOR/name).read_bytes():
            raise ValueError('Optional component license/notice differs from pinned upstream')
    if json.loads((directory/'manifest.json').read_text(encoding='utf8')) != manifest:
        raise ValueError('Optional component model manifest differs')
    info=json.loads((directory/'install-info.json').read_text(encoding='utf8'))
    actual=pe_dependencies(directory/manifest['runtime']['name'])
    if (info.get('kind') != 'optional-yunet-cpu-component' or info.get('dependencies') != actual
            or info.get('external_vc_runtime_required') is not True or info.get('clean_windows_verified') is not False
            or info.get('model_sha256') != manifest['model']['sha256']
            or info.get('runtime_sha256') != manifest['runtime']['sha256']
            or info.get('dependency_policy') != 'Explicit runtime, dependencies restricted to System32'):
        raise ValueError('Optional component must declare the actual imports and external VC++ requirement')
    return info


def install_component(component: Path, stage: Path):
    target=stage/'vision'
    if not stage.is_dir() or target.exists():
        raise ValueError('Existing vision output cannot be replaced')
    verify_component(component)
    target.mkdir()
    for path in component.iterdir():
        shutil.copyfile(path,target/path.name)
    verify_component(target)
    return dict(installed=str(target),external_vc_runtime_required=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('model', 'runtime-archive', 'out', 'component', 'install-to-stage'):
        parser.add_argument('--'+name, type=Path)
    args = parser.parse_args()
    if args.install_to_stage:
        if not args.component:parser.error('--install-to-stage requires --component')
        result=install_component(args.component.resolve(),args.install_to_stage.resolve(strict=True))
    else:
        if not all((args.model,args.runtime_archive,args.out)):parser.error('Preparation requires --model, --runtime-archive and --out')
        result=prepare(args.model.resolve(),args.runtime_archive.resolve(),args.out.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
