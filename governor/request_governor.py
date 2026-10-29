#!/usr/bin/env python3
"""Local LLM Request Governor v1.1: persistent software scheduling policy.

Only controls requests routed through this local HTTP proxy. Does NOT set hardware
power limits, clocks, ROCm, Docker or fan curves. Model remains on 18083; proxy 18084.
Standard library only; supports OpenAI-compatible JSON and streaming SSE.

    python3 governor/request_governor.py init
    python3 governor/request_governor.py set --factor 0.6
    python3 governor/request_governor.py serve
    python3 governor/request_governor.py status
    python3 governor/request_governor.py probe
"""
import argparse
import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

HOME = Path(os.environ.get('GEMMA_GOVERNOR_HOME', str(Path.home() / '.local' / 'state' / 'gemma-governor')))
CONFIG_FILE = HOME / 'governor.json'
LOG_FILE = HOME / 'governor.jsonl'
DEFAULT = {
    'enabled': True,
    'factor': 1.0,
    'listen_host': '127.0.0.1',
    'listen_port': 18084,
    'upstream': 'http://127.0.0.1:18083',
    'max_inflight': 1,
    'min_rest_s': 0.0,
    'max_rest_s': 120.0,
}
CONFIG_LOCK = threading.Lock()
INFERENCE_LOCK = threading.Lock()
STATE_LOCK = threading.Lock()
STATE = {
    'requests': 0, 'failed': 0, 'total_inference_s': 0.0,
    'total_rest_s': 0.0, 'last_inference_s': 0.0,
    'last_rest_s': 0.0, 'last_request_end_monotonic': None,
    'next_allowed_monotonic': 0.0,
    'active': False,
}


def load_config():
    if not CONFIG_FILE.exists():
        return DEFAULT.copy()
    with CONFIG_LOCK:
        obj = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
    cfg = DEFAULT.copy()
    cfg.update(obj)
    if not 0.1 <= float(cfg['factor']) <= 1:
        raise ValueError('factor must be in [0.1, 1.0]')
    if cfg['upstream'].rstrip('/') == f"http://{cfg['listen_host']}:{cfg['listen_port']}":
        raise ValueError('upstream cannot refer to proxy itself')
    return cfg


def save_config(cfg):
    HOME.mkdir(parents=True, exist_ok=True)
    with CONFIG_LOCK:
        target = CONFIG_FILE.with_suffix('.tmp')
        target.write_text(json.dumps(cfg, indent=2) + '\n', encoding='utf-8')
        os.replace(target, CONFIG_FILE)


def log_event(event, **extra):
    HOME.mkdir(parents=True, exist_ok=True)
    data = {'ts': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'event': event, **extra}
    with CONFIG_LOCK:
        with LOG_FILE.open('a', encoding='utf-8') as f:
            f.write(json.dumps(data, ensure_ascii=False) + '\n')


def schedule_rest(busy_s, cfg):
    if not cfg['enabled'] or cfg['factor'] >= 1.0:
        return 0.0
    duty_rest = max(0.0, busy_s * (1.0 / float(cfg['factor']) - 1.0))
    return min(float(cfg['max_rest_s']), max(float(cfg['min_rest_s']), duty_rest))


