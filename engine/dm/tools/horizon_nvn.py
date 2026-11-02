"""NVN on the Linux host (NX2Linux S7bis): the game's graphics API, replaced wholesale (the SDK's NVN driver does not run), as engine/dm/gfx/nvn/hle_nvn.c does on Mach. nvnBootstrapLoader hands the game
host functions for the NVN names it asks for; the objects the game allocates (device, pools, textures, queue, command buffers, window) are tracked here by their guest address.
What exists: devices and limits (Tegra X1 values), memory pools and buffers over the game's own memory, texture storage sizes (nvn_layout.py), sync objects, events, queues, command buffers that record
and replay at submit (render targets, clears, copies), and the window whose textures are shown through horizon_display. Draws, state and shaders are accepted and counted, not rendered: the
picture is what the game clears and copies. Anything not implemented answers 0 and is logged once with its name and arguments, never faked as a result the game could act on."""
import os, re, struct, threading, time
from pathlib import Path

import nvn_layout
from horizon_overrides import hle

OK_BOOL = 1
SYNC_ALREADY_SIGNALED, SYNC_TIMEOUT = 0, 2
NAMES_FILE = Path(__file__).resolve().parent.parent / 'gfx/nvn/names.txt'
M32 = 0xFFFFFFFF
FLAG_NO_CPU = 0x1

# Tegra X1 device properties, measured by calling nvnDeviceGetInteger item by item (nnheaders nvn_DeviceConstantsNX.h; engine/dm/gfx/nvn/hle_nvn.c g_devinfo)
DEVINFO = [55, 15, 14, 65536, 256, 8, 16, 4, 16, 32, 16, 4, 4, 4, 16, 32, 32, 256, 256, 4, 8, 512, 256, 2, 256, 4096, 131072, 16, 1536, 1024, 64, 1536, 65535, 65535, 65535, 8, 1048576, 4096,
           16, 4096, 1, 1, 1, 262144, 15, 2048, 1, 1, 14, 5, 1, 8, 8, 4, 32, 128, 32, 128, 65536, 1, 4, 16384, 134217728, 2048, 16384, 16384, 1, 1, 1, 1, 5, 1, 1, 1, 1, 1, 1, 16, 1024, 32, 4096,
           65536, 65536, 256, 16384, 262144, 4096, 1, 4, 2, 0, 4096, 16384, 4096, 128, 4096]


class Obj(dict):
    """Per-object state keyed by the guest address of the NVN struct"""


