"""A direct `b` out of a function (a tail call) and a fall-through into the next entry (a function split at an inner entry) continue at the AOT entry there (NX2Linux S7: found on a real
module, where sdk functions end in `b other` and `bl` into their own range splits them). Synthetic code, no game data. Needs REMILL_BUILD and clang++; skipped otherwise."""
import struct, sys, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent))
import aot_lift
from test_aot_t3 import READY

RET = 0xD65F03C0
STOP = 0xFFFF0000


def words(*w): return struct.pack(f'<{len(w)}I', *w)


@unittest.skipUnless(READY, 'needs REMILL_BUILD, clang++ and unicorn')
class TailCall(unittest.TestCase):
    def test_b_to_another_function_is_a_tail_call(self):
        # f1: mov x0, #5 ; b f2          f2: add x0, x0, #1 ; ret
        a = aot_lift.Aot([(0x1000, words(0xD28000A0, 0x140003FF)), (0x2000, words(0x91000400, RET))])
        try:
            r = a.run(0x1000, {'x30': STOP})
            self.assertEqual(r.get('fault'), None)
            self.assertEqual(r['x0'], 6)
        finally: a.close()

    def test_falling_through_into_the_next_entry_continues_there(self):
        # f3: mov x0, #7 (no terminator)    f4 at the next word: add x0, x0, #1 ; ret
        a = aot_lift.Aot([(0x3000, words(0xD28000E0)), (0x3004, words(0x91000400, RET))])
        try:
            r = a.run(0x3000, {'x30': STOP})
            self.assertEqual(r.get('fault'), None)
            self.assertEqual(r['x0'], 8)
        finally: a.close()

    def test_a_branch_to_nowhere_still_stops_with_its_address(self):
        a = aot_lift.Aot([(0x1000, words(0xD28000A0, 0x140003FF))])   # f2 does not exist
        try:
            r = a.run(0x1000, {'x30': STOP})
            self.assertIn('0x2000', r.get('fault', ''))
        finally: a.close()


@unittest.skipUnless(READY, 'needs REMILL_BUILD, clang++ and unicorn')
class Traps(unittest.TestCase):
    """The compilers' unreachable/assert trap (0xe7ffdefe, UDF) inside a function: the function lifts, running the trap stops with its address"""
    def test_a_function_with_a_trap_after_its_ret_lifts_and_runs(self):
        a = aot_lift.Aot([(0x1000, words(0xD28000A0, RET, 0xE7FFDEFE))])   # mov x0, #5 ; ret ; <trap>
        try:
            r = a.run(0x1000, {'x30': STOP})
            self.assertEqual((r.get('fault'), r['x0']), (None, 5))
        finally: a.close()

    def test_executing_the_trap_stops_with_its_address(self):
        a = aot_lift.Aot([(0x1000, words(0xD28000A0, 0xE7FFDEFE, RET))])
        try:
            self.assertIn('undefined instruction executed at 0x1004', a.run(0x1000, {'x30': STOP}).get('fault', ''))
        finally: a.close()

    def test_a_peek_inside_an_svc_handler_leaves_the_run_able_to_fault(self):
        """aot_peek/aot_poke used to reset the run's jump buffer to null: a fault after an SVC that read guest memory aborted the process instead of ending the run"""
        a = aot_lift.Aot([(0x1000, words(0xD4000021, 0xE7FFDEFE, RET))])   # svc #1 ; <trap> ; ret
        a.map(0x8000, 0x1000)
        try:
            a.set_svc_handler(lambda num, regs, orig: a.peek(0x8000, 16))
            self.assertIn('undefined instruction executed at 0x1004', a.run(0x1000, {'x30': STOP}).get('fault', ''))
        finally: a.close()

    def test_an_instruction_remill_lacks_but_that_exists_still_makes_the_function_unliftable(self):
        with self.assertRaises(aot_lift.LiftError):
            a = aot_lift.Aot([(0x1000, words(0x4E284800, RET, 0xE7FFDEFE))])   # aese v0.16b, v0.16b
            a.close()


if __name__ == '__main__':
    unittest.main()
