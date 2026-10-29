# Security notes

- The viewer is read-only; it does not execute GPU configuration writes or open network connections.
- The request governor is experimental and **not an authentication boundary**. It has no rate limiting or access control. Keep it on loopback and behind trusted clients only; container `--network host` shares the host network namespace.
- The benchmark CSV is aggregated, not sampled per-card telemetry. Do not publish unreviewed raw logs, prompt files, crash dumps, photos, screenshots of local services, absolute home-directory paths, or internal network identifiers.
- GPU overdrive control is intentionally not included as an executable installer. Performance tuning on a production server requires a verified rollback mechanism and physical thermal protections.
- For suspected vulnerabilities, use a private GitHub security report when configured; do not paste credentials into public issues.
- Use an explicit release file allowlist, automated pattern scans, manual source review and image-metadata inspection before every publication.