def get_status():
    cfg = load_config()
    with STATE_LOCK:
        st = dict(STATE)
    now = time.monotonic()
    st.pop('last_request_end_monotonic', None)
    until = st.pop('next_allowed_monotonic', 0)
    st['cooldown_remaining_s'] = round(max(0, until - now), 2)
    for key in ('total_inference_s', 'total_rest_s', 'last_inference_s', 'last_rest_s'):
        st[key] = round(st[key], 3)
    return {'config': cfg, 'state': st, 'policy': 'global for traffic sent to proxy only'}


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'GemmaGovernor/1.0'

    def log_message(self, fmt, *args):
        print('%s %s' % (self.log_date_time_string(), fmt % args), flush=True)

    def respond(self, code, obj):
        payload = json.dumps(obj).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path in ('/status', '/health'):
            self.respond(200, get_status())
            return
        if not self.path.startswith('/v1/'):
            self.respond(404, {'error': 'Unsupported path'})
            return
        self.forward('GET', b'', False)

    def do_POST(self):
        if not self.path.startswith('/v1/'):
            self.respond(404, {'error': 'Unsupported path'})
            return
        try:
            n = int(self.headers.get('Content-Length', '-1'))
        except ValueError:
            n = -1
        if n < 0 or n > 64 * 1024 * 1024:
            self.respond(413, {'error': 'Content-Length required, up to 64 MiB'})
            return
        body = self.rfile.read(n)
        try:
            payload = json.loads(body)
        except Exception:
            self.respond(400, {'error': 'Invalid JSON'})
            return
        streaming = bool(payload.get('stream', False))
        self.forward('POST', body, streaming)

    def forward(self, method, body, streaming):
        inference = (method == 'POST' and self.path in (
            '/v1/chat/completions', '/v1/completions', '/v1/responses', '/v1/embeddings'))
        if inference:
            with INFERENCE_LOCK:
                self.execute_request(method, body, streaming, True)
        else:
            self.execute_request(method, body, streaming, False)

    def execute_request(self, method, body, streaming, inference):
        cfg = load_config()
        if inference:
            # Holds exclusive lock across cooldown and request. One inference at a time.
            with STATE_LOCK:
                remaining = max(0, STATE['next_allowed_monotonic'] - time.monotonic())
            if remaining:
                time.sleep(remaining)
            with STATE_LOCK:
                STATE['active'] = True
            start = time.monotonic()
        else:
            remaining = 0.0
            start = time.monotonic()
        upstream = cfg['upstream'].rstrip('/') + self.path
        req = Request(upstream, data=body if method == 'POST' else None,
                      headers={'Content-Type': self.headers.get('Content-Type', 'application/json'),
                               'Accept': self.headers.get('Accept', 'application/json')}, method=method)
        success = False
        status_code = 502
        try:
            try:
                response = urlopen(req, timeout=3600)
            except HTTPError as exc:
                response = exc
            with response:
                status_code = response.status
                content_type = response.headers.get('Content-Type', 'application/json')
                # Return content as a stream only when upstream is event-stream.
                is_sse = streaming and 'text/event-stream' in content_type.lower()
                self.send_response(status_code)
                self.send_header('Content-Type', content_type)
                if is_sse:
                    self.send_header('Transfer-Encoding', 'chunked')
                    self.send_header('Cache-Control', 'no-cache')
                else:
                    payload = response.read()
                    self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                if is_sse:
                    while True:
                        chunk = response.readline()
                        if not chunk:
                            break
                        self.wfile.write(f'{len(chunk):x}\r\n'.encode('ascii'))
                        self.wfile.write(chunk + b'\r\n')
                        self.wfile.flush()
                    self.wfile.write(b'0\r\n\r\n')
                    self.wfile.flush()
                else:
                    self.wfile.write(payload)
            success = 200 <= status_code < 400
        except (OSError, URLError, BrokenPipeError) as exc:
            try:
                if not self.wfile.closed and not self.headers.get('Connection') == 'close':
                    self.respond(502, {'error': type(exc).__name__, 'detail': str(exc)[:200]})
            except OSError:
                pass
        finally:
            if inference:
                duration = max(0, time.monotonic() - start)
                rest_s = schedule_rest(duration, cfg)
                with STATE_LOCK:
                    STATE['active'] = False
                    STATE['requests'] += 1
                    STATE['failed'] += 0 if success else 1
                    STATE['total_inference_s'] += duration
                    STATE['total_rest_s'] += rest_s
                    STATE['last_inference_s'] = duration
                    STATE['last_rest_s'] = rest_s
                    STATE['last_request_end_monotonic'] = time.monotonic()
                    STATE['next_allowed_monotonic'] = time.monotonic() + rest_s
                log_event('inference', ok=success, status=status_code,
                          inference_s=round(duration, 3), next_rest_s=round(rest_s, 3),
                          factor=cfg['factor'], streaming=streaming)


def read_sysfs_telemetry(root=Path('/sys/class/hwmon')):
    """Read-only AMDGPU hwmon sensors, no amd-smi binary or alias required.

    Linux hwmon units: power*_average/input in microwatts, temp*_input in
    millidegrees C, fan*_input in RPM. A sensor may be absent on a given GPU.
    """
    def read_str(p):
        try:
            return p.read_text(encoding='utf-8').strip()
        except (OSError, UnicodeError):
            return None

    def read_num(p):
        s = read_str(p)
        if s is None:
            return None
        try:
            return int(s)
        except ValueError:
            return None

    found = []
    for hw in sorted(root.glob('hwmon*')):
        if read_str(hw / 'name') != 'amdgpu':
            continue
        # Include GPU PCI identifier when exposed by the hwmon device symlink.
        try:
            pci_path = (hw / 'device').resolve()
            pci = next((part for part in reversed(pci_path.parts)
                        if len(part) == 12 and part.count(':') == 2 and part.count('.') == 1), None)
        except OSError:
            pci = None
        entry = {'sensor': hw.name, 'pci': pci}
        power = read_num(hw / 'power1_average')
        power_sensor = 'power1_average'
        if power is None:
            power = read_num(hw / 'power1_input')
            power_sensor = 'power1_input'
        if power is not None:
            entry['power_w'] = round(power / 1_000_000, 2)
            entry['power_source'] = power_sensor
        edge, junction, memory = None, None, None
        for p in sorted(hw.glob('temp*_input')):
            i = p.stem[4:-6]  # temp1_input -> 1
            temp = read_num(p)
            if temp is None:
                continue
            label = read_str(hw / f'temp{i}_label')
            label = (label or '').lower()
            if 'junction' in label or 'hotspot' in label:
                junction = temp / 1000
            elif 'mem' in label:
                memory = temp / 1000
            elif 'edge' in label:
                edge = temp / 1000
            elif i == '1' and edge is None:
                edge = temp / 1000
            entry[f'temp{i}_c'] = round(temp / 1000, 1)
            if label:
                entry[f'temp{i}_label'] = label
        if edge is not None:
            entry['edge_c'] = round(edge, 1)
        if junction is not None:
            entry['junction_c'] = round(junction, 1)
        if memory is not None:
            entry['memory_c'] = round(memory, 1)
        fan = read_num(hw / 'fan1_input')
        if fan is not None:
            entry['fan_rpm'] = fan
        busy = read_num(hw / 'gpu_busy_percent')
        if busy is not None:
            entry['gpu_busy_percent'] = busy
        found.append(entry)
    return found


