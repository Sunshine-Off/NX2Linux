"""T5: `svc` reaches the Horizon service handler, and the guest CPU state (thread pointers, guest memory, threads) is right.
  · an svc in AOT code and in JIT code calls the handler with the number, x0..x30 and the address of the svc, and the registers it writes come back: equal to the Unicorn ARM64 reference running the same handler;
  · a service can change the guest memory while code runs (the MapMemory / SetHeapSize shape): the memory it maps is read and written by the guest at once;
  · tpidr_el0, tpidrro_el0 (the Horizon thread-local region pointer) and x18 reach the guest, per thread;
  · host threads run guest threads concurrently, each with its own registers, thread pointers and service calls;
  · code one thread rewrites is recompiled by every other thread's JIT, also when that thread is in the middle of running it (hypotheses.md H15).
Needs REMILL_BUILD (with the SVC and TPIDRRO patch), DYNARMIC_DIR, clang++ and unicorn; skipped otherwise."""
import os, struct, sys, threading, time, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent))
import aot_lift
import arm64_fixture as fx
import arm64_ref as ref
from nso_asm import STP_PRE, LDP_POST, LDRX, STRX
from test_aot_t3 import MEM, SP, READY, same

JIT_READY = READY and os.path.exists(os.path.join(os.environ.get('DYNARMIC_DIR', '/nonexistent'), 'build/src/dynarmic/libdynarmic.a'))
RET, M = aot_lift.RET, ref.MASK
HEAP = 0x300000
ECHO, HEAPFN, TWICE, RETONLY, CALLER, SPIN, STORE, SLOT, UNKNOWN = 0x240, 0x260, 0x2A0, 0x2E0, 0x300, 0x380, 0x3C0, 0x3E0, 0x400


def image():
    words, ranges, _ = fx.text_layout()
    img = bytearray(0x1000)
    def put(at, ws): img[at:at + 4 * len(ws)] = struct.pack('<%dI' % len(ws), *ws)
    put(0, words)
    put(ECHO, [fx.SVC(0x2A), RET])
    put(HEAPFN, [fx.SVC(1), STRX(2, 1, 1), LDRX(0, 1, 1), RET])                                   # x0: size; the service maps it at x1; store x2 at [x1+8] and read it back
    put(TWICE, [fx.SVC(1), fx.MOV(4, 0), fx.MOVZ(0, 0x1000), fx.SVC(1), fx.MOV(5, 0), RET])        # the second mapping overlaps the first: the service refuses
    put(RETONLY, [RET])
    put(CALLER, [STP_PRE(29, 30, 31, -16), fx.BL(CALLER + 4, ECHO), fx.BL(CALLER + 8, ECHO), LDP_POST(29, 30, 31, 16), RET])
    put(SPIN, [fx.B(SPIN, SPIN)])
    put(STORE, [0xB9000001, RET])                                                                  # str w1, [x0]
    put(SLOT, [fx.MOVZ(0, 1), RET])
    put(UNKNOWN, [fx.SVC(99), RET])
    return bytes(img), ranges


IMG, RANGES = image()
MEM_AOT = {**MEM, 0: IMG}


class Horizon:
    """The services of the tests: 0x2A mixes the registers, 1 maps memory at HEAP (the shape of SetHeapSize), anything else is InvalidSvc. `mapper(base, size)` -> bool maps guest memory"""
    def __init__(self, mapper, unmapper=lambda b: None):
        self.mapper, self.unmapper, self.log, self.mapped = mapper, unmapper, [], []
        self.lock = threading.Lock()

    def __call__(self, num, r, orig):
        with self.lock: self.log.append((num, orig, r[0]))
        if num == 0x2A: r[0] = (r[0] * 3 + r[1] + 7) & M; r[1] = num
        elif num == 1:
            size = (r[0] + 0xFFF) & ~0xFFF
            ok = size > 0 and self.mapper(HEAP, size)
            if ok: self.mapped.append(HEAP)
            r[0], r[1] = (0, HEAP) if ok else (0xD001, 0)
        else: r[0] = 0xF001

    def reset(self, _=None):
        for b in self.mapped: self.unmapper(b)
        self.mapped = []


def reference(entry, cases, horizon_log):
    """Unicorn running the same handler; the handler's log is collected in horizon_log, per case"""
    def factory(uc):
        def mapper(base, size):
            try: uc.mem_map(base, size); return True
            except Exception: return False
        h = Horizon(mapper, lambda b: uc.mem_unmap(b, next(s for bb, s in sizes if bb == b)))
        sizes = []
        real = h.mapper
        h.mapper = lambda b, s: (real(b, s) and (sizes.append((b, s)) or True))
        h.log = []; horizon_log.append(h.log)
        return h
    return ref.run_image(IMG, entry, cases, MEM, svc=factory)


