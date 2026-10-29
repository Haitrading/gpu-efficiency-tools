# Optional: measure power separately for each card

The six-point publication table contains **mean combined watts from two GPUs**, not watts per card. An instantaneous per-GPU plot cannot be reconstructed from these six totals. For real per-device results, collect/read local `hwmon` power samples with a supported benchmarking collector and use:

```bash
python3 benchmarks/export_per_gpu_power.py \
  --run Stock=PATH_TO_STOCK_TELEMETRY.csv \
  --run 2400=PATH_TO_2400_TELEMETRY.csv \
  --run 2200=PATH_TO_2200_TELEMETRY.csv \
  --run 2000=PATH_TO_2000_TELEMETRY.csv \
  --run 1600=PATH_TO_1600_TELEMETRY.csv \
  --run 1200=PATH_TO_1200_TELEMETRY.csv \
  --output /tmp/per_gpu_power_summary.csv

python3 benchmarks/plot_per_gpu_power.py /tmp/per_gpu_power_summary.csv --output /tmp/per_gpu_power.png
```

The export **does not include** raw traces, PCI addresses, dates, paths or prompts; it only includes anonymized GPU 1/GPU 2 and summary mean, P95, observed maximum power, sample count per setting. Confirm output and image visually before publishing. This is a **sample-statistics** comparison, not continuous time-resolved waveform data.
