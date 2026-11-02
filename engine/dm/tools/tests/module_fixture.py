"""Synthetic Switch modules with a dynamic section (NX2Linux T6): functions that reach a host function through a PLT stub and a GOT slot, a table of function pointers that only a relocation fills in, exported
data, an init_array entry, an undefined weak symbol and an import nobody implements. Built from a spec, so a test can make as many modules as it needs; no game data. The result is an NSO (uncompressed, segment
hash flags cleared as in arm64_fixture.py) that nso2elf.py turns into the ELF the loader reads.
  spec = dict(name='a', funcs=[Fn(name, export, asm)], imports=[(name, weak, 'plt' | 'got')], data=[(name, export, value | [('func', fname), ...])], inits=[fname], bss=0x100)
asm(c, me) returns the function's words; c.fn(name), c.got(import) and c.data(name) are addresses (0 on the sizing pass)."""
import hashlib, struct, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import arm64_fixture as fx
from nso_asm import B, NOP, ADRP, ADDI, BR, BLR, LDRX, STP_PRE, LDP_POST

PAGE = 0x1000
R_ABS64, R_GLOB_DAT, R_JUMP_SLOT, R_RELATIVE = 257, 1025, 1026, 1027
DT = dict(NULL=0, PLTRELSZ=2, STRTAB=5, SYMTAB=6, RELA=7, RELASZ=8, RELAENT=9, STRSZ=10, SYMENT=11, INIT=12, PLTREL=20, JMPREL=23, INIT_ARRAY=25, INIT_ARRAYSZ=27)
RET = fx.RET


class Fn:
    def __init__(self, name, export, asm, fde=True): self.name, self.export, self.asm, self.fde = name, export, asm, fde


def page(x): return (x + PAGE - 1) & ~(PAGE - 1)
def page8(x): return (x + 7) & ~7


class _Ctx:
    def __init__(self, fns=None, gots=None, datas=None): self._f, self._g, self._d = fns or {}, gots or {}, datas or {}
    def fn(self, n): return self._f.get(n, 0)
    def got(self, n): return self._g.get(n, 0)
    def data(self, n): return self._d.get(n, 0)


