#!/usr/bin/env python3
"""Compare two variants of a run with the A B B A protocol (NX2Linux issue 19, linux/docs/measure-svc-native.md).

Why this exists: in Phase 2 a 5 percent gap between two builds could not be told from noise (1.6 standard errors), after two runs were spoiled by a heavy job that ran at the same time. This tool does the parts a person gets wrong
at the keyboard. It runs the two variants in the order A B B A A B B A A B B A (a drift of the machine, like heat, cancels inside each block), refuses to start a run on a machine that is not idle, keeps one log per run, reads the
frames presented between two marks of the run (so the loading is left out), and says "not a result" when the difference is under two standard errors of the difference.

  ab_bench.py run --env-a NAME=VALUE --env-b NAME=VALUE [--blocks 3] [--marks 30,110] [--pause 30] [--out DIR] -- COMMAND ...
  ab_bench.py report DIR/ab_bench.json      the table again, from the file a run wrote
  ab_bench.py order [--blocks 3]            the order of the runs

COMMAND is the run of the game with NX2LINUX_FRAME_MARKS set by this tool to the marks (horizon_run.py prints "[frames] at 30 s: 412" at each mark). Both variants use the same command, the same cache and the same machine: only
the --env-a and --env-b values differ. This tool starts nothing but COMMAND and reads nothing of a game."""
import argparse
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

SCHEMA = 1
MIN_PER_VARIANT = 6          # the protocol: at least 6 runs of each variant
BLOCK = 'ABBA'
MARK = re.compile(r'^\[frames\] at ([0-9.]+) s: (\d+)\s*$', re.M)


def order(blocks):
    """The variants in the order they run: A B B A repeated"""
    return list(BLOCK * blocks)


def window_frames(text, lo, hi):
    """Frames presented between the marks lo and hi (seconds) in the output of a run, or None when a mark is missing"""
    marks = {float(s): int(n) for s, n in MARK.findall(text)}
    if float(lo) not in marks or float(hi) not in marks: return None
    return marks[float(hi)] - marks[float(lo)]


def summary(xs):
    """mean, sample standard deviation and standard error of the mean. With one value there is no deviation to give"""
    n = len(xs)
    mean = statistics.fmean(xs) if xs else float('nan')
    sd = statistics.stdev(xs) if n > 1 else float('nan')
    return {'n': n, 'mean': mean, 'sd': sd, 'se': sd / math.sqrt(n) if n > 1 else float('nan')}


def compare(a, b):
    """B minus A and its standard error, sqrt(se_A^2 + se_B^2). `result` is "difference" only when the gap is at least two standard errors, else "not a result" (also when a standard error cannot be computed)"""
    sa, sb = summary(a), summary(b)
    diff = sb['mean'] - sa['mean']
    se = math.sqrt(sa['se'] ** 2 + sb['se'] ** 2) if sa['n'] > 1 and sb['n'] > 1 else float('nan')
    ratio = abs(diff) / se if se and not math.isnan(se) else float('nan')
    result = 'difference' if not math.isnan(ratio) and ratio >= 2 else 'not a result'
    return {'A': sa, 'B': sb, 'diff': diff, 'diff_percent': 100 * diff / sa['mean'] if sa['mean'] else float('nan'), 'se_diff': se, 'diff_in_se': ratio, 'result': result}


def read_cpu(path='/proc/stat'):
    f = [int(x) for x in Path(path).read_text().splitlines()[0].split()[1:9]]
    return sum(f), f[3] + f[4]   # all, idle + iowait


def busy_fraction(seconds=5.0, read=read_cpu, sleep=time.sleep):
    """How busy the whole machine was over a few seconds, 0 to 1"""
    t0, i0 = read(); sleep(seconds); t1, i1 = read()
    return 1.0 - (i1 - i0) / (t1 - t0) if t1 > t0 else 1.0


