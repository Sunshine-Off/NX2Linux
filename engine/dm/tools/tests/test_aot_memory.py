"""T3, memory instruction by instruction: loads and stores of 8 to 64 bits, signed and unsigned, unaligned, with a scaled, unscaled, pre-indexed, post-indexed or register offset, and pairs, lifted by Remill, compiled to x86_64
(aot_lift.py) and compared with the Unicorn ARM64 reference on registers, NZCV and the whole guest memory. An access outside the guest memory is an explicit error (address, size, read or write); the reference faults too.
Each word is checked against Capstone's text first. Needs REMILL_BUILD, clang++ and unicorn; skipped otherwise."""
import itertools, struct, sys, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent))
import aot_lift
import arm64_ref as ref
from test_aot_t3 import DATA, DATA_SIZE, INIT, MEM, SP, READY, same

RET = aot_lift.RET
ADDR = 0x1000
M = ref.MASK


def ls(base, rt=2, rn=0, imm=0): return base | (imm & 0x1FF) << 12 | rn << 5 | rt                  # unscaled / pre / post: imm9 at bit 12
def lu(base, rt=2, rn=0, imm12=0): return base | imm12 << 10 | rn << 5 | rt                        # unsigned scaled offset
def lp(base, rt=2, rt2=3, rn=0, imm7=0): return base | (imm7 & 0x7F) << 15 | rt2 << 10 | rn << 5 | rt   # pairs
def lr(base, rt=2, rn=0, rm=3): return base | rm << 16 | rn << 5 | rt                              # register offset

# (word, text Capstone must print). x0 is the address register, x1 the value stored, x3 an index
INSNS = [
    (lu(0xF9000000, 1), 'str x1, [x0]'), (lu(0xB9000000, 1), 'str w1, [x0]'), (lu(0x79000000, 1), 'strh w1, [x0]'), (lu(0x39000000, 1), 'strb w1, [x0]'),
    (lu(0xF9400000), 'ldr x2, [x0]'), (lu(0xB9400000), 'ldr w2, [x0]'), (lu(0x79400000), 'ldrh w2, [x0]'), (lu(0x39400000), 'ldrb w2, [x0]'),
    (lu(0x39800000), 'ldrsb x2, [x0]'), (lu(0x39C00000), 'ldrsb w2, [x0]'), (lu(0x79800000), 'ldrsh x2, [x0]'), (lu(0x79C00000), 'ldrsh w2, [x0]'), (lu(0xB9800000), 'ldrsw x2, [x0]'),
    (lu(0xF9400000, imm12=2), 'ldr x2, [x0, #0x10]'), (lu(0xF9000000, 1, imm12=3), 'str x1, [x0, #0x18]'),
    (ls(0xF8400000, imm=-3), 'ldur x2, [x0, #-3]'), (ls(0xF8000000, 1, imm=-5), 'stur x1, [x0, #-5]'), (ls(0xB8800000, imm=-1), 'ldursw x2, [x0, #-1]'), (ls(0x38400000, imm=1), 'ldurb w2, [x0, #1]'),
    (ls(0xF8400C00, imm=16), 'ldr x2, [x0, #0x10]!'), (ls(0xF8000C00, 1, imm=-16), 'str x1, [x0, #-0x10]!'), (ls(0xF8400400, imm=8), 'ldr x2, [x0], #8'), (ls(0xF8000400, 1, imm=-8), 'str x1, [x0], #0xfffffffffffffff8'),
    (ls(0x38400C00, imm=1), 'ldrb w2, [x0, #1]!'), (ls(0x78000400, 1, imm=2), 'strh w1, [x0], #2'),
    (lp(0xA9000000, 1, 3, imm7=2), 'stp x1, x3, [x0, #0x10]'), (lp(0xA9400000, imm7=-2), 'ldp x2, x3, [x0, #-0x10]'), (lp(0xA9800000, 1, 3, imm7=2), 'stp x1, x3, [x0, #0x10]!'),
    (lp(0xA8C00000, imm7=2), 'ldp x2, x3, [x0], #0x10'), (lp(0x29400000, imm7=1), 'ldp w2, w3, [x0, #4]'), (lp(0x29000000, 1, 3, imm7=1), 'stp w1, w3, [x0, #4]'), (lp(0x69400000, imm7=1), 'ldpsw x2, x3, [x0, #4]'),
    (lr(0xF8606800), 'ldr x2, [x0, x3]'), (lr(0xF8607800), 'ldr x2, [x0, x3, lsl #3]'), (lr(0xF8236800, 2), 'str x2, [x0, x3]'),
    (lr(0xB8607800), 'ldr w2, [x0, x3, lsl #2]'), (lr(0x38606800), 'ldrb w2, [x0, x3]'),
]
# addresses: aligned, odd, across a 4-byte and an 8-byte boundary, near both ends of the region (every access of every instruction stays inside it)
ADDRS = [DATA + 0x40, DATA + 0x41, DATA + 0x43, DATA + 0x47, DATA + 0x1000 - 3, DATA + 0x1000 - 8, DATA + 0x100, DATA + DATA_SIZE - 0x100]
VALUES = [0, 0x80, 0xFFFF8001, 0x123456789ABCDEF0, M, 0x8000000000000000]
INDEX = [0, 1, 5, 0x10]


def snippet(w): return struct.pack('<2I', w, RET)


