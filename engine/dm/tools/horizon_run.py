#!/usr/bin/env python3
"""Run a recompiled game on the Linux host (NX2Linux S7bis): load its modules, replace the SDK functions the host provides, start nn::init::Start's sequence and let the game run for a while.
Usage: horizon_run.py --modules <dir with <name>.manifest.json> --romfs <RomFS.img> --title <id> [--display|--hidden] [--seconds N] [--profile xc2] [--cache DIR]
Needs REMILL_BUILD (the lifter) for functions not in the cache. The window needs linux/host built (libnx2linux_display.so, see horizon_display.py)."""
import argparse, os, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import elf_module as em
import horizon_hle, horizon_overrides

PROFILES = {'0100e95004038000': 'horizon_profile_xc2'}


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--modules', required=True, help='folder holding <name>.manifest.json and <name>.elf')
    p.add_argument('--order', default='main,sdk,subsdk0', help='module names in load order')
    p.add_argument('--romfs'); p.add_argument('--title', default='0000000000000000')
    p.add_argument('--cache', default=str(Path.home() / '.cache/nx2linux/aot-cache'))
    p.add_argument('--display', action='store_true', help='open a window when the game first presents a frame'); p.add_argument('--hidden', action='store_true', help='with --display: keep the window hidden')
    p.add_argument('--seconds', type=float, default=60, help='stop after this long (the game runs on its own threads)')
    p.add_argument('--no-profile', action='store_true', help='do not apply the per-title workarounds'); p.add_argument('--verbose', action='store_true')
    a = p.parse_args()
    mods = [f'{a.modules}/{n}.manifest.json' for n in a.order.split(',')]
    ms = em.ModuleSet(mods, {})
    hz = horizon_hle.Horizon(ms, verbose=a.verbose, romfs=a.romfs, title_id=a.title)
    ovr = horizon_overrides.install(hz)
    prof = PROFILES.get(a.title)
    if prof and not a.no_profile:
        mod = __import__(prof); ovr.update(horizon_overrides.install_profile(hz, mod.PROFILE))
    t = time.time()
    hz.aot = ms.build(jit=False, overrides=ovr, cache=a.cache)
    print(f'built in {time.time() - t:.0f} s ({hz.aot.cache_hits} cached, {hz.aot.cache_misses} built, {len(hz.aot.unlifted)} not translated)', flush=True)
    hz.attach()
    if a.display:
        import horizon_display
        hz.display_factory = lambda: horizon_display.Display(a.title, 1280, 720, hidden=a.hidden)
    # the boot runs on this thread; the game's own threads do the rest. A timer ends the run.
    import threading
    def report():
        print('frames presented:', getattr(hz, 'frames_presented', 0), '| svc calls:', hz.stats, '| not translated functions called:', hz.aot.stat('unlifted_calls'))
        if hasattr(hz, 'nvn'): print('nvn:', hz.nvn.stats, '| not implemented NVN calls:', sorted(hz.nvn.unimpl)[:60])
        if hasattr(hz, 'nvn'): print('NVN calls (most frequent):', sorted(hz.nvn.calls.items(), key=lambda kv: -kv[1])[:30])
        if hasattr(hz, 'nvn'): print('NVN queue/window/recording calls:', {k: v for k, v in hz.nvn.calls.items() if any(x in k for x in ('Queue', 'Window', 'Recording', 'Commands', 'Sync'))})
        if hasattr(hz, 'nvn'): print('NVN functions the game looked up:', len(hz.nvn.addr), '| Submit looked up:', 'nvnQueueSubmitCommands' in hz.nvn.addr, '| command lists recorded:', len(hz.nvn.cmdlists))
        if hasattr(hz, 'nvn'): print('clear colors (on a window texture?, rgba): count:', sorted(hz.nvn.clear_colors.items(), key=lambda kv: -kv[1])[:12])
        print('stubbed overrides called:', sorted(hz.stub_calls)); print('where the guest threads are:\n' + hz.thread_report()); print(hz.ring_report(1)); print('threads that stopped:', hz.thread_faults, flush=True)
    def stop():
        print(f'[run] {a.seconds:.0f} s elapsed: stopping', flush=True); report(); os._exit(0)
    threading.Timer(a.seconds, stop).start()
    r = hz.boot()
    print('result', {k: (hex(v) if isinstance(v, int) else v) for k, v in r.items() if k in ('fault', 'x0', 'step')})
    report()   # the boot thread ended (nnMain returned or faulted); the other guest threads go on until the timer


if __name__ == '__main__': main()
