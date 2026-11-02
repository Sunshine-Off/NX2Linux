"""Floating-point and vector instructions, one by one, lifted by Remill, compiled and compared with the Unicorn ARM64 core (NX2Linux, known blockers 3 and 4: the instructions a real game uses and Remill did not lift).
Each instruction is written as assembly (aarch64-linux-gnu-as turns it into its word), becomes a one-instruction function, and runs on the same inputs on both sides: vector registers holding edge
values (zeros, -0, 1.5, large and tiny floats, infinities, integers on the boundaries), a data region, the general registers. Registers, NZCV, memory and FPCR must be identical. FPSR (the cumulative
exception flags) is compared separately and listed: Remill does not model every flag, and a game almost never reads them. NaNs are left out of the inputs (their payload and sign differ between x86 and
ARM: see the hypotheses file).
Needs aarch64-linux-gnu-as, REMILL_BUILD, clang++ and unicorn; skipped otherwise."""
import os, random, shutil, struct, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent))
import aot_lift
import arm64_ref as ref
from test_aot_t3 import READY

AS = shutil.which('aarch64-linux-gnu-as')
OBJCOPY = shutil.which('aarch64-linux-gnu-objcopy')
RET = 0xD65F03C0
CODE, STEP = 0x10000, 0x100          # instruction i is the function at CODE + i * STEP: the instruction, then ret
DATA, DATA_SIZE, STACK, STACK_SIZE = 0x100000, 0x2000, 0x200000, 0x10000
SP = STACK + STACK_SIZE - 0x800   # room above for loads that post-increment
MASK = (1 << 64) - 1
DATA_BYTES = bytes((i * 131 + 7) & 0xFF for i in range(DATA_SIZE))


def f32(x): return struct.unpack('<I', struct.pack('<f', x))[0]
def f64(x): return struct.unpack('<Q', struct.pack('<d', x))[0]


FLOATS = [0.0, -0.0, 1.0, -1.0, 0.5, 1.5, -2.5, 3.7, -3.7, 100.25, 1e10, -1e10, 1e-10, 2.0 ** 31, -2.0 ** 31, 2.0 ** 63, 3.0e38, 1.17549435e-38]
POOL64 = ([f64(x) for x in FLOATS + [1e300, -1e300, 5e-324, 2.0 ** 64, 4503599627370496.5]] + [f64(float('inf')), f64(float('-inf'))] +
          [f32(a) | f32(b) << 32 for a, b in zip(FLOATS, FLOATS[3:] + FLOATS[:3])] + [f32(float('inf')) | f32(1.0) << 32] +
          [0, 1, MASK, 0x7FFFFFFF, 0x80000000, 0x7FFFFFFFFFFFFFFF, 0x8000000000000000, 0x00FF00FF00FF00FF, 0x0123456789ABCDEF, 0xFEDCBA9876543210, 0x0001000200030004, 0x8000800080008000])


def has_nan(x):
    """Whether the 64 bits are a NaN as a double, or either half is one as a single (or either 16-bit quarter, as a half): NaN payloads, and which NaN wins when there are two, differ between x86 and ARM"""
    if (x >> 52) & 0x7FF == 0x7FF and x & ((1 << 52) - 1): return True
    for sh in (0, 32):
        h = x >> sh & 0xFFFFFFFF
        if (h >> 23) & 0xFF == 0xFF and h & 0x7FFFFF: return True
    return False


POOL_NO_NAN = [v for v in POOL64 if not has_nan(v)]


