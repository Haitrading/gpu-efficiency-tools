# Viewer notes

The viewer discovers AMDGPU PCI devices with accessible `gpu_busy_percent` and `hwmon` power sensors. It uses no external monitoring API and makes no GPU-setting changes.

`--demo` prints synthetic values. `--once` reads one actual sample; `--color never` disables ANSI; `--pci` filters detected devices. Power is sampled in watts via micro-watt Linux hwmon fields.

- LIVE: rolling 3s average for power, utilization, fans and clocks; instantaneous temperature and VRAM occupancy.
- PEAK: raw maximum over rolling 60 seconds (minimum for free VRAM).
- STATUS GREEN/YELLOW/RED: diagnostic thresholds only, **not** a hardware safety shutdown.
- Any optional profile file uses `AMDGPU_SMI_PROFILE_FILE` or `/etc/amdgpu-efficiency-profile`; the viewer never writes it.
- Power consumption from kernel sensors is not equivalent to whole-system wall power.

Docker can read `/sys` read-only; it should not be granted privileged device access to work around inaccessible sensors. Host and guest permissions vary.
