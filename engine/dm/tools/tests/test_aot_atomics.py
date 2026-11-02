"""T3 follow-up, atomics and barriers: ldxr/ldaxr/stxr/stlxr, ldar/stlr, dmb and isb (the ones the Switch's A57 cores use: no LSE).
  · each instruction sequence equals the Unicorn ARM64 reference on registers, NZCV and memory;
  · the order of the barriers around the access is what acquire and release require (read in the lifted IR);
  · an exclusive store with no exclusive load fails, as the hardware's does, and an unaligned exclusive access is an explicit error;
  · several host threads incrementing one counter with an ldaxr/stlxr loop lose no update: the exclusive store is a real compare-and-swap.
Needs a Remill with the ADC/ADCS and exclusive-store patches (fork en cours/remill-patches), REMILL_BUILD, clang++ and unicorn; skipped otherwise."""
import re, struct, sys, tempfile, threading, unittest, os
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent))
import aot_lift
import arm64_fixture as fx
import arm64_ref as ref
from test_aot_t3 import DATA, DATA_SIZE, MEM, SP, READY, same

ADDR = 0x1000
RET = aot_lift.RET
LDXR_X, LDXR_W, STXR_X, STXR_W = 0xC85F7C02, 0x885F7C02, 0xC8047C01, 0x88047C01
LDAXR_X, LDAXR_W, STLXR_X, STLXR_W = 0xC85FFC02, 0x885FFC02, 0xC804FC01, 0x8804FC01
LDAR_X, STLR_X, STLR_W, ISB, DMB_ISH = 0xC8DFFC02, 0xC89FFC01, 0x889FFC01, 0xD5033FDF, 0xD5033BBF
TEXT = {LDXR_X: 'ldxr x2, [x0]', LDXR_W: 'ldxr w2, [x0]', STXR_X: 'stxr w4, x1, [x0]', STXR_W: 'stxr w4, w1, [x0]', LDAXR_X: 'ldaxr x2, [x0]', LDAXR_W: 'ldaxr w2, [x0]',
        STLXR_X: 'stlxr w4, x1, [x0]', STLXR_W: 'stlxr w4, w1, [x0]', LDAR_X: 'ldar x2, [x0]', STLR_X: 'stlr x1, [x0]', STLR_W: 'stlr w1, [x0]', ISB: 'isb', DMB_ISH: 'dmb ish'}


def words(*w): return struct.pack('<%dI' % len(w), *w)


def counter_loop(atomic=True):
    """x0 = the counter's address, x1 = how many times to add 1. atomic: ldaxr / add / stlxr / retry; otherwise ldr / add / str (loses updates under threads)"""
    body = [LDAXR_X, 0x91000442, 0xC803FC02, 0x35000003 | ((-3 & 0x7FFFF) << 5)] if atomic else [0xF9400002, 0x91000442, 0xF9000002]
    n = len(body)
    return words(*body, fx.SUBSI(1, 1, 1), fx.BCOND(ADDR + 4 * (n + 1), ADDR, 1), RET)   # subs x1,x1,#1 ; b.ne loop ; ret


def mapped(code):
    aot = aot_lift.Aot([(ADDR, code)])
    for b, data in MEM.items(): aot.map(b, len(data), data)
    return aot


