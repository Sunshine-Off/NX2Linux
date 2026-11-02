"""MRS of the EL0 registers a game reads: the counter and its frequency (timing), CTR_EL0 and DCZID_EL0 (cache maintenance loops in memcpy/memset). Found on Xenoblade Chronicles 2's sdk, where
`mrs x0, cntpct_el0` stopped the boot. Synthetic code, no game data. Needs REMILL_BUILD and clang++; skipped otherwise."""
import struct, sys, time, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent))
import aot_lift
from test_aot_t3 import READY

RET = 0xD65F03C0
STOP = 0xFFFF0000
FRQ, PCT, CTR, DCZID = 0xD53BE000, 0xD53BE020, 0xD53B0020, 0xD53B00E0   # mrs x0, <reg>


def run(word):
    a = aot_lift.Aot([(0x1000, struct.pack('<2I', word, RET))])
    try: return a.run(0x1000, {'x30': STOP})
    finally: a.close()


@unittest.skipUnless(READY, 'needs REMILL_BUILD, clang++ and unicorn')
class SysRegs(unittest.TestCase):
    def test_the_frequency_and_cache_registers_are_the_switch_constants(self):
        self.assertEqual(run(FRQ)['x0'], 19200000)
        self.assertEqual(run(CTR)['x0'], 0x8444C004)
        self.assertEqual(run(DCZID)['x0'], 4)

    def test_the_counter_follows_the_host_clock_at_the_declared_frequency(self):
        a, t0 = run(PCT)['x0'], time.monotonic()
        time.sleep(0.2)
        b, t1 = run(PCT)['x0'], time.monotonic()
        ticks = (b - a) / 19200000
        self.assertGreater(b, a)
        self.assertAlmostEqual(ticks, t1 - t0, delta=0.1)


DC_CIVAC, DC_ZVA, IC_IVAU, DC_CVAU_X1 = 0xD50B7E20, 0xD50B7420, 0xD50B7520, 0xD50B7B21   # dc civac, x0 / dc zva, x0 / ic ivau, x0 / dc cvau, x1


@unittest.skipUnless(READY, 'needs REMILL_BUILD, clang++ and unicorn')
class CacheMaintenance(unittest.TestCase):
    """Found on Xenoblade 2's sdk (nn::os::FlushDataCache): `dc civac` made the function unliftable. The maintenance operations do nothing on a coherent host; DC ZVA zeroes its 64-byte block"""
    BASE = 0x8000

    def run_word(self, word, x0, x1=0):
        a = aot_lift.Aot([(0x1000, struct.pack('<2I', word, RET))])
        a.map(self.BASE, 0x1000, b'\xff' * 0x1000)
        try:
            r = a.run(0x1000, {'x0': x0, 'x1': x1, 'x30': STOP})
            return r, a.peek(self.BASE, 0x1000)
        finally: a.close()

    def test_the_maintenance_operations_lift_and_change_nothing(self):
        for word in (DC_CIVAC, IC_IVAU, DC_CVAU_X1):
            r, mem = self.run_word(word, self.BASE + 0x40, self.BASE + 0x40)
            self.assertIsNone(r.get('fault'), hex(word)); self.assertEqual(mem, b'\xff' * 0x1000)

    def test_a_cache_operation_after_an_svc_is_not_a_second_svc(self):
        """the hyper call an svc left in the state made the next dc/ic run the svc handler again (Xenoblade 2: phantom ArbitrateLock)"""
        a = aot_lift.Aot([(0x1000, struct.pack('<4I', 0xD4000021, DC_CIVAC, 0xD28000E1, RET))])   # svc #1 ; dc civac, x0 ; mov x1, #7 ; ret
        a.map(self.BASE, 0x1000)
        seen = []
        a.set_svc_handler(lambda num, regs, orig: seen.append(num))
        try:
            r = a.run(0x1000, {'x0': self.BASE, 'x30': STOP})
            self.assertIsNone(r.get('fault')); self.assertEqual(seen, [1]); self.assertEqual(r['x1'], 7)
        finally: a.close()

    def test_dc_zva_zeroes_the_64_byte_block_holding_the_address(self):
        r, mem = self.run_word(DC_ZVA, self.BASE + 0x1A7)   # inside the block 0x180..0x1BF
        self.assertIsNone(r.get('fault'))
        self.assertEqual(mem[0x180:0x1C0], bytes(64))
        self.assertEqual(mem[:0x180] + mem[0x1C0:], b'\xff' * (0x1000 - 64))


if __name__ == '__main__': unittest.main()