class Nvn:
    def __init__(self, hz, api=(53, 313)):
        self.hz, self.api = hz, api
        self.names = [n.strip() for n in NAMES_FILE.read_text().split() if n.strip()] if NAMES_FILE.exists() else []
        self.known = set(self.names)
        self.addr = {}       # name -> import address
        self.o = {}          # guest address -> Obj
        self.cmdlists = {}   # command handle -> recorded commands
        self._handle = 0x1000
        self.unimpl, self.calls, self.clear_colors = {}, {}, {}
        self.trace, self.trace_names = int(os.environ.get('NX2LINUX_NVN_TRACE', '0')), set(filter(None, os.environ.get('NX2LINUX_NVN_TRACE_NAMES', '*').split(',')))
        self.stats = {'submits': 0, 'draws': 0, 'clears': 0, 'copies': 0, 'presents': 0}
        self.display = None
        self.display_factory = None   # called on the first present, on the presenting thread: SDL and Vulkan then live on one thread

    # ---- plumbing ----
    def get(self, a, **init):
        if a not in self.o: self.o[a] = Obj(init)
        return self.o[a]

    def lookup(self, name):
        """The import address of an NVN function, registered on first request"""
        if name in self.addr: return self.addr[name]
        if name not in self.known:
            self.hz.log(f'[nvn] address lookup of an unknown name: {name}')
            return 0
        a = self.hz.ms._import_address(name)
        impl = IMPL.get(name)
        if impl is None:
            if name.endswith('Initialize'): impl = lambda nvn, r: r.__setitem__(0, OK_BOOL)
            else: impl = self.unimplemented(name)
        calls = self.calls
        def fn(r, f=impl, name=name):
            calls[name] = calls.get(name, 0) + 1
            if self.trace and name in self.trace_names or (self.trace and self.trace_names == {'*'}):
                self.trace -= 1; self.hz.log(f'[nvn.trace] {name}({r[0]:#x}, {r[1]:#x}, {r[2]:#x}) thread={threading.current_thread().name}')
            f(self, r)
        if self.hz.aot.register_import(a, fn, name): raise RuntimeError(f'cannot register {name}')
        self.addr[name] = a
        return a

    def unimplemented(self, name):
        def f(nvn, r):
            nvn.unimpl[name] = nvn.unimpl.get(name, 0) + 1
            if nvn.unimpl[name] == 1 and nvn.hz.verbose_nvn:
                nvn.hz.log(f'[nvn] {name} not implemented (x0={r[0]:#x} x1={r[1]:#x} x2={r[2]:#x}); answers 0')
            r[0] = 0
        return f

    def new_handle(self):
        self._handle += 8; return self._handle

    def pool_addr(self, pool_ptr, off=0):
        p = self.o.get(pool_ptr)
        return (p['mem'] if p else 0) + off

    def texture_bitmap(self, t):
        if 'bitmap' not in t:
            t['bitmap'] = bytearray(t['w'] * t['h'] * 4)
        return t['bitmap']

    # ---- replay of a submitted command list ----
    def replay(self, cmds, state):
        for c in cmds:
            op = c[0]
            if op == 'targets': state['rt'] = c[1]
            elif op == 'clear':
                _, index, color, mask = c
                if 0 <= index < len(state.get('rt', [])): self.fill(state['rt'][index], color, mask)
                self.stats['clears'] += 1
            elif op == 'cleartex': self.fill(c[1], c[2], c[3]); self.stats['clears'] += 1
            elif op == 'call':
                for h in c[1]: self.replay(self.cmdlists.get(h, []), state)
            elif op == 'draw': self.stats['draws'] += 1
            elif op == 'signal': self.signal_event(*c[1:])
            elif op == 'copy': self.stats['copies'] += 1

    def window_textures(self):
        return {t for w in self.o.values() if w.get('kind') == 'window' for t in w['textures']}

    def fill(self, tex_ptr, color, mask):
        t = self.o.get(tex_ptr)
        if not t or not t.get('w'): return
        self.clear_colors[(tex_ptr in self.window_textures(), tuple(round(v, 2) for v in color))] = self.clear_colors.get((tex_ptr in self.window_textures(), tuple(round(v, 2) for v in color)), 0) + 1
        bm = self.texture_bitmap(t)
        px = bytes(int(max(0.0, min(1.0, v)) * 255 + 0.5) for v in color)
        if mask & 0xF == 0xF: bm[:] = px * (len(bm) // 4)
        else:
            for i in range(4):
                if mask >> i & 1: bm[i::4] = bytes([px[i]]) * (len(bm) // 4)

    def signal_event(self, ev_ptr, mode, value):
        e = self.o.get(ev_ptr)
        if not e: return
        a = self.pool_addr(e['pool'], e['off'])
        cur = self.hz.rd32(a)
        self.hz.wr32(a, (cur + value) & M32 if mode == 1 else value)

    def present(self, win_ptr, index):
        w = self.o.get(win_ptr)
        self.stats['presents'] += 1
        self.hz.frames_presented = self.stats['presents']
        if not w or not w['textures']: return
        t = self.o.get(w['textures'][index % len(w['textures'])])
        if not t or not t.get('w'): return
        dump = os.environ.get('NX2LINUX_DUMP_FRAME')   # NX2LINUX_DUMP_FRAME=<dir>: every 30th presented frame is written there as a PNG
        if dump and self.stats['presents'] % 30 == 1:
            os.makedirs(dump, exist_ok=True); write_png(os.path.join(dump, f'frame{self.stats["presents"]:05d}.png'), t['w'], t['h'], bytes(self.texture_bitmap(t)))
        if self.display is None and self.display_factory is not None:
            try:
                self.display = self.display_factory(); self.hz.log(f'[nvn] window open on {self.display.gpu}')
            except Exception as e:
                self.hz.log(f'[nvn] cannot open the display: {e}'); self.display_factory = None
        if self.display is not None:
            try:
                self.display.present(bytes(self.texture_bitmap(t)), t['w'], t['h'])
                for ev in self.display.events():
                    if ev[0] == 'quit': self.hz.log('window closed'); os._exit(0)
            except Exception as e: self.hz.log(f'[nvn] present failed: {e}')


def write_png(path, w, h, rgba):
    import zlib
    def chunk(tag, data): return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b''.join(b'\0' + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))
    open(path, 'wb').write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw, 1)) + chunk(b'IEND', b''))


