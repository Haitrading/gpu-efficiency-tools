#!/usr/bin/env python3
"""AMDGPU-SMI v4.2: boxed terminal, 0.5s sensors, 3s LIVE, 60s peaks.

No third-party dependencies. Read-only. Detects AMDGPU PCI devices automatically.
The LIVE values for load/clocks/fan/power are sampled 3s means; VRAM and
thermal readings are current. Peak hold uses raw 60s maxima (VRAM free min).
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import textwrap
from collections import deque
from datetime import datetime
from pathlib import Path
import re
import sys
import time

GPUS = ('01:00.0', '02:00.0')  # demo devices; detected at runtime
BASE = Path('/sys/bus/pci/devices')
PROFILE_FILE = Path(os.environ.get('AMDGPU_SMI_PROFILE_FILE', '/etc/amdgpu-efficiency-profile'))
LIVE_SEC = 3.0
PEAK_SEC = 60.0


def read_number(path: Path | None, scale: float = 1.0):
    if path is None:
        return None
    try:
        return float(path.read_text().strip()) / scale
    except (OSError, ValueError, UnicodeError):
        return None


def read_text(path: Path | None):
    if path is None:
        return ''
    try:
        return path.read_bytes().decode('utf-8', 'replace').replace('\x00', '').strip()
    except OSError:
        return ''


def temp_sensor(hw: Path, label: str):
    for f in hw.glob('temp*_label'):
        if read_text(f).lower() == label:
            return f.with_name(f.name.replace('_label', '_input'))
    return None


def pick_gpus(base: Path = BASE, override: str = '') -> tuple[str, ...]:
    """Discover AMD display/compute devices exposing the amdgpu usage counters."""
    found = []
    for dev in sorted(base.glob('0000:*')):
        try:
            vendor = (dev / 'vendor').read_text().strip().lower()
        except OSError:
            continue
        if vendor != '0x1002' or not (dev / 'gpu_busy_percent').exists():
            continue
        if any((h / 'power1_average').exists() for h in (dev / 'hwmon').glob('hwmon*')):
            found.append(dev.name[5:])
    if override:
        wanted = tuple(x.strip() for x in override.split(',') if x.strip())
        missing = set(wanted) - set(found)
        if missing:
            raise RuntimeError(f'AMDGPU devices not found or unreadable: {sorted(missing)}')
        if len(set(wanted)) != len(wanted):
            raise RuntimeError('Duplicate PCI IDs requested')
        return wanted
    if not found:
        raise RuntimeError('No accessible AMDGPU hwmon sensors found')
    return tuple(found)


def discover(base: Path = BASE):
    sensors = {}
    for gpu in GPUS:
        dev = base / ('0000:' + gpu)
        hw = next((h for h in (dev / 'hwmon').glob('hwmon*')
                   if (h / 'power1_average').exists()), None)
        if hw is None:
            raise RuntimeError(f'AMDGPU power sensor not found: {dev}')
        sensors[gpu] = dict(
            dev=dev, hw=hw, edge=temp_sensor(hw, 'edge'),
            junction=temp_sensor(hw, 'junction'), mem=temp_sensor(hw, 'mem'),
        )
    return sensors


def read_max_mhz(dev: Path):
    text = read_text(dev / 'pp_od_clk_voltage')
    match = re.search(r'(?m)^OD_SCLK:\s*\n(?:.*\n)*?1:\s*(\d+)\s*MHz', text, re.I)
    return int(match.group(1)) if match else None


def sample(sensor: dict):
    dev, hw = sensor['dev'], sensor['hw']
    total = read_number(dev / 'mem_info_vram_total', 1024**3)
    used = read_number(dev / 'mem_info_vram_used', 1024**3)
    temp = {key: read_number(sensor[key], 1000) for key in ('edge', 'junction', 'mem')}
    core = read_number(hw / 'freq1_input', 1e6)
    memclk = read_number(hw / 'freq2_input', 1e6)
    result = {
        'gpu_busy': read_number(dev / 'gpu_busy_percent'),
        'mem_busy': read_number(dev / 'mem_busy_percent'),
        'vram_used': used,
        'vram_free': max(0., total - used) if total is not None and used is not None else None,
        'vram_pct': 100 * used / total if total and used is not None else None,
        'vram_total': total,
        'core_mhz': core if core and core > 0 else None,
        'mem_mhz': memclk if memclk and memclk > 0 else None,
        'edge': temp['edge'],
        'junction': temp['junction'],
        'mem': temp['mem'],
        'delta': (temp['junction'] - temp['edge']) if temp['junction'] is not None and temp['edge'] is not None else None,
        'fan_rpm': read_number(hw / 'fan1_input'),
        'fan_pct': read_number(hw / 'pwm1', 2.55),
        'power': read_number(hw / 'power1_average', 1e6),
        'perf': read_text(dev / 'power_dpm_force_performance_level') or '?',
        'fan_mode': {'1':'LACT/manual', '2':'firmware AUTO'}.get(read_text(hw / 'pwm1_enable'), '?'),
    }
    return result


def recent(history: deque, key: str, seconds: float, now: float, mode: str):
    values = [row[key] for at, row in history if at >= now - seconds and row.get(key) is not None]
    if not values:
        return None
    if mode == 'mean': return sum(values) / len(values)
    if mode == 'min': return min(values)
    if mode == 'max': return max(values)
    return values[-1]


def energy_wh(histories: dict, now: float):
    """Integral of sampled driver power over available part of last 60s, Wh."""
    total = 0.0
    for history in histories.values():
        points = [(t, row['power']) for t, row in history
                  if row.get('power') is not None]
        cutoff = now - PEAK_SEC
        if len(points) >= 2 and points[0][0] < cutoff:
            for i in range(1, len(points)):
                if points[i][0] >= cutoff:
                    t0, p0 = points[i-1]
                    t1, p1 = points[i]
                    p_at_cutoff = p0 + (p1-p0) * (cutoff-t0) / (t1-t0) if t1 > t0 else p1
                    points = [(cutoff, p_at_cutoff)] + points[i:]
                    break
        points = [(t, p) for t, p in points if t >= cutoff]
        for (t0, p0), (t1, p1) in zip(points, points[1:]):
            total += (p0 + p1) * 0.5 * (t1 - t0) / 3600.0
    return total


def fmt(value, unit='', places=0):
    return '-' if value is None else f'{value:.{places}f}{unit}'


def effective_profile(max_clocks: tuple):
    if not max_clocks or None in max_clocks:
        return 'UNKNOWN'
    if len(set(max_clocks)) == 1:
        return {2400: 'BALANCED', 2200: 'EFFICIENT', 2000: 'QUIET'}.get(max_clocks[0], 'CUSTOM')
    return 'CUSTOM'


# Box renderer: 7-bit ASCII geometry; ANSI escapes never count as columns.
ANSI = re.compile(r'\x1b\[[0-9;]*m')
RESET = '\x1b[0m'
COLORS = {'green': '\x1b[92m', 'yellow': '\x1b[93m', 'red': '\x1b[91m',
          'sky': '\x1b[96m'}


def visible(text: str) -> str:
    return ANSI.sub('', str(text))


def visible_len(text: str) -> int:
    return len(visible(text))  # all visible output is ASCII


def colored(value: str, shade: str | None, enabled: bool) -> str:
    return COLORS[shade] + value + RESET if enabled and shade else value


def padded(value: str, width: int, align: str = 'left') -> str:
    plain = visible(value)
    if len(plain) > width:
        # Never allow a value or ANSI code to move the frame right edge.
        value = plain[:max(0, width - 2)] + '..' if width >= 2 else plain[:width]
        plain = visible(value)
    blank = ' ' * (width - len(plain))
    return blank + value if align == 'right' else value + blank


def border(width: int, style: str = '=') -> str:
    return '+' + style * (width - 2) + '+'


def boxed(content: str, width: int) -> str:
    return '|' + padded(' ' + content, width - 2) + '|'


def boxed_lines(content: str, width: int) -> list[str]:
    # Wrap long status/metadata at word boundaries, preserving every value.
    raw = visible(content)
    if len(raw) > width - 3:
        raw = raw.replace(' | ', '; ')
    chunks = textwrap.wrap(raw, width - 3, break_long_words=False,
                           break_on_hyphens=False) or ['']
    return [boxed(chunk, width) for chunk in chunks]


def schema_width(schema: list[tuple[str, int]]) -> int:
    return 4 + sum(w for _, w in schema) + 3 * (len(schema) - 1)


def table_line(values: list[str], schema: list[tuple[str, int]]) -> str:
    return '| ' + ' | '.join(padded(v, w, 'right' if i else 'left')
                           for i, (v, (_, w)) in enumerate(zip(values, schema))) + ' |'


def table_divider(schema: list[tuple[str, int]]) -> str:
    """nvidia-smi-style +---+-----+ borders matching table_line bar positions."""
    return '+' + '+'.join('-' * (w + 2) for _, w in schema) + '+'


def whole_status(histories: dict, now: float, clocks: tuple, boot: str) -> tuple[str, str]:
    """One overall GREEN / YELLOW / RED indicator, based on last 60s raw peaks."""
    grade = 0
    reasons = []
    for gpu in GPUS:
        latest = histories[gpu][-1][1]
        peak_j = recent(histories[gpu], 'junction', PEAK_SEC, now, 'max')
        peak_m = recent(histories[gpu], 'mem', PEAK_SEC, now, 'max')
        peak_f = recent(histories[gpu], 'fan_rpm', PEAK_SEC, now, 'max')
        if any(latest.get(k) is None for k in ('junction', 'mem', 'power')):
            grade = 2
            reasons.append(f'GPU {gpu[:2]} sensor missing')
            continue
        if ((peak_j is not None and peak_j >= 83) or
            (peak_m is not None and peak_m >= 90) or
            (peak_f is not None and peak_f >= 2400)):
            grade = 2
            reasons.append(f'GPU {gpu[:2]} thermal/fan limit')
        elif ((peak_j is not None and peak_j >= 70) or
              (peak_m is not None and peak_m >= 80) or
              (peak_f is not None and peak_f >= 1600)):
            grade = max(grade, 1)
            reasons.append(f'GPU {gpu[:2]} elevated load')
    if boot.lower() in ('quiet','efficient','balanced') and effective_profile(clocks).lower() != boot.lower():
        grade = max(grade, 1)
        reasons.append('boot profile mismatch')
    return ('GREEN','YELLOW','RED')[grade], (', '.join(reasons) if reasons else 'normal')


WIDE_SCHEMA = [('GPU', 3), ('GPU%', 5), ('MEM BUSY', 8), ('CORE', 6),
               ('MCLK', 5), ('EDGE', 4), ('JUNC', 4), ('MEMC', 4),
               ('FAN', 5), ('WATT', 6), ('USED/TOT GiB', 13)]
COMPACT_SCHEMA = [('GPU', 3), ('GPU%', 5), ('CORE', 6), ('JUNC', 5),
                  ('FAN', 5), ('WATT', 6), ('USED/TOT', 11)]
NARROW_SCHEMA = [('GPU', 3), ('GPU%', 5), ('JUNC', 5), ('FAN', 5), ('WATT', 6)]


def best_schema(cols: int, compact: bool = False):
    if not compact and cols >= schema_width(WIDE_SCHEMA):
        return WIDE_SCHEMA
    if cols >= schema_width(COMPACT_SCHEMA):
        return COMPACT_SCHEMA
    if cols >= schema_width(NARROW_SCHEMA):
        return NARROW_SCHEMA
    return None


def meter_color(metric: str, value, peak: bool = False):
    if value is None:
        return None
    if metric == 'junction':
        return 'red' if value >= 83 else ('yellow' if value >= 70 else 'green')
    if metric == 'mem':
        return 'red' if value >= 90 else ('yellow' if value >= 80 else None)
    if metric == 'fan_rpm':
        return 'red' if value >= 2200 else ('yellow' if value >= 1600 else
                                            None)
    if metric == 'power':
        return 'red' if value >= 220 else ('yellow' if value >= 190 else None)
    return None


def numeric(data: dict, key: str, unit: str = '', digits: int = 0,
            use_color: bool = False, peak: bool = False) -> str:
    value = data.get(key)
    if value is None:
        return '-'
    text = f'{value:.{digits}f}{unit}'
    return colored(text, meter_color(key, value, peak), use_color)


def get_values(histories: dict, gpu: str, now: float, peak: bool) -> dict:
    h = histories[gpu]
    d = {}
    averaged = {'gpu_busy', 'mem_busy', 'core_mhz', 'mem_mhz',
                'fan_rpm', 'fan_pct', 'power'}
    primary = ('gpu_busy', 'mem_busy', 'core_mhz', 'mem_mhz', 'edge', 'junction',
               'mem', 'delta', 'fan_rpm', 'fan_pct', 'power',
               'vram_used', 'vram_free', 'vram_pct', 'vram_total')
    for key in primary:
        method = ('min' if key == 'vram_free' else 'max') if peak else (
                 'mean' if key in averaged else 'last')
        d[key] = recent(h, key, PEAK_SEC if peak else LIVE_SEC, now, method)
    return d


def table_data(d: dict, gpu: str, schema, color: bool) -> list[str]:
    val = {
        'GPU': gpu[:2],
        'GPU%': numeric(d, 'gpu_busy', '%'),
        'MEM BUSY': numeric(d, 'mem_busy', '%'),
        'CORE': numeric(d, 'core_mhz') if d['core_mhz'] is not None else 'idle',
        'MCLK': numeric(d, 'mem_mhz'),
        'EDGE': numeric(d, 'edge', digits=0),
        'JUNC': numeric(d, 'junction', digits=0),
        'MEMC': numeric(d, 'mem', digits=0),
        'FAN': numeric(d, 'fan_rpm', digits=0),
        'WATT': numeric(d, 'power', digits=1),
        'USED/TOT GiB': f'{d["vram_used"]:.2f}/{d["vram_total"]:.2f}'
        if d['vram_used'] is not None and d['vram_total'] is not None else '-',
        'USED/TOT': f'{d["vram_used"]:.2f}/{d["vram_total"]:.2f}'
        if d['vram_used'] is not None and d['vram_total'] is not None else '-',
    }
    return [val[label] for label, _ in schema]


def extra_line(gpu: str, d: dict, schema, peak: bool = False) -> str:
    label = 'free MIN' if peak else 'free'
    s = (f'GPU {gpu[:2]}: {label} {fmt(d["vram_free"], " GiB", 2)} | '
         f'VRAM use {fmt(d["vram_pct"], "%", 1)} | '
         f'hotspot delta {fmt(d["delta"], " C", 1)} | '
         f'fan PWM {fmt(d["fan_pct"], "%", 1)}')
    if schema is not WIDE_SCHEMA:
        s += (f' | edge {fmt(d["edge"], " C", 0)} | VRAM temp {fmt(d["mem"], " C", 0)}'
              f' | MCLK {fmt(d["mem_mhz"], " MHz", 0)}')
    return s


def draw(histories: dict, sensors: dict, now: float, sample_interval: float,
         refresh: float, boot_profile_file: Path = PROFILE_FILE,
         *, columns: int = 112, force_compact: bool = False,
         use_color: bool = False, clocks_override=None, boot_override=None):
    schema = best_schema(columns, force_compact)
    if schema is None:
        width = max(20, columns)
        content = [border(width), boxed('AMDGPU-SMI v4.2', width), border(width, '='),
                   boxed('Terminal too narrow: widen to 50+ columns', width), border(width)]
        return '\n'.join(content)

    width = schema_width(schema)
    lines: list[str] = []

    def info(content: str, *, shade=None):
        # ANSI color is applied after line padding; color codes never occupy columns.
        for line in boxed_lines(content, width):
            if shade and use_color:
                line = line[:1] + colored(line[1:-1], shade, True) + line[-1:]
            lines.append(line)

    def section(title: str, desc: str, peak: bool):
        info(f'{title}  |  {desc}', shade='sky')
        lines.append(table_divider(schema))
        head = table_line([n for n, _ in schema], schema)
        if use_color:
            head = colored(head, 'sky', True)
        lines.append(head)
        lines.append(table_divider(schema))
        for gpu in GPUS:
            d = get_values(histories, gpu, now, peak)
            lines.append(table_line(table_data(d, gpu, schema, False), schema))
        lines.append(table_divider(schema))
        for gpu in GPUS:
            info(extra_line(gpu, get_values(histories, gpu, now, peak), schema, peak))
        lines.append(border(width, '='))

    clocks = (tuple(read_max_mhz(sensors[g]['dev']) for g in GPUS)
              if clocks_override is None else tuple(clocks_override))
    profile = effective_profile(clocks)
    boot = (read_text(boot_profile_file) or 'not set') if boot_override is None else str(boot_override)
    status, reason = whole_status(histories, now, clocks, boot)
    stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    lines.append(border(width, '='))
    head = f'{socket.gethostname().upper()[:28]} / AMDGPU-SMI v4.2'
    if len(head) + len(stamp) + 3 <= width - 3:
        info(head + (' ' * max(2, width - 4 - len(head) - len(stamp))) + stamp)
    else:
        info(head)
        info(stamp)
    clock_text = ' '.join(f'{g[:2]}:{c or "?"}' for g, c in zip(GPUS, clocks))
    settings = (f'PROFILE {profile} | {clock_text} MHz'
                f' | sample {sample_interval:g}s | live {LIVE_SEC:g}s | peak {PEAK_SEC:g}s')
    info(settings)
    # Put only one G/Y/R indicator in the header, using readable text and restrained color.
    indicator = 'STATUS: ' + status
    if use_color:
        shade = {'GREEN':'green','YELLOW':'yellow','RED':'red'}[status]
        prefix = 'STATUS: '
        value = colored(status, shade, True)
        status_line = prefix + value
        # The plain ASCII visible string is padded *before* coloring.
        lines.append(boxed(status_line, width))
    else:
        info(indicator)
    if reason != 'normal':
        info('STATUS DETAIL: ' + reason)
    lines.append(border(width, '='))
    section('LIVE', '3s avg: load/clocks/fans/W; temps + VRAM current', False)
    section('60s PEAK HOLD', 'raw max; VRAM free = minimum', True)
    info('SYSTEM STATUS', shade='sky')
    lines.append(border(width, '-'))
    for i, gpu in enumerate(GPUS):
        d=histories[gpu][-1][1]
        info(f'GPU {gpu[:2]}: {clocks[i] or "?"} MHz max'
             f' | fan {d["fan_mode"]} | DPM {d["perf"]}')
    powers = [recent(histories[g], 'power', LIVE_SEC, now, 'mean') for g in GPUS]
    total = f'{sum(powers):.1f} W' if all(p is not None for p in powers) else '?'
    span = min(max(0.0, histories[g][-1][0]-histories[g][0][0]) for g in GPUS)
    span = min(span, PEAK_SEC)
    info(f'TOTAL GPU: {total} (3s avg) | GPU energy: {energy_wh(histories, now):.3f} Wh'
         f' ({span:.0f}/{PEAK_SEC:.0f}s window)')
    info(f'Boot profile: {boot} | driver telemetry only, not wall power')
    lines.append(border(width, '='))
    info('READ-ONLY | Ctrl+C to exit | no GPU settings modified')
    lines.append(border(width, '='))
    bad = [(i+1, visible_len(line)) for i,line in enumerate(lines)
           if visible_len(line) != width]
    if bad:
        raise AssertionError(f'FRAME WIDTH {width}; invalid lines {bad}')
    return '\n'.join(lines)


def demo_histories(now: float):
    # Suitable for presentation and dependency-free regression tests.
    histories = {g: deque() for g in GPUS}
    base_rows = [
        dict(gpu_busy=48,mem_busy=8,vram_used=9.78,vram_free=2.20,vram_pct=81.6,
             vram_total=11.98,core_mhz=1204,mem_mhz=990,edge=38,junction=43,mem=41,delta=5,
             fan_rpm=500,fan_pct=9.2,power=51,perf='manual',fan_mode='LACT/manual'),
        dict(gpu_busy=43,mem_busy=11,vram_used=8.23,vram_free=3.75,vram_pct=68.7,
             vram_total=11.98,core_mhz=1130,mem_mhz=990,edge=39,junction=44,mem=40,delta=5,
             fan_rpm=420,fan_pct=8.9,power=54,perf='manual',fan_mode='LACT/manual'),
    ]
    for i in range(121):
        ts = now - (120-i) * .5
        for gpu, base in zip(GPUS, base_rows):
            datum = base.copy()
            datum['gpu_busy'] += (i % 8) - 4
            datum['core_mhz'] += ((i % 9) - 4) * 5
            datum['power'] += ((i % 11) - 5) * 0.7
            histories[gpu].append((ts,datum))
    class MockDev:
        def __init__(self, mhz): self.mhz = mhz
        def __truediv__(self, other): return Path('/dev/null')
    sensors={g:dict(dev=MockDev(2000)) for g in GPUS}
    return histories,sensors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sample', type=float, default=.5,
                        help='Sensor polling interval, default 0.5s')
    parser.add_argument('--refresh', type=float, default=1.,
                        help='Terminal refresh interval, default 1s')
    parser.add_argument('--no-clear', action='store_true', help='Do not clear between frames')
    parser.add_argument('--demo', action='store_true', help='Render one example frame without GPU hardware')
    parser.add_argument('--once', action='store_true', help='Read real sensors once and exit')
    parser.add_argument('--width', type=int, help='Emulate terminal width (default: detect dynamically)')
    parser.add_argument('--compact', action='store_true', help='Prefer compact table')
    parser.add_argument('--pci', default='', help='Optional PCI list, e.g. 01:00.0,02:00.0')
    parser.add_argument('--color', choices=('auto', 'always', 'never'), default='auto')
    args=parser.parse_args()
    if not 0.2 <= args.sample <= 5 or args.refresh < args.sample:
        parser.error('Require 0.2 <= sample <= 5 and refresh >= sample')
    if args.width is not None and args.width < 20:
        parser.error('--width must be >= 20')
    def width():
        return args.width if args.width is not None else shutil.get_terminal_size((110, 30)).columns
    def color():
        return (args.color == 'always' or
                (args.color == 'auto' and sys.stdout.isatty() and 'NO_COLOR' not in os.environ))
    if args.demo:
        now=time.monotonic()
        histories,sensors=demo_histories(now)
        print(draw(histories,sensors,now,args.sample,args.refresh,Path('/nonexistent'),
                   columns=width(),force_compact=args.compact,use_color=color(),
                   clocks_override=(2000,2000),boot_override='quiet'))
        return 0
    try:
        global GPUS
        GPUS = pick_gpus(override=args.pci)
        sensors=discover()
    except RuntimeError as e:
        print(f'SENSOR_ERROR: {e}',file=sys.stderr)
        return 1
    histories={g:deque() for g in GPUS}
    next_sample=time.monotonic()
    last_print=float('-inf')
    try:
        while True:
            now=time.monotonic()
            for gpu in GPUS:
                histories[gpu].append((now,sample(sensors[gpu])))
                # Hold one sample before window start for accurate 60s energy integral.
                while len(histories[gpu]) > 2 and histories[gpu][1][0] < now - PEAK_SEC:
                    histories[gpu].popleft()
            if now - last_print >= args.refresh - .001:
                frame = draw(histories,sensors,now,args.sample,args.refresh,
                             columns=width(),force_compact=args.compact,use_color=color())
                sys.stdout.write(('' if args.no_clear or args.once else '\033[H\033[2J') + frame + '\n')
                sys.stdout.flush()
                last_print=now
                if args.once:
                    break
            next_sample += args.sample
            time.sleep(max(0,next_sample-time.monotonic()))
    except KeyboardInterrupt:
        print('\nMonitor stopped.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