def cmd_probe():
    # Prefer the kernel's read-only hwmon interface over an external CLI.
    gpus = read_sysfs_telemetry()
    if gpus:
        watts = [g['power_w'] for g in gpus if 'power_w' in g]
        result = {
            'source': 'linux_hwmon_amdgpu_read_only',
            'gpus': gpus,
            'total_reported_gpu_power_w': round(sum(watts), 2) if watts else None,
            'note': ('Driver-reported power, not total wall/socket power. '
                     'Telemetry is diagnostic only; governor duty cycle remains time-based.')
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    binary = shutil.which('amd-smi')
    if binary:
        try:
            proc = subprocess.run(
                [binary, 'metric', '--power', '--temperature', '--fan', '--json'],
                text=True, capture_output=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f'AMD_SMI_ERROR: {exc}')
            return 1
        if proc.returncode:
            print('AMD_SMI_ERROR:', (proc.stderr or proc.stdout)[-600:])
            return 1
        try:
            obj = json.loads(proc.stdout)
        except json.JSONDecodeError:
            print('AMD_SMI_RAW:', proc.stdout[:1500])
            return 1
        print(json.dumps(obj, indent=2, ensure_ascii=False)[:7000])
        print('NOTE: telemetry is diagnostic only; power is not capped in hardware.')
        return 0
    print('TELEMETRY_UNAVAILABLE: neither AMDGPU hwmon nor an amd-smi executable found.')
    print('This does not prevent the governor from scheduling requests on port 18084.')
    return 1


def main():
    parser = argparse.ArgumentParser(description='Local OpenAI-compatible software duty-cycle governor')
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('init')
    sub.add_parser('status')
    sub.add_parser('probe')
    setter = sub.add_parser('set')
    setter.add_argument('--factor', type=float)
    setter.add_argument('--enabled', choices=['on', 'off'])
    srv = sub.add_parser('serve')
    args = parser.parse_args()
    if args.action == 'init':
        if not CONFIG_FILE.exists():
            save_config(DEFAULT.copy())
        print('CONFIG=' + str(CONFIG_FILE))
        print(json.dumps(load_config(), indent=2))
        return 0
    if args.action == 'set':
        cfg = load_config()
        if args.factor is not None:
            if not 0.1 <= args.factor <= 1:
                parser.error('--factor must be between 0.1 and 1.0')
            cfg['factor'] = args.factor
        if args.enabled is not None:
            cfg['enabled'] = (args.enabled == 'on')
        save_config(cfg)
        print('CONFIG=' + str(CONFIG_FILE))
        print(json.dumps(cfg, indent=2))
        return 0
    if args.action == 'status':
        print(json.dumps(get_status(), indent=2))
        print('Live counters: curl -s http://127.0.0.1:18084/status')
        return 0
    if args.action == 'probe':
        return cmd_probe()
    if args.action == 'serve':
        if not CONFIG_FILE.exists():
            save_config(DEFAULT.copy())
        cfg = load_config()
        # Settings are reloaded per request; bind and upstream remain fixed until service restart.
        server = http.server.ThreadingHTTPServer((cfg['listen_host'], cfg['listen_port']), Handler)
        print(f"REQUEST_GOVERNOR=READY http://{cfg['listen_host']}:{cfg['listen_port']} -> {cfg['upstream']}", flush=True)
        print('Only traffic through proxy is throttled; close with Ctrl+C.', flush=True)
        try:
            server.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0


if __name__ == '__main__':
    sys.exit(main())