IMPL = {}


def nvn(name):
    def deco(fn):
        IMPL[name] = fn
        return fn
    return deco


def ret(r, v): r[0] = v & 0xFFFFFFFFFFFFFFFF


# ---- device ----
@nvn('nvnDeviceBuilderSetDefaults')
def _(n, r): n.get(r[0]).clear()
@nvn('nvnDeviceBuilderSetFlags')
def _(n, r): n.get(r[0])['flags'] = r[1] & M32
@nvn('nvnDeviceInitialize')
def _(n, r): n.get(r[0], origin=0, depth=0).update(kind='device'); n.device = r[0]; ret(r, 1)
@nvn('nvnDeviceFinalize')
def _(n, r): n.o.pop(r[0], None)
@nvn('nvnDeviceGetProcAddress')
def _(n, r): ret(r, n.lookup(n.hz.cstring(r[1])))
@nvn('nvnDeviceGetInteger')
def _(n, r):
    what = r[1] & M32
    v = n.api[what] if what <= 1 else 256 if what == 0x67 else DEVINFO[what] if what < len(DEVINFO) else 0
    if what == 0x2E: v = 1
    if what == 0x30: v = 16
    n.hz.wr32(r[2], v)
@nvn('nvnDeviceGetImageHandle')
def _(n, r): ret(r, r[1] & M32)
@nvn('nvnDeviceGetSeparateTextureHandle')
def _(n, r): ret(r, r[1] & M32)
@nvn('nvnDeviceGetSeparateSamplerHandle')
def _(n, r): ret(r, (r[1] & M32) << 20)
@nvn('nvnDeviceGetTextureHandle')
def _(n, r): ret(r, (r[1] & M32) | (r[2] & M32) << 20)
@nvn('nvnDeviceGetTexelFetchHandle')
def _(n, r): ret(r, r[1] & M32)
@nvn('nvnDeviceSetWindowOriginMode')
def _(n, r): n.get(r[0])['origin'] = r[1] & M32
@nvn('nvnDeviceGetWindowOriginMode')
def _(n, r): ret(r, n.get(r[0]).get('origin', 0))
@nvn('nvnDeviceSetDepthMode')
def _(n, r): n.get(r[0])['depth'] = r[1] & M32
@nvn('nvnDeviceGetDepthMode')
def _(n, r): ret(r, n.get(r[0]).get('depth', 0))
@nvn('nvnDeviceGetCurrentTimestampInNanoseconds')
def _(n, r): ret(r, time.monotonic_ns())
@nvn('nvnDeviceInstallDebugCallback')
def _(n, r): pass
@nvn('nvnDeviceFinalizeCommandHandle')
def _(n, r): pass


# ---- memory pools and buffers: over the memory the game gives (GPU address = CPU address) ----
@nvn('nvnMemoryPoolBuilderSetDefaults')
def _(n, r): n.o[r[0]] = Obj()
@nvn('nvnMemoryPoolBuilderSetDevice')
def _(n, r): n.get(r[0])['dev'] = r[1]
@nvn('nvnMemoryPoolBuilderSetStorage')
def _(n, r): n.get(r[0]).update(mem=r[1], size=r[2])
@nvn('nvnMemoryPoolBuilderSetFlags')
def _(n, r): n.get(r[0])['flags'] = r[1] & M32
@nvn('nvnMemoryPoolInitialize')
def _(n, r):
    b = n.get(r[1])
    mem = b.get('mem', 0)
    if not mem and b.get('flags', 0) & 0x200:   # a virtual pool: a range of addresses of its own until pages are mapped into it
        mem = n.virtual_base = getattr(n, 'virtual_base', 0xA_0000_0000) + (b.get('size', 0) + 0xFFFF & ~0xFFFF)
        n.hz.aot.try_map(mem, b.get('size', 0) + 0xFFF & ~0xFFF)
    n.o[r[0]] = Obj(kind='pool', mem=mem, size=b.get('size', 0), flags=b.get('flags', 0))
    ret(r, 1)
