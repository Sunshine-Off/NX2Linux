#!/usr/bin/env python3
"""Jump tables of AArch64 code, found at translation time (NX2Linux, known blocker 2). A `switch` compiles to an indirect `br` into the middle of the function, which the translator cannot follow: without this,
every dispatch left the recompiled code for the JIT. The compilers' shapes handled here (all seen in a retail game's modules and in GCC's output):
    adrp x9, T ; add x9, x9, #lo            the table of byte/halfword/word entries
    adr  x10, BASE                          the label the entries are relative to (or the table itself, for word entries)
    ldrb w11, [x9, x8]                      the entry (ldrb, ldrh, ldr w, or the signed ldrsb, ldrsh, ldrsw; the index scaled by its size)
    add  x10, x10, x11, lsl #2              target = BASE + (entry << 2)   (or an extended register: `add x3, x3, w1, sxtb #2`)
    br   x10
How it is done: a small symbolic run over the straight line before each `br` tracks which registers hold a known address and which hold a table entry; the `br` register must end as BASE + extend(entry) << shift.
The number of entries comes from the guard (`cmp wN, #k` before the `br`, plus a slack of one; with a `ccmp` next to it, the larger of the two), else the table is read while its targets fall inside the function. Every target outside the function is
dropped. An entry that is not really in the table only costs a lifted block nobody jumps to. A `br` this does not recognise is an ordinary indirect jump and stays one (a function pointer in a register, a tail call).
find_jump_tables(img, start, end) -> [JumpTable]"""
import re, struct
from typing import NamedTuple

MASK = (1 << 64) - 1
NO_WRITE = {'cmp', 'cmn', 'tst', 'cbz', 'cbnz', 'tbz', 'tbnz', 'b', 'ret', 'nop', 'br', 'blr', 'bl', 'str', 'strb', 'strh', 'stp', 'stur', 'sturb', 'sturh', 'prfm', 'dmb', 'dsb', 'isb', 'svc', 'brk'}
LOADS = {'ldrb': (1, False), 'ldrh': (2, False), 'ldr': (4, False), 'ldrsb': (1, True), 'ldrsh': (2, True), 'ldrsw': (4, True)}
CAP = 4096


class JumpTable(NamedTuple):
    br: int          # address of the br
    table: int       # address of the table
    targets: tuple   # sorted, deduplicated, inside the function


def _md():
    import capstone
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM); md.skipdata = True
    return md


def _reg(name):
    m = re.fullmatch(r'[xw](\d+)', name)
    return int(m.group(1)) if m else None


def _ext(v, kind):
    bits = {'b': 8, 'h': 16, 'w': 32, 'x': 64}[kind[-1]]
    v &= (1 << bits) - 1
    return v - (1 << bits) if kind[0] == 's' and v >> (bits - 1) else v


def _imm(s): return int(s.lstrip('#'), 0)


def find_jump_tables(img, start, end):
    """The jump tables of the function [start, end) of img (addressed from 0): one JumpTable per recognised br"""
    ins = list(_md().disasm(bytes(img[start:end]), start))
    out = []
    for i, x in enumerate(ins):
        if x.mnemonic != 'br': continue   # x16 and x17 too: LLVM jumps through x16 (a PLT stub's br x17 is not a table: it jumps on a pointer loaded from memory, which _resolve does not accept)
        t = _resolve(img, ins, i, start, end)
        if t: out.append(t)
    return out


