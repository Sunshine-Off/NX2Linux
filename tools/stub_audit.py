#!/usr/bin/env python3
"""Merge the stub audit files of several runs (NX2LINUX_STUB_AUDIT, see linux/docs/stub-audit.md) and rank the not ported SDK functions by use.
Usage: stub_audit.py FILE [FILE...] [--top N] [--never] [--json OUT]
It prints how many stubbed functions the build exports, how many a run called, a table by family (nn::hid, nn::audio, ...), the most called ones with how many runs called them and where the first call came from, and with --never the ones nobody called. The files have to be about one game: the same title.
The family is read from the mangled name (nn::hid for _ZN2nn3hid13GetNpadStates...), no c++filt needed. A symbol that is not a nested C++ name is a family of its own called `other`.
What it cannot say: that a function the runs never called is not needed. It says what the scenes of these runs used."""
import argparse, json, sys

SCHEMA = 1


def components(sym):
    """The names of a mangled symbol: _ZN2nn3hid13GetNpadStatesE... gives [nn, hid, GetNpadStates]. [] when it is not one"""
    if sym.startswith('_ZN'): i = 3
    elif sym.startswith('_Z') and sym[2:3].isdigit(): i = 2
    else: return []
    while i < len(sym) and sym[i] in 'KVr': i += 1
    out = []
    while i < len(sym) and sym[i].isdigit():
        j = i
        while j < len(sym) and sym[j].isdigit(): j += 1
        n = int(sym[i:j]); name = sym[j:j + n]
        if len(name) != n: break
        out.append(name); i = j + n
    return out


def family(sym):
    c = components(sym)
    if not c: return 'other'
    return '::'.join(c[:2]) if len(c) >= 3 else (c[0] if len(c) == 2 else 'global')


def pretty(sym):
    c = components(sym)
    return '::'.join(c) if c else sym


def load(path):
    try:
        with open(path) as f: doc = json.load(f)
    except (OSError, ValueError) as e: raise SystemExit(f'cannot read {path}: {e}')
    if not isinstance(doc, dict) or doc.get('schema') != SCHEMA or 'calls' not in doc or 'stubbed_exported' not in doc:
        raise SystemExit(f'{path} is not a stub audit file (schema {SCHEMA})')
    return doc


def merge(docs):
    """One view of the runs: the union of the stubs, the calls added up, the first site of the first run that called each"""
    titles = {d.get('title') for d in docs}
    if len(titles) > 1: raise SystemExit(f'the files are about different titles: {", ".join(sorted(str(t) for t in titles))}')
    stubbed, calls = set(), {}
    for d in docs:
        stubbed.update(d['stubbed_exported'])
        for sym, c in d['calls'].items():
            e = calls.setdefault(sym, {'count': 0, 'runs': 0, 'first_site': None})
            e['count'] += c['count']; e['runs'] += 1
            if e['first_site'] is None: e['first_site'] = c.get('first_site')
    seconds = sum(d.get('seconds') or 0 for d in docs)
    return {'title': titles.pop() if titles else None, 'runs': len(docs), 'seconds': seconds, 'frames': sum(d.get('frames') or 0 for d in docs),
            'stubbed': sorted(stubbed), 'calls': calls}


def families(m):
    """{family: {stubbed, called, calls}}"""
    out = {}
    for sym in set(m['stubbed']) | set(m['calls']):
        f = out.setdefault(family(sym), {'stubbed': 0, 'called': 0, 'calls': 0})
        if sym in m['stubbed']: f['stubbed'] += 1
        if sym in m['calls']: f['called'] += 1; f['calls'] += m['calls'][sym]['count']
    return out


def ranked(m):
    return sorted(m['calls'].items(), key=lambda kv: (-kv[1]['count'], pretty(kv[0]), kv[0]))


def never(m):
    return sorted(set(m['stubbed']) - set(m['calls']))


def text(m, top=20, show_never=False):
    mins = m['seconds'] / 60 if m['seconds'] else 0
    never_list = never(m)
    lines = [f"stub audit: title {m['title']}, {m['runs']} run(s), {m['seconds'] / 3600:.2f} h, {m['frames']} frames presented",
             f"stubbed functions this build exports: {len(m['stubbed'])}, called in at least one run: {len(m['stubbed']) - len(never_list)}, never called: {len(never_list)}"]
    unknown = [s for s in m['calls'] if s not in m['stubbed']]
    if unknown: lines.append(f'{len(unknown)} called function(s) are not in the exported list of any file (a build that exports more): counted anyway')
    lines += ['', 'by family:', f"  {'family':<24}{'stubbed':>8}{'called':>8}{'calls':>12}"]
    for name, f in sorted(families(m).items(), key=lambda kv: (-kv[1]['calls'], kv[0])):
        lines.append(f"  {name:<24}{f['stubbed']:>8}{f['called']:>8}{f['calls']:>12}")
    lines += ['', f'most called (top {top}):']
    if not m['calls']: lines.append('  none')
    for sym, c in ranked(m)[:top]:
        rate = f"{c['count'] / mins:.1f}/min" if mins else '?'
        lines.append(f"  {c['count']:>10}  {rate:>10}  in {c['runs']}/{m['runs']} runs  {pretty(sym)}  first from {c['first_site'] or '?'}")
    if show_never:
        lines += ['', f'never called ({len(never_list)}):']
        cur = None
        for sym in sorted(never_list, key=lambda s: (family(s), pretty(s))):
            if family(sym) != cur: cur = family(sym); lines.append(f'  {cur}')
            lines.append(f'    {pretty(sym)}')
    return '\n'.join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('files', nargs='+'); p.add_argument('--top', type=int, default=20, help='how many of the most called to list (default 20)')
    p.add_argument('--never', action='store_true', help='also list the stubbed functions no run called, by family')
    p.add_argument('--json', metavar='OUT', help='write the merged view here')
    a = p.parse_args(argv)
    m = merge([load(f) for f in a.files])
    print(text(m, a.top, a.never))
    if a.json:
        with open(a.json, 'w') as f: json.dump(m, f, indent=1, sort_keys=True); f.write('\n')
        print(f'[json] {a.json}')


if __name__ == '__main__': main()
