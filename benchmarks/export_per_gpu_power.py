#!/usr/bin/env python3
"""Sanitize *local* benchmark GPU sensor CSV logs to per-card stage statistics.

Consumes CSVs with `gpu_pci,power_w` columns (also accepts `gpu_pci` + `power_w`);
outputs only setting, anonymized device index and summarized power. Does not
write original PCI IDs, filenames, source paths, dates or samples.
Example:
 python3 benchmarks/export_per_gpu_power.py \
   --run Stock=PATH_A.csv \
   --run 2400=PATH_B.csv \
   --output /tmp/per_gpu_aggregate.csv
"""
import argparse,csv,math,statistics
from collections import defaultdict
from pathlib import Path

def quantile(xs, ratio):
    s=sorted(xs); idx=(len(s)-1)*ratio; i=int(idx); j=min(len(s)-1,i+1)
    return s[i]*(1-(idx-i))+s[j]*(idx-i)

def summarize(inputs):
    rows=[]
    for setting,path in inputs:
        by_gpu=defaultdict(list)
        with Path(path).open(newline='',encoding='utf8') as f:
            reader=csv.DictReader(f)
            if not {'gpu_pci','power_w'} <= set(reader.fieldnames or []):
                raise ValueError('Expected columns: gpu_pci,power_w')
            for record in reader:
                try:
                    n=float(record['power_w'])
                except (ValueError,TypeError):
                    continue
                if math.isfinite(n) and 0 <= n <= 1000:
                    by_gpu[record['gpu_pci']].append(n)
        if not by_gpu:
            raise ValueError(f'No valid power samples in the {setting!r} run')
        for index,(_,values) in enumerate(sorted(by_gpu.items()),1):
            rows.append({'setting':setting,'gpu':f'GPU {index}',
                         'mean_w':round(statistics.fmean(values),2),
                         'p95_w':round(quantile(values,.95),2),
                         'max_sample_w':round(max(values),2),
                         'n_samples':len(values)})
    return rows

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run',action='append',required=True,metavar='LABEL=CSV')
    ap.add_argument('--output',required=True)
    a=ap.parse_args()
    inputs=[]
    for spec in a.run:
        if '=' not in spec: ap.error('--run must be LABEL=CSV')
        label,path=spec.split('=',1)
        if not label or not path: ap.error('--run must be LABEL=CSV')
        inputs.append((label,path))
    result=summarize(inputs)
    out=Path(a.output)
    out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('w',encoding='utf8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['setting','gpu','mean_w','p95_w','max_sample_w','n_samples'])
        writer.writeheader();writer.writerows(result)
    print(f'Aggregated {len(inputs)} runs and {len(result)} per-card summaries into {out}.')
    print('Review the aggregate CSV before sharing. Raw PCI IDs and source paths are not included.')
if __name__=='__main__': main()
