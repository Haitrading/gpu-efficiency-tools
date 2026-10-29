# 64K synthetic inference power sweep

## Research question

Can lowering the maximum GPU *core* frequency on two AMD RX 6700 XT cards reduce **GPU watt-hours per completed inference task**, while retaining useful Gemma 4 throughput?

## Test setup (aggregated / non-identifying)

- GPU: 2 × AMD Radeon RX 6700 XT (12 GiB each), one LLM instance split across the cards.
- Model: Gemma 4 26B-A4B Q4_0 GGUF, llama.cpp/ROCm.
- Context target: 65,536 tokens; synthetic technical-text prompt of approximately 57,768 input tokens; 128 output tokens, deterministic generation settings.
- Three sequential inferences per maximum-core-clock setting. Both cards limited to the same maximum except stock baseline.
- VRAM clock cap: 1,000 MHz. GPU voltage offset: 0 mV.
- Time-series driver-reported GPU power combined and integrated to Wh over each benchmark stage. Junction and fan values are stage maxima.
- Driver telemetry measures GPU activity only; no wall-power measurements; no acoustic meter.

## Results

![Energy/performance/thermal/fan four-panel figure](4square.png)

![Combined mean GPU watts by setting](combined_power.png)

The public CSV contains **one aggregated stage result per setting**, not per-GPU or instantaneous raw samples. The power figure therefore shows the **sum of both cards averaged over each stage**; it must not be interpreted as independent GPU0 or GPU1 power or instantaneous peaks. For a real per-card chart, use the optional local per-card metrics extractor and publish only its anonymized aggregate output.

**Lowest measured driver GPU energy among the six tested points: 2000 MHz on both cards (6.9156 Wh per three runs, compared to 10.7515 Wh at stock).** This is not a universal optimum or a guarantee of lower wall energy; very long processing can shift energy into CPU/PSU overhead.

## Caveats

Each frequency had one three-run stage. Stage ordering and initial temperatures were not randomized, so the exact thermal and RPM deltas are not causal estimates. The workload is predominantly prefill, so other prompt/output ratios may produce a different optimum. GPU hardware limits and safe tuning vary. No automatic clock-changing code ships in this release.