@unittest.skipUnless(READY, 'needs REMILL_BUILD (a Remill build folder), clang++ and unicorn')
class Memory(unittest.TestCase):
    def test_encodings_are_what_the_table_says(self):
        import capstone
        md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
        wrong = []
        for w, want in INSNS:
            i = next(md.disasm(struct.pack('<I', w), ADDR))
            if f'{i.mnemonic} {i.op_str}'.strip() != want: wrong.append(f'{w:#010x}: {i.mnemonic} {i.op_str} != {want}')
        self.assertEqual(wrong, [])

    def test_every_access_equals_the_arm64_reference(self):
        cases = [({'x0': a, 'x1': v, 'x3': ix, 'x2': 0xDEADBEEFCAFEF00D}, nz) for a, v, ix in itertools.product(ADDRS, VALUES, INDEX) for nz in (0, 9)]
        for w, text in INSNS:
            code = snippet(w)
            # the reference maps the snippet at 0: pad so that it sits at ADDR
            image = bytes(ADDR) + code
            aot = aot_lift.Aot([(ADDR, code)])
            try:
                for b, data in MEM.items(): aot.map(b, len(data), data)
                want = ref.run_image(image, ADDR, [(r, n, SP) for r, n in cases], MEM)
                faulted = 0
                for (regs, nz), wr in zip(cases, want):
                    what = f'{text}: x0={regs["x0"]:#x} x1={regs["x1"]:#x} x3={regs["x3"]:#x}'
                    aot.reset()
                    same(self, aot.run(ADDR, dict(regs, x30=ref.STOP), nz, SP), wr, what)   # a case where the reference faults must fault on the AOT code too
                    faulted += 'fault' in wr
                self.assertLess(faulted, len(cases) // 10, f'{text}: too many cases leave the guest memory: they test nothing')
            finally: aot.close()

    def test_stores_reach_the_memory(self):
        """A guard against a comparison that passes because nothing is written: str x1,[x0] changes exactly the 8 bytes"""
        aot = aot_lift.Aot([(ADDR, snippet(INSNS[0][0]))])
        try:
            for b, data in MEM.items(): aot.map(b, len(data), data)
            got = aot.run(ADDR, {'x0': DATA + 0x41, 'x1': 0x0102030405060708, 'x30': ref.STOP}, 0, SP)
            diff = [i for i in range(DATA_SIZE) if got['mem'][DATA][i] != INIT[i]]
            self.assertEqual(got['mem'][DATA][0x41:0x49], bytes.fromhex('0807060504030201'))
            self.assertTrue(diff and min(diff) >= 0x41 and max(diff) < 0x49)
            self.assertEqual((aot.stat('writes'), aot.stat('reads')), (1, 0))
        finally: aot.close()

    def test_access_outside_the_guest_memory_is_an_explicit_error(self):
        cases = [(DATA + DATA_SIZE - 4, 'write', 8, INSNS[0][0]),   # str x1,[x0]: 8 bytes, the last 4 beyond the region
                 (DATA - 1, 'read', 8, INSNS[4][0]),                 # ldr x2,[x0] one byte below it
                 (0x5000, 'read', 1, INSNS[7][0])]                   # ldrb: unmapped
        for addr, what, n, w in cases:
            aot = aot_lift.Aot([(ADDR, snippet(w))])
            try:
                for b, data in MEM.items(): aot.map(b, len(data), data)
                got = aot.run(ADDR, {'x0': addr, 'x1': 1, 'x30': ref.STOP}, 0, SP)
                self.assertIn('fault', got)
                for part in (what, f'{n} bytes', f'{addr:#x}'): self.assertIn(part, got['fault'])
                self.assertIn('fault', ref.run_image(bytes(ADDR) + snippet(w), ADDR, [({'x0': addr, 'x1': 1}, 0, SP)], MEM)[0], 'the reference accepts what the AOT code refuses')
            finally: aot.close()


if __name__ == '__main__':
    unittest.main()


@unittest.skipUnless(READY, 'needs REMILL_BUILD (a Remill build folder), clang++ and unicorn')
class FloatMemory(unittest.TestCase):
    def test_scalar_float_loads_and_stores_equal_the_reference(self):
        """ldr s0 / str s0 and ldr d0 / str d0 copy 4 and 8 bytes through a vector register: the runtime's f32 and f64 intrinsics (missing at first, found by compiled code)"""
        import capstone
        md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
        for ld, st, text in ((0xBD400000, 0xBD000800, ('ldr s0, [x0]', 'str s0, [x0, #8]')), (0xFD400000, 0xFD000800, ('ldr d0, [x0]', 'str d0, [x0, #0x10]'))):
            for w, want in zip((ld, st), text):
                i = next(md.disasm(struct.pack('<I', w), ADDR)); self.assertEqual(f'{i.mnemonic} {i.op_str}'.strip(), want)
            code = struct.pack('<3I', ld, st, RET)
            aot = aot_lift.Aot([(ADDR, code)])
            try:
                for b, data in MEM.items(): aot.map(b, len(data), data)
                for addr in (DATA + 0x40, DATA + 0x43):
                    regs = {'x0': addr}
                    want = ref.run_image(bytes(ADDR) + code, ADDR, [(regs, 0, SP)], MEM)[0]
                    aot.reset(); same(self, aot.run(ADDR, dict(regs, x30=ref.STOP), 0, SP), want, f'{text[0]} at {addr:#x}')
            finally: aot.close()
