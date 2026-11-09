#!/usr/bin/env python3
"""Numbers from the file of a long run (NX2LINUX_SOAK_LOG, see linux/docs/soak-test.md): how long it ran, the frames and the rate, the memory, threads and open files at the start and at the end, how fast the memory grew, the stalls.
Usage: soak_summary.py FILE [--warmup FRACTION]
A line cut in half by a crash is skipped. The memory slope is a least squares line through the resident memory after the first 10 percent of the run (--warmup), in kB per hour. It is a number, not a verdict: a slope near zero and a slope that is small against the memory in use are two different things, and a run of one hour says little about a leak that takes a day."""
import argparse, csv, sys

COLUMNS = ('t_s', 'rss_kb', 'threads', 'fds', 'frames', 'fps', 'idle_s', 'thread_faults', 'stall')


def read(path):
    """The rows of the file as dicts of numbers (None for an empty cell). Lines that do not fit are counted, not guessed at"""
    rows, bad = [], 0
    with open(path, newline='') as f:
        for i, cells in enumerate(csv.reader(f)):
            if i == 0 and cells[:1] == ['t_s']: continue
            if len(cells) != len(COLUMNS): bad += 1; continue
            try: rows.append({c: (None if v == '' else float(v)) for c, v in zip(COLUMNS, cells)})
            except ValueError: bad += 1
    return rows, bad


def slope_per_hour(points):
    """Least squares slope of (seconds, value) in value per hour, None with fewer than 3 points or no time spread"""
    n = len(points)
    if n < 3: return None
    mx = sum(x for x, _ in points) / n; my = sum(y for _, y in points) / n
    sxx = sum((x - mx) ** 2 for x, _ in points)
    if sxx == 0: return None
    return sum((x - mx) * (y - my) for x, y in points) / sxx * 3600


def span(rows, key):
    vals = [r[key] for r in rows if r[key] is not None]
    return (vals[0], vals[-1], min(vals), max(vals)) if vals else None


def summarize(rows, warmup=0.1):
    if not rows: return None
    dur = rows[-1]['t_s'] - rows[0]['t_s']; frames = rows[-1]['frames'] - rows[0]['frames']
    out = {'rows': len(rows), 'seconds': dur, 'frames': rows[-1]['frames'], 'fps_mean': frames / dur if dur > 0 else None,
           'rss_kb': span(rows, 'rss_kb'), 'threads': span(rows, 'threads'), 'fds': span(rows, 'fds'), 'thread_faults': rows[-1]['thread_faults']}
    t0 = rows[0]['t_s'] + dur * warmup
    out['rss_slope_kb_per_hour'] = slope_per_hour([(r['t_s'], r['rss_kb']) for r in rows if r['rss_kb'] is not None and r['t_s'] >= t0])
    starts, prev = 0, 0
    for r in rows:
        if r['stall'] and not prev: starts += 1
        prev = r['stall']
    out['stalls'] = starts; out['longest_idle_s'] = max(r['idle_s'] or 0 for r in rows)
    block = 600.0; blocks = []
    for k in range(int(dur // block) + 1):
        a = [r for r in rows if rows[0]['t_s'] + k * block <= r['t_s'] <= rows[0]['t_s'] + (k + 1) * block]
        if len(a) >= 2 and a[-1]['t_s'] > a[0]['t_s']: blocks.append((k * 10, (a[-1]['frames'] - a[0]['frames']) / (a[-1]['t_s'] - a[0]['t_s'])))
    out['fps_blocks'] = blocks
    return out


def fmt_span(s, scale=1.0, unit=''):
    return 'no data' if s is None else f'start {s[0] / scale:.1f}{unit}, end {s[1] / scale:.1f}{unit}, lowest {s[2] / scale:.1f}{unit}, highest {s[3] / scale:.1f}{unit}'


def text(s, bad=0):
    if s is None: return 'no rows in the file'
    lines = [f"{s['rows']} rows over {s['seconds']:.0f} s ({s['seconds'] / 3600:.2f} h)" + (f', {bad} line(s) skipped (cut or damaged)' if bad else ''),
             f"frames presented: {s['frames']:.0f}" + (f", {s['fps_mean']:.2f} per second on average" if s['fps_mean'] is not None else '')]
    if s['fps_blocks']: lines.append('frames per second by blocks of 10 minutes: ' + ', '.join(f'{m}-{m + 10} min {v:.1f}' for m, v in s['fps_blocks']))
    lines.append('memory (MB): ' + fmt_span(s['rss_kb'], 1024.0))
    sl = s['rss_slope_kb_per_hour']
    lines.append('memory slope after the first 10 percent: ' + ('not enough rows' if sl is None else f'{sl / 1024:+.1f} MB per hour'))
    lines.append('threads: ' + fmt_span(s['threads'])); lines.append('open files: ' + fmt_span(s['fds']))
    lines.append(f"stalls: {s['stalls']} (longest time without a new frame: {s['longest_idle_s']:.0f} s)")
    lines.append(f"guest threads that stopped: {s['thread_faults']:.0f}")
    return '\n'.join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('file'); p.add_argument('--warmup', type=float, default=0.1, help='fraction of the run left out of the memory slope (default 0.1)')
    a = p.parse_args(argv)
    try: rows, bad = read(a.file)
    except OSError as e: sys.exit(f'cannot read {a.file}: {e}')
    print(text(summarize(rows, a.warmup), bad))


if __name__ == '__main__': main()
