"""T2, instruction by instruction: the flag-setting and flag-reading instructions, in 32 and 64 bits, lifted by Remill, compiled to x86_64 and compared with the Unicorn ARM64 reference on every pair of edge values
and every one of the 16 NZCV states going in (the carry and the condition decide adc, sbc, ccmp, csel). Each word is checked against Capstone first, so a wrong encoding is not mistaken for a wrong translation.
Needs REMILL_BUILD, clang++ and unicorn; skipped otherwise."""
import ctypes, itertools, os, struct, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import arm64_ref as ref
sys.path.insert(0, str(HERE.parent))
from aot_lift import lift, LiftError   # noqa: E402  (the lifter and its error live in the tool)
from test_recompile_arith import BUILD, LIFT, unicorn

RET = 0xD65F03C0
M = ref.MASK
VALUES = [0, 1, 2, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF, 0x7FFFFFFFFFFFFFFF, 0x8000000000000000, M]
COND = {'eq': 0, 'ne': 1, 'cs': 2, 'cc': 3, 'mi': 4, 'pl': 5, 'vs': 6, 'vc': 7, 'hi': 8, 'ls': 9, 'ge': 10, 'lt': 11, 'gt': 12, 'le': 13}
# x2 = op(x0, x1) unless the instruction has no destination; encodings from the Arm ARM, each checked against Capstone's text
def rrr(base, rd=2, rn=0, rm=1): return base | rm << 16 | rn << 5 | rd
def ccmp(base, cond, nzcv, rn=0, rm=1): return base | rm << 16 | COND[cond] << 12 | rn << 5 | nzcv
def csel(base, cond): return base | 1 << 16 | COND[cond] << 12 | 0 << 5 | 2
INSNS = [  # (word, expected disassembly)
    (rrr(0xAB000000), 'adds x2, x0, x1'), (rrr(0x2B000000), 'adds w2, w0, w1'), (rrr(0xEB000000), 'subs x2, x0, x1'), (rrr(0x6B000000), 'subs w2, w0, w1'),
    (rrr(0xEB00001F, 31), 'cmp x0, x1'), (rrr(0x6B00001F, 31), 'cmp w0, w1'), (rrr(0xAB00001F, 31), 'cmn x0, x1'), (rrr(0x2B00001F, 31), 'cmn w0, w1'),
    (rrr(0xDA000000), 'sbc x2, x0, x1'), (rrr(0x5A000000), 'sbc w2, w0, w1'), (rrr(0xFA000000), 'sbcs x2, x0, x1'), (rrr(0x7A000000), 'sbcs w2, w0, w1'),
    (rrr(0x9A000000), 'adc x2, x0, x1'), (rrr(0x1A000000), 'adc w2, w0, w1'), (rrr(0xBA000000), 'adcs x2, x0, x1'), (rrr(0x3A000000), 'adcs w2, w0, w1'),
    (rrr(0xEA000000), 'ands x2, x0, x1'), (rrr(0x6A000000), 'ands w2, w0, w1'), (rrr(0xEA00001F, 31), 'tst x0, x1'),
    (0xEB0003E2, 'negs x2, x0'), (0x6B0003E2, 'negs w2, w0'),
    (ccmp(0xFA400000, 'eq', 5), 'ccmp x0, x1, #5, eq'), (ccmp(0x7A400000, 'lt', 2), 'ccmp w0, w1, #2, lt'),
    (ccmp(0xFA400800, 'ne', 3, rm=3), 'ccmp x0, #3, #3, ne'), (ccmp(0x7A400800, 'ge', 9, rm=31), 'ccmp w0, #0x1f, #9, ge'),
    (ccmp(0xBA400800, 'hi', 0, rm=3), 'ccmn x0, #3, #0, hi'), (ccmp(0x3A400800, 'vs', 15, rm=7), 'ccmn w0, #7, #0xf, vs'),
    (csel(0x9A800000, 'lt'), 'csel x2, x0, x1, lt'), (csel(0x1A800000, 'ge'), 'csel w2, w0, w1, ge'), (csel(0x9A800400, 'hi'), 'csinc x2, x0, x1, hi'),
]

