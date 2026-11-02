"""Horizon on Linux, for the recompiled modules (NX2Linux S7): the SVCs a retail game's SDK issues, and the services behind SendSyncRequest. One guest thread (the harness's), so waits that nothing can ever
signal are reported instead of hanging. The memory is the AOT runtime's regions (aot_lift.Aot: peek / poke / try_map); handles live in a table; a service is a Python object whose `command(id, request)`
returns a Response. Everything not implemented is logged as `unhandled ...` and answered with an error, never faked as a success (the boot stops where the game needs it, and the log says where).
Usage: h = Horizon(module_set); h.attach(); module_set.call(entry, ...)."""
import os, struct, sys, threading, time

M64 = (1 << 64) - 1
PAGE = 0x1000

# Horizon result codes: module | description << 9
def rc(module, desc): return module | desc << 9
OK = 0
R_INVALID_SIZE, R_INVALID_ADDRESS, R_INVALID_HANDLE = rc(1, 101), rc(1, 102), rc(1, 114)
R_TIMEOUT, R_NOT_IMPLEMENTED = rc(1, 117), rc(1, 33)
R_INVALID_STATE, R_INVALID_ENUM = rc(1, 106), rc(1, 120)
R_NOT_FOUND = rc(21, 1)   # sm: service not registered

SVC_NAMES = {
    0x01: 'SetHeapSize', 0x02: 'SetMemoryPermission', 0x03: 'SetMemoryAttribute', 0x04: 'MapMemory', 0x05: 'UnmapMemory', 0x06: 'QueryMemory', 0x07: 'ExitProcess', 0x08: 'CreateThread',
    0x09: 'StartThread', 0x0a: 'ExitThread', 0x0b: 'SleepThread', 0x0c: 'GetThreadPriority', 0x0d: 'SetThreadPriority', 0x0e: 'GetThreadCoreMask', 0x0f: 'SetThreadCoreMask',
    0x10: 'GetCurrentProcessorNumber', 0x11: 'SignalEvent', 0x12: 'ClearEvent', 0x13: 'MapSharedMemory', 0x14: 'UnmapSharedMemory', 0x15: 'CreateTransferMemory', 0x16: 'CloseHandle',
    0x17: 'ResetSignal', 0x18: 'WaitSynchronization', 0x19: 'CancelSynchronization', 0x1a: 'ArbitrateLock', 0x1b: 'ArbitrateUnlock', 0x1c: 'WaitProcessWideKeyAtomic',
    0x1d: 'SignalProcessWideKey', 0x1e: 'GetSystemTick', 0x1f: 'ConnectToNamedPort', 0x21: 'SendSyncRequest', 0x22: 'SendSyncRequestWithUserBuffer', 0x24: 'GetProcessId',
    0x25: 'GetThreadId', 0x26: 'Break', 0x27: 'OutputDebugString', 0x29: 'GetInfo', 0x34: 'WaitForAddress', 0x45: 'CreateEvent', 0x35: 'SignalToAddress', 0x36: 'SynchronizePreemptionState',
}

# MemoryType values QueryMemory reports (the ones a game checks)
MT_UNMAPPED, MT_CODE, MT_CODE_DATA, MT_NORMAL, MT_STACK, MT_HEAP = 0, 3, 4, 5, 11, 5
PERM_NONE, PERM_R, PERM_RW, PERM_RX = 0, 1, 3, 5


class Obj:
    """A kernel object a handle names"""
    kind = 'object'
    def __init__(self): self.name = ''


class Event(Obj):
    kind = 'event'
    def __init__(self, signaled=False, auto=False): super().__init__(); self.signaled, self.auto = signaled, auto


class KThread(Obj):
    """A guest thread: its entry, argument, stack, thread-local region, and the Python thread that runs it once started"""
    kind = 'thread'
    _ids = iter(range(2, 1 << 30))

    def __init__(self, hz, entry, arg, stack_top, priority, tlr):
        super().__init__()
        self.hz, self.entry, self.arg, self.stack_top, self.priority, self.tlr = hz, entry, arg, stack_top, priority, tlr
        self.tid, self.handle = next(self._ids), 0
        self.done, self.started, self.granted, self.signaled, self.tag, self.result = False, False, False, False, 0, None

    def start(self):
        if self.started: return
        self.started = True
        self.py = threading.Thread(target=self.main, name=f'guest-{self.tid}', daemon=True)
        self.py.start()

    def main(self):
        hz = self.hz
        hz.local.thread = self
        try:
            self.result = hz.aot.run(self.entry, {'x0': self.arg, 'x1': self.handle, 'x30': hz.ms.STOP}, 0, self.stack_top, self.tlr + 0x800, self.tlr, snapshot=False)
            if 'fault' in self.result and self.result['fault'] != 'thread exited':
                regs = [0] * 31; regs[29], regs[30] = self.result.get('x29', 0), self.result.get('x30', 0)
                hz.log(f'guest thread {self.tid} stopped: {self.result["fault"]} (call chain: {hz.where(regs)})')
                hz.thread_faults.append((self.tid, self.result['fault']))
        finally:
            with hz.cv:
                self.done = True; hz.cv.notify_all()


