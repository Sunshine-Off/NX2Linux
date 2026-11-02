"""The ARM64 reference for the recompilation tests (NX2Linux T2): runs a function of a synthetic module (arm64_fixture.py) on Unicorn's AArch64 core and returns its registers and NZCV.
The recompiled x86_64 code is compared with this, never with a model written by the same hand. model_arith() is a second, independent model in plain Python, used only to cross-check the reference."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import arm64_fixture as fx

MASK = (1 << 64) - 1
BASE, STACK, STOP = 0x10000, 0x200000, 0x400000   # text mapped at BASE; STOP: the return address, where the run ends


def xreg(i):
    """Unicorn's constant for register xi: x0..x28 are consecutive, x29 and x30 are separate constants, so X0 + i is wrong from x29 on"""
    from unicorn import arm64_const as c
    return c.UC_ARM64_REG_X29 if i == 29 else c.UC_ARM64_REG_X30 if i == 30 else c.UC_ARM64_REG_X0 + i


def run(name, regs=None, steps=10000):
    """Run function `name` of the fixture with registers {'x0': v, ...}; returns {'x0'..'x30', 'sp', 'nzcv'} (nzcv: N Z C V as bits 3..0). The run ends when the function returns to STOP.
    An exception of the core (an undecodable word, an unmapped access) propagates: a reference run never hides one."""
    from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
    from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X30, UC_ARM64_REG_SP, UC_ARM64_REG_NZCV
    words, ranges, _ = fx.text_layout()
    import struct
    code = struct.pack('<%dI' % len(words), *words)
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    uc.mem_map(BASE, 0x10000); uc.mem_write(BASE, code); uc.mem_map(STACK - 0x10000, 0x10000); uc.mem_map(STOP, 0x1000)
    for r, v in (regs or {}).items(): uc.reg_write(xreg(int(r[1:])), v & MASK)
    uc.reg_write(UC_ARM64_REG_SP, STACK); uc.reg_write(UC_ARM64_REG_X30, STOP)
    start = BASE + ranges[name][0]
    uc.emu_start(start, STOP, count=steps)
    out = {f'x{i}': uc.reg_read(xreg(i)) for i in range(31)}
    out['sp'] = uc.reg_read(UC_ARM64_REG_SP); out['nzcv'] = uc.reg_read(UC_ARM64_REG_NZCV) >> 28
    return out


def _flags(result, a, b, sub):
    """NZCV of a 64-bit add (sub=False) or subtract a - b (sub=True), from the Arm ARM's AddWithCarry(a, b or ~b, carry in 0 or 1)"""
    if sub: b, cin = ~b & MASK, 1
    else: cin = 0
    full = a + b + cin; r = full & MASK
    sa, sb = a - (1 << 64) if a >> 63 else a, b - (1 << 64) if b >> 63 else b
    return (r >> 63) << 3 | (r == 0) << 2 | (full > MASK) << 1 | (sa + sb + cin != (r - (1 << 64) if r >> 63 else r)), r


def model_arith(x0, x1):
    """What `arith` computes (add x2,x0,x1; adds x3,x2,x0; subs x4,x3,x1; mul x0,x2,x4), by the Arm ARM's definitions: (x0, nzcv)"""
    x2 = (x0 + x1) & MASK
    _, x3 = _flags(0, x2, x0, False)
    nzcv, x4 = _flags(0, x3, x1, True)
    return (x2 * x4) & MASK, nzcv


def run_words(words, cases, steps=100):
    """Run the instruction words (they end with `ret`) once per case on one Unicorn core; each case is (registers {'x0': v, ...}, nzcv in). Returns [({'x0'..'x3'}, nzcv out)].
    For the instruction-level tests (test_recompile_insn.py): the registers not in a case are 0"""
    import struct
    from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
    from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X30, UC_ARM64_REG_SP, UC_ARM64_REG_NZCV
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    uc.mem_map(BASE, 0x10000); uc.mem_write(BASE, struct.pack('<%dI' % len(words), *words)); uc.mem_map(STOP, 0x1000)
    out = []
    for regs, nzcv in cases:
        for i in range(31): uc.reg_write(xreg(i), regs.get(f'x{i}', 0) & MASK)
        uc.reg_write(UC_ARM64_REG_NZCV, nzcv << 28); uc.reg_write(UC_ARM64_REG_X30, STOP); uc.reg_write(UC_ARM64_REG_SP, STOP)
        uc.emu_start(BASE, STOP, count=steps)
        out.append(({f'x{i}': uc.reg_read(UC_ARM64_REG_X0 + i) for i in range(4)}, uc.reg_read(UC_ARM64_REG_NZCV) >> 28))
    return out


