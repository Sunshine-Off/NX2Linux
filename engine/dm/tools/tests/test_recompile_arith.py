"""T2: `arith` of the synthetic fixture, lifted to LLVM IR by Remill, compiled to x86_64 by clang, run and compared with the Unicorn ARM64 reference (arm64_ref.py) on the NZCV edge cases.
Needs Remill built (REMILL_BUILD = its build folder, with bin/lift/remill-lift-*), clang++ and unicorn; skipped otherwise. Any intrinsic the lifted code needs that remill_host.cpp does not define
fails the link: a translation that cannot be done is an error, never a silent guess."""
import ctypes, glob, os, struct, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import arm64_fixture as fx
import arm64_ref as ref
from test_arm64_ref import CASES

BUILD = os.environ.get('REMILL_BUILD')
LIFT = next(iter(sorted(glob.glob(os.path.join(BUILD, 'bin/lift/remill-lift-*')))), None) if BUILD else None
try: import unicorn   # noqa: F401
except ImportError: unicorn = None


def lift_function(name, out_ll):
    """Lift fixture function `name` with remill-lift; returns (its address, the lifted function's symbol)"""
    words, ranges, _ = fx.text_layout()
    s, e = ranges[name]
    code = struct.pack('<%dI' % ((e - s) // 4), *words[s // 4:e // 4])
    subprocess.run([LIFT, '--arch', 'aarch64', '--address', hex(s), '--bytes', code.hex(), '--ir_out', out_ll], check=True, capture_output=True, text=True)
    return s, f'sub_{s:x}'


@unittest.skipUnless(LIFT and unicorn, 'needs REMILL_BUILD (a Remill build folder) and unicorn')
class RecompileArith(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        ll = os.path.join(cls.tmp.name, 'arith.ll'); so = os.path.join(cls.tmp.name, 'arith.so')
        cls.addr, sym = lift_function('arith', ll)
        inc = os.environ.get('REMILL_SRC', os.path.join(BUILD, '..'))
        r = subprocess.run(['clang++', '-O2', '-shared', '-fPIC', '-std=c++17', f'-DENTRY={sym}', '-DADDRESS_SIZE_BITS=64', f'-I{inc}/include', f'-I{inc}', str(HERE / 'remill_host.cpp'), ll, '-o', so],
                           capture_output=True, text=True)
        if r.returncode: raise RuntimeError('compile/link of the lifted function failed:\n' + r.stderr[-2000:])
        cls.lib = ctypes.CDLL(so)
        cls.lib.run_lifted.restype = ctypes.c_uint64
        cls.lib.run_lifted.argtypes = [ctypes.c_uint64, ctypes.c_uint64, ctypes.POINTER(ctypes.c_uint8), ctypes.c_uint64]

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def run_x86(self, a, b):
        nzcv = ctypes.c_uint8()
        x0 = self.lib.run_lifted(a, b, ctypes.byref(nzcv), self.addr)
        return x0, nzcv.value

    def test_aot_equals_arm64_reference(self):
        for a, b in CASES:
            want = ref.run('arith', {'x0': a, 'x1': b})
            self.assertEqual(self.run_x86(a, b), (want['x0'], want['nzcv']), (hex(a), hex(b)))

    def test_unlifted_intrinsic_fails_the_link(self):
        """A function that needs an intrinsic the host does not define (here `blr`, an indirect call) is not silently translated"""
        ll = os.path.join(self.tmp.name, 'indirect.ll')
        _, sym = lift_function('indirect', ll)
        inc = os.environ.get('REMILL_SRC', os.path.join(BUILD, '..'))
        r = subprocess.run(['clang++', '-O2', '-shared', '-fPIC', '-std=c++17', f'-DENTRY={sym}', '-DADDRESS_SIZE_BITS=64', f'-I{inc}/include', f'-I{inc}', str(HERE / 'remill_host.cpp'), ll, '-o', os.path.join(self.tmp.name, 'x.so'),
                            '-Wl,--no-undefined'], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0); self.assertIn('__remill_', r.stderr)


if __name__ == '__main__':
    unittest.main()
