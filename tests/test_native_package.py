"""Distribution failure tests using small synthetic PE/source fixtures."""
import json
from pathlib import Path
import struct
import shutil
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tools import package_native as package


def pe(dll='KERNEL32.dll'):
    data = bytearray(1024)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 0x3c, 0x80)
    data[0x80:0x84] = b'PE\0\0'
    struct.pack_into('<HH', data, 0x84, 0x8664, 1)
    struct.pack_into('<H', data, 0x94, 240)
    opt = 0x98
    struct.pack_into('<H', data, opt, 0x20b)
    struct.pack_into('<I', data, opt+108, 16)
    struct.pack_into('<II', data, opt+120, 0x1000, 40)
    section = opt+240
    struct.pack_into('<III', data, section+12, 0x1000, 512, 512)
    struct.pack_into('<I', data, 512+12, 0x1080)
    encoded = dll.encode()+b'\0'
    data[640:640+len(encoded)] = encoded
    return data


class NativePackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.stage = self.root/'stage'
        for name in ('raw', 'sources', 'licenses'):
            (self.stage/name).mkdir(parents=True)
        (self.stage/'raw/libraw.dll').write_bytes(pe())
        (self.stage/'sources'/package.SDK_NAME).write_bytes(b'synthetic source archive for unit tests')
        sdk_hash = package.sha256(self.stage/'sources'/package.SDK_NAME)
        # Replace only the test trust anchor; actual builds still require the
        # pinned official archive and are tested separately with real NEFs.
        anchor = patch.object(package, 'SDK_HASH', sdk_hash)
        anchor.start()
        self.addCleanup(anchor.stop)
        recipe = self.stage/'sources/build_native_libraw.py'
        recipe.write_bytes(b'synthetic build recipe')
        for name in package.REQUIRED_LICENSES:
            (self.stage/'licenses'/name).write_text('synthetic notice '+name)
        dll = self.stage/'raw/libraw.dll'
        self.runtime = dict(schema_version=1, version='0.22.2', license='CDDL-1.0',
            source_modified=False, archive_sha256=sdk_hash, build_recipe_sha256=package.sha256(recipe),
            dll_sha256=package.sha256(dll), dll_bytes=dll.stat().st_size, imports=['KERNEL32.dll'],
            license_sha256={name: package.sha256(self.stage/'licenses'/name) for name in package.REQUIRED_LICENSES})
        self.write_runtime()
        exe = self.stage/'Z30Studio.exe'
        exe.write_bytes(pe())
        self.info = dict(source_tree_dirty=False, profile='release', raw_bundled=True,
                         source_commit='test-commit', exe_sha256=package.sha256(exe), exe_bytes=exe.stat().st_size)
        self.write_info()

    def write_runtime(self):
        (self.stage/'raw/runtime.json').write_text(json.dumps(self.runtime))

    def test_declared_gui_patch_requires_unchanged_corresponding_source(self):
        self.info['gui_repaint_patch'] = package.PATCH_ID
        self.write_info()
        with self.assertRaises(FileNotFoundError):
            package.verify_stage(self.stage)
        source = self.stage/'sources/slint-winit'
        shutil.copytree(Path(__file__).resolve().parents[1]/'native/vendor/slint-winit', source)
        package.verify_stage(self.stage)
        adapter = source/'winitwindowadapter.rs'
        adapter.write_bytes(adapter.read_bytes()+b'// unintended modification\n')
        with self.assertRaisesRegex(ValueError, 'Changed GUI patch source'):
            package.verify_stage(self.stage)

    def test_gui_patch_cannot_be_silently_undeclared_or_replaced(self):
        self.info['gui_repaint_patch'] = 'unknown-patch'
        self.write_info()
        with self.assertRaisesRegex(ValueError, 'Unknown GUI repaint patch'):
            package.verify_stage(self.stage)
        self.info.pop('gui_repaint_patch')
        self.write_info()
        (self.stage/'sources/slint-winit').mkdir()
        with self.assertRaisesRegex(ValueError, 'Undeclared GUI patch'):
            package.verify_stage(self.stage)

    def write_info(self):
        (self.stage/'build-info.json').write_text(json.dumps(self.info))

    def test_round_trip_and_existing_destination_never_overwritten(self):
        output = self.root/'app.zip'
        package.package(self.stage, output)
        extracted = self.root/'解压 验证'
        with zipfile.ZipFile(output) as archive:
            archive.extractall(extracted)
        package.verify_package(extracted)
        before = package.sha256(output)
        with self.assertRaises(ValueError):
            package.package(self.stage, output)
        self.assertEqual(before, package.sha256(output))
        (extracted/'raw/libraw.dll').write_bytes(b'damaged')
        with self.assertRaises(ValueError):
            package.verify_package(extracted)

    def test_dirty_build_and_changed_executable_rejected(self):
        self.info['source_tree_dirty'] = True
        self.write_info()
        with self.assertRaises(ValueError):
            package.package(self.stage, self.root/'dirty.zip')
        self.info['source_tree_dirty'] = False
        self.write_info()
        (self.stage/'Z30Studio.exe').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            package.verify_stage(self.stage)

    def test_missing_corresponding_source_and_license_rejected(self):
        source = self.stage/'sources'/package.SDK_NAME
        original = source.read_bytes()
        source.write_bytes(b'changed source')
        with self.assertRaises(ValueError):
            package.verify_runtime(self.stage)
        source.write_bytes(original)
        (self.stage/'licenses/LibRaw-CDDL-1.0.txt').unlink()
        with self.assertRaises(FileNotFoundError):
            package.verify_runtime(self.stage)

    def test_actual_pe_imports_checked_even_with_matching_hash(self):
        dll = self.stage/'raw/libraw.dll'
        dll.write_bytes(pe('VCRUNTIME140.dll'))
        self.runtime['dll_sha256'] = package.sha256(dll)
        self.runtime['imports'] = ['VCRUNTIME140.dll']
        self.write_runtime()
        with self.assertRaisesRegex(ValueError, 'unbundled runtime'):
            package.verify_runtime(self.stage)

    def test_malformed_pe_and_delay_imports_rejected(self):
        dll = self.stage/'raw/libraw.dll'
        for data in (b'MZ', b'not a PE', pe()[:600]):
            dll.write_bytes(data)
            with self.assertRaises(ValueError):
                package.pe_imports(dll)
        data = pe()
        struct.pack_into('<I', data, 0x98+112+13*8, 0x1100)
        dll.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'Delay imports'):
            package.pe_imports(dll)

    def test_application_runtime_dependency_rejected(self):
        exe = self.stage/'Z30Studio.exe'
        exe.write_bytes(pe('VCRUNTIME140.dll'))
        self.info['exe_sha256'] = package.sha256(exe)
        self.write_info()
        with self.assertRaisesRegex(ValueError, 'unaudited runtime imports'):
            package.verify_stage(self.stage)

    def test_destination_race_preserves_winner_and_cleans_part(self):
        output = self.root/'race.zip'
        def race(_temporary, destination):
            destination.write_bytes(b'other publisher')
            raise FileExistsError(destination)
        with patch.object(package.os, 'link', side_effect=race):
            with self.assertRaises(FileExistsError):
                package.package(self.stage, output)
        self.assertEqual(output.read_bytes(), b'other publisher')
        self.assertFalse(list(self.root.glob('*.part')))

    def test_stage_mutation_during_zip_is_not_published(self):
        original = zipfile.ZipFile.write
        def changed(archive, filename, *args, **kwargs):
            if Path(filename).name == 'Z30Studio.exe':
                Path(filename).write_bytes(b'changed during packaging')
            return original(archive, filename, *args, **kwargs)
        output = self.root/'changed.zip'
        with patch.object(zipfile.ZipFile, 'write', changed):
            with self.assertRaisesRegex(ValueError, 'changed while packaging'):
                package.package(self.stage, output)
        self.assertFalse(output.exists())
        self.assertFalse(list(self.root.glob('*.part')))


if __name__ == '__main__':
    unittest.main()