def aot_lib(names, jit=False, **kw):
    funcs = [(RANGES[n][0], IMG[RANGES[n][0]:RANGES[n][1]]) if isinstance(n, str) else (n, IMG[n:n + 4 * k]) for n, k in
             [(x, 0) if isinstance(x, str) else x for x in names]]
    a = aot_lift.Aot(funcs, jit=jit, **kw)
    for b, data in MEM_AOT.items(): a.map(b, len(data), data)
    h = Horizon(a.try_map, a.unmap)
    a.set_svc_handler(h)
    return a, h


def run(a, h, entry, regs, nz=0, **kw):
    a.reset(); h.reset(); h.log.clear()
    return a.run(entry, dict(regs, x30=ref.STOP), nz, SP, **kw)


def eq_tls(t, got, want, what):
    same(t, got, want, what)
    if 'fault' not in want: t.assertEqual(got['tpidr_el0'], want['tpidr_el0'], f'{what}: tpidr_el0')


@unittest.skipUnless(READY, 'needs REMILL_BUILD, clang++ and unicorn')
class Svc(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a, cls.h = aot_lib([(ECHO, 2), (HEAPFN, 4), (TWICE, 6), (CALLER, 5), (STORE, 2), (UNKNOWN, 2), 'tls'])

    @classmethod
    def tearDownClass(cls): cls.a.close()

    def compare(self, entry, cases):
        logs = []
        want = reference(entry, [(r, n, SP) for r, n in cases], logs)
        for (regs, nz), w, wl in zip(cases, want, logs):
            self.assertNotIn('fault', w)
            tls = {k: regs[k] for k in ('tpidr_el0', 'tpidrro_el0') if k in regs}
            got = run(self.a, self.h, entry, {k: v for k, v in regs.items() if k not in tls}, nz, **tls)
            eq_tls(self, got, w, f'{regs} nzcv={nz:04b}')
            self.assertEqual(self.h.log, wl, f'{regs}: the handler saw other calls than under the reference')

    def test_svc_registers_in_and_out_equal_the_reference(self):
        self.compare(ECHO, [({'x0': x, 'x1': y, 'x5': 0x55, 'x18': 0x1818}, nz) for x in (0, 1, 7, M, 1 << 63) for y in (0, 9) for nz in (0, 6)])
        self.assertGreater(self.a.stat('svc_calls'), 0)

    def test_the_handler_gets_the_number_and_the_address_of_the_svc(self):
        run(self.a, self.h, ECHO, {'x0': 5})
        self.assertEqual(self.h.log, [(0x2A, ECHO, 5)])

    def test_two_svc_through_a_nested_call_equal_the_reference(self):
        self.compare(CALLER, [({'x0': x, 'x1': 3}, 0) for x in (0, 4, M)])
        run(self.a, self.h, CALLER, {'x0': 1}); self.assertEqual([c[:2] for c in self.h.log], [(0x2A, ECHO)] * 2)

    def test_an_unknown_service_answers_with_its_error_code(self):
        self.compare(UNKNOWN, [({'x0': 1}, 0), ({'x0': M}, 0)])
        self.assertEqual(run(self.a, self.h, UNKNOWN, {'x0': 1})['x0'], 0xF001)

    def test_a_service_maps_memory_the_guest_then_uses(self):
        self.compare(HEAPFN, [({'x0': size, 'x2': v}, 0) for size in (0x1000, 0x1800, 0x10000) for v in (0, 0x1234567890ABCDEF)])
        got = run(self.a, self.h, HEAPFN, {'x0': 0x2000, 'x2': 0xCAFE})
        self.assertEqual((got['x1'], got['x0']), (HEAP, 0xCAFE))
        self.assertEqual(self.a.peek(HEAP + 8, 8), struct.pack('<Q', 0xCAFE))   # it is in guest memory: the harness reads the same bytes

    def test_a_second_mapping_that_overlaps_is_refused_by_the_service(self):
        self.compare(TWICE, [({'x0': 0x1000}, 0), ({'x0': 0x4000}, 0)])

    def test_unmapped_memory_is_an_explicit_error_again(self):
        run(self.a, self.h, HEAPFN, {'x0': 0x1000, 'x2': 1})
        self.assertEqual(self.a.run(STORE, {'x0': HEAP, 'x1': 1, 'x30': ref.STOP}, 0, SP).get('fault'), None)   # mapped by the service
        self.assertTrue(self.a.unmap(HEAP)); self.assertFalse(self.a.unmap(HEAP))
        got = self.a.run(STORE, {'x0': HEAP, 'x1': 1, 'x30': ref.STOP}, 0, SP)
        self.assertIn('outside every mapped region', got['fault'])

    def test_thread_pointers_and_x18_reach_the_guest(self):
        tls = RANGES['tls'][0]
        self.compare(tls, [({'tpidr_el0': a, 'tpidrro_el0': b, 'x18': c}, 0) for a, b, c in ((0, 0, 0), (0x1000, 0x2000, 0x3000), (M, M - 1, 1 << 63))])

    def test_host_threads_run_guest_threads_with_their_own_state(self):
        results, lock = {}, threading.Lock()
        def worker(i):
            for k in range(50):
                got = self.a.run(ECHO, {'x0': i * 1000 + k, 'x1': i, 'x30': ref.STOP}, 0, SP)
                tls = self.a.run(RANGES['tls'][0], {'x18': i, 'x30': ref.STOP}, 0, SP, tpidr_el0=0x1000 * i, tpidrro_el0=0x77000 + i)
                with lock: results[i, k] = (got['x0'], got['x1'], tls['x0'], tls['x3'], tls['x1'])
        before = self.a.stat('svc_calls'); self.h.log.clear()
        ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in ts: t.start()
        for t in ts: t.join()
        for (i, k), r in results.items():
            self.assertEqual(r, (((i * 1000 + k) * 3 + i + 7) & M, 0x2A, 0x1000 * i, 0x77000 + i, i), f'thread {i} call {k}')
        self.assertEqual(self.a.stat('svc_calls') - before, 8 * 50)


@unittest.skipUnless(JIT_READY, 'needs REMILL_BUILD, DYNARMIC_DIR, clang++ and unicorn')
class JitSvc(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a, cls.h = aot_lib(['indirect', (STORE, 2)], jit=True)   # ECHO, CALLER... are not AOT here: the JIT runs them

    @classmethod
    def tearDownClass(cls): cls.a.close()

    def indirect(self, regs, nz=0):
        return run(self.a, self.h, RANGES['indirect'][0], regs, nz)

    def test_svc_in_jit_code_equals_the_reference(self):
        cases = [({'x0': x, 'x1': ECHO, 'x2': RETONLY, 'x5': 3}, nz) for x in (0, 1, 9, M) for nz in (0, 9)]
        logs = []
        want = reference(RANGES['indirect'][0], [(r, n, SP) for r, n in cases], logs)
        for (regs, nz), w, wl in zip(cases, want, logs):
            same(self, self.indirect(regs, nz), w, f'{regs}')
            self.assertEqual(self.h.log, wl)
        self.assertGreaterEqual(self.a.stat('jit_entries'), len(cases))

    def test_svc_in_jit_code_that_maps_memory(self):
        got = self.indirect({'x0': 0x1000, 'x2': RETONLY, 'x1': HEAPFN, 'x3': 0xBEEF})   # HEAPFN stores x2 (the tail target) at [x1+8] and loads it back
        self.assertEqual((got['x1'], got['x0']), (HEAP, RETONLY))

    def test_a_pending_service_with_no_handler_is_an_explicit_error(self):
        self.a.set_svc_handler(None)
        try: got = self.indirect({'x1': ECHO, 'x2': RETONLY})
        finally: self.a.set_svc_handler(self.h)
        self.assertIn('svc #42', got['fault']); self.assertIn('no Horizon service handler', got['fault'])

    def test_code_rewritten_by_another_thread_is_recompiled(self):
        """thread A runs the code at SLOT in its JIT (movz x0,#1), thread B rewrites it by an AOT store, A runs it again and gets the new code"""
        res, go, done = {}, threading.Event(), threading.Event()
        def thread_a():
            res['first'] = self.indirect({'x1': SLOT, 'x2': RETONLY})['x0']
            done.set(); go.wait(10)
            res['second'] = self.a.run(RANGES['indirect'][0], {'x1': SLOT, 'x2': RETONLY, 'x30': ref.STOP}, 0, SP)['x0']   # not run(): its reset() would put the old code back
        t = threading.Thread(target=thread_a); t.start()
        done.wait(10)
        b = self.a.run(STORE, {'x0': SLOT, 'x1': fx.MOVZ(0, 7), 'x30': ref.STOP}, 0, SP)   # this thread is B: it has no JIT at all
        self.assertNotIn('fault', b)
        go.set(); t.join(10)
        self.assertEqual((res['first'], res['second']), (1, 7))
        self.assertGreaterEqual(self.a.stat('jit_invalidations'), 1)
        self.a.reset()

    def test_a_thread_spinning_in_jit_code_is_stopped_by_a_rewrite(self):
        """A spins in `b .`; B replaces the branch by `ret`: A's compiled loop is halted and recompiled, so A returns (it would spin for ever otherwise)"""
        res = {}
        t = threading.Thread(target=lambda: res.update(self.indirect({'x1': SPIN, 'x2': RETONLY, 'x0': 5})))
        t.daemon = True; t.start()
        time.sleep(0.5)
        self.assertTrue(t.is_alive(), 'the spin loop ended by itself')
        self.a.run(STORE, {'x0': SPIN, 'x1': RET, 'x30': ref.STOP}, 0, SP)
        t.join(10)
        self.assertFalse(t.is_alive(), 'the spinning thread did not see the rewritten code')
        self.assertNotIn('fault', res); self.assertEqual(res['x0'], 5)
        self.a.reset()


if __name__ == '__main__': unittest.main()
