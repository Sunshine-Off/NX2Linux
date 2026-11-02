"""nn::os fibers on the Linux host (NX2Linux S7bis). The SDK switches fibers in assembly (DispatchFiberContext): it saves the callee-saved registers, swaps sp and `ret`s into the other fiber. In recompiled code a
`ret` returns to the C caller, not to an arbitrary address, so the switch is replaced: every fiber runs on a host thread of its own, only one runs at a time, and a switch hands control from one to the other.
A fiber is resumed by whichever guest thread asks (a job system's workers): the thread pointers (tpidr_el0, tpidrro_el0: GetCurrentThread, thread-local storage) follow the thread that resumes it.
Context layout (FiberContextForHorizon, 0xf0 bytes, built by FiberImplByHorizon::Initialize): x0, x19..x28, x29 at +0, +8..+0x58, x30 at +0x60, q8..q15 from +0x70; sp after the switch is the context + 0xf0."""
import struct
import threading

from horizon_overrides import hle

TOKEN_BASE = 0xF1BE_0000_0000   # what a blocked fiber leaves in its context slot, so a later switch to it resumes it and a re-initialised slot (a real context pointer) starts a new fiber
CTX_SIZE = 0xF0


class FiberRecord:
    def __init__(self, token):
        self.token, self.sem, self.tls, self.logical = token, threading.Semaphore(0), (0, 0), None


class Fibers:
    def __init__(self, hz):
        self.hz, self.lock, self.by_token, self.next = hz, threading.Lock(), {}, 1
        self.local = threading.local()
        self.started = 0

    def current(self):
        rec = getattr(self.local, 'fiber', None)
        if rec is None:
            with self.lock: rec = FiberRecord(TOKEN_BASE + self.next * 0x100); self.next += 1; self.by_token[rec.token] = rec
            self.local.fiber = rec
        return rec

    def spawn(self, ctx, tls, logical):
        hz = self.hz
        def u64(off): return struct.unpack('<Q', hz.rd(ctx + off, 8))[0]
        regs = {'x0': u64(0), 'x30': u64(0x60)}
        for k, off in zip(range(19, 30), range(8, 0x60, 8)): regs[f'x{k}'] = u64(off)
        vregs = {f'v{8 + i}': int.from_bytes(hz.rd(ctx + 0x70 + 16 * i, 16), 'little') for i in range(8)}
        entry = regs['x30']
        def main():
            rec = self.current()
            hz.local.thread = logical
            r = hz.aot.run(entry, regs, 0, ctx + CTX_SIZE, tls[0], tls[1], vregs=vregs, snapshot=False)
            if 'fault' in r and r['fault'] != 'thread exited':
                chain = [0] * 31; chain[29], chain[30] = r.get('x29', 0), r.get('x30', 0)
                hz.log(f'fiber stopped: {r["fault"]} (call chain: {hz.where(chain)})'); hz.thread_faults.append((-1, r['fault']))
        self.started += 1
        threading.Thread(target=main, name=f'fiber-{self.started}', daemon=True).start()


@hle('_ZN2nn2os6detail20DispatchFiberContextEPPNS1_22FiberContextForHorizonES4_', 'fibers switch by `ret` into another stack, which recompiled code cannot do: a fiber is a host thread and a switch hands control over')
def dispatch(hz, r):
    F = hz.fibers
    from_slot, to_slot = r[0], r[1]
    cur = F.current()
    tls = (hz.aot.get_tls(0), hz.aot.get_tls(1))
    logical = hz.current()
    hz.wr64(from_slot, cur.token)   # the saved context of this fiber: a token, not a pointer
    target = hz.rd64(to_slot)
    if target in F.by_token and target != cur.token:   # a blocked fiber: wake it, on this thread's identity
        rec = F.by_token[target]
        rec.tls, rec.logical = tls, logical
        rec.sem.release()
    elif target == cur.token:
        return   # switching to itself
    else:
        F.spawn(target, tls, logical)   # a fresh fiber: `target` is the context FiberImplByHorizon::Initialize built
    cur.sem.acquire()   # until some fiber switches back to this one
    hz.aot.set_tls(*cur.tls)
    hz.local.thread = cur.logical
    r[0] = 0   # the context restore leaves x0 = 0 (it was saved as 0)


def install_state(hz):
    if not hasattr(hz, 'fibers'): hz.fibers = Fibers(hz)
