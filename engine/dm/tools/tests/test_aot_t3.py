"""T3: branches, memory and calls. The synthetic fixture's functions (arm64_fixture.py) lifted by Remill and compiled to x86_64 by aot_lift.py, run against the Unicorn ARM64 reference: every register, NZCV, the
stack pointer and the guest memory must be identical. Indirect targets that are not AOT functions, accesses outside the guest memory and the instruction classes not translated yet are explicit errors.
Needs REMILL_BUILD (a Remill build with the ADC/ADCS patch for the instruction tests), clang++ and unicorn; skipped otherwise."""
import itertools, struct, sys, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent))
import aot_lift
import arm64_fixture as fx
import arm64_ref as ref

try: import unicorn   # noqa: F401
except ImportError: unicorn = None
READY = aot_lift.lifter() and unicorn

M = ref.MASK
DATA, DATA_SIZE, STACK, STACK_SIZE = 0x100000, 0x2000, 0x200000, 0x10000
SP = STACK + STACK_SIZE
VALUES = [0, 1, 2, 0x7FFFFFFF, 0x80000000, M, 0x7FFFFFFFFFFFFFFF, 0x8000000000000000]
INIT = bytes((i * 131 + 7) & 0xFF for i in range(DATA_SIZE))   # fixed, uneven contents: a load of the wrong bytes shows
MEM = {DATA: INIT, STACK: bytes(STACK_SIZE)}


def fixture():
    words, ranges, has_fde = fx.text_layout()
    code = struct.pack('<%dI' % len(words), *words)
    return code, ranges


def new_aot(functions):
    a = aot_lift.Aot(functions)
    for b, data in MEM.items(): a.map(b, len(data), data)
    return a


def same(test, a, b, what):
    """Two run results equal, field by field, so a failure names the register or the memory that differs"""
    test.assertEqual(('fault' in a), ('fault' in b), f'{what}: one run faulted, the other did not: {a.get("fault")!r} / {b.get("fault")!r}')
    if 'fault' in a: return
    for k in [f'x{i}' for i in range(31)] + ['sp', 'nzcv']: test.assertEqual(a[k], b[k], f'{what}: {k}: {a[k]:#x} != {b[k]:#x}')
    for base in b['mem']:   # the regions the reference knows (the AOT side may map more, such as a code region)
        if a['mem'][base] != b['mem'][base]:
            off = next(i for i, (p, q) in enumerate(zip(a['mem'][base], b['mem'][base])) if p != q)
            test.fail(f'{what}: memory differs at {base + off:#x}: {a["mem"][base][off]:#04x} != {b["mem"][base][off]:#04x}')


@unittest.skipUnless(READY, 'needs REMILL_BUILD (a Remill build folder), clang++ and unicorn')
class Functions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code, cls.ranges = fixture()
        cls.fns = [(s, cls.code[s:e]) for n, (s, e) in cls.ranges.items() if n not in ('svc_setheap', 'tls')]   # svc and the thread registers are T5
        cls.aot = new_aot(cls.fns)

    @classmethod
    def tearDownClass(cls): cls.aot.close()

    def compare(self, name, cases):
        """Each case on the reference and on the AOT code; the reference must complete (a case where both fault would pass `same` for nothing)"""
        entry = self.ranges[name][0]
        want = ref.run_image(self.code, entry, [(r, n, SP) for r, n in cases], MEM)
        for (regs, nzcv), w in zip(cases, want):
            what = f'{name}({", ".join(f"{k}={v:#x}" for k, v in regs.items())}) nzcv={nzcv:04b}'
            self.assertNotIn('fault', w, f'{what}: the reference itself faulted: a bad test case')
            self.aot.reset()
            same(self, self.aot.run(entry, dict(regs, x30=ref.STOP), nzcv, SP), w, what)

    def test_conditional_branch(self):
        self.compare('branch', [({'x0': v, 'x1': 9}, n) for v in VALUES for n in (0, 4, 15)])

    def test_loop_with_a_back_edge(self):
        self.compare('loop', [({'x0': v}, 0) for v in (0, 1, 2, 7, 50, 1000)])

    def test_nested_direct_calls_and_the_stack(self):
        """caller: stp x29,x30 to the stack, bl arith, bl branch, ldp: the saved registers and the memory they leave behind must match"""
        self.compare('caller', [({'x0': a, 'x1': b, 'x29': 0xAAAA5555AAAA5555}, 0) for a, b in itertools.product(VALUES, VALUES)])

    def test_indirect_call_and_jump_to_known_functions(self):
        """indirect: blr x1 then br x2 (a tail call). The call target must leave x2 alone, which arith does not: it is `branch`; the jump goes to each function"""
        r = self.ranges
        for f2 in ('branch', 'arith', 'loop'):
            vals = (0, 1, 5) if f2 == 'loop' else (0, 1, 5, M)   # loop counts x0 down: a huge count would run for ever
            self.compare('indirect', [({'x0': v, 'x1': r['branch'][0], 'x2': r[f2][0]}, 0) for v in vals])

    def test_indirect_targets_are_counted(self):
        self.aot.lib.aot_stats_reset()
        got = self.aot.run(self.ranges['indirect'][0], {'x0': 1, 'x1': self.ranges['branch'][0], 'x2': self.ranges['arith'][0], 'x30': ref.STOP}, 0, SP)
        self.assertNotIn('fault', got)
        self.assertEqual((self.aot.stat('indirect_calls'), self.aot.stat('indirect_jumps'), self.aot.stat('fallback_requests')), (1, 1, 0))

    def test_unknown_indirect_call_is_an_explicit_error_and_a_fallback_request(self):
        self.aot.lib.aot_stats_reset()
        got = self.aot.run(self.ranges['indirect'][0], {'x1': 0x12345678, 'x2': self.ranges['branch'][0], 'x30': ref.STOP}, 0, SP)
        self.assertIn('indirect call to 0x12345678', got['fault']); self.assertIn('no JIT fallback', got['fault'])
        self.assertEqual(self.aot.stat('fallback_requests'), 1)
        # the reference also cannot go there: it fetches from unmapped memory
        self.assertIn('fault', ref.run_image(self.code, self.ranges['indirect'][0], [({'x1': 0x12345678, 'x2': 0}, 0, SP)], MEM)[0])

    def test_jump_into_the_middle_of_a_function_is_an_explicit_error(self):
        mid = self.ranges['arith'][0] + 4
        got = self.aot.run(self.ranges['indirect'][0], {'x1': self.ranges['branch'][0], 'x2': mid, 'x30': ref.STOP}, 0, SP)
        self.assertIn(f'indirect jump to {mid:#x}', got['fault']); self.assertIn('not the entry', got['fault'])

    def test_svc_without_a_service_handler_is_an_explicit_error(self):
        """`svc` lifts (Remill patch 0003); with no Horizon service handler set it stops naming the number and the address of the instruction. test_aot_svc.py connects the services"""
        s, e = self.ranges['svc_setheap']
        a = aot_lift.Aot([(s, self.code[s:e])])
        try:
            got = a.run(s, {'x30': ref.STOP}, 0, SP)
            self.assertIn('svc #1', got['fault']); self.assertIn(f'{s:#x}', got['fault']); self.assertIn('no Horizon service handler', got['fault'])
        finally: a.close()


if __name__ == '__main__':
    unittest.main()
