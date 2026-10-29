"""Dependency-free renderer and discovery checks; no hardware writes."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / 'viewer/amdgpu_smi.py'
spec = importlib.util.spec_from_file_location('amdgpu_smi', MODULE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ViewerTests(unittest.TestCase):
    def setUp(self):
        m.GPUS = ('01:00.0', '02:00.0')

    def test_frames_have_equal_visible_width(self):
        now = 1000.0
        h, s = m.demo_histories(now)
        for width in (20, 40, 60, 80, 100, 112, 140):
            for color in (True, False):
                rendered = m.draw(h, s, now, .5, 1.,
                                  columns=width, use_color=color,
                                  clocks_override=(2000, 2000), boot_override='quiet')
                lengths = [m.visible_len(line) for line in rendered.splitlines()]
                self.assertEqual(len(set(lengths)), 1, (width, color, lengths))
                self.assertLessEqual(lengths[0], max(width, 20))

    def test_status_threshold(self):
        now = 1000.0
        h, _ = m.demo_histories(now)
        self.assertEqual(m.whole_status(h, now, (2000, 2000), 'quiet')[0], 'GREEN')
        h['01:00.0'][-1][1]['junction'] = 84
        self.assertEqual(m.whole_status(h, now, (2000, 2000), 'quiet')[0], 'RED')

    def test_discovery_and_restriction(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            for pci in ('0000:01:00.0', '0000:02:00.0'):
                p = base / pci
                (p / 'hwmon' / 'hwmon5').mkdir(parents=True)
                (p / 'vendor').write_text('0x1002\n')
                (p / 'gpu_busy_percent').write_text('1\n')
                (p / 'hwmon' / 'hwmon5' / 'power1_average').write_text('10000000\n')
            self.assertEqual(m.pick_gpus(base), ('01:00.0','02:00.0'))
            self.assertEqual(m.pick_gpus(base, '02:00.0'), ('02:00.0',))
            with self.assertRaises(RuntimeError):
                m.pick_gpus(base, '04:00.0')

    def test_three_gpu_render(self):
        now = 1000.0
        h, s = m.demo_histories(now)
        h['03:00.0'] = h['02:00.0'].copy()
        s['03:00.0'] = s['02:00.0']
        m.GPUS = ('01:00.0','02:00.0','03:00.0')
        rendered = m.draw(h, s, now, .5, 1., columns=112, use_color=True,
                          clocks_override=(2000,2000,2000), boot_override='not set')
        self.assertEqual(len(set(m.visible_len(l) for l in rendered.splitlines())),1)


if __name__ == '__main__':
    unittest.main()
