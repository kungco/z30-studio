import unittest
import automation
import server

class AutomationTests(unittest.TestCase):
    def test_limits_strength_and_composition_are_respected(self):
        base=server.DEFAULTS|dict(crop='1:1',rotation=1)
        proposed=base|dict(exposure=4,saturation=100,temperature=-100,sharpen=90)
        result=automation.constrain(base,proposed,dict(strength=50,exposure_limit=.6,color_limit=10))
        self.assertAlmostEqual(result['exposure'],.3);self.assertEqual(result['saturation'],5)
        self.assertEqual(result['temperature'],-5);self.assertEqual(result['sharpen'],10)
        self.assertEqual(result['crop'],'1:1');self.assertEqual(result['rotation'],1)
    def test_invalid_preferences_are_rejected_and_styles_are_distinct(self):
        for p in ({'style':'execute'},{'strength':float('nan')},{'exposure_limit':99},{'command':'bad'}):
            with self.assertRaises(ValueError):automation.options(p)
        self.assertNotEqual(automation.goal({'style':'night'}),automation.goal({'style':'portrait'}))