def plt(name):
    """The classic stub: adrp x16, GOT page; ldr x17, [x16, #slot]; add x16, x16, #slot; br x17"""
    def asm(c, me):
        g = c.got(name)
        return [ADRP(16, me, g), LDRX(17, 16, (g & 0xFFF) // 8), ADDI(16, 16, g & 0xFFF), BR(17)]
    return asm


def build(spec):
    """(NSO bytes, info) with info = dict(name, text/ro/data offsets, fn {name: (start, end)}, got {import: address}, data {name: address}, bss)"""
    funcs, imports, data, inits, bss = spec['funcs'], spec.get('imports', []), spec.get('data', []), spec.get('inits', []), spec.get('bss', 0x100)
    def assemble(c):
        ws, at, o = [], {}, 0x10
        sizes = {}
        for f in funcs:
            w = f.asm(c, c.fn(f.name) or o); sizes[f.name] = len(w) * 4; at[f.name] = o; o += sizes[f.name]
            ws.append(w)
        return ws, at, sizes
    ws, at, sizes = assemble(_Ctx())                                  # sizing pass
    text_len = 0x10 + sum(sizes.values()); ro_off = page(text_len)
    ranges = {f.name: (at[f.name], at[f.name] + sizes[f.name]) for f in funcs}
    # data layout: GOT slots, then the data symbols (each as many words as it has), then the init_array
    d, got, dsym = 0, {}, {}
    for n, _, _ in imports: got[n] = d; d += 8
    for n, _, v in data: dsym[n] = d; d += (page8(len(v)) if isinstance(v, bytes) else 8 * (len(v) if isinstance(v, list) else 1))
    init_at = d; d += 8 * len(inits)
    data_len = d
    # ro: MOD0, .eh_frame + hdr, dynsym, dynstr, rela.dyn, rela.plt, dynamic
    eh, hdr, hdr_at = fx.eh_frame([ranges[f.name] for f in funcs if f.fde], ro_off)
    names = [n for n, _, _ in imports] + [f.name for f in funcs if f.export] + [n for n, e, _ in data if e]
    strtab = b'\0'; stroff = {}
    for n in names: stroff[n] = len(strtab); strtab += n.encode() + b'\0'
    nsym = 1 + len(names)
    sym_at = (hdr_at + len(hdr) + 7) & ~7; str_at = sym_at + 24 * nsym
    rela_at = (str_at + len(strtab) + 7) & ~7
    # data_off needs the ro size, which needs the relocation counts only (not the addresses): two passes over the addresses
    n_dyn = sum(1 for n, _, k in imports if k == 'got') + sum(len(v) if isinstance(v, list) else 0 for _, _, v in data) + len(inits)
    n_plt = sum(1 for n, _, k in imports if k == 'plt')
    plt_at = rela_at + 24 * n_dyn; dyn_at = (plt_at + 24 * n_plt + 15) & ~15
    n_tags = 11 + (1 if inits else 0) * 2
    ro_len = dyn_at + 16 * n_tags
    data_off = ro_off + page(ro_len)
    A = lambda ro_rel: ro_off + ro_rel
    ctx = _Ctx({f.name: at[f.name] for f in funcs}, {n: data_off + o for n, o in got.items()}, {n: data_off + o for n, o in dsym.items()})
    ws, at, sizes = assemble(ctx)
    assert len(ws) == len(funcs) and all(len(w) * 4 == sizes[f.name] for w, f in zip(ws, funcs))
    text = [B(0, 0x10), ro_off, NOP, NOP] + [x for w in ws for x in w]
    textb = bytearray(struct.pack('<%dI' % len(text), *text)); textb += bytes(ro_off - len(textb))
    ro = bytearray(ro_len)
    # MOD0: magic, dynamic, bss start and end (relative to MOD0, which is at ro_off), eh_frame_hdr start and end, library name
    struct.pack_into('<4siiiiii', ro, 0, b'MOD0', dyn_at, data_off + data_len - ro_off, data_off + data_len + bss - ro_off, hdr_at, hdr_at + len(hdr), 0)
    ro[0x40:0x40 + len(eh)] = eh; ro[hdr_at:hdr_at + len(hdr)] = hdr
    # symbols: null, imports (undefined), exports (defined)
    syms = [struct.pack('<IBBHQQ', 0, 0, 0, 0, 0, 0)]
    for n, weak, _ in imports: syms.append(struct.pack('<IBBHQQ', stroff[n], ((2 if weak else 1) << 4) | 2, 0, 0, 0, 0))
    for f in funcs:
        if f.export: syms.append(struct.pack('<IBBHQQ', stroff[f.name], (1 << 4) | 2, 0, 1, at[f.name], sizes[f.name]))
    for n, e, v in data:
        if e: syms.append(struct.pack('<IBBHQQ', stroff[n], (1 << 4) | 1, 0, 2, data_off + dsym[n], len(v) if isinstance(v, bytes) else 8 * (len(v) if isinstance(v, list) else 1)))
    ro[sym_at:sym_at + 24 * nsym] = b''.join(syms); ro[str_at:str_at + len(strtab)] = strtab
    symidx = {n: i + 1 for i, n in enumerate(names)}
    dyn_rel = [(data_off + got[n], R_GLOB_DAT, symidx[n], 0) for n, _, k in imports if k == 'got']
    for n, _, v in data:
        if isinstance(v, list):
            for i, (kind, fname) in enumerate(v): assert kind == 'func'; dyn_rel.append((data_off + dsym[n] + 8 * i, R_RELATIVE, 0, at[fname]))
    for i, fname in enumerate(inits): dyn_rel.append((data_off + init_at + 8 * i, R_RELATIVE, 0, at[fname]))
    plt_rel = [(data_off + got[n], R_JUMP_SLOT, symidx[n], 0) for n, _, k in imports if k == 'plt']
    for base, rows in ((rela_at, dyn_rel), (plt_at, plt_rel)):
        for i, (off, t, si, add) in enumerate(rows): struct.pack_into('<QQq', ro, base + 24 * i, off, (si << 32) | t, add)
    tags = [(DT['PLTRELSZ'], 24 * n_plt), (DT['JMPREL'], A(plt_at)), (DT['PLTREL'], 7), (DT['RELA'], A(rela_at)), (DT['RELASZ'], 24 * n_dyn), (DT['RELAENT'], 24),
            (DT['STRTAB'], A(str_at)), (DT['SYMTAB'], A(sym_at)), (DT['STRSZ'], len(strtab)), (DT['SYMENT'], 24)]
    if inits: tags += [(DT['INIT_ARRAY'], data_off + init_at), (DT['INIT_ARRAYSZ'], 8 * len(inits))]
    tags.append((DT['NULL'], 0))
    assert len(tags) <= n_tags, (len(tags), n_tags)
    for i, (t, v) in enumerate(tags): struct.pack_into('<qQ', ro, dyn_at + 16 * i, t, v)
    datab = bytearray(data_len)
    for n, _, v in data:
        if isinstance(v, bytes): datab[dsym[n]:dsym[n] + len(v)] = v
        elif not isinstance(v, list): struct.pack_into('<Q', datab, dsym[n], v)
    hdr_nso = bytearray(0x100); hdr_nso[:4] = b'NSO0'; hdr_nso[0x40:0x60] = hashlib.sha256(b'nx2linux-module-' + spec['name'].encode()).digest()
    struct.pack_into('<I', hdr_nso, 0x3c, bss)
    body, fo = b'', 0x100
    for i, (seg, mo) in enumerate(((bytes(textb), 0), (bytes(ro), ro_off), (bytes(datab), data_off))):
        struct.pack_into('<III', hdr_nso, 0x10 + 0x10 * i, fo + len(body), mo, len(seg)); struct.pack_into('<I', hdr_nso, 0x60 + 4 * i, len(seg))
        hdr_nso[0xa0 + 0x20 * i:0xc0 + 0x20 * i] = hashlib.sha256(seg).digest(); body += seg
    info = dict(name=spec['name'], ro_off=ro_off, data_off=data_off, fn={n: (a, a + sizes[n]) for n, a in at.items()}, got={n: data_off + o for n, o in got.items()},
                data={n: data_off + o for n, o in dsym.items()}, init_array=data_off + init_at, bss=bss, data_len=data_len)
    return bytes(hdr_nso + body), info


# ---- the two modules of the tests ----
def module_a():
    """Exports mod_entry (x0 -> hle_add(x0, 5) + 1), mod_table_call (x0 = index, x1 = argument: calls fa or fb through a table the relocations fill in), mod_value_get, mod_init (init_array: sets mod_value),
    mod_weak_get (the GOT slot of an undefined weak symbol), mod_calls_missing (an import nobody implements)"""
    def mod_entry(c, me): return [STP_PRE(29, 30, 31, -16), fx.MOVZ(1, 5), fx.BL(me + 8, c.fn('plt_hle_add')), ADDI(0, 0, 1), LDP_POST(29, 30, 31, 16), RET]
    def mod_table_call(c, me):
        t = c.data('mod_table')
        return [STP_PRE(29, 30, 31, -16), ADRP(2, me + 4, t), ADDI(2, 2, t & 0xFFF), 0xF8607843, fx.MOV(0, 1), BLR(3), LDP_POST(29, 30, 31, 16), RET]   # ldr x3, [x2, x0, lsl #3]
    def mod_value_get(c, me): v = c.data('mod_value'); return [ADRP(0, me, v), LDRX(0, 0, (v & 0xFFF) // 8), RET]
    def mod_init(c, me): v = c.data('mod_value'); return [ADRP(0, me, v), fx.MOVZ(1, 0x1234), 0xF9000001 | (((v & 0xFFF) // 8) << 10), RET]   # str x1, [x0, #lo]
    def mod_weak_get(c, me): g = c.got('weak_fn'); return [ADRP(0, me, g), LDRX(0, 0, (g & 0xFFF) // 8), RET]
    def mod_calls_missing(c, me): return [STP_PRE(29, 30, 31, -16), fx.BL(me + 4, c.fn('plt_missing_fn')), LDP_POST(29, 30, 31, 16), RET]
    return dict(name='a', funcs=[
        Fn('plt_hle_add', False, plt('hle_add'), fde=False), Fn('plt_missing_fn', False, plt('missing_fn'), fde=False),
        Fn('mod_entry', True, mod_entry), Fn('mod_table_call', True, mod_table_call),
        Fn('fa', False, lambda c, me: [fx.ADDR(0, 0, 0), RET]), Fn('fb', False, lambda c, me: [ADDI(0, 0, 100), RET]),
        Fn('mod_value_get', True, mod_value_get), Fn('mod_init', True, mod_init), Fn('mod_weak_get', True, mod_weak_get), Fn('mod_calls_missing', True, mod_calls_missing)],
        imports=[('hle_add', False, 'plt'), ('missing_fn', False, 'plt'), ('weak_fn', True, 'got')],
        data=[('mod_value', True, 0x1111), ('mod_table', False, [('func', 'fa'), ('func', 'fb')])], inits=['mod_init'])


def module_b():
    """Imports mod_entry from module a: its call goes to a's function, not to a host function"""
    def b_call(c, me): return [STP_PRE(29, 30, 31, -16), fx.BL(me + 4, c.fn('plt_mod_entry')), ADDI(0, 0, 1000), LDP_POST(29, 30, 31, 16), RET]
    return dict(name='b', funcs=[Fn('plt_mod_entry', False, plt('mod_entry'), fde=False), Fn('b_call', True, b_call)], imports=[('mod_entry', False, 'plt')])


def module_c():
    """c_main starts with a register-form ccmn, which Remill cannot lift: it runs in the JIT, and its call through the PLT reaches the host function from there"""
    def c_main(c, me): return [STP_PRE(29, 30, 31, -16), 0xBA408000, fx.ADDR(0, 0, 1), fx.MOVZ(1, 3), fx.BL(me + 16, c.fn('plt_hle_add')), LDP_POST(29, 30, 31, 16), RET]
    return dict(name='c', funcs=[Fn('plt_hle_add', False, plt('hle_add'), fde=False), Fn('c_main', True, c_main)], imports=[('hle_add', False, 'plt')])


def ADR(rd, pc, target):
    off = target - pc; assert -(1 << 20) <= off < (1 << 20)
    return 0x10000000 | ((off & 3) << 29) | (((off >> 2) & 0x7FFFF) << 5) | rd


def module_d():
    """d_switch(x0): a switch as LLVM compiles it for AArch64, a table of byte offsets (in words) from the first case: x0 in 0..3 returns 10, 20, 30, 40, anything else 99"""
    def d_switch(c, me):
        t = c.data('d_table'); cases = me + 8 * 4   # the eight words before the first case
        words = [0x71000C1F, fx.BCOND(me + 4, me + 8 * 4 + 4 * 8, 8),   # cmp w0, #3 ; b.hi default
                 ADRP(9, me + 8, t), ADDI(9, 9, t & 0xFFF), ADR(10, me + 16, cases),
                 0x38606800 | (0 << 16) | (9 << 5) | 11,                   # ldrb w11, [x9, x0]
                 0x8B000000 | (11 << 16) | (2 << 10) | (10 << 5) | 10,     # add x10, x10, x11, lsl #2
                 BR(10)]
        for k in range(4): words += [fx.MOVZ(0, 10 * (k + 1)), RET]
        return words + [fx.MOVZ(0, 99), RET]
    return dict(name='d', funcs=[Fn('d_switch', True, d_switch)], data=[('d_table', False, bytes([0, 2, 4, 6]))])