def other_runs(pattern='horizon_run.py', proc='/proc', me=os.getpid()):
    """Pids of other processes whose command line holds the pattern (a game that is already running spoils a measurement)"""
    out = []
    for p in Path(proc).glob('[0-9]*'):
        try:
            if int(p.name) == me: continue
            if pattern in (p / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace'): out.append(int(p.name))
        except (OSError, ValueError): continue
    return out


def host_info():
    info = {'python': sys.version.split()[0], 'kernel': os.uname().release, 'cpus': os.cpu_count()}
    for key, path in (('governor', '/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor'),):
        try: info[key] = Path(path).read_text().strip()
        except OSError: pass
    try: info['cpu_model'] = next(l.split(':', 1)[1].strip() for l in Path('/proc/cpuinfo').read_text().splitlines() if l.startswith('model name'))
    except (OSError, StopIteration): pass
    return info


def wait_until_idle(max_busy, wait, sample, say, busy=busy_fraction, others=other_runs, sleep=time.sleep, now=time.monotonic):
    """Wait for an idle machine: busy at most max_busy over a sample, and no other horizon_run. Returns (busy fraction, True), or (last busy fraction, False) when wait seconds were not enough"""
    end = now() + wait
    while True:
        pids = others()
        b = busy(sample) if not pids else 1.0
        if not pids and b <= max_busy: return b, True
        if now() >= end: return b, False
        say(f'[ab] machine not idle (busy {b:.0%}{", other horizon_run: " + str(pids) if pids else ""}): waiting')
        sleep(min(10.0, max(1.0, wait / 30)))


def execute(command, env, timeout):
    p = subprocess.run(command, env=env, capture_output=True, text=True, errors='replace', timeout=timeout)
    return p.returncode, p.stdout + '\n' + p.stderr


def run_bench(command, env_a, env_b, blocks=3, marks=(30, 110), pause=30.0, out=None, timeout=1800, max_busy=0.03, idle_wait=600, sample=5.0, b_must_match=None, a_must_not_match=None,
              say=print, execute=execute, wait=wait_until_idle, sleep=time.sleep, now=time.time):
    """Run the protocol. Returns the result dict (also written to out/ab_bench.json). Raises ValueError when blocks give fewer than MIN_PER_VARIANT runs of a variant"""
    seq = order(blocks)
    if seq.count('A') < MIN_PER_VARIANT: raise ValueError(f'{blocks} block(s) give {seq.count("A")} runs of each variant, the protocol needs at least {MIN_PER_VARIANT} (3 blocks of A B B A)')
    lo, hi = marks
    out = Path(out or f'ab_bench-{time.strftime("%Y%m%d-%H%M%S")}'); out.mkdir(parents=True, exist_ok=True)
    runs = []
    for i, v in enumerate(seq, 1):
        busy, idle = wait(max_busy, idle_wait, sample, say)
        env = {**os.environ, **(env_a if v == 'A' else env_b), 'NX2LINUX_FRAME_MARKS': ','.join(f'{m:g}' for m in marks)}
        say(f'[ab] run {i}/{len(seq)}: variant {v}')
        t0 = now()
        try: code, text = execute(command, env, timeout)
        except subprocess.TimeoutExpired: code, text = -1, f'timed out after {timeout:g} s'
        log = out / f'run-{i:02d}-{v}.log'; log.write_text(text)
        frames = window_frames(text, lo, hi)
        in_effect = True   # a variant that did not take effect (the library was not found, so B ran as A) would make the whole series a comparison of A with A
        if v == 'B' and b_must_match and not re.search(b_must_match, text, re.M): in_effect = False
        if v == 'A' and a_must_not_match and re.search(a_must_not_match, text, re.M): in_effect = False
        r = {'index': i, 'variant': v, 'exit_code': code, 'frames': frames, 'fps': frames / (hi - lo) if frames is not None else None, 'busy_before': round(busy, 4), 'idle_ok': idle, 'in_effect': in_effect,
             'started': t0, 'seconds': round(now() - t0, 1), 'log': log.name}
        runs.append(r)
        say(f'[ab] run {i}: {frames} frames in {lo:g}-{hi:g} s' + ('' if idle else ' (the machine was NOT idle)') + ('' if code == 0 else f' (exit code {code})') + ('' if in_effect else ' (the variant was NOT in effect)'))
        if i < len(seq): sleep(pause)
    result = analyse(runs, marks)
    result.update(schema=SCHEMA, command=command, env_a=env_a, env_b=env_b, marks=list(marks), pause=pause, max_busy=max_busy, host=host_info(), blocks=blocks)
    (out / 'ab_bench.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    return result


def analyse(runs, marks):
    """The comparison of the good runs: exit code 0, marks found, machine idle. A run that is not good is listed, not counted, and the result says the series is unbalanced"""
    good = [r for r in runs if r['exit_code'] == 0 and r['frames'] is not None and r['idle_ok'] and r.get('in_effect', True)]
    a = [r['fps'] for r in good if r['variant'] == 'A']; b = [r['fps'] for r in good if r['variant'] == 'B']
    res = compare(a, b)
    res['runs'] = runs
    res['excluded'] = [r['index'] for r in runs if r not in good]
    res['balanced'] = len(a) == len(b) and len(a) >= MIN_PER_VARIANT
    return res


def table(res):
    lo, hi = res.get('marks', [None, None])
    f = lambda x: 'n/a' if x is None or (isinstance(x, float) and math.isnan(x)) else f'{x:.2f}'
    lines = [f'frames per second between {lo:g} s and {hi:g} s of the run' if lo is not None else 'frames per second', '',
             f'{"variant":<9}{"runs":>5}{"mean":>10}{"sd":>9}{"se":>9}']
    for v in 'AB':
        s = res[v]; lines.append(f'{v:<9}{s["n"]:>5}{f(s["mean"]):>10}{f(s["sd"]):>9}{f(s["se"]):>9}')
    lines += ['', f'B - A = {f(res["diff"])} frames per second ({f(res["diff_percent"])} percent), standard error of the difference {f(res["se_diff"])}, {f(res["diff_in_se"])} standard errors',
              f'result: {res["result"]}' + ('' if res['result'] == 'difference' else ' (a gap under two standard errors is not a result)')]
    if not res['balanced']: lines.append(f'WARNING: the series is not balanced or has under {MIN_PER_VARIANT} good runs of a variant: do not conclude from it')
    if res['excluded']: lines.append(f'runs left out (failed, no mark, the machine was not idle, or the variant was not in effect): {res["excluded"]}')
    lines += ['', 'in the order they ran:'] + [f'  {r["index"]:>2} {r["variant"]} frames {r["frames"]} busy before {r["busy_before"]:.1%} exit {r["exit_code"]} {r["log"]}' for r in res['runs']]
    return '\n'.join(lines)


def parse_env(items):
    out = {}
    for it in items or []:
        k, sep, v = it.partition('=')
        if not sep or not k: raise SystemExit(f'ab_bench: {it!r} is not NAME=VALUE')
        out[k] = v
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = p.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('run', help='run the protocol: the command after -- is started once per run'); r.add_argument('--env-a', action='append', metavar='NAME=VALUE'); r.add_argument('--env-b', action='append', metavar='NAME=VALUE')
    r.add_argument('--blocks', type=int, default=3, help='blocks of A B B A (3 gives 6 runs of each, the least the protocol accepts)')
    r.add_argument('--marks', default='30,110', help='two seconds of the run: the frames between them are the measure (default 30,110)')
    r.add_argument('--pause', type=float, default=30.0, help='seconds between two runs'); r.add_argument('--out', help='folder for the logs and ab_bench.json'); r.add_argument('--timeout', type=float, default=1800)
    r.add_argument('--max-busy', type=float, default=0.03, help='the machine must be busy at most this fraction (0.03 is half a core on 16) over a 5 s sample before each run'); r.add_argument('--idle-wait', type=float, default=600, help='seconds to wait for it'); r.add_argument('--sample', type=float, default=5.0, help='seconds of the CPU sample before each run')
    r.add_argument('--b-must-match', metavar='REGEX', help='the output of every B run must match this, else the run is left out (for example "native synchronisation: 4 kernel calls": B really ran the native path)')
    r.add_argument('--a-must-not-match', metavar='REGEX', help='the output of an A run must not match this, else the run is left out')
    r.add_argument('command', nargs=argparse.REMAINDER)
    rp = sub.add_parser('report', help='print the table of a result file'); rp.add_argument('file')
    o = sub.add_parser('order', help='print the order of the runs'); o.add_argument('--blocks', type=int, default=3)
    a = p.parse_args(argv)
    if a.cmd == 'order': print(' '.join(order(a.blocks))); return 0
    if a.cmd == 'report': print(table(json.loads(Path(a.file).read_text()))); return 0
    cmd = a.command[1:] if a.command and a.command[0] == '--' else a.command
    if not cmd: p.error('give the command after --')
    try: marks = tuple(float(x) for x in a.marks.split(','))
    except ValueError: p.error('--marks wants two numbers like 30,110')
    if len(marks) != 2 or not 0 <= marks[0] < marks[1]: p.error('--marks wants two numbers like 30,110, the first below the second')
    ea, eb = parse_env(a.env_a), parse_env(a.env_b)
    if ea == eb: p.error('the two variants are the same: give --env-a and --env-b')
    try: res = run_bench(cmd, ea, eb, blocks=a.blocks, marks=marks, pause=a.pause, out=a.out, timeout=a.timeout, max_busy=a.max_busy, idle_wait=a.idle_wait, sample=a.sample, b_must_match=a.b_must_match, a_must_not_match=a.a_must_not_match)
    except ValueError as e: p.error(str(e))
    print('\n' + table(res))
    return 0


if __name__ == '__main__': sys.exit(main())
