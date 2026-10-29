"""Pure policy tests: do not start a server or write host GPU settings."""
import importlib.util
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / 'governor/request_governor.py'
spec = importlib.util.spec_from_file_location('request_governor', MODULE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class GovernorTests(unittest.TestCase):
    def test_factor_one_no_pause(self):
        self.assertEqual(m.schedule_rest(30, {'enabled':True,'factor':1.0,'max_rest_s':120,'min_rest_s':0}),0)
    def test_factor_06_pause(self):
        self.assertAlmostEqual(m.schedule_rest(30, {'enabled':True,'factor':.6,'max_rest_s':120,'min_rest_s':0}),20)
    def test_pause_cap(self):
        self.assertEqual(m.schedule_rest(300, {'enabled':True,'factor':.2,'max_rest_s':120,'min_rest_s':0}),120)
    def test_disabled(self):
        self.assertEqual(m.schedule_rest(30, {'enabled':False,'factor':.4,'max_rest_s':120,'min_rest_s':0}),0)

if __name__ == '__main__': unittest.main()
