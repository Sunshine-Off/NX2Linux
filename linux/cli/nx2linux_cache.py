"""`nx2linux.py cache-prune`: list, and on request remove, the old finished libraries of an AOT cache (issue #18).

An AOT cache folder holds obj/ (one object per lifted function, named by a hash of its bytes and the toolchain) and lib/ (one finished library per build key, with a .json beside it).
Each change of the toolchain, the runtime or the set of functions leaves a new library and nothing removes the old ones: on one machine 36 libraries made 8.6 GB.
A removed library costs one relink from the objects next time, never a new lift or compile, so only lib/ is touched and obj/ never is."""
import os
import re
import sys
import time
from pathlib import Path

KEY = re.compile(r'^[0-9a-f]{64}$')


def libraries(cache):
    """[(modification time, key, [files], bytes)] of the finished libraries of a cache, newest first. Only regular files named like a build key, never a link"""
    found = []
    for so in (Path(cache) / 'lib').glob('*.so'):
        if not KEY.match(so.stem) or so.is_symlink() or not so.is_file(): continue
        files = [so] + [p for p in (so.with_suffix('.json'),) if p.is_file() and not p.is_symlink()]
        found.append((so.stat().st_mtime, so.stem, files, sum(p.stat().st_size for p in files)))
    return sorted(found, key=lambda t: (-t[0], t[1]))


def run(args):
    cache = Path(args.cache).expanduser()
    if not (cache / 'obj').is_dir() or not (cache / 'lib').is_dir():
        print(f'{cache} is not an AOT cache: it needs an obj/ and a lib/ folder. A cache is the folder given as --cache or AOT_CACHE, a folder of .elf and .manifest.json files holds modules', file=sys.stderr)
        return 2
    if args.keep < 0:
        print('--keep must be 0 or more', file=sys.stderr)
        return 2
    libs = libraries(cache)
    old = libs[args.keep:]
    kept = libs[:args.keep]
    print(f'{cache}: {len(libs)} finished libraries, {sum(t[3] for t in libs):,} bytes. Keeping the {min(args.keep, len(libs))} newest.')
    for mtime, key, files, size in kept:
        print(f'  keep    {key[:12]}  {size:>14,} bytes  {(time.time() - mtime) / 86400:6.1f} days old')
    if not old:
        print('nothing to remove')
        return 0
    for mtime, key, files, size in old:
        print(f'  remove  {key[:12]}  {size:>14,} bytes  {(time.time() - mtime) / 86400:6.1f} days old')
    total = sum(t[3] for t in old)
    if not args.delete:
        print(f'{len(old)} libraries, {total:,} bytes could be freed. nothing was removed. Add --delete to remove them. The objects are not touched: the next build relinks instead of reloading.')
        return 0
    for mtime, key, files, size in old:
        for p in files: p.unlink()
    print(f'removed {len(old)} libraries, {total:,} bytes freed. The objects are untouched.')
    return 0
