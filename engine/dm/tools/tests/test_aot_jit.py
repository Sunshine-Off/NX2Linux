"""T4, the JIT fallback (aot_jit.cpp, dynarmic): code the AOT translator never saw runs in the JIT when AOT code reaches it by an indirect call or jump, and everything the JIT does is counted, logged and limited.
  · AOT, JIT and the Unicorn ARM64 reference agree on registers, NZCV, sp and memory;
  · code generated at run time runs, and recompiles when the guest rewrites it (stores by AOT code, by JIT code and by the harness);
  · JIT code can call an AOT function and come back; an AOT function can call JIT code;
  · budgets (entries, cycles, depth), no code at an address, an access outside guest memory, `svc` and the like are explicit errors naming the address;
  · several host threads use the JIT at once, one dynarmic instance each.
Needs REMILL_BUILD, DYNARMIC_DIR (dynarmic built with CMAKE_POSITION_INDEPENDENT_CODE=ON), clang++ and unicorn; skipped otherwise."""
import itertools, os, struct, sys, threading, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent))
import aot_lift
import arm64_fixture as fx
import arm64_ref as ref
from nso_asm import STP_PRE, LDP_POST, BLR, NOP
from test_aot_t3 import DATA, DATA_SIZE, MEM, SP, READY, same

READY = READY and os.path.exists(os.path.join(os.environ.get('DYNARMIC_DIR', '/nonexistent'), 'build/src/dynarmic/libdynarmic.a'))
RET, M = aot_lift.RET, ref.MASK
SLOT, CALL, SPIN, SVC5, BADREAD, RETONLY, COUNT, PATCHME = 0x240, 0x200, 0x280, 0x2A0, 0x2C0, 0x2E0, 0x300, 0x400   # generated code, in the code region at 0


def b_to(frm, to): return fx.B(frm, to)


def image():
    """The code region: the fixture's text at 0, then the code a game would have generated"""
    words, ranges, _ = fx.text_layout()
    img = bytearray(0x1000)
    def put(at, ws): img[at:at + 4 * len(ws)] = struct.pack('<%dI' % len(ws), *ws)
    put(0, words)
    put(CALL, [STP_PRE(29, 30, 31, -16), fx.BL(CALL + 4, ranges['branch'][0]), LDP_POST(29, 30, 31, 16), RET])   # calls `branch`, which is an AOT function in the second library
    put(SLOT, [NOP, RET])                                                                                           # the word at SLOT is rewritten by patchme
    put(SPIN, [b_to(SPIN, SPIN)])                                                                                   # b .
    put(SVC5, [fx.SVC(5), RET])
    put(BADREAD, [0xF9400080, RET])                                                                                 # ldr x0, [x4]
    put(RETONLY, [RET])
    put(COUNT, [0xC85FFC04, 0x91000484, 0xC805FC04, 0x35000005 | ((-3 & 0x7FFFF) << 5), fx.SUBSI(3, 3, 1), fx.BCOND(COUNT + 20, COUNT, 1), RET])   # x0: counter; x3: times
    # patchme: store the word x1 at [x0] (code!), call it, return
    put(PATCHME, [STP_PRE(29, 30, 31, -16), 0xB9000001, BLR(0), LDP_POST(29, 30, 31, 16), RET])
    return bytes(img), ranges


IMG, RANGES = image()
MEM_AOT = {**MEM, 0: IMG}   # the AOT side maps the code region too: the JIT reads its code from guest memory


def make(aot_names=('indirect',), extra=(), **kw):
    funcs = [(RANGES[n][0], IMG[RANGES[n][0]:RANGES[n][1]]) for n in aot_names] + list(extra)
    a = aot_lift.Aot(funcs, jit=True, **kw)
    for b, data in MEM_AOT.items(): a.map(b, len(data), data)
    return a


