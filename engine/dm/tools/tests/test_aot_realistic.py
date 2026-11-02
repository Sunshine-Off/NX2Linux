"""Code a real compiler made (GCC for AArch64, from realistic_sample.c: my own C, no game data), lifted and run, against the Unicorn reference. It pins what works and, as important, what does not yet:
  · loops, memory, byte loads, 128-bit multiplies, float comparisons and exclusive loops (32 and 64 bits) equal the reference;
  · a switch compiled to a jump table is resolved at translation time (jump_tables.py): its cases are entries of their own, a dispatch stays in the recompiled code, with no JIT entry and no error;
  · the instructions Remill cannot lift today (vector and some scalar FP, byte and halfword exclusives) are listed, so that a Remill change that fixes one shows up here.
Needs aarch64-linux-gnu-gcc, REMILL_BUILD, DYNARMIC_DIR, clang++ and unicorn; skipped otherwise."""
import os, shutil, struct, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent))
import aot_lift
import arm64_ref as ref
import jump_tables
from test_aot_t3 import DATA, SP, MEM, same
from test_aot_jit import READY as JIT_READY

GCC = shutil.which('aarch64-linux-gnu-gcc')
BASE, RODATA = 0x10000, 0x12000
GLOBALS = {0x2F000: bytes(0x2000)}   # the sample's .bss (g_lock, g_counter, g_flag at 0x2ffe8..0x30000), mapped for the AOT code and the reference alike
MEM_ALL = {**MEM, **GLOBALS}
NAMES = ('sw', 'sw2', 'hash', 'sort', 'strlen_', 'mul128', 'cmpf', 'lock', 'unlock', 'inc', 'cas', 'inc8', 'cas16', 'objcopy', 'conv', 'vadd', 'bits', 'scale')
LIFTS_NOT = set()   # every function of the sample lifts since the vector instructions (patch 0069): the vector multiply by element, cnt, the Q stores and the scalar conversions
INSNS_NOT = set()   # and so does every instruction


def build(tmp):
    elf = os.path.join(tmp, 'sample.elf'); flat = os.path.join(tmp, 'flat.bin')
    subprocess.run([GCC, '-O2', '-ffreestanding', '-fno-stack-protector', '-fno-pic', '-march=armv8-a', '-mno-outline-atomics', '-nostdlib', '-static', f'-Wl,-Ttext={BASE:#x}',
                    f'-Wl,--section-start=.rodata={RODATA:#x}', '-Wl,-e,sw', '-o', elf, str(HERE / 'realistic_sample.c')], check=True, capture_output=True)
    subprocess.run([GCC.replace('gcc', 'objcopy'), '-O', 'binary', '--gap-fill=0', '-j', '.text', '-j', '.rodata', elf, flat], check=True)
    nm = subprocess.run([GCC.replace('gcc', 'nm'), '-n', elf], check=True, capture_output=True, text=True).stdout.split('\n')
    syms = sorted((int(l.split()[0], 16), l.split()[2]) for l in nm if len(l.split()) == 3 and l.split()[1] == 'T')
    text_end = BASE + int(subprocess.run([GCC.replace('gcc', 'readelf'), '-S', '-W', elf], check=True, capture_output=True, text=True).stdout.split('.text')[1].split()[3], 16)   # the .text section's size
    ends = [a for a, _ in syms[1:]] + [text_end]
    return Path(flat).read_bytes(), {n: (a, e) for (a, n), e in zip(syms, ends)}, text_end