def assemble(lines, tmp):
    """The words of each line: instructions separated by ';' are one function (the function is followed by a ret). Each function starts on a STEP boundary of the assembled code"""
    src = Path(tmp) / 'insns.s'
    src.write_text('.text\n' + ''.join(f'.balign {STEP}\n' + l.replace(';', '\n') + '\nret\n' for l in lines))
    obj, binf = Path(tmp) / 'insns.o', Path(tmp) / 'insns.bin'
    r = subprocess.run([AS, '-march=armv8.2-a+fp16+crypto', str(src), '-o', str(obj)], capture_output=True, text=True, errors='replace')
    if r.returncode: raise RuntimeError(r.stderr)
    subprocess.run([OBJCOPY, '-O', 'binary', '-j', '.text', str(obj), str(binf)], check=True)
    data = binf.read_bytes().ljust(STEP * len(lines), b'\0')
    out = []
    for i in range(len(lines)):
        words = struct.unpack('<%dI' % (STEP // 4), data[i * STEP:(i + 1) * STEP])
        out.append(words[:words.index(RET) + 1])
    return out


class Harness:
    def __init__(self, lines, tmp):
        self.lines, self.words = lines, assemble(lines, tmp)
        funcs = [(CODE + i * STEP, struct.pack('<%dI' % len(w), *w)) for i, w in enumerate(self.words)]
        self.unlifted = {}
        self.aot = aot_lift.Aot(funcs, stub_unliftable=True)
        for a, why in self.aot.unlifted.items(): self.unlifted[self.lines[(a - CODE) // STEP]] = why
        for b, data in ((DATA, DATA_BYTES), (STACK, bytes(STACK_SIZE))): self.aot.map(b, len(data), data)
        from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
        from unicorn.arm64_const import UC_ARM64_REG_CPACR_EL1
        self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        self.uc.mem_map(CODE, 0x10000 * ((len(lines) * STEP + 0xFFFF) // 0x10000))
        for i, w in enumerate(self.words): self.uc.mem_write(CODE + i * STEP, struct.pack('<%dI' % len(w), *w))
        self.uc.mem_map(DATA, DATA_SIZE); self.uc.mem_map(STACK, STACK_SIZE); self.uc.mem_map(ref.STOP, 0x1000)
        self.uc.reg_write(UC_ARM64_REG_CPACR_EL1, 3 << 20)   # floating point and vectors on

    def close(self): self.aot.close()

    def inputs(self, rng, n, nan_free=True):
        pool = POOL_NO_NAN if nan_free else POOL64 + [rng.getrandbits(64) for _ in range(24)] + [rng.getrandbits(8) * 0x0101010101010101 for _ in range(4)] + [rng.getrandbits(16) | rng.getrandbits(16) << 32 for _ in range(4)]   # integer instructions: plenty of random bit patterns too
        out = []
        for _ in range(n):
            v = {f'v{i}': rng.choice(pool) | rng.choice(pool) << 64 for i in range(32)}
            x = {'x0': DATA + 0x100, 'x1': rng.choice([0, 1, 2, 3, 5, 8]), 'x2': DATA + 0x400, 'x3': rng.choice([0, 1, 5, 200, 1 << 40, MASK, 0x80000000, 0x7FFFFFFF, 0xFFFFFFFF, 1 << 63]),
                 'x4': rng.choice(pool), 'x5': 77}
            out.append((x, rng.randrange(16), v))
        return out

    def run(self, idx, case):
        """-> (got, want) dicts of everything compared; got may be {'fault': ...}"""
        from unicorn import UcError
        from unicorn.arm64_const import UC_ARM64_REG_SP, UC_ARM64_REG_NZCV, UC_ARM64_REG_FPCR, UC_ARM64_REG_FPSR, UC_ARM64_REG_Q0
        x, nz, v = case
        uc, entry = self.uc, CODE + idx * STEP
        uc.mem_write(DATA, DATA_BYTES); uc.mem_write(STACK, bytes(STACK_SIZE))
        for i in range(31): uc.reg_write(ref.xreg(i), x.get(f'x{i}', 0))
        for i in range(32): uc.reg_write(UC_ARM64_REG_Q0 + i, v.get(f'v{i}', 0))
        uc.reg_write(ref.xreg(30), ref.STOP); uc.reg_write(UC_ARM64_REG_SP, SP); uc.reg_write(UC_ARM64_REG_NZCV, nz << 28)
        uc.reg_write(UC_ARM64_REG_FPCR, 0); uc.reg_write(UC_ARM64_REG_FPSR, 0)
        try: uc.emu_start(entry, ref.STOP, count=200); want_fault = None
        except UcError as e: want_fault = str(e)
        self.aot.reset()
        got = self.aot.run(entry, dict(x, x30=ref.STOP), nz, SP, vregs=v)
        if want_fault or 'fault' in got: return got, ({'fault': want_fault} if want_fault else None)
        want = {f'x{i}': uc.reg_read(ref.xreg(i)) for i in range(31)}
        want.update({f'v{i}': uc.reg_read(UC_ARM64_REG_Q0 + i) for i in range(32)})
        want.update(sp=uc.reg_read(UC_ARM64_REG_SP), nzcv=uc.reg_read(UC_ARM64_REG_NZCV) >> 28, fpcr=uc.reg_read(UC_ARM64_REG_FPCR), fpsr=uc.reg_read(UC_ARM64_REG_FPSR))
        want['mem'] = {DATA: bytes(uc.mem_read(DATA, DATA_SIZE)), STACK: bytes(uc.mem_read(STACK, STACK_SIZE))}
        return got, want

    def compare(self, idx, cases):
        """(number of cases equal, [description of each difference], [fpsr differences])"""
        eq, diffs, fpsr = 0, [], []
        for case in cases:
            got, want = self.run(idx, case)
            if ('fault' in got) != (want is not None and 'fault' in want):   # both faulting proves nothing, and is not counted; one faulting is a difference
                diffs.append(f'fault: aot {got.get("fault")!r} unicorn {None if want is None else want.get("fault")!r}'); continue
            if 'fault' in got: continue
            bad = [k for k in want if k not in ('mem', 'fpsr') and got[k] != want[k]]
            bad += [f'mem@{b:#x}' for b in want['mem'] if got['mem'][b] != want['mem'][b]]
            if got['fpsr'] != want['fpsr']: fpsr.append((got['fpsr'], want['fpsr']))
            if bad: diffs.append(f'differs in {bad[:5]}: ' + ', '.join(f'{k} {got[k]:#x} != {want[k]:#x}' for k in bad[:3] if k in got and not k.startswith('mem')))
            else: eq += 1
        return eq, diffs, fpsr


MEMORY = [   # batch 1: stores of vector registers (offset, pre/post index, register offset) and pairs; the loads that already lifted are controls
    'stp q0, q1, [x0]', 'stp q2, q3, [x0, #32]', 'stp q4, q5, [x0, #-64]', 'stp q6, q7, [x0, #-96]!', 'stp q8, q9, [x0, #48]!', 'stp q10, q11, [x0], #32', 'stp q12, q13, [x0], #-16',
    'stp d8, d9, [sp, #-16]!', 'stp d10, d11, [x0, #16]!', 'stp d12, d13, [x0], #24', 'stp d0, d1, [x0, #8]', 'stp s0, s1, [x0, #-8]!', 'stp s2, s3, [x0], #12', 'stp s4, s5, [x0, #4]',
    'str q0, [x0], #16', 'str q1, [x0], #-16', 'str q2, [x0, #-32]!', 'str q3, [x0, x1, lsl #4]', 'str q4, [x0, x1]', 'stur q5, [x0, #-3]',
    'str d0, [x0], #8', 'str d1, [x0, #-8]!', 'str d2, [x0, x1, lsl #3]', 'str d3, [x0, x1]', 'str d4, [x0, #8]', 'str d5, [x0, w1, uxtw #3]', 'str d6, [x0, w3, sxtw #3]',
    'str s0, [x0], #4', 'str s1, [x0, #-4]!', 'str s2, [x0, x1, lsl #2]', 'str s3, [x0, w1, uxtw #2]', 'str s4, [x0, x1]',
    'str h0, [x0], #2', 'str h1, [x0, #-2]!', 'str h2, [x0, x1, lsl #1]', 'str h3, [x0, x1]',
    'str b0, [x0], #1', 'str b1, [x0, #-1]!', 'str b2, [x0, x1]', 'str b3, [x0, w1, uxtw]',
    'ldp q0, q1, [x0]', 'ldp d8, d9, [sp], #16', 'ldp q2, q3, [x0, #-32]!', 'ldr q0, [x0], #16', 'ldr d1, [x0, x1, lsl #3]', 'ldr s2, [x0, #-4]!', 'ldr h3, [x0, #2]', 'ldr b4, [x0, x1]',
]

SCALAR = (   # batch 2: scalar floating point
    [f'fcsel {r}0, {r}1, {r}2, {c}' for r in 'ds' for c in ('eq', 'ne', 'cs', 'cc', 'mi', 'pl', 'vs', 'vc', 'hi', 'ls', 'ge', 'lt', 'gt', 'le', 'al')] +
    [f'{op} {d}, {s}' for op in ('scvtf', 'ucvtf') for d, s in (('d0', 'x3'), ('d0', 'w3'), ('s0', 'x3'), ('s0', 'w3'), ('d1', 'd2'), ('s1', 's2'))] +
    [f'{op} {d}, {s}, #{k}' for op in ('scvtf', 'ucvtf') for d, s, k in (('d0', 'x3', 16), ('d0', 'w3', 8), ('s0', 'x3', 31), ('s0', 'w3', 4), ('d1', 'd2', 12), ('s1', 's2', 5))] +
    [f'{op} {d}, {s}' for op in ('fcvtzs', 'fcvtzu', 'fcvtms', 'fcvtmu', 'fcvtps', 'fcvtpu', 'fcvtas', 'fcvtau', 'fcvtns', 'fcvtnu') for d, s in (('w0', 's1'), ('w0', 'd1'), ('x0', 's1'), ('x0', 'd1'))] +
    [f'{op} {d}, {s}, #{k}' for op in ('fcvtzs', 'fcvtzu') for d, s, k in (('w9', 's0', 13), ('x9', 'd0', 20), ('w9', 'd0', 5), ('x9', 's0', 33))] +
    [f'{op} {r}0, {r}1, {r}2' for op in ('fmin', 'fmax', 'fminnm', 'fmaxnm', 'fnmul', 'fdiv', 'fadd', 'fsub', 'fmul') for r in 'ds'] +
    [f'{op} {r}0, {r}1, {r}2, {r}3' for op in ('fmadd', 'fmsub', 'fnmadd', 'fnmsub') for r in 'ds'] +
    [f'{op} {r}0, {r}1' for op in ('fsqrt', 'frintm', 'frintp', 'frintz', 'frinta', 'frintx', 'frinti', 'frintn', 'fabs', 'fneg', 'fmov') for r in 'ds'] +
    [f'{op} {r}1, {r}2, #{n}, {c}' for op in ('fccmp', 'fccmpe') for r in 'ds' for n, c in ((0, 'ls'), (5, 'eq'), (15, 'ge'))] +
    ['fcmp d0, d1', 'fcmp s0, s1', 'fcmp d0, #0.0', 'fcmpe d0, d1', 'fcmpe s0, #0.0', 'fcvt d0, s1', 'fcvt s0, d1', 'fmov d0, #1.5', 'fmov s0, #-2.0', 'fmov x0, d1', 'fmov d0, x1', 'fmov w0, s1', 'fmov s0, w1'])

EXCL = (   # batch 3: byte and halfword exclusives, release stores, CLREX, DSB, the prefetches (blocker 4); the word and doubleword ones are controls
    [f'ldxr{sz} w1, [x0]; add w1, w1, #1; stxr{sz} w2, w1, [x0]' for sz in ('b', 'h')] + [f'ldaxr{sz} w1, [x0]; add w1, w1, #1; stlxr{sz} w2, w1, [x0]' for sz in ('b', 'h')] +
    [f'ldxr{sz} w1, [x0]; str{sz} w3, [x0]; stxr{sz} w2, w1, [x0]' for sz in ('b', 'h')] +        # another store in between: fails unless it wrote the same value (the model of QEMU too)
    [f'ldxr{sz} w1, [x0]; clrex; stxr{sz} w2, w1, [x0]' for sz in ('b', 'h')] +
    [f'ldxr{sz} w1, [x0]; stxr{sz} w2, w1, [x2]' for sz in ('b', 'h')] +                          # a different address than the load
    [f'stxr{sz} w2, w1, [x0]' for sz in ('b', 'h')] + [f'stlxr{sz} w2, w1, [x0]' for sz in ('b', 'h')] +   # no load at all
    [f'ldxr{sz} w1, [x0]; stxr{sz} w2, w1, [x0]; stxr{sz} w4, w1, [x0]' for sz in ('b', 'h')] +    # the second store finds the monitor cleared
    ['ldxr w1, [x0]; add w1, w1, #1; stxr w2, w1, [x0]', 'ldxr x1, [x0]; add x1, x1, #1; stxr w2, x1, [x0]', 'ldaxr w1, [x0]; stlxr w2, w1, [x0]', 'ldxr w1, [x0]; clrex; stxr w2, w1, [x0]'] +
    ['stlrb w1, [x0]', 'stlrh w1, [x0]', 'stlr w1, [x0]', 'stlr x1, [x0]', 'ldarb w1, [x0]', 'ldarh w1, [x0]', 'ldar w1, [x0]', 'ldar x1, [x0]'] +
    ['clrex', 'clrex #5', 'dsb sy', 'dsb ish', 'dmb ish', 'isb', 'prfm pldl1keep, [x0]', 'prfm pstl2strm, [x0, #8]', 'prfm pldl3keep, [x0, x1]', 'prfum pldl1keep, [x0, #-3]', 'nop'])


A_BHS = ['8b', '16b', '4h', '8h', '2s', '4s']; A_ALL = A_BHS + ['2d']; A_HS = ['4h', '8h', '2s', '4s']; A_B = ['8b', '16b']
def three(op, arrs, regs='v0, v1, v2'): return [f'{op} v0.{a}, v1.{a}, v2.{a}' for a in arrs]
NEON_INT = (   # batch 4: integer NEON (the instructions Balatro uses most: shifts, permutes, compares, saturating and halving arithmetic, moves between elements, tables, widening)
    sum([three(op, A_ALL) for op in ('ushl', 'sshl', 'urshl', 'srshl', 'cmhi', 'cmhs', 'sqadd', 'uqadd', 'sqsub', 'uqsub')], []) +
    sum([three(op, A_BHS) for op in ('shadd', 'uhadd', 'srhadd', 'urhadd', 'shsub', 'uhsub', 'mul', 'mla', 'mls', 'sabd', 'uabd', 'saba', 'uaba')], []) +
    sum([three(op, A_HS) for op in ('sqdmulh', 'sqrdmulh')], []) + three('orn', A_B) +
    [f'{op} v0.{a}, v1.{a}' for op in ('neg', 'abs') for a in A_ALL] + [f'cnt v0.{a}, v1.{a}' for a in A_B] + [f'{op} v0.{a}, v1.{a}' for op in ('clz', 'cls') for a in A_BHS] +
    [f'rev64 v0.{a}, v1.{a}' for a in A_BHS] + [f'rev32 v0.{a}, v1.{a}' for a in ('8b', '16b', '4h', '8h')] + [f'rev16 v0.{a}, v1.{a}' for a in A_B] +
    [f'{op} v0.{d}, v1.{s}' for op in ('uaddlp', 'saddlp', 'uadalp', 'sadalp') for d, s in (('4h', '8b'), ('8h', '16b'), ('2s', '4h'), ('4s', '8h'), ('1d', '2s'), ('2d', '4s'))] +
    [f'{op} {d}0, v1.{s}' for op in ('uaddlv', 'saddlv') for d, s in (('h', '8b'), ('h', '16b'), ('s', '4h'), ('s', '8h'), ('d', '4s'))] +
    [f'xtn v0.{d}, v1.{s}' for d, s in (('8b', '8h'), ('4h', '4s'), ('2s', '2d'))] + [f'xtn2 v0.{d}, v1.{s}' for d, s in (('16b', '8h'), ('8h', '4s'), ('4s', '2d'))] +
    [f'{op} v0.{d}, v1.{s}, #{k}' for op in ('shrn', 'rshrn', 'sqshrn', 'sqrshrn') for d, s, k in (('8b', '8h', 3), ('4h', '4s', 9), ('2s', '2d', 17))] +
    [f'{op}2 v0.{d}, v1.{s}, #{k}' for op in ('shrn', 'rshrn', 'sqshrn', 'sqrshrn') for d, s, k in (('16b', '8h', 5), ('8h', '4s', 16), ('4s', '2d', 32))] +
    [f'shl v0.{a}, v1.{a}, #{k}' for a, k in (('8b', 3), ('16b', 7), ('4h', 5), ('8h', 15), ('2s', 1), ('4s', 31), ('2d', 63), ('4s', 0))] +
    [f'{op} v0.{a}, v1.{a}, #{k}' for op in ('ushr', 'sshr', 'usra', 'ssra', 'urshr', 'srshr', 'ursra', 'srsra', 'sri') for a, k in (('8b', 3), ('16b', 8), ('4h', 5), ('8h', 16), ('2s', 1), ('4s', 32), ('2d', 64), ('4s', 7))] +
    [f'sli v0.{a}, v1.{a}, #{k}' for a, k in (('8b', 3), ('16b', 7), ('4h', 5), ('8h', 15), ('2s', 1), ('4s', 31), ('2d', 63))] +
    [f'{op} v0.{d}, v1.{s}, #{k}' for op in ('ushll', 'sshll') for d, s, k in (('8h', '8b', 3), ('4s', '4h', 0), ('2d', '2s', 17))] + [f'{op}2 v0.{d}, v1.{s}, #{k}' for op in ('ushll', 'sshll') for d, s, k in (('8h', '16b', 7), ('4s', '8h', 1), ('2d', '4s', 31))] +
    [f'shll v0.{d}, v1.{s}, #{k}' for d, s, k in (('8h', '8b', 8), ('4s', '4h', 16), ('2d', '2s', 32))] + [f'shll2 v0.{d}, v1.{s}, #{k}' for d, s, k in (('8h', '16b', 8), ('4s', '8h', 16), ('2d', '4s', 32))] +
    sum([three(op, A_ALL) for op in ('uzp1', 'uzp2', 'zip1', 'zip2', 'trn1', 'trn2')], []) +
    [f'{op} w0, w1, {r}2' for op in ('crc32b', 'crc32h', 'crc32w', 'crc32cb', 'crc32ch', 'crc32cw') for r in ('w',)] + [f'{op} w0, w1, x2' for op in ('crc32x', 'crc32cx')] + ['crc32w w3, w3, w3', 'yield'] +   # the checksum instructions of a real library, and the hint
    [f'ext v0.16b, v1.16b, v2.16b, #{k}' for k in (0, 1, 7, 8, 15)] + [f'ext v0.8b, v1.8b, v2.8b, #{k}' for k in (0, 3, 7)] + ['ext v3.16b, v2.16b, v2.16b, #8', 'ext v1.16b, v1.16b, v2.16b, #5', 'ext v2.8b, v1.8b, v2.8b, #4'] +   # a real RNG swaps the halves of a register with ext v,v,v,#8
    ['dup v0.4s, v1.s[2]', 'dup v0.2d, v1.d[1]', 'dup v0.8b, v1.b[5]', 'dup v0.16b, v1.b[15]', 'dup v0.4h, v1.h[3]', 'dup v0.8h, v1.h[0]', 'dup v0.2s, v1.s[1]',
     'mov v0.d[1], v1.d[0]', 'mov v0.s[2], v1.s[3]', 'ins v0.b[3], v1.b[9]', 'mov v0.h[1], v3.h[5]', 'mov v0.d[0], v1.d[1]'] +
    ['bic v0.4h, #0xff, lsl #8', 'bic v0.8h, #0x12', 'bic v0.2s, #0x3, lsl #16', 'bic v0.4s, #0xf, lsl #24', 'orr v0.4h, #0xff', 'orr v0.8h, #0x12, lsl #8', 'orr v0.2s, #0x3, lsl #16', 'orr v0.4s, #0x90, lsl #24'] +
    ['tbl v0.16b, {v1.16b}, v2.16b', 'tbl v0.8b, {v1.16b}, v2.8b', 'tbl v0.8b, {v1.16b, v2.16b}, v3.8b', 'tbl v0.16b, {v30.16b, v31.16b, v0.16b}, v2.16b', 'tbl v5.16b, {v6.16b, v7.16b, v8.16b, v9.16b}, v2.16b'] +
    [f'{op} v0.{d}, v1.{s}, v2.{s}' for op in ('smull', 'umull', 'smlal', 'umlal', 'smlsl', 'umlsl', 'saddl', 'uaddl', 'ssubl', 'usubl', 'sabdl', 'uabdl') for d, s in (('8h', '8b'), ('4s', '4h'), ('2d', '2s'))] +
    [f'{op}2 v0.{d}, v1.{s}, v2.{s}' for op in ('smull', 'umull', 'smlal', 'umlal', 'smlsl', 'umlsl', 'saddl', 'uaddl', 'ssubl', 'usubl', 'sabdl', 'uabdl') for d, s in (('8h', '16b'), ('4s', '8h'), ('2d', '4s'))] +
    [f'{op} v0.{d}, v1.{d}, v2.{s}' for op in ('saddw', 'uaddw', 'ssubw', 'usubw') for d, s in (('8h', '8b'), ('4s', '4h'), ('2d', '2s'))] + [f'{op}2 v0.{d}, v1.{d}, v2.{s}' for op in ('saddw', 'uaddw', 'ssubw', 'usubw') for d, s in (('8h', '16b'), ('4s', '8h'), ('2d', '4s'))])


F3 = ['2s', '4s', '2d']
NEON_FP = (   # batch 5: floating-point vectors
    sum([three(op, F3) for op in ('fadd', 'fsub', 'fmul', 'fdiv', 'fmax', 'fmin', 'fmaxnm', 'fminnm', 'fabd', 'fcmeq', 'fcmge', 'fcmgt', 'facge', 'facgt', 'fmla', 'fmls', 'faddp')], []) +
    [f'{op} v0.{a}, v1.{a}, #0.0' for op in ('fcmeq', 'fcmge', 'fcmgt', 'fcmle', 'fcmlt') for a in F3] +
    [f'{op} v0.{a}, v1.{a}' for op in ('fneg', 'fabs', 'fsqrt', 'frintn', 'frinta', 'frintm', 'frintp', 'frintz', 'frintx', 'frinti', 'scvtf', 'ucvtf', 'fcvtzs', 'fcvtzu') for a in F3] +
    ['scvtf v0.4s, v1.4s, #7', 'ucvtf v0.2d, v1.2d, #20', 'scvtf v0.2s, v1.2s, #1', 'fcvtzs v0.4s, v1.4s, #3', 'fcvtzu v0.2d, v1.2d, #60', 'fcvtzs v0.2s, v1.2s, #32', 'fcvtzu v0.4s, v1.4s, #12'] +
    ['fcvtn v0.2s, v1.2d', 'fcvtn2 v0.4s, v1.2d', 'fcvtl v0.2d, v1.2s', 'fcvtl2 v0.2d, v1.4s'] +
    [f'{op} v0.{a}, v1.{a}, v2.{e}[{i}]' for op in ('fmul', 'fmla', 'fmls') for a, e, i in (('4s', 's', 3), ('2s', 's', 1), ('2d', 'd', 1), ('4s', 's', 0), ('2d', 'd', 0))] +
    [f'{op} {a}0, {a}1, v2.{a}[{i}]' for op in ('fmul', 'fmla', 'fmls') for a, i in (('s', 2), ('d', 1), ('s', 0), ('d', 0))] +
    ['fabd s0, s1, s2', 'fabd d0, d1, d2', 'fcvtzs s0, s1', 'fcvtzs d0, d1', 'fcvtzu s0, s1', 'fcvtzu d0, d1'] +
    ['fmov v0.4s, #1.0', 'fmov v0.2s, #-2.5', 'fmov v0.2d, #0.5', 'fmov v0.4s, #31.0', 'fmov v0.2d, #-0.125'])


def _regs(first, n, a): return ', '.join(f'v{(first + i) % 32}.{a}' for i in range(n))
_POST = [('', ''), (', #{b}', ''), (', x1', '')]
def _memlines():
    out = []
    for op in ('ld', 'st'):
        for n in (1, 2, 3, 4):
            for a, regb in (('8b', 8), ('16b', 16), ('4h', 8), ('8h', 16), ('2s', 8), ('4s', 16), ('2d', 16), ('1d', 8)):
                if n > 1 and a == '1d': continue
                for post in ('', f', #{regb * n}', ', x1'):
                    out.append(f'{op}{n} {{{_regs(30 if n == 4 else 3, n, a)}}}, [x0]{post}')
        for n in (1, 2, 3, 4):
            for e, a, sz in (('b', 'b', 1), ('h', 'h', 2), ('s', 's', 4), ('d', 'd', 8)):
                for idx in {'b': (0, 5, 15), 'h': (0, 3, 7), 's': (0, 2, 3), 'd': (0, 1)}[e]:
                    for post in ('', f', #{sz * n}', ', x1'):
                        out.append(f'{op}{n} {{{_regs(5, n, e).replace("." + e, "." + e)}}}[{idx}], [x0]{post}'.replace('{' + _regs(5, n, e) + '}[', '{' + _regs(5, n, e) + '}['))
    for n in (1, 2, 3, 4):
        for a, sz in (('8b', 1), ('16b', 1), ('4h', 2), ('8h', 2), ('2s', 4), ('4s', 4), ('1d', 8), ('2d', 8)):
            for post in ('', f', #{sz * n}', ', x1'): out.append(f'ld{n}r {{{_regs(29, n, a)}}}, [x0]{post}')
    return out
STRUCT = _memlines()
NEON_MORE = (   # batch 6b: scalar SIMD forms, by-element integer forms, and a few integer instructions found in real code
    ['add d0, d1, d2', 'cmeq d0, d1, d2', 'sqadd b0, b1, b2', 'sqadd h0, h1, h2', 'sqadd s0, s1, s2', 'sqadd d0, d1, d2', 'sqsub b0, b1, b2', 'sqsub s0, s1, s2', 'sqsub d0, d1, d2',
     'mov s8, v5.s[1]', 'mov d0, v1.d[1]', 'mov b3, v2.b[7]', 'mov h1, v4.h[2]', 'dup s0, v1.s[3]'] +
    [f'{op} v0.{a}, v1.{a}, v2.{e}[{i}]' for op in ('mul', 'mla', 'mls', 'sqdmulh', 'sqrdmulh') for a, e, i in (('4h', 'h', 3), ('8h', 'h', 7), ('2s', 's', 1), ('4s', 's', 3), ('8h', 'h', 0), ('4s', 's', 0)) if not (op != 'mul' and False)] +
    ['sqdmulh h0, h1, v2.h[2]', 'sqrdmulh s0, s1, v2.s[1]', 'sqdmulh s0, s1, v2.s[3]'] +
    [f'{op} v0.{d}, v1.{s}, v2.{e}[{i}]' for op in ('smull', 'umull', 'smlal', 'umlal', 'smlsl', 'umlsl') for d, s, e, i in (('4s', '4h', 'h', 5), ('2d', '2s', 's', 3), ('4s', '4h', 'h', 0), ('2d', '2s', 's', 0))] +
    [f'{op}2 v0.{d}, v1.{s}, v2.{e}[{i}]' for op in ('smull', 'umull', 'smlal', 'umlal', 'smlsl', 'umlsl') for d, s, e, i in (('4s', '8h', 'h', 1), ('2d', '4s', 's', 2))] +
    ['smsubl x0, w1, w2, x3', 'umsubl x0, w1, w2, x3', 'ldursb x0, [x2, #-3]', 'ldursh x0, [x2, #-30]', 'ldursb x0, [x2]', 'ldursh x0, [x2, #7]'])


@unittest.skipUnless(AS and OBJCOPY and READY, 'needs aarch64-linux-gnu-as, REMILL_BUILD, clang++ and unicorn')
class Batch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.groups = {}

    @classmethod
    def tearDownClass(cls):
        for h in cls.groups.values(): h.close()
        cls.tmp.cleanup()

    def check(self, name, lines, cases=24, allow_unlifted=(), nan_free=True):
        h = Harness(lines, self.tmp.name); self.groups[name] = h
        rng = random.Random(name)
        report = []
        for i, line in enumerate(lines):
            if line in h.unlifted:
                if line in allow_unlifted: continue
                report.append(f'{line}: not lifted: {h.unlifted[line][:100]}'); continue
            eq, diffs, fpsr = h.compare(i, h.inputs(rng, cases, nan_free))
            if diffs: report.append(f'{line}: {len(diffs)} of {cases} differ; first: {diffs[0]}')
        self.assertEqual(report, [], '\n' + '\n'.join(l[:230] for l in report))

    def test_memory_batch(self): self.check('memory', MEMORY)

    def test_scalar_fp_batch(self): self.check('scalar', SCALAR)

    def test_the_exception_flags_of_the_scalar_batch_agree_except_the_known_ones(self):
        """FPSR's cumulative flags (inexact, invalid...) come from the host FPU around each operation. They equal Unicorn's for all but two families: the inexact flag of a conversion from an integer
        (scvtf: the host sets it in cases the lowering of the conversion hides), and fmadd's underflow (Remill's own workaround). A new difference is a regression"""
        h = Harness(SCALAR, self.tmp.name); self.groups['scalar_fpsr'] = h
        rng = random.Random('scalar')
        differ = set()
        for i, line in enumerate(SCALAR):
            if line in h.unlifted: continue
            if h.compare(i, h.inputs(rng, 24))[2]: differ.add(line.split()[0])
        self.assertLessEqual(differ, {'scvtf', 'fmadd'}, sorted(differ))

    def test_neon_integer_batch(self): self.check('neon_int', NEON_INT, cases=64, nan_free=False)

    def test_neon_floating_point_batch(self): self.check('neon_fp', NEON_FP, cases=32)

    def test_the_exception_flags_of_the_neon_batches_agree_except_the_known_ones(self):
        """The saturation flag QC of the integer instructions equals Unicorn's on every form; the floating-point lane operations differ in their flags for a few (the inexact flag of a conversion or a division,
        the host's underflow rules): frozen here, a new one is a regression"""
        h = Harness(NEON_FP + NEON_INT, self.tmp.name); self.groups['neon_fpsr'] = h
        rng = random.Random('fp')
        differ = set()
        for i, line in enumerate(h.lines):
            if line in h.unlifted: continue
            if h.compare(i, h.inputs(rng, 32, line not in NEON_INT))[2]: differ.add(line)
        self.assertLessEqual({l.split()[0] for l in differ}, {'fdiv', 'scvtf', 'fmul', 'fmla'}, sorted(differ))
        self.assertEqual({l for l in differ if l in NEON_INT}, set())

    def test_structure_loads_and_stores(self): self.check('struct', STRUCT, cases=12, nan_free=False)

    def test_scalar_simd_and_element_forms(self): self.check('more', NEON_MORE, cases=40, nan_free=False)

    def test_exclusives_batch(self): self.check('excl', EXCL, nan_free=False)


if __name__ == '__main__': unittest.main()
