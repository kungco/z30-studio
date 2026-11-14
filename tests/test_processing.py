import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
import tifffile

from imaging import srgb_to_linear
import processing as core
import server


class ToneTests(unittest.TestCase):
    def test_gamut_does_not_amplify_two_lightness_ulps_into_colour_steps(self):
        lab=np.array([[[.8651548624038696,-.012072279118001461,.24119898676872253]]],np.float32)
        shifted=lab.copy()
        for _ in range(2):shifted[...,0]=np.nextafter(shifted[...,0],np.float32(np.inf))
        from colour_management import encode
        a=encode(core.gamut_map(lab.copy()),'srgb');b=encode(core.gamut_map(shifted),'srgb')
        self.assertLess(float(np.max(abs(a-b))),2e-5)

    def test_gamut_matches_independent_cubic_boundary_reference(self):
        fixture=json.loads((Path(__file__).resolve().parents[1]/'native/crates/core/tests/fixtures/gamut-boundaries.json').read_text(encoding='utf8'))
        lab=np.array([case['lab'] for case in fixture['cases']],np.float32)[None]
        expected=np.array([case['expected'] for case in fixture['cases']],np.float32)[None]
        self.assertLess(float(np.max(abs(core.gamut_map(lab)-expected))),2e-6)

    def test_tone_is_monotonic_and_preserves_true_black(self):
        y = np.linspace(0, 12, 30000, dtype=np.float32)
        for shadows in (-100, 0, 100):
            for contrast in (-100, 0, 100):
                for highlights in (-100, 0, 100):
                    p = server.DEFAULTS | dict(shadows=shadows, contrast=contrast, highlights=highlights)
                    out = core.tone_luminance(y, p)
                    self.assertEqual(out[0], 0)
                    self.assertTrue(np.isfinite(out).all())
                    self.assertGreaterEqual(float(np.diff(out).min()), -1e-6)
                    self.assertLessEqual(float(out.max()), 1)

    def test_highlight_shoulder_is_continuous_with_matching_slope(self):
        y = np.array([.649, .65, .651], np.float32)
        out = core.tone_luminance(y, server.DEFAULTS)
        self.assertLess(abs((out[1] - out[0]) - (out[2] - out[1])), 5e-6)

    def test_tone_table_error_and_monotonicity_against_analytic_curve(self):
        y = np.geomspace(1e-7, 40, 30000).astype(np.float32)
        for shadows, contrast, highlights in ((100, -100, -100), (-100, 100, 100), (20, 7, -35), (0, 0, 0)):
            p = server.DEFAULTS | dict(shadows=shadows, contrast=contrast, highlights=highlights)
            a, b = core.fast_tone(y, p), core.tone_luminance(y, p)
            self.assertLess(float(abs(a - b).max()), 2e-6)
            self.assertGreaterEqual(float(np.diff(a).min()), -1e-6)

    def test_white_and_neutrals_do_not_acquire_a_cast(self):
        ramp = np.repeat(np.linspace(0, 2, 1000, dtype=np.float32)[None, :, None], 3, axis=2)
        out = core.render_array(ramp, server.DEFAULTS | dict(exposure=1, shadows=30, contrast=20), bits=16)
        self.assertLessEqual(int(np.abs(out[:, :, 0].astype(int) - out[:, :, 1]).max()), 3)
        self.assertLess(float(np.mean(out == 65535)), .001)

    def test_saturation_boost_preserves_warm_hue(self):
        encoded = np.array([[[.65, .40, .25], [.8, .6, .45], [.42, .25, .17]]], np.float32)
        linear = srgb_to_linear(encoded)
        p = server.DEFAULTS | dict(saturation=100, vibrance=100)
        output = core.display_chunk(linear, p)
        a = core.rgb_to_oklab(linear)
        b = core.rgb_to_oklab(srgb_to_linear(output))
        hue_error = np.arctan2(np.sin(np.arctan2(b[..., 2], b[..., 1]) - np.arctan2(a[..., 2], a[..., 1])),
                              np.cos(np.arctan2(b[..., 2], b[..., 1]) - np.arctan2(a[..., 2], a[..., 1])))
        self.assertLess(float(abs(hue_error).max()), .03)
        self.assertTrue(np.all((output > 0) & (output < 1)))

    def test_strong_saturated_inputs_map_into_gamut_without_channel_clipping(self):
        a = np.array([[[1, .05, .01], [.01, 1, .05], [.03, .01, 1], [-.03, .2, .7]]], np.float32)
        out = core.display_chunk(a, server.DEFAULTS | dict(exposure=1, saturation=80))
        self.assertTrue(np.isfinite(out).all())
        self.assertGreaterEqual(float(out.min()), 0)
        self.assertLessEqual(float(out.max()), 1)
        self.assertLess(np.mean((out <= 1e-7) | (out >= 1 - 1e-7)), .1)

    def test_monochrome_is_neutral_and_input_is_not_modified(self):
        a = np.random.default_rng(4).random((10, 20, 3), dtype=np.float32)
        before = a.copy()
        out = core.render_array(a, server.DEFAULTS | dict(saturation=-100), bits=16)
        np.testing.assert_allclose(out[:, :, 0].astype(int), out[:, :, 1].astype(int), atol=2)
        np.testing.assert_array_equal(a, before)

    def test_chunked_sharpening_has_no_row_seams(self):
        a = np.random.default_rng(7).random((263, 100, 3), dtype=np.float32) * .7
        p = server.DEFAULTS | dict(sharpen=80, shadows=20)
        x = core.render_array(a, p, bits=16, chunk_rows=17)
        y = core.render_array(a, p, bits=16, chunk_rows=1000)
        self.assertLessEqual(int(np.abs(x.astype(int) - y.astype(int)).max()), 1)

    def test_tiff_is_genuine_rgb16_with_embedded_profile_and_sub8bit_steps(self):
        a = np.repeat(np.linspace(.001, .5, 4096, dtype=np.float32).reshape(32, 128, 1), 3, axis=2)
        with patch.object(server, 'decode', return_value=a):
            data = server.export_bytes({}, server.DEFAULTS | dict(sharpen=20), 'TIFF16')
        with tifffile.TiffFile(io.BytesIO(data)) as tiff:
            page = tiff.pages[0]; pixels = page.asarray()
            self.assertEqual(pixels.dtype, np.uint16)
            self.assertEqual(page.bitspersample, 16)
            self.assertEqual(page.samplesperpixel, 3)
            self.assertEqual(page.tags[34675].value, server.ICC)
            self.assertGreater(np.unique(pixels[:, :, 0]).size, 1000)
            self.assertFalse(np.all(pixels % 257 == 0))


if __name__ == '__main__':
    unittest.main()
