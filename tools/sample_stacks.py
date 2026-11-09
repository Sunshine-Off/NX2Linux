#!/usr/bin/env python3
"""Where the time goes in a running game: stack samples of every thread with gdb (NX2Linux issue 19, linux/docs/profile-sampling.md). perf is not installed here and the host spends its time in places Python's own profiler
cannot see (the GIL, ctypes, the driver), so this does what S8 did by hand: attach gdb to the running host a few times, take `bt` of all threads, and count.

  sample_stacks.py run (--pid N | --match TEXT) [--samples 14] [--interval 5] [--depth 30] [--out DIR]
  sample_stacks.py report DIR

`run` attaches gdb, prints the stacks to DIR/sample-NN.txt and detaches, `samples` times. Each attach stops the game for as long as gdb takes (written to the table: it is not free). `--match` finds the one process whose command
line holds TEXT (for example horizon_run.py). `report` classifies every thread of every sample:
  waiting             in futex, condition, sleep, poll... (not working)
  waiting for the GIL in take_gil: a Python thread that wants to run and waits for the interpreter lock
  running             anything else, then split by what it runs: recompiled game code (sub_<address>), the AOT runtime (aot_*, __remill_*), Python and ctypes, the host's GPU or audio libraries, the Vulkan driver, other
and shows the functions the running threads are in, and each kind of thread (guest-N, nvn-queue...) apart. A sample says where threads are, at one instant. 14 samples of 40 threads is a picture and not a measurement: read the shares as
orders of magnitude, as S8 did. Needs gdb and the right to attach (kernel.yama.ptrace_scope): when gdb is refused the tool says so and changes nothing."""
import argparse
import collections
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

SCHEMA = 1
HEADER = re.compile(r'^Thread (\d+) \(.*?\(LWP (\d+)\)(?: "([^"]*)")?\):\s*$')
FRAME = re.compile(r'^#(\d+)\s+(?:0x[0-9a-f]+ in )?(\S+?)(?: \(.*?\))?(?: (?:at (\S+)|from (\S+)))?\s*$')
ATTACH_REFUSED = ('Operation not permitted', 'ptrace: ', 'Could not attach', 'No such process', 'tracer')

# innermost frames that mean "not working": exact function names (glibc adds versions after @)
WAIT_NAMES = {'pthread_cond_wait', 'pthread_cond_timedwait', 'pthread_cond_clockwait', 'pthread_join', 'pthread_mutex_lock', 'pthread_mutex_timedlock', 'sem_wait', 'sem_timedwait', 'sem_clockwait', 'nanosleep',
              'clock_nanosleep', 'sleep', 'usleep', 'epoll_wait', 'epoll_pwait', 'poll', 'ppoll', 'select', 'pselect', 'sigtimedwait', 'sigwait', 'sigwaitinfo', 'pause', 'accept', 'futex', 'syscall',
              '__lll_lock_wait', '__lll_lock_wait_private', '__futex_abstimed_wait_common', '__futex_abstimed_wait_cancelable64', '__futex_clocklock64', 'futex_wait', 'futex_wait_cancelable', 'do_futex_wait',
              'recv', 'recvfrom', 'recvmsg', 'read', 'wait4', 'waitpid', 'SDL_WaitEvent', 'SDL_WaitEventTimeout', 'SDL_SemWait'}
GENERIC = {'__syscall_cancel_arch', '__internal_syscall_cancel', '__syscall_cancel', 'start_thread', '__clone3', '__clone', 'clone', 'clone3', 'syscall'}
GIL_NAMES = ('take_gil', 'PyEval_RestoreThread', 'PyEval_AcquireThread')
LIBC = ('libc.so', 'libm.so', 'ld-linux', 'libpthread', 'libgcc_s', 'libstdc++')


def parse(text):
    """The threads of one gdb output: [{'num', 'lwp', 'name', 'frames': [(index, function, file or library)]}], innermost frame first"""
    threads, cur = [], None
    for line in text.splitlines():
        m = HEADER.match(line)
        if m: cur = {'num': int(m.group(1)), 'lwp': int(m.group(2)), 'name': m.group(3) or '', 'frames': []}; threads.append(cur); continue
        m = FRAME.match(line)
        if m and cur is not None:
            idx, fn, at, lib = m.groups()
            cur['frames'].append((int(idx), fn.split('@')[0], at or lib or ''))
    return threads