@unittest.skipUnless(READY, 'needs REMILL_BUILD (a Remill build folder), clang++ and unicorn')
class Atomics(unittest.TestCase):
    def test_encodings_are_what_the_table_says(self):
        import capstone
        md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
        for w, want in TEXT.items():
            i = next(md.disasm(struct.pack('<I', w), ADDR))
            self.assertEqual(f'{i.mnemonic} {i.op_str}'.strip(), want, hex(w))

    def test_sequences_equal_the_arm64_reference(self):
        seqs = [(LDXR_X, STXR_X), (LDXR_W, STXR_W), (LDAXR_X, STLXR_X), (LDAXR_W, STLXR_W), (LDAXR_X, STXR_X), (LDXR_X, STLXR_X),
                (STLR_X, LDAR_X), (STLR_W, LDAR_X), (LDAR_X, STLR_X), (ISB, LDXR_X, STXR_X), (STLR_X, DMB_ISH, LDAR_X), (LDXR_X, DMB_ISH, STXR_X)]
        for seq in seqs:
            code = words(*seq, RET)
            aot = mapped(code)   # one library per sequence; the memory is put back before each run
            try:
                for value in (0, 1, 0xFFFFFFFF, 0x123456789ABCDEF0):
                    for addr in (DATA + 0x40, DATA + 0x1008):
                        regs = {'x0': addr, 'x1': value, 'x2': 0xDEADBEEF, 'x4': 0x77}
                        want = ref.run_image(bytes(ADDR) + code, ADDR, [(regs, 0, SP)], MEM)[0]   # one core per run: the exclusive monitor of an earlier run is gone
                        what = f'{" ; ".join(TEXT[w] for w in seq)}: x1={value:#x} at {addr:#x}'
                        aot.reset()
                        got = aot.run(ADDR, dict(regs, x30=ref.STOP), 0, SP)
                        same(self, got, want, what)
                        if {STXR_X, STXR_W, STLXR_X, STLXR_W} & set(seq): self.assertEqual(got['x4'], 0, f'{what}: the exclusive store after its load must succeed')
            finally: aot.close()

    def test_exclusive_store_without_an_exclusive_load_fails(self):
        for w in (STXR_X, STLXR_W):
            code = words(w, RET)
            regs = {'x0': DATA + 0x40, 'x1': 0x55, 'x4': 0x77}
            want = ref.run_image(bytes(ADDR) + code, ADDR, [(regs, 0, SP)], MEM)[0]
            aot = mapped(code)
            try:
                got = aot.run(ADDR, dict(regs, x30=ref.STOP), 0, SP)
                same(self, got, want, TEXT[w])
                self.assertEqual(got['x4'], 1); self.assertEqual(got['mem'][DATA], MEM[DATA], 'a failed exclusive store wrote')
            finally: aot.close()

    def test_unaligned_exclusive_access_is_an_explicit_error(self):
        aot = mapped(words(LDXR_X, STXR_X, RET))
        try:
            got = aot.run(ADDR, {'x0': DATA + 0x41, 'x1': 1, 'x30': ref.STOP}, 0, SP)
            self.assertIn('not naturally aligned', got['fault']); self.assertIn(f'{DATA + 0x41:#x}', got['fault'])
        finally: aot.close()

    def test_barriers_surround_the_access_in_the_order_acquire_and_release_need(self):
        """Read in the lifted IR: acquire = access then barriers; release = barriers, access, then store->load (the instructions are sequentially consistent with each other); dmb and isb = full"""
        want = {STLR_X: ['load_store', 'store_store', 'write_memory_64', 'store_load'], STLR_W: ['load_store', 'store_store', 'write_memory_32', 'store_load'],
                LDAR_X: ['read_memory_64', 'load_load', 'load_store'], LDAXR_X: ['read_memory_64', 'load_load', 'load_store'],
                DMB_ISH: ['store_store', 'store_load'], ISB: ['store_store', 'store_load']}
        with tempfile.TemporaryDirectory() as d:
            for w, order in want.items():
                ll = os.path.join(d, 'a.ll'); aot_lift.lift(w, ll)
                calls = [re.sub('^__remill_(barrier_)?', '', c) for c in re.findall(r'call [^@\n]*@(__remill_[a-z_0-9]+)', Path(ll).read_text())
                         if re.match(r'__remill_(barrier_|read_memory|write_memory)', c)]
                self.assertEqual(calls, order, TEXT[w])

    def test_exclusive_store_is_a_compare_and_swap(self):
        """ldaxr/stlxr is lifted into __remill_compare_exchange_memory (the 32-bit one for w registers), which the runtime does with a locked instruction"""
        with tempfile.TemporaryDirectory() as d:
            for w, n in ((STXR_X, 64), (STXR_W, 32), (STLXR_X, 64), (STLXR_W, 32)):
                ll = os.path.join(d, 'a.ll'); aot_lift.lift(w, ll)
                self.assertIn(f'@__remill_compare_exchange_memory_{n}', Path(ll).read_text(), TEXT[w])

    def test_counter_loop_equals_the_reference(self):
        code = counter_loop()
        regs = {'x0': DATA + 0x80, 'x1': 7}
        want = ref.run_image(bytes(ADDR) + code, ADDR, [(regs, 0, SP)], MEM)[0]
        aot = mapped(code)
        try:
            same(self, aot.run(ADDR, dict(regs, x30=ref.STOP), 0, SP), want, 'counter loop')
            self.assertEqual(struct.unpack_from('<Q', aot.peek(DATA, DATA_SIZE), 0x80)[0] - struct.unpack_from('<Q', MEM[DATA], 0x80)[0], 7)
        finally: aot.close()

    def threads_add(self, atomic, nthreads=4, n=20000):
        aot = mapped(counter_loop(atomic))
        try:
            start = struct.unpack_from('<Q', MEM[DATA], 0x80)[0]
            errs = []
            def work():
                r = aot.run(ADDR, {'x0': DATA + 0x80, 'x1': n, 'x30': ref.STOP}, 0, SP)   # each thread has its own stack in a real guest; this code does not use it
                if 'fault' in r: errs.append(r['fault'])
            ts = [threading.Thread(target=work) for _ in range(nthreads)]
            for t in ts: t.start()
            for t in ts: t.join()
            self.assertEqual(errs, [])
            return struct.unpack_from('<Q', aot.peek(DATA, DATA_SIZE), 0x80)[0] - start, nthreads * n
        finally: aot.close()

    def test_threads_incrementing_one_counter_lose_no_update(self):
        for _ in range(3):
            got, want = self.threads_add(True)
            self.assertEqual(got, want, 'an exclusive store succeeded although another thread had written the counter')

    def test_the_race_is_real_without_exclusives(self):
        """Sensitivity of the test above: the same threads with ldr/add/str do lose updates (by chance, so a few tries; skipped if the host never interleaves them)"""
        for _ in range(5):
            got, want = self.threads_add(False)
            self.assertLessEqual(got, want)
            if got < want: return
        self.skipTest('no lost update seen in 5 tries: this host did not interleave the threads')


if __name__ == '__main__':
    unittest.main()
