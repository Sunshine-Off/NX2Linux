"""The translation cache (aot_lift.Aot(cache=...)): what was lifted and compiled once is kept in a folder and not done again.
  · the first build misses on every function and fills the cache; an identical second build loads the finished library without lifting, compiling or linking, and gives the same results;
  · changing one function's bytes, or its address, misses on that function only;
  · a function Remill cannot lift is remembered with its reason (no second attempt), and a function that needs a runtime symbol nobody defines is stubbed from the symbols recorded beside its object;
  · the toolchain is part of every key.
Needs REMILL_BUILD, clang++ and unicorn; skipped otherwise."""
import struct, sys, tempfile, time, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent))
import aot_lift
import arm64_fixture as fx
import arm64_ref as ref
from test_aot_t3 import DATA, MEM, SP, READY, fixture
from test_aot_stub import needs_missing_symbol

G, D = 0x500, 0x600   # an unliftable function (ccmn, register form) and a plain one that the tests make need a symbol nobody defines


@unittest.skipUnless(READY, 'needs REMILL_BUILD, clang++ and unicorn')
class Cache(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code, cls.ranges = fixture()

    def setUp(self):   # a cache of its own per test: hits and misses depend on what the test before left
        self.dir = tempfile.TemporaryDirectory()
        self.cache = str(Path(self.dir.name) / 'cache')

    def tearDown(self): self.dir.cleanup()

    def funcs(self, names=('arith', 'branch', 'loop', 'caller')):
        return [(self.ranges[n][0], self.code[self.ranges[n][0]:self.ranges[n][1]]) for n in names]

    def build(self, functions, **kw):
        a = aot_lift.Aot(functions, cache=self.cache, **kw)
        for b, data in MEM.items(): a.map(b, len(data), data)
        return a

    def test_the_second_build_hits_and_gives_the_same_results(self):
        fs = self.funcs()
        a = self.build(fs)
        self.assertEqual((a.cache_hits, a.cache_misses), (0, 4))
        want = a.run(self.ranges['caller'][0], {'x0': 7, 'x1': 3, 'x30': ref.STOP}, 0, SP); a.close()
        t = time.time(); b = self.build(fs); fast = time.time() - t
        try:
            self.assertEqual((b.cache_hits, b.cache_misses), (4, 0))
            self.assertEqual(b.run(self.ranges['caller'][0], {'x0': 7, 'x1': 3, 'x30': ref.STOP}, 0, SP), want)
        finally: b.close()
        self.assertLess(fast, 3, 'a build with everything cached should only load a library')

    def test_a_changed_function_or_address_misses_on_that_function_only(self):
        fs = self.funcs(('arith', 'branch', 'loop'))
        self.build(fs).close()
        changed = [fs[0], (fs[1][0], fs[1][1][:4] + struct.pack('<I', 0xD503201F) + fs[1][1][8:]), fs[2]]   # branch with its second word replaced by a nop
        a = self.build(changed); self.assertEqual((a.cache_hits, a.cache_misses), (2, 1)); a.close()
        b = self.build([fs[0], fs[1], (fs[2][0] + 0x1000, fs[2][1])]); self.assertEqual((b.cache_hits, b.cache_misses), (2, 1)); b.close()   # the same bytes elsewhere lift differently (adrp, bl)

    def test_an_unliftable_function_is_remembered_with_its_reason(self):
        g = (G, struct.pack('<4I', 0xBA408000, fx.ADDR(0, 0, 1), 0x9A9F57E2, aot_lift.RET))
        a = self.build([g] + self.funcs(('arith',)), stub_unliftable=True)
        self.assertEqual((a.cache_hits, a.cache_misses), (0, 2)); why = a.unlifted[G]; a.close()
        b = self.build([g] + self.funcs(('arith',)), stub_unliftable=True)
        try: self.assertEqual((b.cache_hits, b.unlifted[G]), (2, why))
        finally: b.close()
        with self.assertRaises(aot_lift.LiftError) as c: self.build([g], stub_unliftable=False)   # not stubbing: the remembered reason is raised, still explicit
        self.assertIn('ccmn', str(c.exception))

    def test_a_function_that_needs_an_undefined_runtime_symbol_is_stubbed_from_the_recorded_symbols(self):
        d = (D, struct.pack('<2I', 0x91000400, aot_lift.RET))   # add x0, x0, #1 ; ret
        fs = [d] + self.funcs(('arith',))
        with needs_missing_symbol():
            a = self.build(fs, stub_unliftable=True)
            self.assertEqual(sorted(a.unlifted), sorted([D, self.ranges['arith'][0]])); a.close()
        self.assertTrue(list(Path(self.cache).rglob('*.syms')))
        more = fs + self.funcs(('branch',))   # one function more, so that the library is not cached as a whole but both earlier objects are
        with needs_missing_symbol():
            b = self.build(more, stub_unliftable=True)
        try: self.assertEqual((b.cache_hits, b.cache_misses, sorted(b.unlifted)), (2, 1, sorted([D, self.ranges['arith'][0], self.ranges['branch'][0]])))   # the first two were stubbed from their recorded .syms
        finally: b.close()

    def test_the_toolchain_is_part_of_the_key(self):
        before = aot_lift.toolchain_key()
        aot_lift._toolkey = None
        try:
            aot_lift.MAX_LIFT_BYTES, old = 1234, aot_lift.MAX_LIFT_BYTES
            self.assertNotEqual(aot_lift.toolchain_key(), before)
        finally: aot_lift.MAX_LIFT_BYTES = old; aot_lift._toolkey = None


if __name__ == '__main__': unittest.main()
