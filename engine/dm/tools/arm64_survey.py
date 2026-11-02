#!/usr/bin/env python3
"""Read-only survey of a Switch module's AArch64 code, the first step of recompiling it (NX2Linux T1): what a translator would have to handle, and what it could not.
For each function the unwind data gives (nso_code.fde_ranges) it lists the instructions that matter: direct branches and calls (with where they land), indirect branches (br, blr, ret),
svc, the system registers the rewriter (patch_nso.py) rewrites and the uses of x18. It also finds the SDK's `svc #n; ret` wrappers, which have no unwind entry (nso_match.py), and
names every other stretch of text no function covers. A word inside a function that does not decode is a SurveyError naming the address, the word and the function: the survey
never guesses past it (a literal pool inside a function would be one; none is expected in the fixture, see the hypotheses file).
Usage: arm64_survey.py <module.nso> (needs capstone 5, as patch_nso.py does)"""
import os, struct, sys
from typing import NamedTuple
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nso
from nso_code import fde_ranges

SYSREGS = ('tpidr_el0', 'tpidrro_el0', 'cntpct_el0')   # what patch_nso.py replaces (its header comment)
DIRECT = {'b': 'branch', 'cbz': 'branch', 'cbnz': 'branch', 'tbz': 'branch', 'tbnz': 'branch', 'bl': 'call'}


class SurveyError(ValueError):
    pass


class Insn(NamedTuple):
    addr: int
    word: int
    text: str       # mnemonic and operands
    kind: str       # 'branch', 'call' (direct); 'ibranch', 'icall', 'ret'; 'svc'; 'sysreg'; 'x18'; '' for anything else
    target: int     # a direct branch's or call's destination, else -1
    where: str      # of a direct target: 'inside' (this function), 'function:<name>' (another one's start), 'unknown'; else ''


class Function(NamedTuple):
    name: str
    start: int
    end: int
    insns: tuple

    def of(self, kind): return [i for i in self.insns if i.kind == kind]


class Survey(NamedTuple):
    functions: tuple    # Function, by address
    svc_stubs: tuple    # ((address, svc number),) wrappers `svc #n; ret` that no unwind entry covers
    gaps: tuple         # ((start, end),) text no function and no svc stub covers: data, padding or code the unwind data does not describe

    def report(self):
        lines = [f'{len(self.functions)} functions, {len(self.svc_stubs)} svc stubs, {len(self.gaps)} gaps']
        for f in self.functions:
            c = {k: len(f.of(k)) for k in ('branch', 'call', 'ibranch', 'icall', 'ret', 'svc', 'sysreg', 'x18')}
            lines.append(f'{f.name} {f.start:#x}..{f.end:#x} {len(f.insns)} insns ' + ' '.join(f'{k}={n}' for k, n in c.items() if n))
            for i in f.insns:
                if i.kind and i.kind != 'ret': lines.append(f'  {i.addr:#x} {i.kind:7} {i.text}' + (f' -> {i.target:#x} ({i.where})' if i.target >= 0 else ''))
        for a, n in self.svc_stubs: lines.append(f'svc stub {a:#x}: svc #{n:#x}')
        for s, e in self.gaps: lines.append(f'gap {s:#x}..{e:#x} ({e - s} bytes)')
        return '\n'.join(lines)


def _md():
    import capstone
    if capstone.cs_version()[0] != 5: raise SurveyError(f'Capstone {capstone.__version__} is unsupported; the survey needs Capstone 5.x (as patch_nso.py does)')
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM); md.detail = True
    return md


def _classify(ins, start, end, starts):
    m, ops = ins.mnemonic, ins.op_str
    kind, target, where = '', -1, ''
    if m in DIRECT or m.startswith('b.'):
        kind = DIRECT.get(m, 'branch')
        target = int(ops.rsplit('#', 1)[1], 0)   # capstone writes a direct branch's destination as an absolute #address
        where = 'inside' if start <= target < end and kind == 'branch' else (f'function:{starts[target]}' if target in starts else 'unknown')
    elif m == 'br': kind = 'ibranch'
    elif m == 'blr': kind = 'icall'
    elif m == 'ret': kind = 'ret'
    elif m == 'svc': kind = 'svc'
    elif m in ('mrs', 'msr') and any(r in ops for r in SYSREGS): kind = 'sysreg'
    else:
        read, written = ins.regs_access()   # x18 is the platform register Apple's kernel owns, which patch_nso.py moves to a thread slot
        if any(ins.reg_name(r) in ('x18', 'w18') for r in list(read) + list(written)): kind = 'x18'
    return kind, target, where


def survey(module):
    """Survey of an nso.Module (its image and text size); SurveyError when the unwind data cannot be read or a function holds a word that does not decode"""
    md, img, text_end = _md(), module.image, module.text_end
    try: ranges = [(s, e) for s, e in fde_ranges(img) if 0 <= s < e <= text_end]
    except (AssertionError, ValueError, IndexError, struct.error) as e: raise SurveyError(f'cannot read the function boundaries from the unwind data: {e}') from e
    starts = {s: f'sub_{s:x}' for s, _ in ranges}
    functions = []
    for s, e in ranges:
        if s % 4 or e % 4: raise SurveyError(f'function {starts[s]} at {s:#x}..{e:#x} is not word aligned')
        insns = []
        for a in range(s, e, 4):
            w = struct.unpack_from('<I', img, a)[0]
            ins = next(md.disasm(bytes(img[a:a + 4]), a), None)
            if ins is None: raise SurveyError(f'{a:#x}: word {w:#010x} does not decode as AArch64, inside {starts[s]} ({s:#x}..{e:#x})')
            kind, target, where = _classify(ins, s, e, starts)
            insns.append(Insn(a, w, f'{ins.mnemonic} {ins.op_str}'.strip(), kind, target, where))
        functions.append(Function(starts[s], s, e, tuple(insns)))
    def stub(a): return a + 8 <= text_end and struct.unpack_from('<I', img, a)[0] & 0xffe0001f == 0xd4000001 and struct.unpack_from('<I', img, a + 4)[0] == 0xd65f03c0
    stubs, gaps, last = [], [], 0
    for s, e in sorted(ranges) + [(text_end, text_end)]:
        a, open_at = (last + 3) & ~3, None   # a gap: `svc #n; ret` stubs are recognised, every other stretch is reported
        while a < s:
            if stub(a) and a + 8 <= s:
                if open_at is not None: gaps.append((open_at, a)); open_at = None
                stubs.append((a, (struct.unpack_from('<I', img, a)[0] >> 5) & 0xffff)); a += 8
            else:
                if open_at is None: open_at = a
                a += 4
        if open_at is not None: gaps.append((open_at, s))
        last = max(last, e)
    return Survey(tuple(functions), tuple(stubs), tuple(gaps))


if __name__ == '__main__':
    if len(sys.argv) != 2: sys.exit(__doc__)
    try: print(survey(nso.cli_read(sys.argv[1])).report())
    except SurveyError as e: sys.exit(f'[RED] {e}')
