import hashlib
import io
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageCms, PngImagePlugin

import imaging
import server
from tests.color_profiles import wide_rgb_profile, A98_TO_SRGB


def sizes(**kw):
    d = dict(raw_width=16, raw_height=14, width=12, height=10, left_margin=2, top_margin=2,
             crop_left_margin=4, crop_top_margin=4, crop_width=8, crop_height=6, flip=0)
    return SimpleNamespace(**(d | kw))


class RawGeometryTests(unittest.TestCase):
    def test_sensor_inset_is_relative_to_active_image(self):
        self.assertEqual(imaging.raw_crop(sizes()), (2, 2, 8, 6))

    def test_invalid_and_outside_crop_fall_back(self):
        for kw in (dict(crop_left_margin=65535), dict(crop_width=0), dict(crop_left_margin=30)):
            self.assertEqual(imaging.raw_crop(sizes(**kw)), (0, 0, 12, 10))

    def test_all_mirrored_and_rotated_orientations(self):
        a = np.arange(6).reshape(2, 3)
        expected = [a, a[:, ::-1], a[::-1], a[::-1, ::-1], a.T,
                    np.rot90(a), np.rot90(a, -1), a.T[::-1, ::-1]]
        for f, e in enumerate(expected):
            with self.subTest(flip=f):
                np.testing.assert_array_equal(imaging.orient(a, f), e)

    def test_import_metadata_dimensions_follow_all_raw_orientations(self):
        for flip in range(8):
            with self.subTest(flip=flip), patch.object(server.rawpy, 'imread') as read, patch.object(server, 'preview_result', return_value={}):
                read.return_value.__enter__.return_value.sizes = sizes(flip=flip)
                result = server.import_image(b'synthetic RAW metadata test', 'fixture.NEF')
                try:
                    expected = (8, 6) if flip < 4 else (6, 8)
                    self.assertEqual((result['width'], result['height']), expected)
                finally:
                    server.SESSIONS.pop(result['id'])['path'].unlink()

    def test_fractional_odd_crop_keeps_sampling_center(self):
        # Cell values are a linear coordinate ramp at each 2x2 cell's center.
        s = sizes(raw_width=14, raw_height=12, width=14, height=12, left_margin=0, top_margin=0,
                  crop_left_margin=3, crop_top_margin=3, crop_width=8, crop_height=6)
        yy, xx = np.mgrid[:6, :7]
        rgb = np.stack([xx * 2 + .5, yy * 2 + .5, xx * 0], axis=2)
        rgb = np.rint(rgb / 20 * 65535).astype(np.uint16)
        result = imaging.raw_pixels(rgb, s, True)
        # Correct left/top sampling centers are 3.5, not old floor(3/2)*2+.5=2.5.
        np.testing.assert_allclose(result[0, 0, :2] * 20, [3.5, 3.5], atol=.001)

    def test_rgb_shape_mismatch_is_not_silently_cropped(self):
        with self.assertRaises(ValueError):
            imaging.raw_pixels(np.zeros((2, 2, 3), np.uint16), sizes(), False)

    def test_user_rotation_and_crop_for_all_orientations(self):
        for flip in range(8):
            a = imaging.orient(np.zeros((20, 30, 3)), flip)
            for rotation in range(4):
                for crop in ('original', '1:1', '3:2', '16:9'):
                    b = server.geometry(a, server.DEFAULTS | dict(rotation=rotation, crop=crop))
                    self.assertGreater(min(b.shape[:2]), 0)
                    if crop == '1:1':
                        self.assertEqual(b.shape[0], b.shape[1])


