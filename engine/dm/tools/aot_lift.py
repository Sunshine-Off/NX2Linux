#!/usr/bin/env python3
"""Ahead-of-time recompilation of AArch64 functions to x86_64 (NX2Linux T3): each function is lifted to LLVM IR by Remill's remill-lift, compiled by clang++ with the host runtime (aot_runtime.cpp)
and a generated table of the functions' addresses, into one shared library that Aot wraps for ctypes.
  · the lifter is an existing brick (Remill); this file only drives it. REMILL_BUILD is Remill's build folder (bin/lift/remill-lift-*); REMILL_SRC its source folder (default: REMILL_BUILD/..)
  · a function Remill cannot lift (its IR calls __remill_error) is a LiftError naming the address, the word and the instruction; an intrinsic the runtime does not define (svc, floating point...)
    is a LinkError naming the symbols (the library is linked with --no-undefined): nothing is translated approximately
  · what a direct `bl` or `b` reaches is resolved by the linker (sub_<address>); an indirect call or jump goes through the runtime's table (aot_runtime.cpp)
Usage as a module (tests/test_aot_t3.py): Aot([(address, code bytes)]) then .map(), .run(pc, regs) and .stat()."""
import concurrent.futures, ctypes, glob, hashlib, itertools, json, os, re, struct, subprocess, tempfile, threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
RET = 0xD65F03C0
STATS = ('indirect_calls', 'indirect_jumps', 'fallback_requests', 'reads', 'writes', 'regions', 'jit_entries', 'jit_to_aot', 'jit_invalidations', 'jit_pages', 'unlifted_calls', 'svc_calls', 'import_calls')


def missing_symbols(linker_stderr):
    """The symbols a failed link reports as undefined, in the linker's English or French wording (« name » has a space inside the quotes)"""
    return sorted(set(re.findall(r'(?:undefined reference to|référence indéfinie vers)[\s«`\'"]*([A-Za-z_0-9]+)', linker_stderr)))


class LiftError(Exception):
    pass


class LinkError(Exception):
    pass


def lifter():
    """remill-lift of the Remill build named by REMILL_BUILD, or None"""
    b = os.environ.get('REMILL_BUILD')
    return next(iter(sorted(glob.glob(os.path.join(b, 'bin/lift/remill-lift-*')))), None) if b else None


def dynarmic_dir():
    """The dynarmic folder named by DYNARMIC_DIR (its source with a build/ folder built with CMAKE_POSITION_INDEPENDENT_CODE=ON)"""
    d = os.environ.get('DYNARMIC_DIR')
    if not d or not os.path.exists(os.path.join(d, 'build/src/dynarmic/libdynarmic.a')): raise LinkError('the JIT needs DYNARMIC_DIR, a dynarmic checkout built with -DCMAKE_POSITION_INDEPENDENT_CODE=ON')
    return d


def include_dir():
    return os.environ.get('REMILL_SRC', os.path.join(os.environ['REMILL_BUILD'], '..'))


def table_obj(table, base, d, n):
    """The generated address table compiled on its own (the JIT build links objects, not sources)"""
    obj = os.path.join(d, f'table{n}.o')
    r = subprocess.run(['clang++', '-c', '-std=c++17', *base, table, '-o', obj], capture_output=True, text=True, errors='replace')
    if r.returncode: raise LinkError('build of the address table failed:\n' + r.stderr[-2000:])
    return obj


_objs = {}
_objs_dir = tempfile.TemporaryDirectory()


def fixed_obj(src, std, base, dyn):
    """The runtime and the JIT do not depend on the functions: compiled once per process"""
    if (src, std) not in _objs:
        obj = os.path.join(_objs_dir.name, f'{Path(src).stem}.o')
        r = subprocess.run(['clang++', '-c', f'-std={std}', *base, f'-I{dyn}/src', f'-I{dyn}/externals/mcl/include', f'-I{dyn}/externals/fmt/include', f'-I{dyn}/build/src', str(HERE / src), '-o', obj], capture_output=True, text=True, errors='replace')
        if r.returncode: raise LinkError(f'build of {src} failed:\n' + r.stderr[-2000:])
        _objs[src, std] = obj
    return _objs[src, std]


