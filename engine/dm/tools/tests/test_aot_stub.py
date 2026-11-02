"""A function the translator cannot lift no longer stops the build of its callers (known blocker 1, fork en cours/blocages-connus.md).
  · by default it is still an explicit error: LiftError for the instruction, LinkError for the caller whose callee is missing;
  · with stub_unliftable it gets a stub `sub_<address>`: a direct `bl` to it links and runs it through the JIT, or stops naming the function and the reason when there is no JIT;
  · a function whose code needs a runtime symbol that is not defined is stubbed the same way;
  · with the JIT the result equals the Unicorn ARM64 reference.
Needs REMILL_BUILD, DYNARMIC_DIR, clang++ and unicorn; skipped otherwise."""
import os, struct, sys, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent))
import aot_lift
import arm64_fixture as fx
import arm64_ref as ref
from nso_asm import STP_PRE, LDP_POST
from test_aot_t3 import MEM, SP, READY, same

READY = READY and os.path.exists(os.path.join(os.environ.get('DYNARMIC_DIR', '/nonexistent'), 'build/src/dynarmic/libdynarmic.a'))
RET = aot_lift.RET
G, F, D, P = 0x100, 0x200, 0x500, 0x600   # G: not liftable (ccmn, register form); F calls G; D: fadd, which lifts but needs runtime symbols; P calls D


def code():
    img = bytearray(0x1000)
    def put(at, ws): img[at:at + 4 * len(ws)] = struct.pack('<%dI' % len(ws), *ws)
    put(G, [0xBA408000, fx.ADDR(0, 0, 1), 0x9A9F57E2, RET])                     # ccmn x0, x1, #0, hi ; add x0, x0, x1 ; cset x2, mi
    put(F, [STP_PRE(29, 30, 31, -16), fx.BL(F + 4, G), LDP_POST(29, 30, 31, 16), RET])
    put(D, [0x1E612800, RET])                                                  # fadd d0, d0, d1
    put(P, [STP_PRE(29, 30, 31, -16), fx.BL(P + 4, D), LDP_POST(29, 30, 31, 16), RET])
    return bytes(img)


IMG = code()
MEM_AOT = {**MEM, 0: IMG}
fn = lambda at, n: (at, IMG[at:at + 4 * n])


import contextlib


@contextlib.contextmanager
def needs_missing_symbol():
    """Every function lifted inside the block also refers to a symbol the runtime does not define"""
    real = aot_lift.lift_function
    def lift_function(code, address, ll, entry=None):
        real(code, address, ll, entry)
        with open(ll, 'a') as f: f.write('\ndeclare void @nx_missing_symbol()\ndefine void @nx_user() { call void @nx_missing_symbol() ret void }\n')
    aot_lift.lift_function = lift_function
    try: yield
    finally: aot_lift.lift_function = real


def mapped(a):
    for b, data in MEM_AOT.items(): a.map(b, len(data), data)
    return a


@unittest.skipUnless(READY, 'needs REMILL_BUILD, DYNARMIC_DIR, clang++ and unicorn')
class Stub(unittest.TestCase):
    def test_by_default_it_is_an_explicit_error(self):
        with self.assertRaisesRegex(aot_lift.LiftError, 'ccmn'): aot_lift.Aot([fn(F, 4), fn(G, 4)])
        with self.assertRaisesRegex(aot_lift.LinkError, 'sub_100'): aot_lift.Aot([fn(F, 4)])

    def test_without_jit_it_stops_naming_the_function_and_the_reason(self):
        a = mapped(aot_lift.Aot([fn(F, 4), fn(G, 4)], stub_unliftable=True))
        try:
            self.assertEqual(list(a.unlifted), [G]); self.assertIn('ccmn', a.unlifted[G])
            r = a.run(F, {'x0': 1, 'x1': 2, 'x30': ref.STOP}, 0, SP)
            self.assertIn('0x100', r['fault']); self.assertIn('not translated', r['fault']); self.assertIn('ccmn', r['fault'])
            self.assertEqual(a.stat('unlifted_calls'), 1)
        finally: a.close()

    def test_with_jit_the_stub_runs_the_function_and_equals_the_reference(self):
        a = mapped(aot_lift.Aot([fn(F, 4), fn(G, 4)], jit=True, stub_unliftable=True))
        try:
            cases = [({'x0': x, 'x1': y}, nz) for x in (0, 1, 5, ref.MASK) for y in (0, 3, 1 << 63) for nz in (0, 6, 9)]
            for (regs, nz), want in zip(cases, ref.run_image(IMG, F, [(r, n, SP) for r, n in cases], MEM)):
                self.assertNotIn('fault', want)
                a.reset()
                same(self, a.run(F, dict(regs, x30=ref.STOP), nz, SP), want, f'{regs} nzcv={nz:04b}')
            self.assertEqual(a.stat('unlifted_calls'), len(cases)); self.assertEqual(a.stat('jit_entries'), len(cases))
        finally: a.close()

    def test_entering_the_unliftable_function_directly_goes_to_the_jit_too(self):
        a = mapped(aot_lift.Aot([fn(G, 4)], jit=True, stub_unliftable=True))
        try:
            r = a.run(G, {'x0': 4, 'x1': 5, 'x30': ref.STOP}, 0, SP)   # G is no AOT function: the harness entry falls back to the JIT, as an indirect call does
            self.assertNotIn('fault', r); self.assertEqual((r['x0'], a.stat('jit_entries')), (9, 1))
        finally: a.close()

    def test_a_function_that_lifts_but_needs_an_undefined_runtime_symbol_is_stubbed(self):
        # a lifted function whose IR calls a symbol the runtime does not define (what fadd did before the FPU exception intrinsics existed): the link fails, the function is stubbed, the rest links
        with needs_missing_symbol():
            with self.assertRaisesRegex(aot_lift.LinkError, 'nx_missing_symbol'): aot_lift.Aot([fn(P, 4)])
            a = mapped(aot_lift.Aot([fn(P, 4), fn(D, 2)], jit=True, stub_unliftable=True))
        try:
            self.assertEqual(sorted(a.unlifted), [D, P]); self.assertIn('nx_missing_symbol', a.unlifted[D])
        finally: a.close()

    def test_a_callee_nobody_gave_stays_an_error_even_in_stub_mode(self):
        with self.assertRaisesRegex(aot_lift.LinkError, 'sub_100'): aot_lift.Aot([fn(F, 4)], stub_unliftable=True)

    def test_every_function_unliftable_still_builds(self):
        a = mapped(aot_lift.Aot([fn(G, 4)], stub_unliftable=True))
        a.close()


if __name__ == '__main__': unittest.main()
