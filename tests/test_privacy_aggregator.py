import tempfile, unittest
from pathlib import Path
import importlib.util
MOD=Path(__file__).resolve().parents[1]/'benchmarks/export_per_gpu_power.py'
s=importlib.util.spec_from_file_location('agg',MOD)
m=importlib.util.module_from_spec(s)
s.loader.exec_module(m)
class TestAggregate(unittest.TestCase):
 def test_quantile_single_sample(self):
  self.assertEqual(m.quantile([123],.95),123)
 def test_quantile_two_samples(self):
  self.assertAlmostEqual(m.quantile([100,200],.95),195)
 def test_aggregate_anonymizes_pci_and_paths(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'trace.csv'
   p.write_text('elapsed_s,gpu_pci,power_w\n0,0000:01:00.0,100\n0,0000:02:00.0,200\n1,0000:01:00.0,120\n1,0000:02:00.0,240\n')
   out=m.summarize([('2000',str(p))])
   self.assertEqual(len(out),2)
   self.assertEqual(out[0]['mean_w'],110)
   self.assertEqual(out[1]['mean_w'],220)
   self.assertNotIn('pci',str(out).lower())
   self.assertNotIn('01:00.0',str(out))
if __name__=='__main__':unittest.main()
