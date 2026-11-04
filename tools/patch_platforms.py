#!/usr/bin/env python3
"""The platform of every patch: common (useful to Apple and Linux), apple, or linux (patches/platforms.tsv).

  patch_platforms.py check     every patch of patches/series has one row with a valid class and a note, and no row is left over
  patch_platforms.py summary   the count per class, and the common patches that still wait for an upstream pull request

The class is a review aid for splitting the series (plan rule 4) and for choosing what to send to seedds/nxnative. It does not change how patches apply."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLASSES = ('common', 'apple', 'linux')


def series(root=ROOT):
    return [l.strip() for l in (root / 'patches' / 'series').read_text().splitlines() if l.strip() and not l.startswith('#')]


def table(root=ROOT):
    rows = {}
    for n, line in enumerate((root / 'patches' / 'platforms.tsv').read_text().splitlines()[1:], 2):
        f = line.split('\t')
        if len(f) != 3:
            raise ValueError(f'platforms.tsv:{n}: expected file, class, note')
        rows[f[0]] = (f[1], f[2])
    return rows


def problems(root=ROOT):
    rows, out = table(root), []
    names = series(root)
    out += [f'{n}: no row in platforms.tsv' for n in names if n not in rows]
    out += [f'{n}: in platforms.tsv but not in the series' for n in rows if n not in names]
    out += [f'{n}: class {c!r} is not one of {", ".join(CLASSES)}' for n, (c, _) in rows.items() if c not in CLASSES]
    out += [f'{n}: empty note' for n, (_, note) in rows.items() if not note.strip()]
    return out


def status_of(name, root=ROOT):
    for line in (root / 'patches' / name).read_text(errors='replace').splitlines():
        if line.startswith('Upstream-Status:'):
            return line.split(':', 1)[1].strip()
    return '?'


def main(argv=None):
    cmd = (argv if argv is not None else sys.argv[1:])[:1]
    if cmd == ['check']:
        bad = problems()
        print('\n'.join(bad) if bad else f'patch platforms: ok. {len(table())} patches classified')
        return 1 if bad else 0
    if cmd == ['summary']:
        rows = table()
        for c in CLASSES:
            print(f'{c:7} {sum(1 for v in rows.values() if v[0] == c)}')
        print('common patches not yet forwarded upstream:')
        for n, (c, _) in rows.items():
            if c == 'common' and not status_of(n).startswith('forwarded'):
                print(f'  {n}  [{status_of(n)}]')
        return 0
    print(__doc__)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