def _resolve(img, ins, i, start, end):
    regs = {}   # register number -> ('c', value) a known address | ('t', table address, width, signed, index shift) a table entry | ('j', base, entry, extend, shift) the jump value
    for x in ins[max(0, i - 400):i]:   # far enough to reach a table address set in the prologue; a stale value only misses targets, never invents a wrong result
        m, ops = x.mnemonic, x.op_str
        parts = [p.strip() for p in re.split(r',\s*(?![^\[]*\])', ops)] if ops else []
        d = _reg(parts[0]) if parts else None
        if m in ('adrp', 'adr') and d is not None: regs[d] = ('c', _imm(parts[1])); continue
        if m == 'mov' and d is not None and len(parts) == 2 and _reg(parts[1]) is not None: regs[d] = regs.get(_reg(parts[1])) or regs.pop(d, None) or ('?',); continue
        if m == 'add' and d is not None:
            n, rest = _reg(parts[1]), parts[2:]
            a = regs.get(n)
            if len(rest) == 1 and rest[0].startswith('#') and a and a[0] == 'c': regs[d] = ('c', (a[1] + _imm(rest[0])) & MASK); continue
            if len(rest) >= 1 and _reg(rest[0]) is not None:
                b = regs.get(_reg(rest[0]))
                kind, sh = 'xx', 0
                if len(rest) == 2:
                    mm = re.fullmatch(r'(lsl|[su]xt[bhwx])(?: #(\d+))?', rest[1])
                    if mm: kind, sh = ('xx' if mm.group(1) == 'lsl' else mm.group(1)), int(mm.group(2) or 0)
                if a and b and a[0] == 'c' and b[0] == 't': regs[d] = ('j', a[1], b, kind, sh); continue
                if a and b and a[0] == 't' and b[0] == 'c' and len(rest) == 1: regs[d] = ('j', b[1], a, 'xx', 0); continue
        if m in LOADS and d is not None:
            mm = re.fullmatch(r'\[(\w+), (\w+)(?:, (lsl|[su]xt[wx]) ?#?(\d+)?|, ([su]xt[wx]))?\]', parts[1]) if len(parts) == 2 else None
            width, signed = LOADS[m]
            if m == 'ldr' and not parts[0].startswith('w'): mm = None   # a 64-bit load is a pointer, not a table entry
            base = regs.get(_reg(mm.group(1))) if mm else None
            if mm and base and base[0] == 'c':
                shift = int(mm.group(4)) if mm.group(4) else 0
                if shift in (0, width.bit_length() - 1): regs[d] = ('t', base[1], width, signed, shift, _reg(mm.group(2))); continue
        if m not in NO_WRITE and not m.startswith('b.') and d is not None: regs.pop(d, None)
        if m in ('stp', 'ldp', 'ldpsw') and len(parts) > 1 and _reg(parts[1]) is not None: regs.pop(_reg(parts[1]), None)
    j = regs.get(_reg(ins[i].op_str))
    if not j or j[0] != 'j': return None
    _, base, (_, taddr, width, signed, ishift, index_reg), kind, shift = j
    # the entry count: the nearest `cmp reg, #k` before the br (the guard of the switch), one more for slack; else scan while the targets are inside the function
    n = None
    for k in range(i - 1, max(0, i - 120) - 1, -1):
        x = ins[k]
        if x.mnemonic == 'cmp' and re.fullmatch(r'[xw]\d+, #\w+', x.op_str):
            n = _imm(x.op_str.split(', ')[1])
            # a compound guard (`cmp w4, #0 ; ccmp w8, #3, #2, ne ; b.hi default`, LLVM's `a != 0 && b <= 3`): the bound is the largest immediate of the compares right around it
            for y in ins[max(0, k - 3):k + 4]:
                if y.mnemonic == 'ccmp':
                    mm = re.fullmatch(r'[xw]\d+, #(\w+), #\w+, \w+', y.op_str)
                    if mm: n = max(n, _imm(mm.group(1)))
            n = min(n + 2, CAP); break
    # A conditional branch can enter a later dispatch after skipping another
    # switch.  In that shape the nearest linear cmp belongs to the skipped
    # table, while the real bound is immediately before the incoming branch.
    # Accept only a branch into the small address/load/add suffix of this br,
    # and only a cmp of the register used to index the table.
    dispatch_lo = ins[max(0, i - 8)].address
    incoming = None
    for k, x in enumerate(ins[:i]):
        if not x.mnemonic.startswith('b.') or not x.op_str.startswith('#'): continue
        target = _imm(x.op_str)
        if not dispatch_lo <= target <= ins[i].address: continue
        for y in reversed(ins[max(0, k - 4):k]):
            mm = re.fullmatch(r'[xw](\d+), #(\w+)', y.op_str) if y.mnemonic == 'cmp' else None
            if mm and int(mm.group(1)) == index_reg:
                incoming = max(incoming or 0, _imm(mm.group(2)) + 2)
                break
    if incoming is not None: n = min(max(n or 0, incoming), CAP)
    targets, k = set(), 0
    while k < (n or CAP):
        at = taddr + k * width
        if at + width > len(img): break
        v = int.from_bytes(img[at:at + width], 'little', signed=signed)
        v = _ext(v, kind) if kind != 'xx' else v
        t = (base + (v << shift)) & MASK
        if start <= t < end and t % 4 == 0: targets.add(t)
        elif n is None: break
        k += 1
    return JumpTable(ins[i].address, taddr, tuple(sorted(targets))) if targets else None