@nvn('nvnMemoryPoolFinalize')
def _(n, r): n.o.pop(r[0], None)
@nvn('nvnMemoryPoolGetBufferAddress')
def _(n, r): ret(r, n.get(r[0]).get('mem', 0))
@nvn('nvnMemoryPoolMap')
def _(n, r): p = n.get(r[0]); ret(r, 0 if p.get('flags', 0) & FLAG_NO_CPU else p.get('mem', 0))
@nvn('nvnMemoryPoolGetSize')
def _(n, r): ret(r, n.get(r[0]).get('size', 0))
@nvn('nvnMemoryPoolGetFlags')
def _(n, r): ret(r, n.get(r[0]).get('flags', 0))
@nvn('nvnMemoryPoolFlushMappedRange')
def _(n, r): pass
@nvn('nvnBufferBuilderSetDefaults')
def _(n, r): n.o[r[0]] = Obj()
@nvn('nvnBufferBuilderSetDevice')
def _(n, r): pass
@nvn('nvnBufferBuilderSetStorage')
def _(n, r): n.get(r[0]).update(pool=r[1], off=r[2], size=r[3])
@nvn('nvnBufferInitialize')
def _(n, r): n.o[r[0]] = Obj(kind='buffer', **{k: v for k, v in n.get(r[1]).items()}); ret(r, 1)
@nvn('nvnBufferFinalize')
def _(n, r): n.o.pop(r[0], None)
@nvn('nvnBufferGetAddress')
def _(n, r): b = n.get(r[0]); ret(r, n.pool_addr(b.get('pool', 0), b.get('off', 0)))
@nvn('nvnBufferMap')
def _(n, r):
    b = n.get(r[0]); p = n.o.get(b.get('pool', 0), {})
    ret(r, 0 if p.get('flags', 0) & FLAG_NO_CPU else n.pool_addr(b.get('pool', 0), b.get('off', 0)))
@nvn('nvnBufferGetSize')
def _(n, r): ret(r, n.get(r[0]).get('size', 0))
@nvn('nvnBufferGetMemoryPool')
def _(n, r): ret(r, n.get(r[0]).get('pool', 0))
@nvn('nvnBufferGetMemoryOffset')
def _(n, r): ret(r, n.get(r[0]).get('off', 0))
@nvn('nvnBufferFlushMappedRange')
def _(n, r): pass
@nvn('nvnBufferInvalidateMappedRange')
def _(n, r): pass


# ---- descriptor pools ----
@nvn('nvnTexturePoolInitialize')
def _(n, r): n.o[r[0]] = Obj(kind='texpool', pool=r[1], off=r[2], count=r[3] & M32, tex={}); ret(r, 1)
@nvn('nvnTexturePoolRegisterTexture')
def _(n, r): n.get(r[0]).setdefault('tex', {})[r[1] & M32] = r[2]
@nvn('nvnSamplerPoolInitialize')
def _(n, r): n.o[r[0]] = Obj(kind='samplerpool', pool=r[1], off=r[2], count=r[3] & M32); ret(r, 1)


# ---- textures ----
def desc_of(b): return nvn_layout.Desc(**{k: b.get(k, getattr(nvn_layout.Desc, k)) for k in ('target', 'width', 'height', 'depth', 'levels', 'samples', 'format', 'flags', 'stride', 'pkg_block_h')})


@nvn('nvnTextureBuilderSetDefaults')
def _(n, r): n.o[r[0]] = Obj(target=1, width=0, height=0, depth=0, levels=1, samples=0, format=0, flags=0, stride=0, pkg_block_h=0)
for _name, _key in (('Target', 'target'), ('Width', 'width'), ('Height', 'height'), ('Depth', 'depth'), ('Levels', 'levels'), ('Format', 'format'), ('Samples', 'samples'), ('Flags', 'flags'), ('Stride', 'stride')):
    IMPL[f'nvnTextureBuilderSet{_name}'] = (lambda k: lambda n, r: n.get(r[0]).__setitem__(k, r[1] & M32))(_key)