@unittest.skipUnless(GCC and aot_lift.lifter(), 'needs aarch64-linux-gnu-gcc and REMILL_BUILD')
class Realistic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.flat, cls.fn, cls.text_end = build(cls.tmp.name)
        cls.image = bytes(BASE) + cls.flat

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def code(self, n): a, e = self.fn[n]; return a, self.flat[a - BASE:e - BASE]

    def test_which_functions_and_instructions_lift(self):
        import capstone
        md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
        no, ll = set(), os.path.join(self.tmp.name, 'f.ll')
        for n in self.fn:
            try: aot_lift.lift_function(self.code(n)[1], self.code(n)[0], ll)
            except aot_lift.LiftError: no.add(n)
        self.assertEqual(no, LIFTS_NOT, 'the set of functions Remill cannot lift changed: update LIFTS_NOT (a fix is good news)')
        bad = set()
        for off in range(0, self.text_end - BASE, 4):
            w = struct.unpack_from('<I', self.flat, off)[0]
            try: aot_lift.lift(w, ll, 0x1000)
            except aot_lift.LiftError: bad.add(next(md.disasm(struct.pack('<I', w), 0)).mnemonic)
        self.assertEqual(bad, INSNS_NOT)

    def cases(self):
        """The cases of every switch of the sample, as the entries the manifest of a module lists (entry, the function's bytes, the function's start)"""
        out = []
        for n in NAMES:
            s, e = self.fn[n]
            for jt in jump_tables.find_jump_tables(self.image, s, e): out += [(t, self.flat[s - BASE:e - BASE], s) for t in jt.targets]
        return out

    def aot(self, jit, tables=True):
        a = aot_lift.Aot([self.code(n) for n in NAMES] + (self.cases() if tables else []), jit=jit)
        for b, d in MEM_ALL.items(): a.map(b, len(d), d)
        a.map(BASE, 0x3000, self.flat.ljust(0x3000, b'\0'))
        return a

    def compare(self, a, name, cases, expect_errors=0):
        want = ref.run_image(self.image, self.fn[name][0], [(r, 0, SP) for r in cases], MEM_ALL)
        errors = 0
        for r, w in zip(cases, want):
            self.assertNotIn('fault', w)
            a.reset(); got = a.run(self.fn[name][0], dict(r, x30=ref.STOP), 0, SP)
            if 'fault' in got: errors += 1; self.assertIn('indirect jump', got['fault']); continue
            same(self, got, w, f'{name}({r})')
        self.assertEqual(errors, expect_errors, name)

    def check_all(self, a, sw2_errors):
        self.compare(a, 'sw2', [{'x0': x, 'x1': 7} for x in range(-1, 23)], sw2_errors)
        self.compare(a, 'sw', [{'x0': x, 'x1': 5} for x in range(-1, 10)])
        self.compare(a, 'hash', [{'x0': DATA + 3, 'x1': n} for n in (0, 1, 5, 100)])
        self.compare(a, 'sort', [{'x0': DATA + 8, 'x1': n} for n in (0, 1, 2, 17)])
        self.compare(a, 'strlen_', [{'x0': DATA + o} for o in (0, 1, 9)])
        self.compare(a, 'mul128', [{'x0': x, 'x1': y, 'x2': DATA + 16} for x, y in ((3, 5), (2 ** 63, 2 ** 63), (2 ** 64 - 1, 2 ** 64 - 1))])
        self.compare(a, 'cmpf', [{'x0': DATA + o, 'x1': DATA + o + 8} for o in (0, 4, 40)])
        self.compare(a, 'cas', [{'x0': DATA + 32, 'x1': e, 'x2': 9} for e in (0, 5)])
        self.compare(a, 'inc', [{}])
        self.compare(a, 'bits', [{'x0': x} for x in (1, 0x80, 0xF0F0, 0xFFFFFFFF, 0x12345678)])   # clz, ctz, popcount (cnt) and bswap
        self.compare(a, 'inc8', [{'x0': DATA + o} for o in (40, 41, 255)])
        self.compare(a, 'cas16', [{'x0': DATA + 34, 'x1': e, 'x2': 9} for e in (0, 0x2321, 0x2322)])   # 16-bit exclusives
        self.compare(a, 'objcopy', [{'x0': DATA + 0x100, 'x1': DATA + o} for o in (0x20, 0x31)])        # copies a struct with Q stores/loads

    def test_the_tables_are_found(self):
        tables = [jt for n in NAMES for jt in jump_tables.find_jump_tables(self.image, *self.fn[n])]
        self.assertEqual([len(t.targets) for t in tables], [20], 'GCC compiles sw to compares and sw2 to one table of 20 cases (the inputs 0..19 that reach it): update if the compiler changed')

    def test_aot_only_equals_the_reference_including_the_switches(self):
        a = self.aot(False)
        try:
            self.check_all(a, sw2_errors=0)   # every input, the 20 that reach the table included
            self.assertEqual(a.stat('fallback_requests'), 0, 'a dispatch through the table must stay in the recompiled code')
            self.assertGreater(a.stat('indirect_jumps'), 0)
        finally: a.close()

    def test_without_the_tables_it_is_an_explicit_error_as_before(self):
        a = self.aot(False, tables=False)
        try:
            self.check_all(a, sw2_errors=20)
            self.assertEqual(a.stat('fallback_requests'), 20)
        finally: a.close()

    @unittest.skipUnless(JIT_READY, 'needs DYNARMIC_DIR')
    def test_with_the_jit_no_switch_enters_it(self):
        a = self.aot(True)
        try:
            self.check_all(a, sw2_errors=0)
            self.assertEqual(a.stat('jit_entries'), 0, 'jump tables are resolved at translation time: the JIT is not needed for a switch')
        finally: a.close()

    @unittest.skipUnless(JIT_READY, 'needs DYNARMIC_DIR')
    def test_with_the_jit_but_no_tables_every_switch_enters_it(self):
        a = self.aot(True, tables=False)
        try:
            self.check_all(a, sw2_errors=0)
            self.assertEqual(a.stat('jit_entries'), 20)
        finally: a.close()

if __name__ == '__main__':
    unittest.main()