class RasterColorTests(unittest.TestCase):
    def decode(self, image, fmt='PNG', **kw):
        b = io.BytesIO(); image.save(b, fmt, **kw); b.seek(0)
        with Image.open(b) as source:
            return imaging.raster_linear(source)

    def test_untagged_rgb_has_explicit_srgb_warning(self):
        a, policy, warnings = self.decode(Image.new('RGB', (2, 2), (128, 64, 32)))
        np.testing.assert_allclose(a[0, 0], imaging.srgb_to_linear(np.array([128, 64, 32]) / 255), atol=1e-7)
        self.assertIn('无 ICC', policy); self.assertTrue(warnings)

    def test_srgb_icc_is_not_double_gamma_transformed(self):
        a, policy, _ = self.decode(Image.new('RGB', (2, 2), (128, 64, 32)), icc_profile=server.ICC)
        np.testing.assert_allclose(a[0, 0], imaging.srgb_to_linear(np.array([128, 64, 32]) / 255), atol=1e-7)
        self.assertIn('ICC', policy)

    def test_wide_gamut_icc_is_really_converted(self):
        encoded = np.array([.5, .3, .2])
        color = tuple(np.rint(encoded * 255).astype(int))
        a, _, _ = self.decode(Image.new('RGB', (2, 2), color), icc_profile=wide_rgb_profile())
        reference = (np.array(color) / 255) ** (563 / 256)
        reference = A98_TO_SRGB @ reference
        np.testing.assert_allclose(a[0, 0], reference, atol=.004)
        self.assertGreater(abs(float(a[0, 0, 0]) - float(imaging.srgb_to_linear(encoded)[0])), .025)

    def test_palette_profile_and_transparency(self):
        image = Image.new('P', (2, 1)); image.putpalette([255, 0, 0] + [0] * 765)
        a, _, warnings = self.decode(image, icc_profile=server.ICC, transparency=0)
        np.testing.assert_array_equal(a, np.ones((1, 2, 3)))
        self.assertTrue(warnings)

    def test_alpha_is_composited_in_linear_light(self):
        a, _, _ = self.decode(Image.new('RGBA', (1, 1), (0, 0, 0, 128)), icc_profile=server.ICC)
        np.testing.assert_allclose(a, 127 / 255, atol=1e-6)

    def test_gray_and_gray_alpha(self):
        a, _, _ = self.decode(Image.new('L', (2, 2), 128))
        self.assertEqual(a.shape, (2, 2, 3))
        a, _, _ = self.decode(Image.new('LA', (1, 1), (0, 0)))
        np.testing.assert_array_equal(a, np.ones((1, 1, 3)))

    def test_gray16_keeps_more_than_256_levels(self):
        a, _, _ = self.decode(Image.fromarray(np.arange(1024, dtype=np.uint16).reshape(32, 32)))
        self.assertEqual(np.unique(a[:, :, 0]).size, 1024)

    def test_untagged_cmyk_and_corrupt_or_mismatched_icc_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'CMYK'):
            self.decode(Image.new('CMYK', (2, 2)), 'JPEG')
        with self.assertRaisesRegex(ValueError, 'ICC'):
            self.decode(Image.new('RGB', (2, 2)), icc_profile=b'broken')
        with self.assertRaisesRegex(ValueError, 'ICC'):
            self.decode(Image.new('CMYK', (2, 2)), 'JPEG', icc_profile=server.ICC)

    def test_png_linear_gamma_is_honored(self):
        info = PngImagePlugin.PngInfo(); import struct
        info.add(b'gAMA', struct.pack('>I', 100000))
        a, policy, _ = self.decode(Image.new('RGB', (2, 2), (128, 64, 32)), pnginfo=info)
        np.testing.assert_allclose(a[0, 0], np.array([128, 64, 32]) / 255, atol=1e-6)
        self.assertIn('gAMA', policy)

    def test_exif_adobe_rgb_interop_is_not_assumed_srgb(self):
        exif = Image.Exif(); exif[34665] = {40961: 65535, 40965: {1: 'R03'}}
        a, policy, warnings = self.decode(Image.new('RGB', (2, 2), (128, 80, 40)), exif=exif)
        self.assertIn('Adobe RGB', policy)
        reference = A98_TO_SRGB @ (
            np.array([128, 80, 40]) / 255) ** (563 / 256)
        np.testing.assert_allclose(a[0, 0], reference, atol=1e-6)

    def test_all_exif_orientations(self):
        image = Image.fromarray(np.arange(18, dtype=np.uint8).reshape(2, 3, 3) * 12)
        expected = {1: image, 2: image.transpose(Image.Transpose.FLIP_LEFT_RIGHT),
                    3: image.transpose(Image.Transpose.ROTATE_180), 4: image.transpose(Image.Transpose.FLIP_TOP_BOTTOM),
                    5: image.transpose(Image.Transpose.TRANSPOSE), 6: image.transpose(Image.Transpose.ROTATE_270),
                    7: image.transpose(Image.Transpose.TRANSVERSE), 8: image.transpose(Image.Transpose.ROTATE_90)}
        for f, e in expected.items():
            exif = Image.Exif(); exif[274] = f
            a, _, _ = self.decode(image, exif=exif)
            np.testing.assert_allclose(a, imaging.srgb_to_linear(np.asarray(e) / 255), atol=1e-6)


@unittest.skipUnless(os.environ.get('Z30_RAW_SAMPLES'), 'Set Z30_RAW_SAMPLES to a directory containing 12/14-bit Z30 NEF samples')
class RealNefTests(unittest.TestCase):
    def test_12_and_14_bit_originals_and_geometry(self):
        for bit in ('12bit', '14bit'):
            path = Path(os.environ['Z30_RAW_SAMPLES']) / ('Z30-' + bit + '.NEF')
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            session = dict(path=path, raw=True, cache={})
            a, b = server.decode(session, 'camera', False), server.decode(session, 'camera', True)
            self.assertEqual(a.shape, (5568, 3712, 3)); self.assertEqual(b.shape, (1600, 1067, 3))
            small = imaging.resize_linear(a, (b.shape[1], b.shape[0]))
            self.assertLess(float(np.mean(abs(small - b))), .02)
            self.assertEqual(a.dtype, np.float32)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_real_14bit_nef_exports_as_rgb16_tiff(self):
        import tifffile
        path = Path(os.environ['Z30_RAW_SAMPLES']) / 'Z30-14bit.NEF'
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        data = server.export_bytes(dict(path=path, raw=True, cache={}), server.DEFAULTS, 'TIFF16')
        with tifffile.TiffFile(io.BytesIO(data)) as tiff:
            pixels = tiff.asarray()
            self.assertEqual(pixels.shape, (5568, 3712, 3))
            self.assertEqual(pixels.dtype, np.uint16)
            self.assertEqual(tiff.pages[0].bitspersample, 16)
            self.assertGreater(np.unique(pixels[::16, ::16]).size, 256)
            self.assertFalse(np.all(pixels[::16, ::16] % 257 == 0))
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)


if __name__ == '__main__':
    unittest.main()