# Remill (commit 56918a8) has no semantics for these: its lifted code calls __remill_error. The translator must stop on them, naming the instruction (LiftError), never run what it did not translate.
# adc and adcs were here too until the fork's Remill patch (fork en cours/remill-patches/0001-aarch64-add-adc-adcs.patch) gave Remill their semantics and decoders
UNSUPPORTED = [(ccmp(0xBA400000, 'hi', 0), 'ccmn x0, x1, #0, hi'), (ccmp(0x3A400000, 'vs', 15), 'ccmn w0, w1, #0xf, vs')]


def build_lib(word, tmp):
    ll, so = os.path.join(tmp, f'i{word:08x}.ll'), os.path.join(tmp, f'i{word:08x}.so')   # one path per word: dlopen of a path already loaded returns the old library
    lift(word, ll)
    inc = os.environ.get('REMILL_SRC', os.path.join(BUILD, '..'))
    r = subprocess.run(['clang++', '-O2', '-shared', '-fPIC', '-std=c++17', '-DENTRY=sub_1000', '-DADDRESS_SIZE_BITS=64', f'-I{inc}/include', f'-I{inc}', str(HERE / 'remill_host.cpp'), ll, '-o', so, '-Wl,--no-undefined'],
                       capture_output=True, text=True)
    if r.returncode: raise RuntimeError(f'link of {word:#010x} failed:\n' + r.stderr[-1500:])
    lib = ctypes.CDLL(so)
    lib.run_state.argtypes = [ctypes.POINTER(ctypes.c_uint64), ctypes.c_uint8, ctypes.POINTER(ctypes.c_uint8), ctypes.c_uint64]
    return lib


@unittest.skipUnless(LIFT and unicorn, 'needs REMILL_BUILD (a Remill build folder) and unicorn')
class Instructions(unittest.TestCase):
    def test_encodings_are_what_the_table_says(self):
        import capstone
        md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
        for w, want in INSNS + UNSUPPORTED:
            i = next(md.disasm(struct.pack('<I', w), 0))
            self.assertEqual(f'{i.mnemonic} {i.op_str}'.strip(), want, hex(w))

    def test_every_instruction_equals_the_arm64_reference(self):
        cases = [({'x0': a, 'x1': b, 'x2': 0xDEADBEEFCAFEF00D, 'x3': 0x1122334455667788}, nzcv) for a, b in itertools.product(VALUES, VALUES) for nzcv in range(16)]
        with tempfile.TemporaryDirectory() as tmp:
            for w, text in INSNS:
                want = ref.run_words([w, RET], cases)
                lib = build_lib(w, tmp)
                for (regs, nzcv), (wx, wn) in zip(cases, want):
                    x = (ctypes.c_uint64 * 4)(*(regs[f'x{i}'] for i in range(4))); out = ctypes.c_uint8()
                    lib.run_state(x, nzcv, ctypes.byref(out), 0x1000)
                    self.assertEqual(([x[i] for i in range(4)], out.value), ([wx[f'x{i}'] for i in range(4)], wn),
                                     f'{text}: x0={regs["x0"]:#x} x1={regs["x1"]:#x} nzcv in={nzcv:04b}')

    def test_unsupported_instruction_is_an_explicit_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            for w, text in UNSUPPORTED:
                with self.assertRaises(LiftError, msg=f'{text}: if Remill lifts it now, move it to INSNS') as c: lift(w, os.path.join(tmp, 'u.ll'))
                self.assertIn(f'{w:#010x}', str(c.exception)); self.assertIn('0x1000', str(c.exception))


if __name__ == '__main__':
    unittest.main()