def where(frame):
    """What a frame is: (category, function) with the category among the names used by `kind`"""
    _, fn, place = frame
    base = os.path.basename(place.split(':')[0]) if place else ''
    if fn.startswith('sub_') and re.fullmatch(r'sub_[0-9a-f]+', fn): return 'recompiled game code'
    if fn.startswith(('aot_', '__remill_', 'nx2_', 'import_call')) or 'aot' in base and base.endswith('.so'): return 'AOT runtime'
    if 'libnx2linux_gpu' in base or fn.startswith('nx2gpu_'): return 'host GPU library'
    if 'libnx2linux_audio' in base or 'libnx2linux_movie' in base or 'libnx2linux_display' in base or 'libnx2linux_sync' in base or 'libSDL' in base: return 'host audio, movie, window, sync libraries'
    if any(x in base for x in ('libvulkan', 'libnvidia', 'libGLX', 'libEGL', 'libGL', 'nvidia', 'libvulkan_', 'libdrm', 'amdgpu', 'radeon', 'lvp')): return 'Vulkan driver'
    if fn.startswith(('_Py', 'Py', '_ctypes', 'ffi_', 'ctypes', 'cfunction', 'method_vectorcall', 'slot_', 'type_call', 'PyObject')) or 'libpython' in base or base.startswith('python') or base == '_ctypes.cpython': return 'Python and ctypes'
    if any(x in base for x in LIBC) or fn in GENERIC: return 'libc'
    return 'other'


def kind(thread):
    """('waiting' | 'waiting for the GIL' | 'running', what it runs or waits in, innermost function that is not generic)"""
    frames = thread['frames']
    names = [f[1] for f in frames]
    if not frames: return 'running', 'other', '?'
    if any(n in GIL_NAMES for n in names[:12]): return 'waiting for the GIL', 'Python and ctypes', next((n for n in names if n in GIL_NAMES), names[0])
    waiter = next((n for n in reversed(names[:8]) if n in WAIT_NAMES and n != 'syscall'), None)   # the outermost wait: pthread_cond_wait, not the futex call inside it
    if waiter: return 'waiting', 'libc', waiter
    for f in frames:
        c = where(f)
        if c not in ('libc',) and f[1] not in GENERIC: return 'running', c, f[1]
    return 'running', 'libc', next((n for n in names if n not in GENERIC), names[0])


def family(name):
    """guest-12 and guest-13 are one kind of thread"""
    return re.sub(r'\d+', '*', name) or '(no name)'


def analyse(samples):
    """samples: [text of gdb output]. Returns the numbers of the report"""
    states, running, funcs, by_family = collections.Counter(), collections.Counter(), collections.Counter(), collections.defaultdict(collections.Counter)
    per_sample, waits = [], collections.Counter()
    for text in samples:
        ts = parse(text)
        per_sample.append(len(ts))
        for t in ts:
            st, cat, fn = kind(t)
            states[st] += 1; by_family[family(t['name'])][st] += 1
            if st == 'running': running[cat] += 1; funcs[(cat, fn)] += 1
            else: waits[(st, fn)] += 1
    total = sum(states.values())
    return {'samples': len(samples), 'threads_per_sample': per_sample, 'thread_samples': total, 'states': dict(states), 'running_by_kind': dict(running),
            'top_running': [[c, f, n] for (c, f), n in funcs.most_common(15)], 'top_waits': [[s, f, n] for (s, f), n in waits.most_common(8)],
            'by_thread_kind': {k: dict(v) for k, v in sorted(by_family.items(), key=lambda kv: -sum(kv[1].values()))}}


def pct(n, d): return f'{100 * n / d:5.1f}%' if d else '  n/a'


def table(res, meta=None):
    n, tot = res['thread_samples'], res['thread_samples']
    run = res['states'].get('running', 0)
    lines = [f'{res["samples"]} samples, {n} thread-samples, about {n / res["samples"]:.0f} threads per sample' if res['samples'] else '0 samples']
    if meta and meta.get('gdb_seconds'): lines.append(f'the game was stopped about {sum(meta["gdb_seconds"]) / len(meta["gdb_seconds"]):.1f} s per sample ({sum(meta["gdb_seconds"]):.0f} s in all)')
    lines += ['', f'{"state":<26}{"thread-samples":>15}{"share":>8}']
    for s in ('waiting', 'waiting for the GIL', 'running'): lines.append(f'{s:<26}{res["states"].get(s, 0):>15}{pct(res["states"].get(s, 0), tot):>8}')
    lines += ['', f'what the running threads run ({run} thread-samples):']
    for c, k in sorted(res['running_by_kind'].items(), key=lambda kv: -kv[1]): lines.append(f'  {c:<42}{k:>6}{pct(k, run):>8}')
    lines += ['', 'functions the running threads are in:'] + [f'  {n:>5}  {c}: {f}' for c, f, n in res['top_running']]
    lines += ['', 'what the waiting threads wait in:'] + [f'  {n:>5}  {s}: {f}' for s, f, n in res['top_waits']]
    lines += ['', f'{"kind of thread":<22}{"waiting":>9}{"GIL":>6}{"running":>9}']
    for k, v in res['by_thread_kind'].items(): lines.append(f'{k:<22}{v.get("waiting", 0):>9}{v.get("waiting for the GIL", 0):>6}{v.get("running", 0):>9}')
    lines += ['', 'A sample is where threads are at one instant: with this few, read the shares as orders of magnitude.']
    return '\n'.join(lines)


