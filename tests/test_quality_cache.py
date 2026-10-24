import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from PIL import Image

from cache import ByteLRU
import quality
import server


class CacheTests(unittest.TestCase):
    def test_byte_budget_eviction_and_oversize_bypass(self):
        cache = ByteLRU(1024)
        cache.put(('a', 'image'), b'a' * 600)
        cache.put(('b', 'image'), b'b' * 600)
        self.assertIsNone(cache.get(('a', 'image')))
        self.assertEqual(cache.get(('b', 'image')), b'b' * 600)
        cache.put(('c', 'image'), b'c' * 3000)
        self.assertIsNone(cache.get(('c', 'image')))
        self.assertLessEqual(cache.stats()['bytes'], 1024)

    def test_session_purge_does_not_remove_another_session(self):
        cache = ByteLRU(4096)
        for token in ('a', 'b'):
            cache.put((token, 'linear'), np.zeros((2, 2, 3), np.float32))
        cache.remove_source('a')
        self.assertIsNone(cache.get(('a', 'linear')))
        self.assertIsNotNone(cache.get(('b', 'linear')))

    def test_identical_render_uses_cache_and_new_parameters_do_not(self):
        s = dict(path=Path('generated'), raw=False, cache={}, _cache_id='test-render-cache')
        arr = np.full((6, 8, 3), .2, np.float32)
        with patch.object(server, 'decode', return_value=arr), patch.object(server, 'develop', wraps=server.develop) as develop:
            first = server.preview_result(s, server.DEFAULTS, True)
            calls = develop.call_count
            second = server.preview_result(s, dict(server.DEFAULTS), True)
            self.assertEqual(calls, develop.call_count)
            self.assertEqual(first['image'], second['image'])
            server.preview_result(s, server.DEFAULTS | dict(exposure=.5), True)
            self.assertGreater(develop.call_count, calls)
        server.CACHE.remove_source('test-render-cache')


class QualityTests(unittest.TestCase):
    def test_advice_filter_checks_active_source_and_reduces_bad_suggestion(self):
        arr = np.repeat(np.linspace(.01, .8, 256, dtype=np.float32)[None, :, None], 3, axis=2)
        key = 'test-quality-advice'
        server.SESSIONS[key] = dict(path=Path('generated'), raw=True, cache={})
        try:
            with patch.object(server, 'decode', return_value=arr):
                checked = server.filter_advice(key, server.DEFAULTS, dict(settings=server.DEFAULTS | dict(exposure=4)))
            self.assertLess(checked['settings']['exposure'], 4)
            self.assertLess(checked['quality']['strength'], 1)
        finally:
            server.SESSIONS.pop(key)
        with self.assertRaises(ValueError):
            server.filter_advice(key, server.DEFAULTS, {})

    def test_aggressive_exposure_falls_back_without_touching_input_or_composition(self):
        linear = np.repeat(np.linspace(.01, .8, 256, dtype=np.float32)[None, :, None], 3, axis=2)
        original = linear.copy()
        base = server.DEFAULTS | dict(crop='3:2', rotation=1)
        candidate = base | dict(exposure=4, contrast=100, saturation=100)
        p, report = quality.guard(linear, base, candidate, server.develop)
        self.assertLess(report['strength'], 1)
        self.assertGreater(report['strength'], 0)
        self.assertTrue(report['reasons'])
        self.assertEqual(p['rotation'], 1); self.assertEqual(p['crop'], '3:2')
        self.assertLess(p['exposure'], 4)
        np.testing.assert_array_equal(linear, original)

    def test_mild_improvement_keeps_full_strength(self):
        a = np.repeat(np.linspace(.01, .5, 256, dtype=np.float32)[None, :, None], 3, axis=2)
        p, report = quality.guard(a, server.DEFAULTS, server.DEFAULTS | dict(exposure=.25, shadows=10), server.develop)
        self.assertEqual(report['strength'], 1)
        self.assertEqual(p['exposure'], .25)

    def test_unchanged_and_monochrome_requests_do_not_get_arbitrary_fallback(self):
        a = np.random.default_rng(9).random((30, 30, 3), dtype=np.float32) * .4
        for target in (server.DEFAULTS, server.DEFAULTS | dict(saturation=-100)):
            _, report = quality.guard(a, server.DEFAULTS, target, server.develop)
            self.assertEqual(report['strength'], 1)

    def test_flat_small_and_noisy_images_have_finite_explainable_metrics(self):
        for image in (Image.new('RGB', (1, 1)), Image.new('RGB', (30, 30), 'white'),
                      Image.fromarray(np.random.default_rng(2).integers(0, 255, (20, 20, 3), dtype=np.uint8))):
            report = quality.measure(image)
            self.assertTrue(all(np.isfinite(v) for v in report.values() if isinstance(v, float)))
            self.assertIn('noise_texture_proxy', report)
            self.assertIsInstance(report['message'], str)


if __name__ == '__main__':
    unittest.main()