@nvn('nvnTextureBuilderSetDevice')
def _(n, r): pass
@nvn('nvnTextureBuilderSetSize1D')
def _(n, r): n.get(r[0]).update(width=r[1] & M32)
@nvn('nvnTextureBuilderSetSize2D')
def _(n, r): n.get(r[0]).update(width=r[1] & M32, height=r[2] & M32)
@nvn('nvnTextureBuilderSetSize3D')
def _(n, r): n.get(r[0]).update(width=r[1] & M32, height=r[2] & M32, depth=r[3] & M32)
@nvn('nvnTextureBuilderSetStorage')
def _(n, r): n.get(r[0]).update(pool=r[1], off=r[2])
@nvn('nvnTextureBuilderSetPackagedTextureLayout')
def _(n, r): n.get(r[0])['pkg_block_h'] = (n.hz.rd(r[1], 1)[0] + 1) if r[1] else 0
@nvn('nvnTextureBuilderGetStorageSize')
def _(n, r): ret(r, nvn_layout.layout(desc_of(n.get(r[0]))).size)
@nvn('nvnTextureBuilderGetStorageAlignment')
def _(n, r): ret(r, nvn_layout.layout(desc_of(n.get(r[0]))).align)
for _name, _key in (('Target', 'target'), ('Width', 'width'), ('Height', 'height'), ('Depth', 'depth'), ('Levels', 'levels'), ('Format', 'format'), ('Samples', 'samples'), ('Flags', 'flags'), ('Stride', 'stride'), ('MemoryPool', 'pool'), ('MemoryOffset', 'off')):
    IMPL[f'nvnTextureBuilderGet{_name}'] = (lambda k: lambda n, r: ret(r, n.get(r[0]).get(k, 0)))(_key)
@nvn('nvnTextureInitialize')
def _(n, r):
    b = n.get(r[1])
    d = desc_of(b)
    n.o[r[0]] = Obj(kind='texture', w=max(1, d.width), h=max(1, d.height) if d.target != 0 else 1, d=d, pool=b.get('pool', 0), off=b.get('off', 0), flags=d.flags)
    ret(r, 1)
@nvn('nvnTextureFinalize')
def _(n, r): n.o.pop(r[0], None)
for _name, _get in (('Width', lambda t: t['d'].width), ('Height', lambda t: t['d'].height), ('Depth', lambda t: t['d'].depth), ('Levels', lambda t: t['d'].levels), ('Samples', lambda t: t['d'].samples),
                    ('Format', lambda t: t['d'].format), ('Target', lambda t: t['d'].target), ('Flags', lambda t: t['d'].flags), ('Stride', lambda t: t['d'].stride),
                    ('StorageSize', lambda t: nvn_layout.layout(t['d']).size), ('MemoryPool', lambda t: t['pool']), ('MemoryOffset', lambda t: t['off'])):
    IMPL[f'nvnTextureGet{_name}'] = (lambda g: lambda n, r: ret(r, g(n.get(r[0])) if 'd' in n.get(r[0]) else 0))(_get)
@nvn('nvnTextureGetTextureAddress')
def _(n, r): t = n.get(r[0]); ret(r, (n.pool_addr(t.get('pool', 0), t.get('off', 0)) if t else 0))
@nvn('nvnTextureGetZCullStorageSize')
def _(n, r): ret(r, 0)


