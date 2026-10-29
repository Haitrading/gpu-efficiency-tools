# GPU Efficiency Tools v0.1.3 — first public release

An open-source Linux toolkit for observing AMDGPU telemetry and investigating energy efficiency during local LLM inference.

## Included
- **AMDGPU-SMI v4.2 viewer:** read-only terminal dashboard with 0.5-second sampling, 3-second LIVE averages, 60-second peak hold, and GPU-status colors. Available as a Python script or in an optional minimal Docker container.
- **Experimental request-pacing proxy:** inserts pauses between OpenAI-compatible API requests; **does not** cap instantaneous GPU power and did not improve Wh per task in the tested workload.
- **Six-point 64K benchmark:** actual, owner-approved measurements from 2 × RX 6700 XT running Gemma 4 26B-A4B Q4_0 via llama.cpp/ROCm. Includes CSV data, methodology and charts.
- **Analysis helpers and documentation** for repeatable GPU-power comparisons.

## Benchmark highlight
For the tested 57,768-input-token / 128-output-token workload (three sequential runs per clock setting), a 2000 MHz maximum core-clock setting yielded **6.9156 Wh of combined driver-reported GPU energy**, versus **10.7515 Wh at stock** (35.7% lower). Decode throughput was 51.23 versus 54.49 tokens/s; prefill throughput was 1136 versus 1482 tokens/s. The GPU-energy curve rose again at 1600 and 1200 MHz.

**Limitations:** one prefill-heavy workload, one three-request stage per setting, no randomized order. These are combined GPU-driver readings, **not** wall power or separate instantaneous per-card wattage. Fan RPM is not a direct noise measurement. Results are not a general recommendation for all AMD cards or inference workloads.

## Safety
The viewer is read-only. The request proxy has no authentication and is intended for localhost use. **This release does not ship an automatic GPU clock or power-limit controller.** Do not expose the proxy directly to untrusted networks.

MIT licensed. Contributions and independent reproductions welcome.