def _disasm(word, address):
    try:
        import capstone
        i = next(capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM).disasm(struct.pack('<I', word), address))
        return f'{i.mnemonic} {i.op_str}'.strip()
    except Exception:
        return '?'


MAX_LIFT_BYTES = 60000   # remill-lift takes the code as one hex argument, and Linux caps an argument at 128 KiB: a longer function cannot be handed over


def _is_trap(word, address):
    """An instruction no decoder knows, or UDF: code that is meant to fault if it ever runs (the compiler's unreachable/assert trap, e.g. 0xe7ffdefe)"""
    d = _disasm(word, address)
    return d == '?' or d.startswith('udf')


def _run_lift(code, address, ll, entry=None):
    if len(code) > MAX_LIFT_BYTES:
        raise LiftError(f'function {address:#x}: {len(code)} bytes are more than the {MAX_LIFT_BYTES} remill-lift accepts on its command line (one hex argument, 128 KiB at most)')
    subprocess.run([lifter(), '--arch', 'aarch64', '--address', hex(address), *(['--entry_address', hex(entry)] if entry is not None else []), '--bytes', code.hex(), '--ir_out', ll], check=True, capture_output=True, text=True, errors='replace')
    return '@__remill_error' in Path(ll).read_text()


def lift(word, ll, address=0x1000):
    """Lift one instruction (+ ret) to the IR file ll; LiftError when Remill has no semantics for it"""
    if _run_lift(struct.pack('<2I', word, RET), address, ll):
        raise LiftError(f'{address:#x}: {word:#010x} ({_disasm(word, address)}): Remill has no semantics for this instruction (the lifted code calls __remill_error)')


def lift_function(code, address, ll, entry=None):
    """Lift the function whose bytes are code, at address, to ll; LiftError naming the first instruction Remill cannot lift.
    entry: lift from this address inside the bytes instead of from their start (a case of a switch, reached by the jump table), the whole function being there for the branches that go back before it"""
    if not _run_lift(code, address, ll, entry): return
    scratch = ll + '.insn.ll'
    traps = 0
    for i, (w,) in enumerate(struct.iter_unpack('<I', code)):
        try: lift(w, scratch, address + 4 * i)
        except LiftError as e:
            if _is_trap(w, address + 4 * i): traps += 1; continue   # an undefined encoding on purpose: __remill_error in the IR stops the run at it (aot_runtime.cpp)
            raise LiftError(f'function {address:#x}: {e}') from None
    if traps: return
    raise LiftError(f'function {address:#x}: Remill could not lift it (its IR calls __remill_error), though each instruction lifts alone: a control flow it does not follow')


_counter = itertools.count()


_toolkey = None


def toolchain_key():
    """What a cached object depends on besides the function's bytes: the lifter, the C++ compiler, the flags and the limits. A new Remill build or clang makes every entry stale"""
    global _toolkey
    if _toolkey is None:
        st = os.stat(lifter())
        cv = subprocess.run(['clang++', '--version'], capture_output=True, text=True, errors='replace').stdout
        _toolkey = hashlib.sha256(f'{lifter()}|{st.st_size}|{st.st_mtime_ns}|{cv}|O2 fPIC|{MAX_LIFT_BYTES}|{include_dir()}'.encode()).hexdigest()
    return _toolkey