def find_pid(text, proc='/proc'):
    me = {os.getpid(), os.getppid()}
    found = []
    for p in Path(proc).glob('[0-9]*'):
        try:
            if int(p.name) in me: continue
            cmd = (p / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')
        except (OSError, ValueError): continue
        if text in cmd and 'sample_stacks' not in cmd and 'gdb' not in cmd.split(' ')[0]: found.append((int(p.name), cmd.strip()[:120]))
    return found


def one_sample(pid, depth, gdb='gdb', run=subprocess.run, timeout=180):
    """The gdb output for all threads of pid, and how long gdb held the process. Raises PermissionError when gdb is refused"""
    cmd = [gdb, '-nx', '-batch', '-ex', 'set pagination off', '-ex', 'set debuginfod enabled off', '-ex', 'set confirm off', '-p', str(pid), '-ex', f'thread apply all bt {depth}']
    t0 = time.monotonic()
    p = run(cmd, capture_output=True, text=True, errors='replace', timeout=timeout)
    took = time.monotonic() - t0
    out = p.stdout
    if not parse(out):
        if any(x in p.stderr + p.stdout for x in ATTACH_REFUSED): raise PermissionError('gdb could not attach to the process: ' + (p.stderr or p.stdout).strip().splitlines()[-1][:200])
        raise RuntimeError('gdb printed no thread: ' + (p.stderr or p.stdout).strip()[-300:])
    return out, took


def do_run(args, say=print, sleep=time.sleep, sample=one_sample):
    pid = args.pid
    if pid is None:
        found = find_pid(args.match)
        if not found: raise SystemExit(f'sample_stacks: no process has {args.match!r} in its command line')
        if len(found) > 1: raise SystemExit('sample_stacks: more than one process matches, give --pid:\n' + '\n'.join(f'  {p}  {c}' for p, c in found))
        pid = found[0][0]
    out = Path(args.out or f'stacks-{time.strftime("%Y%m%d-%H%M%S")}'); out.mkdir(parents=True, exist_ok=True)
    took = []
    for i in range(1, args.samples + 1):
        text, secs = sample(pid, args.depth, gdb=args.gdb)
        (out / f'sample-{i:02d}.txt').write_text(text); took.append(round(secs, 2))
        say(f'[sample] {i}/{args.samples}: {len(parse(text))} threads, gdb held the process {secs:.1f} s')
        if i < args.samples: sleep(args.interval)
    meta = {'schema': SCHEMA, 'pid': pid, 'samples': args.samples, 'interval': args.interval, 'depth': args.depth, 'gdb_seconds': took, 'started': time.time()}
    (out / 'meta.json').write_text(json.dumps(meta, indent=2) + '\n')
    return out


def do_report(folder):
    folder = Path(folder)
    files = sorted(folder.glob('sample-*.txt'))
    if not files: raise SystemExit(f'sample_stacks: no sample-*.txt in {folder}')
    res = analyse([f.read_text(errors='replace') for f in files])
    meta = json.loads((folder / 'meta.json').read_text()) if (folder / 'meta.json').is_file() else None
    text = table(res, meta)
    (folder / 'report.txt').write_text(text + '\n'); (folder / 'report.json').write_text(json.dumps(res, indent=2, sort_keys=True) + '\n')
    return text


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = p.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('run', help='sample a running process with gdb'); g = r.add_mutually_exclusive_group(required=True)
    g.add_argument('--pid', type=int); g.add_argument('--match', metavar='TEXT', help='the one process whose command line holds this')
    r.add_argument('--samples', type=int, default=14); r.add_argument('--interval', type=float, default=5.0, help='seconds between two samples'); r.add_argument('--depth', type=int, default=30, help='frames kept per thread')
    r.add_argument('--out'); r.add_argument('--gdb', default='gdb')
    rp = sub.add_parser('report', help='classify the samples of a folder'); rp.add_argument('folder')
    a = p.parse_args(argv)
    if a.cmd == 'report': print(do_report(a.folder)); return 0
    if a.samples < 1 or a.depth < 4: p.error('--samples wants at least 1 and --depth at least 4')
    if shutil.which(a.gdb) is None: raise SystemExit(f'sample_stacks: {a.gdb} is not installed (on Fedora: sudo dnf install gdb). I do not install it for you')
    try: out = do_run(a)
    except PermissionError as e:
        scope = Path('/proc/sys/kernel/yama/ptrace_scope')
        raise SystemExit(f'sample_stacks: {e}\n  kernel.yama.ptrace_scope is {scope.read_text().strip() if scope.exists() else "not there"}: 0 lets you attach to your own processes, 1 only to your own children. Change it only if you decide to')
    print('\n' + do_report(out)); print(f'\nfiles: {out}')
    return 0


if __name__ == '__main__': sys.exit(main())
