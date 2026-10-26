"""Optional asset preparation must fail closed without replacing data."""
from pathlib import Path
import tempfile
import unittest
from tools.prepare_native_vision import prepare, pe_dependencies, verified


class NativeVisionAssetsTests(unittest.TestCase):
    def test_existing_destination_and_marker_are_never_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = root/'existing'; output.mkdir()
            marker = output/'keep.txt'; marker.write_bytes(b'keep')
            with self.assertRaises(ValueError):
                prepare(root/'missing.onnx', root/'missing.zip', output)
            self.assertEqual(marker.read_bytes(), b'keep')

    def test_missing_and_bad_hash_cannot_publish_component(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaises(ValueError):
                prepare(root/'missing.onnx', root/'missing.zip', root/'new')
            self.assertFalse((root/'new').exists())
            asset = root/'asset'; asset.write_bytes(b'abc')
            with self.assertRaises(ValueError):
                verified(asset, 3, '0'*64)
            self.assertEqual(asset.read_bytes(), b'abc')

    def test_malformed_pe_is_rejected_by_the_separate_delay_import_audit(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)/'fake.dll'; source.write_bytes(b'MZ')
            with self.assertRaises(ValueError):
                pe_dependencies(source)


if __name__ == '__main__':
    unittest.main()