def _atomic(path, data):
    """Write data to path so that a reader (or a second build at the same time) never sees half of it"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f'{path}.{os.getpid()}.{threading.get_ident()}.part'
    with open(tmp, 'wb') as f: f.write(data)
    os.replace(tmp, path)


def undefined_symbols(obj):
    """The undefined symbols of an object file (nm -u)"""
    out = subprocess.run(['nm', '-u', obj], capture_output=True, text=True, errors='replace').stdout
    return sorted({l.split()[-1] for l in out.splitlines() if l.strip()})


def needs_symbols(path, symbols):
    """Which of symbols the lifted function behind path (a .ll, or a compiled .o with its .syms beside it) refers to"""
    if path.endswith('.ll'):
        text = Path(path).read_text()
        return [m for m in symbols if re.search(rf'@{re.escape(m)}\b', text)]
    side = path[:-2] + '.syms'
    have = set(Path(side).read_text().split()) if os.path.exists(side) else set(undefined_symbols(path))
    return [m for m in symbols if m in have]


class Aot:
    def __init__(self, functions, tmp=None, jit=False, jit_entries=0, jit_ticks=0, jit_depth=8, stub_unliftable=False, jobs=None, cache=None, overrides=()):
        """functions: [(address, bytes)] or [(entry address, bytes, address of the first byte)]: the three-item form lifts from an entry inside the bytes (the targets of a jump table, jump_tables.py).
        Builds and loads the library. LiftError / LinkError otherwise.
        stub_unliftable: a function Remill cannot lift, or whose code needs a runtime symbol that is not defined (svc...), no longer fails the build: it gets a stub `sub_<address>` (so that its direct
        callers still link) that runs it through the JIT, or stops with the reason when there is no JIT. self.unlifted maps those addresses to the reason. The stub is not in the AOT table: an indirect
        call to it goes to the JIT as any unknown target does.
        cache: a folder (kept between runs, and outside the repository: it holds code derived from the game) where the compiled functions and the finished library are kept, under a hash of their bytes,
        address and toolchain. A function already there is not lifted again, and a build whose every function is there loads its library at once. Default: the AOT_CACHE environment variable, else none.
        jit: also build the dynarmic fallback (DYNARMIC_DIR: its source folder with the build/ folder inside) and turn it on, limited to jit_entries entries (0: no limit),
        jit_ticks cycles per entry (0: 2**40) and jit_depth nested entries"""
        self.dir = tempfile.TemporaryDirectory() if tmp is None else None
        d = tmp or self.dir.name
        n = next(_counter)
        functions = [(f[0], f[1], f[2] if len(f) > 2 else f[0]) for f in functions]
        replaced = set(overrides)   # functions the host replaces: not lifted, their stub calls the host function registered at the address (register_import)
        functions = [f for f in functions if not (f[0] == f[2] and f[0] in replaced)]
        lls, self.unlifted = {}, {}
        cache = cache or os.environ.get('AOT_CACHE') or None
        self.cache_hits = self.cache_misses = 0
        if cache:
            tk = toolchain_key()
            fkeys = {a: hashlib.sha256(f'{tk}|{a:x}|{o:x}|'.encode() + c).hexdigest() for a, c, o in functions}
            runtime = ''.join(hashlib.sha256(Path(HERE / f).read_bytes()).hexdigest() for f in ('aot_runtime.cpp', 'aot_runtime.h') + (('aot_jit.cpp',) if jit else ()))
            key0 = hashlib.sha256(f'{tk}|{int(jit)}|{int(stub_unliftable)}|{runtime}|{os.environ.get("DYNARMIC_DIR") if jit else ""}|{sorted(replaced)}|'.encode() + ''.join(sorted(fkeys.values())).encode()).hexdigest()
            cached_so, cached_meta = os.path.join(cache, 'lib', f'{key0}.so'), os.path.join(cache, 'lib', f'{key0}.json')
            if os.path.exists(cached_so) and os.path.exists(cached_meta):   # the whole library is there: nothing to lift, compile or link
                self.unlifted = {int(a): why for a, why in json.loads(Path(cached_meta).read_text()).items()}
                self.cache_hits = len(functions)
                self._attach(cached_so, jit, jit_entries, jit_ticks, jit_depth)
                return
        jobs = jobs or (min(16, os.cpu_count() or 1) if len(functions) > 8 else 1)   # remill-lift and clang each handle one function at a time: many functions go through a pool
        def lift_item(item):
            addr, code, origin = item
            ll = os.path.join(d, f'f{n}_{addr:x}.ll')
            try: lift_function(code, origin, ll, addr if addr != origin else None); return addr, ll, None
            except LiftError as e: return addr, ll, e
        def compile_ll(ll, obj):
            r = subprocess.run(['clang++', '-c', '-O2', '-fPIC', '-Wno-override-module', ll, '-o', obj], capture_output=True, text=True, errors='replace')
            if r.returncode: raise LinkError(f'build of {ll} failed:\n' + r.stderr[-1500:])
        if cache:
            def cached_item(item):   # -> (addr, object path, None) or (addr, None, reason); lifts and compiles only what the cache lacks
                addr = item[0]
                base_path = os.path.join(cache, 'obj', fkeys[addr][:2], fkeys[addr])
                if os.path.exists(base_path + '.o'): return addr, base_path + '.o', None, True
                if os.path.exists(base_path + '.fail'): return addr, None, Path(base_path + '.fail').read_text(), True
                a2, ll, e = lift_item(item)
                if e is not None: _atomic(base_path + '.fail', str(e).encode()); return addr, None, str(e), False
                tmp_obj = ll[:-3] + '.o'
                compile_ll(ll, tmp_obj)
                _atomic(base_path + '.syms', ' '.join(undefined_symbols(tmp_obj)).encode())
                _atomic(base_path + '.o', Path(tmp_obj).read_bytes())
                os.unlink(ll); os.unlink(tmp_obj)
                return addr, base_path + '.o', None, False
            with concurrent.futures.ThreadPoolExecutor(jobs) as ex: done = list(ex.map(cached_item, functions))
            objs = {}
            for addr, path, why, hit in done:
                self.cache_hits += hit; self.cache_misses += not hit
                if path: lls[addr] = objs[addr] = path
                else:
                    if not stub_unliftable: raise LiftError(why)
                    self.unlifted[addr] = why
        else:
            if jobs > 1:
                with concurrent.futures.ThreadPoolExecutor(jobs) as ex: lifted = list(ex.map(lift_item, functions))
            else: lifted = [lift_item(f) for f in functions]
            for addr, ll, e in lifted:
                if e is None: lls[addr] = ll; continue
                if not stub_unliftable: raise e
                self.unlifted[addr] = str(e)
            if jobs > 1 and len(lls) > 8:   # compile each function on its own, in parallel; the link then has only objects to read
                def compile_item(item):
                    addr, ll = item
                    obj = ll[:-3] + '.o'
                    compile_ll(ll, obj)
                    return addr, obj
                with concurrent.futures.ThreadPoolExecutor(jobs) as ex: objs = dict(ex.map(compile_item, list(lls.items())))
            else: objs = dict(lls)
        so = os.path.join(d, f'aot{n}.so')   # one path per library: dlopen of a path already loaded returns the old one
        inc = include_dir()
        base = ['-O2', '-fPIC', f'-I{inc}/include', f'-I{inc}', '-DADDRESS_SIZE_BITS=64']
        dyn = dynarmic_dir() if jit else None
        for a in replaced: self.unlifted[a] = 'replaced by a host function'
        for _ in range(len(functions) + 1):   # each round that fails to link turns at least one more function into a stub
            table, stubs = os.path.join(d, f'table{n}.cpp'), os.path.join(d, f'stubs{n}.cpp')
            rows = sorted(lls)
            Path(table).write_text('#include "remill/Arch/AArch64/Runtime/State.h"\n#include <cstdint>\n'
                                   + ''.join(f'extern "C" Memory *sub_{a:x}(State *, uint64_t, Memory *);\n' for a in rows)
                                   + 'struct AotEntry { uint64_t addr; Memory *(*fn)(State *, uint64_t, Memory *); };\n'
                                   + 'extern "C" const AotEntry aot_table[] = {' + ''.join(f'{{{a:#x}, sub_{a:x}}}, ' for a in rows) + '{~0ull, nullptr}};\n'   # the last row is a stop, so the array is never empty
                                   + f'extern "C" const unsigned aot_table_n = {len(rows)};\n')
            Path(stubs).write_text('#include "remill/Arch/AArch64/Runtime/State.h"\n#include <cstdint>\nextern "C" Memory *aot_unlifted(State *, uint64_t, Memory *, const char *);\n'
                                   + ''.join(f'extern "C" Memory *sub_{a:x}(State *s, uint64_t pc, Memory *m) {{ return aot_unlifted(s, pc, m, {json.dumps(why)}); }}\n' for a, why in sorted(self.unlifted.items())))
            if jit:
                fixed = [fixed_obj(src, std, base, dyn) for src, std in (('aot_runtime.cpp', 'c++17'), ('aot_jit.cpp', 'c++20'))]   # dynarmic's headers need C++20; the lifted code and the runtime do not
                libs = [f'{dyn}/build/src/dynarmic/libdynarmic.a', f'{dyn}/build/externals/mcl/src/libmcl.a', f'{dyn}/build/externals/fmt/libfmt.a',
                        f'{dyn}/build/externals/zydis/libZydis.a', f'{dyn}/build/externals/zydis/zycore/libZycore.a']
                cmd = ['clang++', '-shared', *fixed, table_obj(table, base, d, n), table_obj(stubs, base, d, f'{n}s'), *objs.values(), '-o', so, '-Wl,--start-group', *libs, '-Wl,--end-group', '-lpthread', '-Wl,--no-undefined']
            else:
                cmd = ['clang++', '-shared', '-std=c++17', *base, str(HERE / 'aot_runtime.cpp'), table, stubs, *objs.values(), '-o', so, '-Wl,--no-undefined']
            rsp = os.path.join(d, f'link{n}.rsp')   # a whole library has tens of thousands of objects: more than the kernel's argument limit, so clang reads them from a response file
            Path(rsp).write_text('\n'.join(json.dumps(a) for a in cmd[1:]))
            r = subprocess.run([cmd[0], f'@{rsp}'], capture_output=True, text=True, errors='replace')
            if not r.returncode: break
            missing = missing_symbols(r.stderr)
            if stub_unliftable:
                lacking = [m for m in missing if not m.startswith('sub_')]   # a sub_ symbol is a function nobody gave: that stays an error
                bad = {}
                if lacking:
                    with concurrent.futures.ThreadPoolExecutor(jobs) as ex: found = list(ex.map(lambda kv: (kv[0], needs_symbols(kv[1], lacking)), list(lls.items())))
                    bad = {a: miss for a, miss in found if miss}
                for a, miss in bad.items():
                    self.unlifted[a] = f'function {a:#x}: its code needs ' + ', '.join(miss) + ', which the runtime does not define'
                    del lls[a]; objs.pop(a, None)
                if bad: continue
            if missing: raise LinkError('the runtime does not define: ' + ', '.join(missing) + ' (an instruction class not translated yet)')
            raise LinkError('build failed:\n' + r.stderr[-2000:])
        else:
            raise LinkError('build failed: functions kept failing to link')
        if cache:   # keep the finished library: the next build of the same functions loads it without lifting, compiling or linking
            os.makedirs(os.path.join(cache, 'lib'), exist_ok=True)
            _atomic(cached_meta, json.dumps({str(a): why for a, why in self.unlifted.items()}).encode())
            _atomic(cached_so, Path(so).read_bytes())
            so = cached_so
        self._attach(so, jit, jit_entries, jit_ticks, jit_depth)

    def _attach(self, so, jit, jit_entries, jit_ticks, jit_depth):
        """Load the library and set up its entry points"""
        self.lib = ctypes.CDLL(so)
        L = self.lib
        L.aot_map.argtypes = [ctypes.c_uint64, ctypes.c_uint64]
        L.aot_try_map.argtypes = [ctypes.c_uint64, ctypes.c_uint64]
        L.aot_unmap.argtypes = [ctypes.c_uint64]
        L.aot_poke.argtypes = [ctypes.c_uint64, ctypes.c_char_p, ctypes.c_uint64]
        L.aot_peek.argtypes = [ctypes.c_uint64, ctypes.c_char_p, ctypes.c_uint64]
        L.aot_stat.argtypes = [ctypes.c_int]; L.aot_stat.restype = ctypes.c_uint64
        L.aot_run.argtypes = [ctypes.c_void_p, ctypes.c_uint64, ctypes.c_char_p, ctypes.c_uint64]
        self.regions = []
        if jit:
            L.aot_jit_enable.argtypes = [ctypes.c_uint64, ctypes.c_uint64, ctypes.c_int]
            L.aot_jit_enable(jit_entries, jit_ticks, jit_depth)
            L.aot_jit_log.argtypes = [ctypes.c_char_p, ctypes.c_uint64]

    def jit_log(self):
        """The JIT's log: one line per (target, kind), in the order first seen"""
        buf = ctypes.create_string_buffer(1 << 16)
        self.lib.aot_jit_log(buf, len(buf))
        return buf.value.decode()

    class _Guest(ctypes.Structure):
        _fields_ = [('x', ctypes.c_uint64 * 31), ('sp', ctypes.c_uint64), ('nzcv', ctypes.c_uint64), ('tpidr_el0', ctypes.c_uint64), ('tpidrro_el0', ctypes.c_uint64), ('v', ctypes.c_uint64 * 64), ('fpcr', ctypes.c_uint64), ('fpsr', ctypes.c_uint64)]

    def map(self, base, size, data=None):
        self.lib.aot_map(base, size); self.regions.append((base, size, bytes(data) if data else bytes(size)))
        self.reset()

    def reset(self):
        """Every region back to the contents it was mapped with (the guest memory is global: a run leaves its writes behind)"""
        for b, _, data in self.regions: assert self.lib.aot_poke(b, data, len(data)) == 0

    def try_map(self, base, size):
        """Map zeroed guest memory while guest code may be running (what a MapMemory or SetHeapSize service does): True, or False when it overlaps a region. The reset() contents are not touched"""
        return self.lib.aot_try_map(base, size) == 0

    def unmap(self, base):
        return self.lib.aot_unmap(base) == 0

    SVC = ctypes.CFUNCTYPE(ctypes.c_uint64, ctypes.c_int, ctypes.POINTER(ctypes.c_uint64), ctypes.c_uint64)

    def set_svc_handler(self, fn):
        """fn(num, regs, orig_addr), regs a list of x0..x30 that fn changes in place (the shape of svc_dispatch in hle_svc.c); None removes the handler"""
        slot = ctypes.c_void_p.in_dll(self.lib, 'aot_svc_handler')
        if fn is None: slot.value = None; self._svc = None; return
        def cb(num, regs, orig):
            r = list(regs[:31]); fn(num, r, orig)
            for i in range(31): regs[i] = r[i] & (2 ** 64 - 1)
            return 0
        self._svc = self.SVC(cb)   # kept alive: the library holds only the raw pointer
        slot.value = ctypes.cast(self._svc, ctypes.c_void_p).value

    IMPORT = ctypes.CFUNCTYPE(None, ctypes.POINTER(ctypes.c_uint64), ctypes.c_void_p)

    def register_import(self, addr, fn, name):
        """A host function at a guest address (the GOT of a loaded module points at it): fn(regs) gets x0..x30 as a list and changes it in place. fn None: an unresolved import, whose call stops naming it.
        Returns 0, or 1 when the address is taken"""
        L = self.lib
        L.aot_register_import.argtypes = [ctypes.c_uint64, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p]
        cb = None
        if fn is not None:
            def call(regs, _):
                r = list(regs[:31]); fn(r)
                for i in range(31): regs[i] = r[i] & (2 ** 64 - 1)
            cb = self.IMPORT(call); self._imports = getattr(self, '_imports', []) + [cb]   # kept alive: the library holds only the raw pointer
        return L.aot_register_import(addr, ctypes.cast(cb, ctypes.c_void_p).value if cb else None, None, name.encode())

    def peek(self, base, size):
        buf = ctypes.create_string_buffer(size)
        assert self.lib.aot_peek(base, buf, size) == 0
        return buf.raw

    def stat(self, name): return self.lib.aot_stat(STATS.index(name))

    def run(self, pc, regs=None, nzcv=0, sp=0, tpidr_el0=0, tpidrro_el0=0, vregs=None, fpcr=0, fpsr=0, snapshot=True):
        """Run the AOT function at pc. Returns {'x0'..'x30', 'sp', 'nzcv', 'tpidr_el0', 'mem': {base: bytes}} or {'fault': reason}.
        tpidr_el0, tpidrro_el0: the thread pointers this guest thread sees (on Horizon tpidrro_el0 points at the thread-local region).
        snapshot: also return every mapped region's bytes ('mem'). vregs: {'v0': 128-bit int, ...} the vector registers going in (the others are 0), fpcr, fpsr; the result has 'v0'..'v31' (128-bit ints), 'fpcr' and 'fpsr' too"""
        g = self._Guest()
        for k, v in (regs or {}).items(): g.x[int(k[1:])] = v & (2 ** 64 - 1)
        g.sp, g.nzcv, g.tpidr_el0, g.tpidrro_el0 = sp, nzcv, tpidr_el0, tpidrro_el0
        for k, v in (vregs or {}).items(): i = int(k[1:]); g.v[2 * i] = v & (2 ** 64 - 1); g.v[2 * i + 1] = v >> 64 & (2 ** 64 - 1)
        g.fpcr, g.fpsr = fpcr, fpsr
        err = ctypes.create_string_buffer(300)
        rc = self.lib.aot_run(ctypes.byref(g), pc, err, 300)
        if rc: return {'fault': err.value.decode(), 'x29': g.x[29], 'x30': g.x[30], 'sp': g.sp, 'x0': g.x[0], 'x1': g.x[1]}   # the registers as the lifted code last stored them: enough for a call chain
        out = {f'x{i}': g.x[i] for i in range(31)}; out['sp'] = g.sp; out['nzcv'] = g.nzcv; out['tpidr_el0'] = g.tpidr_el0
        for i in range(32): out[f'v{i}'] = g.v[2 * i] | g.v[2 * i + 1] << 64
        out['fpcr'], out['fpsr'] = g.fpcr, g.fpsr
        if snapshot: out['mem'] = {b: self.peek(b, n) for b, n, _ in self.regions}   # a game's heap is gigabytes: its threads run with snapshot=False
        return out

    def get_tls(self, which):
        """The thread pointer of the run the calling thread executes: 0 tpidr_el0, 1 tpidrro_el0"""
        self.lib.aot_get_tls.restype = ctypes.c_uint64; self.lib.aot_get_tls.argtypes = [ctypes.c_int]
        return self.lib.aot_get_tls(which)

    def set_tls(self, tpidr_el0, tpidrro_el0):
        """Change the thread pointers of the run the calling thread executes (a fiber resumed by another guest thread)"""
        self.lib.aot_set_tls.argtypes = [ctypes.c_uint64, ctypes.c_uint64]
        self.lib.aot_set_tls(tpidr_el0, tpidrro_el0)

    def close(self):
        self.lib.aot_unmap_all()
        if self.dir: self.dir.cleanup()
