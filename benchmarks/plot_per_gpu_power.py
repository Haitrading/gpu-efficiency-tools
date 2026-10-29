#!/usr/bin/env python3
"""Plot truly measured per-card mean/P95/max power from an anonymized aggregate CSV.

Requires matplotlib; run only after export_per_gpu_power.py has processed real logs.
"""
import argparse,csv
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('csv_file',type=Path)
    ap.add_argument('--output',type=Path,default=Path('per_gpu_power.png'))
    args=ap.parse_args()
    with args.csv_file.open(newline='',encoding='utf8') as f:
        data=list(csv.DictReader(f))
    settings=list(dict.fromkeys(x['setting'] for x in data))
    gpus=sorted({x['gpu'] for x in data})
    if not settings or not gpus: raise SystemExit('No samples')
    fig,ax=plt.subplots(figsize=(11,6),dpi=180,layout='constrained')
    for gpu in gpus:
        item={x['setting']:x for x in data if x['gpu']==gpu}
        xx=[i for i,s in enumerate(settings) if s in item]
        yy=[float(item[settings[i]]['mean_w']) for i in xx]
        p95=[float(item[settings[i]]['p95_w']) for i in xx]
        maxs=[float(item[settings[i]]['max_sample_w']) for i in xx]
        ax.plot(xx,yy,'o-',label=f'{gpu}: mean W',linewidth=2)
        ax.scatter(xx,p95,marker='^',s=35,label=f'{gpu}: P95')
        ax.scatter(xx,maxs,marker='x',s=35,label=f'{gpu}: observed max sample')
    ax.set_xticks(range(len(settings)),settings)
    ax.set_ylabel('Driver-reported power (W per GPU)')
    ax.set_xlabel('Max GPU core clock setting (MHz)')
    ax.set_title('Measured power per card: mean, P95 and maximum sample')
    ax.grid(alpha=.25)
    ax.legend(ncol=2,fontsize=9)
    fig.savefig(args.output,bbox_inches='tight',facecolor='white')
    print(args.output)
if __name__=='__main__': main()