# ---- sync, events ----
@nvn('nvnSyncInitialize')
def _(n, r): n.o[r[0]] = Obj(kind='sync', fenced=False); ret(r, 1)
@nvn('nvnSyncFinalize')
def _(n, r): n.o.pop(r[0], None)
@nvn('nvnQueueFenceSync')
def _(n, r): n.get(r[1])['fenced'] = True
@nvn('nvnQueueWaitSync')
def _(n, r): ret(r, 1)
@nvn('nvnSyncWait')
def _(n, r): ret(r, SYNC_ALREADY_SIGNALED if n.get(r[0]).get('fenced') else SYNC_TIMEOUT)
@nvn('nvnEventBuilderSetDefaults')
def _(n, r): n.o[r[0]] = Obj()
@nvn('nvnEventBuilderSetStorage')
def _(n, r): n.get(r[0]).update(pool=r[1], off=r[2])
@nvn('nvnEventInitialize')
def _(n, r): n.o[r[0]] = Obj(kind='event', pool=n.get(r[1]).get('pool', 0), off=n.get(r[1]).get('off', 0)); ret(r, 1)
@nvn('nvnEventFinalize')
def _(n, r): n.o.pop(r[0], None)
@nvn('nvnEventGetValue')
def _(n, r): e = n.get(r[0]); ret(r, n.hz.rd32(n.pool_addr(e.get('pool', 0), e.get('off', 0))))
@nvn('nvnEventSignal')
def _(n, r): n.signal_event(r[0], r[1] & M32, r[2] & M32)


# ---- queue ----
@nvn('nvnQueueBuilderSetDefaults')
def _(n, r): n.o[r[0]] = Obj(cmd=0x40000, comp=0x40000, ctl=0x40000)
@nvn('nvnQueueBuilderSetCommandMemorySize')
def _(n, r): n.get(r[0])['cmd'] = r[1]
@nvn('nvnQueueBuilderSetComputeMemorySize')
def _(n, r): n.get(r[0])['comp'] = r[1]
@nvn('nvnQueueBuilderSetControlMemorySize')
def _(n, r): n.get(r[0])['ctl'] = r[1]
@nvn('nvnQueueBuilderGetQueueMemorySize')
def _(n, r): b = n.get(r[0]); ret(r, (b.get('cmd', 0) + b.get('comp', 0) + b.get('ctl', 0) + 4095) & ~4095)
@nvn('nvnQueueInitialize')
def _(n, r): n.o[r[0]] = Obj(kind='queue'); ret(r, 1)
@nvn('nvnQueueFinalize')
def _(n, r): n.o.pop(r[0], None)
@nvn('nvnQueueGetError')
def _(n, r): ret(r, 0)
@nvn('nvnQueueFlush')
def _(n, r): pass
@nvn('nvnQueueFinish')
def _(n, r): pass
@nvn('nvnQueueSubmitCommands')
def _(n, r):
    count = r[1] & M32
    handles = struct.unpack(f'<{count}Q', n.hz.rd(r[2], 8 * count)) if count else ()
    n.stats['submits'] += 1
    state = {}
    for h in handles: n.replay(n.cmdlists.get(h, []), state)


# ---- command buffers: recorded in Python, replayed at submit ----
@nvn('nvnCommandBufferInitialize')
def _(n, r): n.o[r[0]] = Obj(kind='cmd', cmds=[], recording=False); ret(r, 1)
@nvn('nvnCommandBufferFinalize')
def _(n, r): n.o.pop(r[0], None)
@nvn('nvnCommandBufferSetMemoryCallback')
def _(n, r): pass
@nvn('nvnCommandBufferSetMemoryCallbackData')
def _(n, r): pass
@nvn('nvnCommandBufferAddCommandMemory')
def _(n, r): pass
@nvn('nvnCommandBufferAddControlMemory')
def _(n, r): pass
@nvn('nvnCommandBufferBeginRecording')
def _(n, r): c = n.get(r[0]); c['cmds'] = []; c['recording'] = True
@nvn('nvnCommandBufferEndRecording')
def _(n, r):
    c = n.get(r[0]); c['recording'] = False
    h = n.new_handle(); n.cmdlists[h] = c['cmds']; c['used'] = 16 * (len(c['cmds']) + 1); c['cmds'] = []
    ret(r, h)