@unittest.skipUnless(READY, 'needs REMILL_BUILD, DYNARMIC_DIR (dynarmic built with -DCMAKE_POSITION_INDEPENDENT_CODE=ON), clang++ and unicorn')
class Jit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.patch_code = IMG[PATCHME:PATCHME + 20]
        cls.aot = make(('indirect',), [(PATCHME, cls.patch_code)])      # arith, branch and the generated code are not AOT functions here: the JIT takes them

    @classmethod
    def tearDownClass(cls): cls.aot.close()

    def entry(self): return RANGES['indirect'][0]

    def run_aot(self, a, regs, nz=0, entry=None):
        a.reset()
        return a.run(entry or self.entry(), dict(regs, x30=ref.STOP), nz, SP)

    def reference(self, cases, entry=None):
        return ref.run_image(IMG, entry or self.entry(), [(r, n, SP) for r, n in cases], MEM)

    def test_unknown_functions_run_in_the_jit_and_equal_the_reference(self):
        """indirect: blr x1 (branch) then br x2 (a tail call to arith): neither is an AOT function here"""
        cases = [({'x0': v, 'x1': RANGES['branch'][0], 'x2': RANGES['arith'][0]}, nz) for v in (0, 1, 5, M, 1 << 63) for nz in (0, 6)]
        for (regs, nz), want in zip(cases, self.reference(cases)):
            self.assertNotIn('fault', want)
            same(self, self.run_aot(self.aot, regs, nz), want, f'x0={regs["x0"]:#x} nzcv={nz:04b}')

    def test_every_entry_is_counted_and_logged(self):
        a = make(('indirect',))
        try:
            self.run_aot(a, {'x0': 1, 'x1': RANGES['branch'][0], 'x2': RANGES['arith'][0]})
            self.assertEqual((a.stat('jit_entries'), a.stat('jit_to_aot'), a.stat('fallback_requests')), (2, 0, 2))
            log = a.jit_log().splitlines()
            self.assertEqual(len(log), 2)
            self.assertIn(f'pc={RANGES["branch"][0]:#x} call count=1', log[0]); self.assertIn(f'pc={RANGES["arith"][0]:#x} jump count=1', log[1])
            self.assertGreaterEqual(a.stat('jit_pages'), 1)
        finally: a.close()

    def test_jump_into_the_middle_of_a_function_runs_in_the_jit(self):
        """The AOT build refuses it (test_aot_t3); with the JIT the original code in guest memory runs, and must equal the reference"""
        cases = [({'x0': v, 'x1': RANGES['branch'][0], 'x2': RANGES['arith'][0] + 4, 'x3': 5}, 0) for v in (0, 3, M)]
        for (regs, nz), want in zip(cases, self.reference(cases)):
            self.assertNotIn('fault', want)
            same(self, self.run_aot(self.aot, regs, nz), want, f'x0={regs["x0"]:#x}')

    def test_generated_code_runs_and_is_recompiled_when_rewritten(self):
        """patchme stores the word x1 at SLOT then calls SLOT: the same JIT'd page changes under it, three times"""
        a = self.aot
        for word, want in ((fx.MOVZ(0, 7), 7), (fx.MOVZ(0, 9), 9), (fx.MOVZ(0, 7), 7), (fx.MOVZ(0, 0x123), 0x123)):
            got = a.run(PATCHME, {'x0': SLOT, 'x1': word, 'x30': ref.STOP}, 0, SP)   # no reset: the code written before is still there
            self.assertNotIn('fault', got)
            self.assertEqual(got['x0'], want, 'the JIT ran stale code')
        self.assertGreaterEqual(a.stat('jit_invalidations'), 3)
        a.reset()

    def test_generated_code_equals_the_reference(self):
        for word in (fx.MOVZ(0, 7), fx.MOVZ(0, 9), 0x91000400):   # mov x0,#7 ; mov x0,#9 ; add x0,x0,#1
            regs = {'x0': SLOT, 'x1': word}
            want = self.reference([(regs, 0)], PATCHME)[0]
            self.assertNotIn('fault', want)
            same(self, self.run_aot(self.aot, regs, 0, PATCHME), want, f'word {word:#x}')

    def test_the_harness_rewriting_code_also_invalidates(self):
        """aot_poke (how a harness, and later the loader, writes guest memory) tells the JIT too: SLOT is rewritten between calls, with no store by guest code"""
        a = make(('indirect',))
        try:
            for imm in (11, 12, 13):
                a.lib.aot_poke(SLOT, struct.pack('<I', fx.MOVZ(0, imm)), 4)
                self.assertEqual(a.run(self.entry(), {'x1': SLOT, 'x2': RETONLY, 'x30': ref.STOP}, 0, SP)['x0'], imm)
            self.assertGreaterEqual(a.stat('jit_invalidations'), 2)
        finally: a.close()

    def test_jit_code_can_call_an_aot_function_and_come_back(self):
        """CALL (generated) does `bl branch`; here `branch` and `arith` are AOT functions, so that bl leaves the JIT for the AOT code and returns"""
        a = make(('indirect', 'branch', 'arith'))
        try:
            cases = [({'x0': v, 'x1': CALL, 'x2': RANGES['arith'][0]}, 0) for v in (0, 1, 7, M)]
            for (regs, nz), want in zip(cases, self.reference(cases)):
                self.assertNotIn('fault', want)
                a.lib.aot_stats_reset()
                same(self, self.run_aot(a, regs, nz), want, f'x0={regs["x0"]:#x}')
                self.assertEqual((a.stat('jit_entries'), a.stat('jit_to_aot')), (1, 1))
            self.assertIn('jit->aot count=', a.jit_log())
        finally: a.close()

    def test_a_cycle_budget_stops_a_loop_with_no_exit(self):
        a = make(('indirect',), jit_ticks=200_000)
        try:
            got = self.run_aot(a, {'x1': SPIN, 'x2': RANGES['branch'][0]})
            self.assertIn('JIT budget exceeded', got['fault']); self.assertIn(f'{SPIN:#x}', got['fault'])
        finally: a.close()

    def test_an_entry_budget_limits_the_jit(self):
        a = make(('indirect',), jit_entries=1)
        try:
            got = self.run_aot(a, {'x1': RANGES['branch'][0], 'x2': RANGES['arith'][0]})   # blr is the first entry, br the second
            self.assertIn('JIT budget exceeded', got['fault']); self.assertIn('1 entries', got['fault']); self.assertIn(f'{RANGES["arith"][0]:#x}', got['fault'])
            self.assertEqual(a.stat('jit_entries'), 1)
        finally: a.close()

    def test_no_code_at_the_target_is_an_explicit_error(self):
        got = self.run_aot(self.aot, {'x1': 0x12345678, 'x2': RANGES['branch'][0]})
        self.assertIn('0x12345678', got['fault']); self.assertIn('no code to run', got['fault'])

    def test_access_outside_guest_memory_in_jit_code(self):
        ok = self.run_aot(self.aot, {'x1': BADREAD, 'x2': RETONLY, 'x4': DATA})   # a control: the same code reading mapped memory
        self.assertNotIn('fault', ok)
        got = self.run_aot(self.aot, {'x1': BADREAD, 'x2': RETONLY, 'x4': 0x5000})
        self.assertIn('JIT: read of 8 bytes at 0x5000', got['fault'])

    def test_svc_in_jit_code_without_a_handler_is_an_explicit_error(self):
        got = self.run_aot(self.aot, {'x1': SVC5, 'x2': RANGES['branch'][0]})
        self.assertIn('svc #5', got['fault']); self.assertIn('no Horizon service handler', got['fault'])

    def test_threads_use_the_jit_at_once(self):
        a = make(('indirect',))
        try:
            want = a.run(self.entry(), {'x0': 3, 'x1': RANGES['branch'][0], 'x2': RANGES['arith'][0], 'x30': ref.STOP}, 0, SP)['x0']
            bad = []
            def work():
                for _ in range(200):
                    r = a.run(self.entry(), {'x0': 3, 'x1': RANGES['branch'][0], 'x2': RANGES['arith'][0], 'x30': ref.STOP}, 0, SP)
                    if r.get('x0') != want: bad.append(r.get('fault') or r['x0'])
            ts = [threading.Thread(target=work) for _ in range(4)]
            for t in ts: t.start()
            for t in ts: t.join()
            self.assertEqual(bad, [])
            self.assertEqual(a.stat('jit_entries'), 2 + 4 * 200 * 2)   # the reference run enters twice (blr, br), then each of 800 runs does
        finally: a.close()

    def test_exclusive_counter_loop_in_the_jit_loses_no_update(self):
        """The ldaxr/stlxr loop at COUNT is JIT code: dynarmic's exclusive monitor and the compare-and-swap callbacks must keep four threads exact"""
        a = make(('indirect',))
        try:
            start = struct.unpack_from('<Q', MEM[DATA], 0x80)[0]; errs = []
            def work():
                r = a.run(self.entry(), {'x0': DATA + 0x80, 'x3': 20000, 'x1': COUNT, 'x2': RANGES['branch'][0], 'x30': ref.STOP}, 0, SP)
                if 'fault' in r: errs.append(r['fault'])
            ts = [threading.Thread(target=work) for _ in range(4)]
            for t in ts: t.start()
            for t in ts: t.join()
            self.assertEqual(errs, [])
            self.assertEqual(struct.unpack_from('<Q', a.peek(DATA, DATA_SIZE), 0x80)[0] - start, 4 * 20000)
        finally: a.close()


if __name__ == '__main__':
    unittest.main()