class Session(Obj):
    kind = 'session'
    def __init__(self, service): super().__init__(); self.service = service


class Response:
    def __init__(self, result=OK, raw=b'', copy=(), move=(), objects=()):
        self.result, self.raw, self.copy, self.move, self.objects = result, raw, list(copy), list(move), list(objects)   # objects: Obj to register and move as handles


class Request:
    """A decoded IPC request: its command id, the raw words after the command id, the handles it carried, its buffers and whether it is a control message"""
    def __init__(self, kind, cmd, raw, copy, move, pid, bufs):
        self.kind, self.cmd, self.raw, self.copy, self.move, self.pid, self.bufs = kind, cmd, raw, copy, move, pid, bufs   # bufs: [(kind, addr, size)] of the A/B/W/X/C descriptors


class Service:
    """A named service: command(id, request) -> Response. Subclasses implement cmd_<id>"""
    name = '?'
    def __init__(self, hz): self.hz = hz
    def command(self, cmd, req):
        f = getattr(self, f'cmd_{cmd}', None)
        if f is None:
            self.hz.log(f'unhandled {self.name} command {cmd} (raw {req.raw[:32].hex()})')
            return Response(R_NOT_IMPLEMENTED)
        return f(req)


class Horizon:
    HEAP_BASE = 0x2_0000_0000
    MEM_BASE = 0x3_0000_0000   # MapMemory / transfer / shared memory the game asks for with no fixed address
    FIRST_HANDLE = 0xD000
    TLS_PER_THREAD = 0x200

    def __init__(self, ms, out=None, verbose=False, romfs=None, title_id='0000000000000000'):
        self.ms, self.aot, self.out, self.verbose = ms, ms.aot, out or sys.stderr, verbose
        self.handles, self._next_handle = {}, self.FIRST_HANDLE
        self.heap_size = 0
        self.services = {}      # sm: name -> factory(hz) -> Service
        self.named_ports = {}   # ConnectToNamedPort names -> factory
        self.stats, self.unhandled, self.start = {}, {}, time.monotonic()
        self.stop_reason = None
        self.verbose_nvn = bool(os.environ.get('NX2LINUX_NVN_LOG'))
        self.display_factory = None   # () -> horizon_display.Display, called when the game first presents a frame
        self.thread_faults = []
        self.stub_calls = {}
        self.thread_state = {}
        import collections
        self.ring = collections.deque(maxlen=200000) if os.environ.get('NX2LINUX_SVC_RING') else None   # the last synchronisation svcs, for a hang report
        self.main_thread_obj = KThread(self, 0, 0, 0, 44, ms.TLS)
        self.main_thread_obj.tid = 1
        self._init_kernel()
        import horizon_fiber
        horizon_fiber.install_state(self)
        self.process_id, self.thread_id = 0x80, 1
        self._mem_cursor = self.MEM_BASE
        self.mem_types = []     # (addr, size, type, perm) of what the process mapped beyond the module regions
        import horizon_services, horizon_fs
        horizon_services.install(self)
        self.fs = horizon_fs.FileSystem(self, romfs, title_id)

    # ---- plumbing ----
    def thread_report(self):
        now = time.monotonic(); out = []
        for tid, (name, orig, fp, lr, at) in sorted(self.thread_state.items()):
            regs = [0] * 31; regs[29], regs[30] = fp, lr
            try: chain = self.where(regs, 14 if tid in (1, 3, 4) else 5)
            except Exception: chain = '?'
            out.append(f'  thread {tid}: {name} {now - at:.1f} s ago: {chain}')
        return '\n'.join(out)

    def ring_report(self, tid, last=40):
        """The last synchronisation svcs on the mutex and key that thread `tid` is blocked on"""
        if self.ring is None: return ''
        mine = [e for e in self.ring if e[1] == tid and e[2] == 'WaitProcessWideKeyAtomic']
        if not mine: return ''
        mutex, key = mine[-1][3], mine[-1][4]
        rel = [e for e in self.ring if e[3] in (mutex, key) or e[4] == key]
        t0 = rel[-1][0]
        return f'thread {tid} waits on mutex {mutex:#x} key {key:#x}; last svcs on them:\n' + '\n'.join(f'    {e[0] - t0:+8.3f} s thread {e[1]} {e[2]}({e[3]:#x}, {e[4]:#x}, {e[5]:#x}, {e[6]:#x})' for e in rel[-last:])

    def note(self, msg):
        if self.verbose: self.log(msg)

    def log(self, msg):
        print(f'[hz {time.monotonic() - self.start:7.3f}] {msg}', file=self.out, flush=True)

    def attach(self):
        self.aot.set_svc_handler(self.svc)
        return self

    def new_handle(self, obj):
        h = self._next_handle; self._next_handle += 1; self.handles[h] = obj; return h

    def rd(self, a, n): return self.aot.peek(a, n)
    def wr(self, a, data): assert self.aot.lib.aot_poke(a, bytes(data), len(data)) == 0, f'poke {a:#x}+{len(data):#x}'
    def rd32(self, a): return struct.unpack('<I', self.rd(a, 4))[0]
    def rd64(self, a): return struct.unpack('<Q', self.rd(a, 8))[0]
    def wr32(self, a, v): self.wr(a, struct.pack('<I', v & 0xFFFFFFFF))
    def wr64(self, a, v): self.wr(a, struct.pack('<Q', v & M64))

    def cstring(self, a, limit=256):
        out = b''
        while len(out) < limit:
            try: c = self.rd(a + len(out), 1)
            except AssertionError: break
            if c == b'\0': break
            out += c
        return out.decode('utf-8', 'replace')

    # ---- boot: nn::init::Start, step by step (host/main.c guest_main does the same on the Mach host) ----
    ARGV_BASE = 0x1_0000_0000

    def boot(self, argv=('game',), stop_after=None):
        """Run the SDK's startup in the order nn::init::Start has it, then nnMain. Returns the first step's result that faulted ({'step': name, 'fault': ...}), or the result of nnMain. stop_after: a step name to end on"""
        ms = self.ms
        class Thread(Obj): kind = 'thread'
        main_thread = self.new_handle(Thread())
        # argv for nn::os::SetHostArgv: the strings, then the pointer array
        blob = b''.join(a.encode() + b'\0' for a in argv); offs = []; o = 0
        for a in argv: offs.append(o); o += len(a.encode()) + 1
        arr = self.ARGV_BASE + len(blob) + (-len(blob) % 8)
        total = arr + 8 * (len(argv) + 1) - self.ARGV_BASE
        if not self.aot.try_map(self.ARGV_BASE, (total + PAGE - 1) & ~(PAGE - 1)): raise RuntimeError('argv region overlaps another')
        self.wr(self.ARGV_BASE, blob + b'\0' * (-len(blob) % 8) + b''.join(struct.pack('<Q', self.ARGV_BASE + x) for x in offs) + b'\0' * 8)
        def run_inits():
            for mod in ms.modules:
                d = mod.m['dynamic']
                if 'INIT' not in d: continue
                r = ms.call(mod.base + d['INIT'], snapshot=False)
                if 'fault' in r: return r
            return {}
        steps = [('_init_libc0', lambda: ms.call('_init_libc0', snapshot=False)),
                 ('nnosInitialize', lambda: ms.call('nnosInitialize', {'x0': main_thread, 'x1': 0}, snapshot=False)),
                 ('_init_libc1', lambda: ms.call('_init_libc1', snapshot=False)),
                 ('nninitInitializeSdkModule', lambda: ms.call('nninitInitializeSdkModule', snapshot=False)),
                 ('SetHostArgc', lambda: ms.call('_ZN2nn2os11SetHostArgcEi', {'x0': len(argv)}, snapshot=False)),
                 ('SetHostArgv', lambda: ms.call('_ZN2nn2os11SetHostArgvEPPc', {'x0': arr}, snapshot=False)),
                 ('nninitStartup', lambda: ms.call('nninitStartup', snapshot=False)),
                 ('_init_libc2', lambda: ms.call('_init_libc2', snapshot=False)),
                 ('DT_INIT', run_inits),
                 ('nnMain', lambda: ms.call('nnMain', snapshot=False))]
        for name, f in steps:
            self.log(f'>>> {name}')
            r = f()
            if 'fault' in r:
                regs = [0] * 31; regs[29], regs[30] = r.get('x29', 0), r.get('x30', 0)
                self.log(f"fault in {name}: {r['fault']} (sp={r.get('sp', 0):#x} x0={r.get('x0', 0):#x} x1={r.get('x1', 0):#x}; call chain: {self.where(regs)})")
                return dict(r, step=name)
            if name == stop_after: return dict(r, step=name)
        return dict(r, step='nnMain')

    # ---- the SVC entry ----
    def svc(self, num, r, orig):
        name = SVC_NAMES.get(num, f'svc{num:#x}')
        self.stats[name] = self.stats.get(name, 0) + 1
        f = getattr(self, f'svc_{name}', None)
        if f is None:
            self.unhandled[name] = self.unhandled.get(name, 0) + 1
            self.log(f'unhandled svc {name} ({num:#x}) at {orig:#x}: x0={r[0]:#x} x1={r[1]:#x} x2={r[2]:#x} x3={r[3]:#x}')
            r[0] = R_NOT_IMPLEMENTED
            return
        before = list(r[:4])
        if self.ring is not None and name in ('WaitProcessWideKeyAtomic', 'SignalProcessWideKey', 'ArbitrateLock', 'ArbitrateUnlock'): self.ring.append((time.monotonic(), self.current().tid if self.current() else 1, name, before[0], before[1], before[2], before[3]))
        t = self.current(); self.thread_state[t.tid if t else 1] = (name, orig, r[29], r[30], time.monotonic())   # where each guest thread last entered the kernel: the report shows who waits on what
        try: f(r)
        except AssertionError as e:   # a guest address the kernel cannot read or write: the call fails, as it does on the device, instead of raising inside the guest's thread
            self.invalid = getattr(self, 'invalid', 0) + 1
            if self.invalid <= 5: self.log(f'{name}: invalid guest address ({e}) at {orig:#x} x0..x2={before[0]:#x},{before[1]:#x},{before[2]:#x}; {self.where(r)}')
            r[0] = R_INVALID_ADDRESS
        if self.verbose: self.log(f'{name}({", ".join(hex(x) for x in before)}) -> {r[0]:#x} {r[1]:#x}')

    # ---- memory ----
    def svc_SetHeapSize(self, r):
        size = r[1]
        if size % 0x200000: r[0] = R_INVALID_SIZE; return
        if size > self.heap_size:
            if not self.aot.try_map(self.HEAP_BASE + self.heap_size, size - self.heap_size): r[0] = R_INVALID_STATE; return
            self.mem_types.append((self.HEAP_BASE + self.heap_size, size - self.heap_size, MT_HEAP, PERM_RW))
        self.heap_size = size
        r[0], r[1] = OK, self.HEAP_BASE

    def svc_MapMemory(self, r):   # dst, src, size: the guest keeps both views; the host only needs dst to be addressable and alias src
        dst, src, size = r[0], r[1], r[2]
        if not self.aot.try_map(dst, size): r[0] = R_INVALID_STATE; return
        self.wr(dst, self.rd(src, size)); self.mem_types.append((dst, size, MT_STACK, PERM_RW)); r[0] = OK

    def svc_UnmapMemory(self, r): r[0] = OK

    def svc_MapPhysicalMemory(self, r):   # addr, size: zeroed memory inside the alias region (the process's extra memory pool)
        addr, size = r[0], r[1]
        if addr % PAGE or size % PAGE: r[0] = R_INVALID_SIZE; return
        if not self.aot.try_map(addr, size): r[0] = R_INVALID_STATE; return
        self.mem_types.append((addr, size, MT_NORMAL, PERM_RW)); r[0] = OK

    def svc_UnmapPhysicalMemory(self, r): r[0] = OK
    def svc_SetMemoryPermission(self, r): r[0] = OK
    def svc_SetMemoryAttribute(self, r): r[0] = OK

    def _region_of(self, a):
        for base, size, _ in self.aot.regions:
            if base <= a < base + size: return base, size
        return None

    def memory_info(self, a):
        a &= ~(PAGE - 1)
        for mod in self.ms.modules:
            if mod.base <= a < mod.base + mod.size:
                end = mod.base + mod.size
                segs = mod.m['segments']
                for s in segs:
                    lo, hi = mod.base + s['vaddr'], mod.base + ((s['vaddr'] + s['memsz'] + PAGE - 1) & ~(PAGE - 1))
                    if lo <= a < hi: return lo, hi - lo, (MT_CODE if s['perms'] == 'r-x' else MT_CODE_DATA), {'r-x': PERM_RX, 'r--': PERM_R}.get(s['perms'], PERM_RW)
                return mod.base, end - mod.base, MT_CODE_DATA, PERM_RW
        for base, size, mt, perm in self.mem_types:
            if base <= a < base + size: return base, size, mt, perm
        reg = self._region_of(a)
        if reg: return reg[0], reg[1], MT_NORMAL, PERM_RW
        return None

    def svc_QueryMemory(self, r):
        out, addr = r[0], r[2]
        mi = self.memory_info(addr)
        if mi is None:   # unmapped: report the hole up to the next mapping, as the kernel does
            nxt = min([b for b, _, _ in self.aot.regions if b > addr] + [1 << 39])
            base, size, mt, perm = addr & ~(PAGE - 1), nxt - (addr & ~(PAGE - 1)), MT_UNMAPPED, PERM_NONE
        else: base, size, mt, perm = mi
        self.wr(out, struct.pack('<QQIIIIII', base, size, mt, 0, perm, 0, 0, 0))
        r[0], r[1] = OK, 0

    # ---- information ----
    def svc_GetInfo(self, r):
        kind, handle, sub = r[1], r[2], r[3]
        # the address space layout, one region each: ASLR 0x08000000.., heap 0x2_0000_0000, alias 0x4_0000_0000, stack 0x6_0000_0000
        v = {0: 0xF, 1: 0xFFFFFFFFFFFFFFFF >> 20, 2: 0x4_0000_0000, 3: 0x1_0000_0000, 4: self.HEAP_BASE, 5: 0x1_0000_0000, 6: 0x1_0000_0000, 7: 0x2000_0000, 8: 0, 9: 0,
             11: 0, 12: 0x0800_0000, 13: 0x7F_F800_0000, 14: 0x6_0000_0000, 15: 0x1_0000_0000, 16: 0, 17: 0, 18: 0x0100E95004038000, 19: self.heap_size, 20: 0x2000_0000, 21: 0, 22: 0}.get(kind)
        if kind == 11: v = 0xF
        if v is None:
            self.log(f'unhandled GetInfo type {kind} handle {handle:#x} sub {sub}'); r[0] = R_INVALID_ENUM; return
        r[0], r[1] = OK, v

    def svc_GetSystemTick(self, r): r[0] = int((time.monotonic() - self.start) * 19200000)
    def svc_GetProcessId(self, r): r[0], r[1] = OK, self.process_id

    def where(self, r, depth=24):
        """The guest call chain as module+offset: x30, then the saved link registers along the x29 frame chain"""
        if not hasattr(self, '_syms'):
            self._syms = sorted((x, n) for mod in self.ms.modules for n, x in mod.exports.items())
        import bisect
        def loc(a):
            for mod in self.ms.modules:
                if mod.base <= a < mod.base + mod.size:
                    i = bisect.bisect_right(self._syms, (a, '\uffff')) - 1
                    near = f' {self._syms[i][1][:70]}+{a - self._syms[i][0]:#x}' if i >= 0 and a - self._syms[i][0] < 0x4000 else ''
                    return f'{mod.name}+{a - mod.base:#x}{near}'
            return f'{a:#x}'
        out, fp = [loc(r[30])], r[29]
        for _ in range(depth):
            try: fp, lr = struct.unpack('<QQ', self.rd(fp, 16))
            except AssertionError: break
            if not lr: break
            out.append(loc(lr))
        return ' <- '.join(o for o in out if '2sf4hipc' not in o and '2sf4cmif' not in o)

    def svc_OutputDebugString(self, r):
        msg = self.rd(r[0], min(r[1], 512)).decode('utf-8', 'replace').rstrip()
        self.log('debug: ' + msg)
        if msg.startswith('Failed assertion'): self.log('  at ' + self.where(r))
        r[0] = OK

    def svc_Break(self, r):
        reason, info, size = r[0], r[1], r[2]
        self.log(f'Break reason {reason:#x} info {info:#x} size {size:#x} at ' + self.where(r))
        self.stop_reason = f'svcBreak({reason:#x})'
        self.on_break(reason, info, size)

    def on_break(self, reason, info, size):
        os._exit(3)   # a ctypes callback cannot unwind the guest: the guest would loop on `b .` after Break

    def svc_ExitProcess(self, r):
        self.log('ExitProcess'); os._exit(0)

    # ---- threads and synchronisation: every guest thread is a Python thread running the recompiled code (the GIL is released while it runs); one condition variable guards the kernel's state ----
    LISTENERS = 0x40000000   # a Horizon mutex word: the owner's handle, plus this bit while threads wait for it
    THREAD_REGIONS = 0x7_0000_0000

    def _init_kernel(self):
        self.cv = threading.Condition()
        self.local = threading.local()
        self.threads, self.mutex_waiters, self.key_waiters = {}, {}, {}
        self._tlr_next = 0
        self._scratch_n = 0

    def current(self):
        return getattr(self.local, 'thread', None)

    SCRATCH_BASE = 0x9_0000_0000
    SCRATCH_SIZE = 0x20000

    def call_guest(self, symbol, regs=None):
        """Run an exported guest function from inside a service handler (a nested run on this thread, on a scratch stack of its own, with this thread's thread pointers). Returns x0; a fault raises"""
        stack = getattr(self.local, 'scratch', None)
        if stack is None:
            with self.cv:
                base = self.SCRATCH_BASE + self._scratch_n * self.SCRATCH_SIZE; self._scratch_n += 1
            if not self.aot.try_map(base, self.SCRATCH_SIZE): raise RuntimeError('no room for a scratch stack')
            stack = self.local.scratch = base + self.SCRATCH_SIZE
        r = self.aot.run(self.ms.symbol(symbol), dict(regs or {}, x30=self.ms.STOP), 0, stack, self.aot.get_tls(0), self.aot.get_tls(1), snapshot=False)
        if 'fault' in r: raise RuntimeError(f'{symbol}: {r["fault"]}')
        return r['x0']

    def svc_GetThreadId(self, r):
        t = self.current(); r[0], r[1] = OK, (t.tid if t else self.thread_id)

    def svc_CloseHandle(self, r):
        if r[0] in self.handles: del self.handles[r[0]]; r[0] = OK
        else: r[0] = R_INVALID_HANDLE

    def svc_SleepThread(self, r):
        ns = r[0]
        if ns > 0 and ns < (1 << 62): time.sleep(ns / 1e9)
        else: time.sleep(0)
        r[0] = OK

    def svc_GetCurrentProcessorNumber(self, r): r[0] = 0
    def svc_GetThreadPriority(self, r):
        t = self.handles.get(r[1]); r[0], r[1] = OK, getattr(t, 'priority', 44)
    def svc_SetThreadPriority(self, r):
        t = self.handles.get(r[0])
        if t is not None: t.priority = r[1] & 0xFFFFFFFF
        r[0] = OK
    def svc_GetThreadCoreMask(self, r): r[0], r[1], r[2] = OK, 0, 0xF
    def svc_SetThreadCoreMask(self, r): r[0] = OK
    def svc_SynchronizePreemptionState(self, r): r[0] = OK

    def _ready(self, o):
        if isinstance(o, Event): return o.signaled
        if isinstance(o, KThread): return o.done
        return True   # a session or the like: a request on it never blocks here

    def _take(self, o):
        if isinstance(o, Event) and o.auto: o.signaled = False

    def svc_WaitSynchronization(self, r):
        ptr, n, timeout = r[1], r[2] & 0xFFFFFFFF, r[3]
        hs = struct.unpack(f'<{n}I', self.rd(ptr, 4 * n)) if n else ()
        deadline = None if timeout >= (1 << 63) or timeout == M64 else time.monotonic() + timeout / 1e9
        with self.cv:
            while True:
                for i, h in enumerate(hs):
                    o = self.handles.get(h)
                    if o is None: r[0] = R_INVALID_HANDLE; return
                    if self._ready(o): self._take(o); r[0], r[1] = OK, i; return
                if deadline is not None and time.monotonic() >= deadline: r[0] = R_TIMEOUT; return
                self.cv.wait(None if deadline is None else max(0.0, deadline - time.monotonic()))

    def svc_ResetSignal(self, r):
        o = self.handles.get(r[0])
        if not isinstance(o, Event): r[0] = R_INVALID_HANDLE; return
        with self.cv:
            r[0] = OK if o.signaled else R_INVALID_STATE
            o.signaled = False

    def svc_ClearEvent(self, r): self.svc_ResetSignal(r)

    def svc_SignalEvent(self, r):
        o = self.handles.get(r[0])
        if not isinstance(o, Event): r[0] = R_INVALID_HANDLE; return
        with self.cv:
            o.signaled = True; self.cv.notify_all()
        r[0] = OK

    def svc_CreateEvent(self, r):   # -> (writable handle, readable handle) of one event
        e = Event(); r[1] = self.new_handle(e); r[2] = self.new_handle(e); r[0] = OK

    def _unlock(self, addr):
        """ArbitrateUnlock: hand the mutex word to the first waiter (keeping the listeners bit while others wait), or clear it"""
        q = self.mutex_waiters.get(addr)
        if q:
            t = q.pop(0); t.granted = True
            self.wr32(addr, t.tag | (self.LISTENERS if q else 0))
            self.cv.notify_all()
        else: self.wr32(addr, 0)

    def _lock(self, addr, tag, owner=None):
        """Take the mutex word, or wait until an unlock hands it to this thread (caller holds self.cv)"""
        me = self.current() or self.main_thread_obj
        while True:
            v = self.rd32(addr)
            if v == 0: self.wr32(addr, tag); return
            if owner is not None and v != (owner | self.LISTENERS): return   # the word changed under the caller: nothing to wait for
            if not v & self.LISTENERS: self.wr32(addr, v | self.LISTENERS)
            me.granted, me.tag = False, tag
            self.mutex_waiters.setdefault(addr, []).append(me)
            while not me.granted: self.cv.wait()
            return

    def svc_ArbitrateLock(self, r):   # (owner thread handle, mutex addr, requesting thread's tag)
        owner, addr, tag = r[0] & 0xFFFFFFFF, r[1], r[2] & 0xFFFFFFFF
        with self.cv:
            v = self.rd32(addr)
            if v != (owner | self.LISTENERS): r[0] = OK; return
            me = self.current() or self.main_thread_obj
            me.granted, me.tag = False, tag
            self.mutex_waiters.setdefault(addr, []).append(me)
            while not me.granted: self.cv.wait()
        r[0] = OK

    def svc_ArbitrateUnlock(self, r):
        with self.cv: self._unlock(r[0])
        r[0] = OK

    def svc_WaitProcessWideKeyAtomic(self, r):   # (mutex addr, key addr, tag, timeout): release the mutex, wait for the key, take the mutex again
        mutex, key, tag, timeout = r[0], r[1], r[2] & 0xFFFFFFFF, r[3]
        deadline = None if timeout >= (1 << 63) else time.monotonic() + timeout / 1e9
        me = self.current() or self.main_thread_obj
        with self.cv:
            self._unlock(mutex)
            me.signaled = False
            self.key_waiters.setdefault(key, []).append(me)
            result = OK
            while not me.signaled:
                if deadline is not None and time.monotonic() >= deadline:
                    if me in self.key_waiters.get(key, []): self.key_waiters[key].remove(me)
                    result = R_TIMEOUT; break
                self.cv.wait(None if deadline is None else max(0.0, deadline - time.monotonic()))
            self._lock(mutex, tag)
        r[0] = result

    def svc_SignalProcessWideKey(self, r):   # (key addr, count): wake that many waiters, all when count <= 0
        key, count = r[0], r[1] & 0xFFFFFFFF
        if count >= 0x80000000: count = 0
        with self.cv:
            q = self.key_waiters.get(key, [])
            for t in (q[:] if count == 0 else q[:count]):
                q.remove(t); t.signaled = True
            self.cv.notify_all()
        r[0] = OK

    def svc_CreateThread(self, r):   # (entry, arg, stack top, priority, core) -> handle
        entry, arg, top, prio = r[1], r[2], r[3], r[4] & 0xFFFFFFFF
        base = self.THREAD_REGIONS + self._tlr_next * PAGE; self._tlr_next += 1
        if not self.aot.try_map(base, PAGE): r[0] = R_INVALID_STATE; return
        t = KThread(self, entry, arg, top, prio, base)
        t.handle = self.new_handle(t); self.threads[t.tid] = t
        tls = bytearray(PAGE); struct.pack_into('<Q', tls, 0x1f8, base + 0x200); struct.pack_into('<Q', tls, 0x110, t.tid)
        self.wr(base, bytes(tls))
        r[0], r[1] = OK, t.handle

    def svc_StartThread(self, r):
        t = self.handles.get(r[0])
        if not isinstance(t, KThread): r[0] = R_INVALID_HANDLE; return
        t.start(); r[0] = OK

    def svc_ExitThread(self, r):
        t = self.current()
        if t is None: self.svc_ExitProcess(r); return
        self.aot.lib.aot_request_exit()   # the run ends in C when this handler returns

    def svc_GetThreadContext3(self, r): r[0] = R_NOT_IMPLEMENTED

    def svc_CreateTransferMemory(self, r):
        class Transfer(Obj): kind = 'transfer'
        o = Transfer(); o.addr, o.size = r[1], r[2]; r[1] = self.new_handle(o); r[0] = OK

    # ---- services and IPC ----
    def svc_ConnectToNamedPort(self, r):
        name = self.cstring(r[1], 12)
        f = self.named_ports.get(name)
        if f is None:
            self.log(f'unhandled named port {name!r}'); r[0] = R_NOT_FOUND; return
        r[0], r[1] = OK, self.new_handle(Session(f(self)))

    def svc_SendSyncRequest(self, r):
        h = r[0]; sess = self.handles.get(h)
        self.regs = r
        self.ipc(sess, self.ms.TLS, r)   # the command buffer is the first 0x100 bytes of the ThreadLocalRegion

    def svc_SendSyncRequestWithUserBuffer(self, r):
        self.ipc(self.handles.get(r[2]), r[0], r)

    def decode(self, base):
        w = struct.unpack('<2I', self.rd(base, 8))
        kind, nx, na, nb, nw = w[0] & 0xFFFF, w[0] >> 16 & 0xF, w[0] >> 20 & 0xF, w[0] >> 24 & 0xF, w[0] >> 28 & 0xF
        raw_words, rcflags, has_handles = w[1] & 0x3FF, w[1] >> 10 & 0xF, w[1] >> 31
        off = 8; pid = None; copy, move = [], []
        if has_handles:
            hw = self.rd32(base + off); off += 4
            if hw & 1: pid = self.rd64(base + off); off += 8
            ncopy, nmove = hw >> 1 & 0xF, hw >> 5 & 0xF
            copy = list(struct.unpack_from(f'<{ncopy}I', self.rd(base + off, 4 * ncopy))) if ncopy else []; off += 4 * ncopy
            move = list(struct.unpack_from(f'<{nmove}I', self.rd(base + off, 4 * nmove))) if nmove else []; off += 4 * nmove
        bufs = []
        off += 8 * nx   # X (pointer) descriptors: not used by the services here
        for k, n in (('A', na), ('B', nb), ('W', nw)):
            for _ in range(n):
                size, lo, packed = struct.unpack('<III', self.rd(base + off, 12)); off += 12
                bufs.append((k, lo | (packed >> 28 & 0xF) << 32 | (packed >> 2 & 7) << 36, size | (packed >> 24 & 0xF) << 32))
        off = (off + 15) & ~15   # raw data is aligned to 16 bytes from the start of the message
        raw = self.rd(base + off, raw_words * 4) if raw_words else b''
        return kind, raw, copy, move, pid, bufs, off

    def ipc(self, sess, base, r):
        if sess is None or not isinstance(sess, Session): r[0] = R_INVALID_HANDLE; return
        kind, raw, copy, move, pid, bufs, off = self.decode(base)
        if kind == 4 or kind == 6:   # Request / RequestWithContext
            # raw: SFCI magic (8 bytes), command id (4), token (4), then the arguments
            cmd = struct.unpack_from('<I', raw, 8)[0] if len(raw) >= 12 else -1
            req = Request('request', cmd, raw[16:], copy, move, pid, bufs)
            resp = sess.service.command(cmd, req)
        elif kind == 5 or kind == 7:   # Control / ControlWithContext: ConvertCurrentObjectToDomain (0), CopyFromCurrentDomain (1), CloneCurrentObject (2), QueryPointerBufferSize (3), CloneEx (4)
            cmd = struct.unpack_from('<I', raw, 8)[0] if len(raw) >= 12 else -1
            if cmd in (2, 4):
                s2 = Session(sess.service); resp = Response(OK, b'', move=[], objects=[s2])
            elif cmd == 3: resp = Response(OK, struct.pack('<I', 0x8000))
            else: self.log(f'unhandled control command {cmd}'); resp = Response(R_NOT_IMPLEMENTED)
        elif kind == 2:   # Close
            r[0] = OK; return
        else:
            self.log(f'unhandled IPC message type {kind} on {sess.service.name}: nonzero TLR words {[(hex(o), hex(v)) for o, v in enumerate(struct.unpack('<%dI' % 0x100, self.rd(self.ms.TLS, 0x400))) if v][:20]} lr {self.where(r)}'); r[0] = R_NOT_IMPLEMENTED; return
        self.reply(base, resp); r[0] = OK

    def reply(self, base, resp):
        """A Response as an IPC reply at `base`: header, handle descriptor, then (16-byte aligned) SFCO, the result and the raw data"""
        handles = [self.new_handle(o) for o in resp.objects] + list(resp.move)
        data = b'SFCO' + struct.pack('<III', 0, resp.result, 0) + resp.raw
        data = data.ljust((len(data) + 3) & ~3, b'\0')
        has_h = 1 if (handles or resp.copy) else 0
        out = struct.pack('<2I', 0, (len(data) // 4 + 4 & 0x3FF) | (has_h << 31))   # +4 words: the slack the 16-byte alignment may take
        if has_h:
            out += struct.pack('<I', (len(resp.copy) << 1) | (len(handles) << 5))
            out += b''.join(struct.pack('<I', c) for c in resp.copy) + b''.join(struct.pack('<I', h) for h in handles)
        out += b'\0' * (-len(out) % 16)
        self.wr(base, out + data)