def run_image(code, entry, cases, mem=None, steps=1000000, stop=STOP, svc=None, imports=None):
    """Run the function at `entry` of `code` (mapped at address 0) once per case on one Unicorn core, with the memory regions {base: bytes} (a size that is a whole number of pages) restored before each case.
    A case is (registers {'x0': v, ...}, nzcv in, sp); the registers may also hold 'tpidr_el0' and 'tpidrro_el0'. imports: {address: fn(regs)}, host functions at addresses a loaded module's GOT points at: the address holds a `ret`; a hook on it runs fn on the registers as a list, then the `ret` returns to x30. svc: a factory svc(uc) returning the handler handler(num, regs, orig_addr) (regs: list x0..x30 it changes
    in place, the shape of svc_dispatch), called once per case; its reset(uc) method, when it has one, runs after the case (to drop what the services mapped). Returns, per case, {'x0'..'x30', 'sp', 'nzcv', 'mem': {base: bytes}}, or {'fault': reason} when the core raised (a fetch, read or write outside the regions)."""
    from unicorn import Uc, UcError, UC_ARCH_ARM64, UC_MODE_ARM
    from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X30, UC_ARM64_REG_SP, UC_ARM64_REG_NZCV
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    uc.mem_map(0, (len(code) + 0xFFF) & ~0xFFF); uc.mem_write(0, code); uc.mem_map(stop, 0x1000)
    for b, data in (mem or {}).items(): uc.mem_map(b, len(data))
    from unicorn import UC_HOOK_INTR
    from unicorn.arm64_const import UC_ARM64_REG_TPIDR_EL0, UC_ARM64_REG_TPIDRRO_EL0, UC_ARM64_REG_PC
    handler = [None]
    def on_intr(u, intno, _):
        if intno != 2 or not handler[0]: return
        pc = u.reg_read(UC_ARM64_REG_PC)
        for at in (pc - 4, pc):   # Unicorn reports the svc with PC at the next instruction or at the svc, depending on its version
            w = int.from_bytes(u.mem_read(at, 4), 'little')
            if w & 0xFFE0001F == 0xD4000001: break
        else: raise RuntimeError(f'svc interrupt with no svc at {pc:#x}')
        r = [u.reg_read(xreg(i)) for i in range(31)]
        handler[0]((w >> 5) & 0xFFFF, r, at)
        for i in range(31): u.reg_write(xreg(i), r[i] & MASK)
    if svc: uc.hook_add(UC_HOOK_INTR, on_intr)
    if imports:   # each import address holds a `ret`, and a hook on that instruction runs the host function first; the `ret` then returns to x30
        from unicorn import UC_HOOK_CODE
        lo, hi = min(imports) & ~0xFFF, (max(imports) + 0x1000) & ~0xFFF
        uc.mem_map(lo, hi - lo); uc.mem_write(lo, b'\xc0\x03\x5f\xd6' * ((hi - lo) // 4))
        def on_import(u, addr, size, _):
            fn = imports.get(addr)
            if fn is None: return
            r = [u.reg_read(xreg(i)) for i in range(31)]
            fn(r)
            for i in range(31): u.reg_write(xreg(i), r[i] & MASK)
        uc.hook_add(UC_HOOK_CODE, on_import, begin=lo, end=hi)
    out = []
    for regs, nzcv, sp in cases:
        handler[0] = svc(uc) if svc else None
        uc.reg_write(UC_ARM64_REG_TPIDR_EL0, regs.get('tpidr_el0', 0)); uc.reg_write(UC_ARM64_REG_TPIDRRO_EL0, regs.get('tpidrro_el0', 0))
        for b, data in (mem or {}).items(): uc.mem_write(b, data)
        for i in range(31): uc.reg_write(xreg(i), regs.get(f'x{i}', 0) & MASK)
        uc.reg_write(UC_ARM64_REG_X30, regs.get('x30', stop) & MASK); uc.reg_write(UC_ARM64_REG_SP, sp); uc.reg_write(UC_ARM64_REG_NZCV, nzcv << 28)
        try: uc.emu_start(entry, stop, count=steps)
        except UcError as e:
            out.append({'fault': str(e)}); getattr(handler[0], 'reset', lambda u: None)(uc); continue
        r = {f'x{i}': uc.reg_read(xreg(i)) for i in range(31)}
        r['sp'] = uc.reg_read(UC_ARM64_REG_SP); r['nzcv'] = uc.reg_read(UC_ARM64_REG_NZCV) >> 28; r['tpidr_el0'] = uc.reg_read(UC_ARM64_REG_TPIDR_EL0)
        r['mem'] = {b: bytes(uc.mem_read(b, len(data))) for b, data in (mem or {}).items()}
        out.append(r)
        getattr(handler[0], 'reset', lambda u: None)(uc)
    return out