@nvn('nvnCommandBufferGetCommandMemoryUsed')
def _(n, r): c = n.get(r[0]); ret(r, c.get('used', 0) if not c.get('recording') else 16 * (len(c.get('cmds', [])) + 1))
@nvn('nvnCommandBufferGetControlMemoryUsed')
def _(n, r): ret(r, 0)
@nvn('nvnCommandBufferIsRecording')
def _(n, r): ret(r, int(bool(n.get(r[0]).get('recording'))))
@nvn('nvnCommandBufferCopyCommands')
def _(n, r): c = n.get(r[0]); k = r[1] & M32; c.setdefault('cmds', []).append(('call', struct.unpack(f'<{k}Q', n.hz.rd(r[2], 8 * k)) if k else ()))
IMPL['nvnCommandBufferCallCommands'] = IMPL['nvnCommandBufferCopyCommands']
@nvn('nvnCommandBufferSetRenderTargets')
def _(n, r):
    k = r[1] & M32
    n.get(r[0]).setdefault('cmds', []).append(('targets', list(struct.unpack(f'<{k}Q', n.hz.rd(r[2], 8 * k))) if k else []))
@nvn('nvnCommandBufferClearColor')
def _(n, r): n.get(r[0]).setdefault('cmds', []).append(('clear', r[1] & M32, struct.unpack('<4f', n.hz.rd(r[2], 16)), r[3] & M32))
@nvn('nvnCommandBufferClearTexture')
def _(n, r): n.get(r[0]).setdefault('cmds', []).append(('cleartex', r[1], struct.unpack('<4f', n.hz.rd(r[4], 16)), r[5] & M32))
@nvn('nvnCommandBufferSignalEvent')
def _(n, r): n.get(r[0]).setdefault('cmds', []).append(('signal', r[1], r[2] & M32, r[5] & M32))
@nvn('nvnCommandBufferFenceSync')
def _(n, r): n.get(r[1])['fenced'] = True
for _name in ('DrawArrays', 'DrawArraysInstanced', 'DrawArraysIndirect', 'DrawElements', 'DrawElementsBaseVertex', 'DrawElementsInstanced', 'DrawElementsIndirect', 'DrawTexture'):
    IMPL[f'nvnCommandBuffer{_name}'] = lambda n, r: n.get(r[0]).setdefault('cmds', []).append(('draw',))
for _name in ('CopyBufferToTexture', 'CopyTextureToBuffer', 'CopyTextureToTexture', 'CopyBufferToBuffer'):
    IMPL[f'nvnCommandBuffer{_name}'] = lambda n, r: n.get(r[0]).setdefault('cmds', []).append(('copy',))


# ---- window ----
@nvn('nvnWindowBuilderSetDefaults')
def _(n, r): n.o[r[0]] = Obj(textures=[])
@nvn('nvnWindowBuilderSetTextures')
def _(n, r): k = r[1] & M32; n.get(r[0])['textures'] = list(struct.unpack(f'<{k}Q', n.hz.rd(r[2], 8 * k))) if k else []
@nvn('nvnWindowBuilderSetNumActiveTextures')
def _(n, r): n.get(r[0])['active'] = r[1] & M32
@nvn('nvnWindowInitialize')
def _(n, r): b = n.get(r[1]); n.o[r[0]] = Obj(kind='window', textures=list(b.get('textures', [])), next=0); ret(r, 1)
@nvn('nvnWindowFinalize')
def _(n, r): n.o.pop(r[0], None)
@nvn('nvnWindowAcquireTexture')
def _(n, r):
    w = n.get(r[0]); k = len(w.get('textures', []))
    idx = w['next'] % k if k else 0; w['next'] = w.get('next', 0) + 1
    n.hz.wr32(r[2], idx)
    if r[1]: n.get(r[1])['fenced'] = True
    ret(r, 0)
@nvn('nvnQueueAcquireTexture')
def _(n, r): IMPL['nvnWindowAcquireTexture'](n, [r[1], r[2], r[3]]); ret(r, 0)
@nvn('nvnQueuePresentTexture')
def _(n, r): n.present(r[1], r[2] & M32)
@nvn('nvnWindowSetPresentInterval')
def _(n, r): pass
@nvn('nvnWindowGetPresentInterval')
def _(n, r): ret(r, 1)


# ---- the entry point the game imports ----
@hle('nvnBootstrapLoader', 'the NVN graphics API is replaced wholesale with a host device; the SDK\'s NVN driver does not run')
def bootstrap_loader(hz, r):
    if not hasattr(hz, 'nvn'):
        hz.nvn = Nvn(hz); hz.nvn.display_factory = hz.display_factory
    r[0] = hz.nvn.lookup(hz.cstring(r[0]))
