"""Build the pinned LibRaw source as a self-contained Windows DLL (CDDL 1.0).

No installer, global PATH change, or modification of the input SDK. The output
must be a new directory. Ship raw/, sources/ and licenses/ together.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
LICENSE_ROOT = ROOT / 'native/licenses' if (ROOT / 'native/licenses').is_dir() else ROOT / 'licenses'
SDK_HASH = 'ac64fa12bb00a7581332d4c6ab918c0533fb3f119d6b668d47a6875410dca948'
SDK_NAME = 'LibRaw-0.22.2-Win64.zip'
SYSTEM_DLLS = {'kernel32.dll', 'msvcrt.dll', 'ucrtbase.dll', 'user32.dll',
               'advapi32.dll', 'ws2_32.dll'}


def sha256(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def library_sources(makefile: str) -> list[str]:
    match = re.search(r'LIB_OBJECTS=(.*?)\n\n', makefile, re.S)
    if not match:
        raise ValueError('Pinned source Makefile has no LIB_OBJECTS')
    objects = re.findall(r'object/[\w]+\.o', match.group(1))
    sources = []
    for obj in objects:
        rule = re.search(r'-o ' + re.escape(obj) + r' (src/[\w/]+\.cpp)', makefile)
        if not rule:
            raise ValueError(f'Missing compilation rule: {obj}')
        sources.append(rule.group(1))
    if len(sources) != 79 or len(set(sources)) != 79:
        raise ValueError('Unexpected LibRaw 0.22.2 source list')
    return sources


def check_system_imports(imports: list[str]) -> None:
    if not imports or any(name.lower() not in SYSTEM_DLLS for name in imports):
        raise ValueError(f'DLL still requires an unbundled runtime: {imports}')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--toolchain-bin', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=4, choices=range(1, 9))
    args = parser.parse_args()
    archive = args.archive.resolve(strict=True)
    if sha256(archive) != SDK_HASH:
        raise ValueError('SDK SHA-256 mismatch; refusing to build')
    toolchain = args.toolchain_bin.resolve(strict=True)
    compiler = toolchain / 'g++.exe'
    objdump = toolchain / 'objdump.exe'
    for tool in (compiler, objdump):
        if not tool.is_file():
            raise FileNotFoundError(tool)
    for name in ('GCC-COPYING3.txt', 'GCC-RUNTIME-EXCEPTION.txt', 'MinGW-w64-runtime.txt'):
        if not (LICENSE_ROOT / name).is_file():
            raise FileNotFoundError(name)
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=False)
    build = output / 'build'
    build.mkdir()
    with zipfile.ZipFile(archive) as sdk:
        for item in sdk.infolist():
            target = (build / item.filename).resolve()
            if not target.is_relative_to(build):
                raise ValueError('Unsafe source archive member')
        sdk.extractall(build)
    source = build / 'LibRaw-0.22.2'
    sources = library_sources((source / 'Makefile.mingw').read_text())
    objects_dir = build / 'objects'
    objects_dir.mkdir()
    raw = output / 'raw'
    raw.mkdir()
    env = {**os.environ, 'PATH': str(toolchain) + os.pathsep + os.environ.get('PATH', '')}
    flags = ['-O3', '-std=gnu++17', '-I.', '-DLIBRAW_BUILDLIB', '-DLIBRAW_NOTHREADS',
             '-ffp-contract=off', '-fno-fast-math']

    def compile_source(item: tuple[int, str]) -> Path:
        index, path = item
        obj = objects_dir / f'{index:03}.o'
        command = [str(compiler), *flags, '-c', path, '-o', str(obj)]
        result = subprocess.run(command, cwd=source, env=env, capture_output=True, text=True)
        (objects_dir / f'{index:03}.log').write_text(result.stdout + result.stderr, encoding='utf-8')
        if result.returncode:
            raise RuntimeError(f'Compile failed: {path}; see {objects_dir / f"{index:03}.log"}')
        return obj

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        objects = list(pool.map(compile_source, enumerate(sources)))
    link_flags = ['-shared', '-static', '-static-libgcc', '-static-libstdc++',
                  '-Wl,--no-insert-timestamp', '-s']
    dll = raw / 'libraw.dll'
    subprocess.run([str(compiler), *link_flags, *map(str, objects), '-o', str(dll),
                    '-lws2_32'], cwd=source, env=env, check=True)
    headers = subprocess.check_output([str(objdump), '-p', str(dll)], env=env, text=True)
    imports = re.findall(r'DLL Name:\s*(\S+)', headers)
    check_system_imports(imports)
    source_out = output / 'sources'
    source_out.mkdir()
    shutil.copyfile(archive, source_out / SDK_NAME)
    shutil.copyfile(__file__, source_out / 'build_native_libraw.py')
    licenses = output / 'licenses'
    licenses.mkdir()
    for original, name in [('COPYRIGHT', 'LibRaw-COPYRIGHT.txt'),
                           ('LICENSE.CDDL', 'LibRaw-CDDL-1.0.txt'),
                           ('LICENSE.LGPL', 'LibRaw-LGPL-2.1.txt')]:
        shutil.copyfile(source / original, licenses / name)
    for name in ('GCC-COPYING3.txt', 'GCC-RUNTIME-EXCEPTION.txt', 'MinGW-w64-runtime.txt'):
        shutil.copyfile(LICENSE_ROOT / name, licenses / name)
    manifest = {
        'schema_version': 1, 'version': '0.22.2', 'license': 'CDDL-1.0',
        'source_url': 'https://www.libraw.org/data/' + SDK_NAME,
        'archive_sha256': SDK_HASH, 'source_modified': False,
        'compiler': subprocess.check_output([str(compiler), '--version'], env=env, text=True).splitlines()[0],
        'compiler_sha256': sha256(compiler), 'compile_flags': flags, 'link_flags': link_flags,
        'sources': sources, 'dll_sha256': sha256(dll), 'dll_bytes': dll.stat().st_size,
        'build_recipe_sha256': sha256(source_out / 'build_native_libraw.py'),
        'license_sha256': {p.name: sha256(p) for p in sorted(licenses.iterdir())},
        'imports': imports, 'optional_codecs': [], 'openmp': False,
        'notice': 'Unmodified LibRaw source, GNU Windows build. No MSVC runtime required. '
                  'Corresponding source, build recipe and licenses are included.',
    }
    (raw / 'runtime.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(output), 'dll': str(dll), 'sha256': manifest['dll_sha256'],
                      'imports': imports}, indent=2))


if __name__ == '__main__':
    main()
