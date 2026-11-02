#!/usr/bin/env python3
"""ELF modules for the recompiled runtime (NX2Linux T6): what the Mach-O loader (host/loader.c) got from Mach-O sections, `<name>.patched.bin`, `<name>.fixups.tsv` and `nro.tsv` is now an ELF and a manifest.
  · convert_nso(nso, out_dir, name) writes `<name>.elf` (nso2elf.py: the NSO's memory layout as PT_LOAD/PT_DYNAMIC/PT_GNU_EH_FRAME) and `<name>.manifest.json`, which lists what the recompiler and the loader need
    and would otherwise rediscover: segments, the dynamic section, exports, imports, relocations, and every function entry (unwind data, exported functions, the targets of direct calls and of function
    pointers in the data). The manifest is *derived from the ELF*: load_manifest regenerates it and refuses a manifest that does not match its ELF (SHA-256 and content).
  · ModuleSet(manifests, hle) maps the modules at bases of its own, applies the relocations as loader.c relocate() does (RELATIVE, ABS64, GLOB_DAT, JUMP_SLOT), binds each symbol to a loaded module's export, then to a
    host function in `hle` (an *import*: an address in guest space that runs a Python/C function, aot_runtime.cpp), then to 0 for a weak one, and otherwise to an import that stops with its name when called. Nothing is
    bound silently. build() lifts every function with aot_lift.Aot; functions Remill cannot lift run in the JIT.
  · what the Mach-O build needed for the iOS/Apple kernel is not needed here and is not produced: the patch area (x18, TLS register and svc rewrites: the recompiled code handles those itself, T3/T5), the key page
    (pthread TSD slots), the 16K re-mapping, signed code segments, the embedded NRO table (the manifest per module replaces it).
Usage: elf_module.py <module.nso> <out_dir> <name>          convert
       elf_module.py build <cache dir> <manifest> [<manifest>...]   lift, compile and link the modules, keeping every result in the cache folder (outside the repository: it holds code derived from the game);
                                                                   a second run of the same modules loads the finished library in seconds"""
import hashlib, json, os, struct, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import jump_tables, nso2elf, nso_code

FORMAT = 'nx2linux-module/1'
PAGE = 0x1000
PT_LOAD, PT_DYNAMIC, PT_GNU_EH_FRAME = 1, 2, 0x6474e550
DT = {2: 'PLTRELSZ', 5: 'STRTAB', 6: 'SYMTAB', 7: 'RELA', 8: 'RELASZ', 9: 'RELAENT', 10: 'STRSZ', 11: 'SYMENT', 12: 'INIT', 13: 'FINI', 20: 'PLTREL', 23: 'JMPREL', 25: 'INIT_ARRAY', 27: 'INIT_ARRAYSZ'}
R_ABS64, R_GLOB_DAT, R_JUMP_SLOT, R_RELATIVE = 257, 1025, 1026, 1027
RNAME = {R_ABS64: 'ABS64', R_GLOB_DAT: 'GLOB_DAT', R_JUMP_SLOT: 'JUMP_SLOT', R_RELATIVE: 'RELATIVE'}
STOP = 0xFFFF0000   # the x30 the harness gives a function it calls: never executed


class ModuleError(Exception):
    pass


def page(x): return (x + PAGE - 1) & ~(PAGE - 1)


# ---- reading an ELF module ----
def parse_elf(data):
    """(loads, dynamic vaddr, eh_frame_hdr vaddr) of an ELF64 AArch64 shared object; ModuleError names what is wrong"""
    if len(data) < 0x40 or data[:4] != b'\x7fELF' or data[4] != 2 or data[5] != 1: raise ModuleError('not a little-endian ELF64 file')
    etype, machine = struct.unpack_from('<HH', data, 16)
    if etype != 3 or machine != 183: raise ModuleError(f'ELF type {etype} machine {machine}: a shared object (3) for AArch64 (183) is needed')
    phoff, phentsize, phnum = struct.unpack_from('<QxxxxxxxxxxxxxxHH', data, 32)[0], *struct.unpack_from('<HH', data, 54)
    loads, dyn, eh = [], None, None
    for i in range(phnum):
        t, flags, off, va, _, filesz, memsz, _ = struct.unpack_from('<IIQQQQQQ', data, phoff + i * phentsize)
        if t == PT_LOAD:
            if off + filesz > len(data) or filesz > memsz: raise ModuleError(f'PT_LOAD {i} runs past the file or its memory size')
            loads.append(dict(vaddr=va, offset=off, filesz=filesz, memsz=memsz, flags=flags))
        elif t == PT_DYNAMIC: dyn = va
        elif t == PT_GNU_EH_FRAME: eh = va
    if not loads: raise ModuleError('no PT_LOAD segment')
    return loads, dyn, eh


def image_of(data, loads):
    """The module's memory (bss included) as the loader lays it out at base 0"""
    size = max(s['vaddr'] + s['memsz'] for s in loads)
    img = bytearray(size)
    for s in loads: img[s['vaddr']:s['vaddr'] + s['filesz']] = data[s['offset']:s['offset'] + s['filesz']]
    return img


def read_dynamic(img, dyn):
    if dyn is None: raise ModuleError('no PT_DYNAMIC')
    out, o = {}, dyn
    while True:
        tag, val = struct.unpack_from('<qQ', img, o); o += 16
        if tag == 0: return out
        if tag in DT: out[DT[tag]] = val
        if o > len(img) - 16: raise ModuleError('the dynamic section has no end')


def read_symbols(img, dynamic):
    """[(name, binding, type, defined, value, size)], index 0 being the null symbol. The count is (strtab - symtab) / 24, as host/loader.c has it: the table must end where the string table begins"""
    if 'SYMTAB' not in dynamic or 'STRTAB' not in dynamic: return []
    sym, st = dynamic['SYMTAB'], dynamic['STRTAB']
    if st < sym or (st - sym) % 24: raise ModuleError('the symbol table does not end where the string table begins (host/loader.c relies on that too)')
    out = []
    for i in range((st - sym) // 24):
        name, info, _, shndx, value, size = struct.unpack_from('<IBBHQQ', img, sym + 24 * i)
        end = img.index(b'\0', st + name)
        out.append((bytes(img[st + name:end]).decode(), info >> 4, info & 15, shndx != 0, value, size))
    return out


def read_relocs(img, dynamic):
    """[(offset, type, symbol index, addend, table)] from DT_RELA and DT_JMPREL"""
    out = []
    for key, size_key, table in (('RELA', 'RELASZ', 'rela'), ('JMPREL', 'PLTRELSZ', 'plt')):
        if key not in dynamic: continue
        n = dynamic.get(size_key, 0) // 24
        for i in range(n):
            off, info, add = struct.unpack_from('<QQq', img, dynamic[key] + 24 * i)
            out.append((off, info & 0xFFFFFFFF, info >> 32, add, table))
    return out


def find_functions(img, text_end, symbols, relocs):
    """{start: (end, how)}: every function entry the recompiler must know. how: 'fde' (unwind data), 'export' (an exported function without one), 'call' (the target of a direct bl or b outside every known
    function: the PLT stubs and tail-called code the unwind data does not describe) or 'pointer' (a function pointer a RELATIVE relocation stores). A discovered entry runs to the next known entry and is scanned
    up to its first unconditional branch, so the data after a stub is not mistaken for code"""
    import bisect, capstone
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM); md.detail = False; md.skipdata = True   # a data word inside a function is skipped, not an error: the lift follows control flow
    try: fde = [(s, e) for s, e in nso_code.fde_ranges(img) if 0 <= s < e <= text_end]
    except (AssertionError, ValueError, IndexError, struct.error) as e: raise ModuleError(f'cannot read the function boundaries from the unwind data: {e}') from e
    funcs = {s: [e, 'fde'] for s, e in fde}
    order = sorted(funcs)   # the entries, ascending, for the bisect lookups below (a game has tens of thousands)
    def covered(a):
        i = bisect.bisect_right(order, a) - 1
        return i >= 0 and a < funcs[order[i]][0]
    def add(a, how):
        if a % 4 or not 0 <= a < text_end: return False
        i = bisect.bisect_right(order, a)
        end = order[i] if i < len(order) else text_end
        if i and a < funcs[order[i - 1]][0]:
            # Inside a known function. A direct call or branch from another function lands there only on an entry: the PLT is the usual case (one FDE for the whole table, each stub called on its own).
            # The function is split at the target (also at an exported function: the SDK's svc wrappers share a unwind entry); anything else that merely points inside is not an entry
            if how not in ('call', 'export') or a == order[i - 1]: return False   # an exported function symbol is an entry too: the SDK's svc wrappers (`svc #n; ret`) share one unwind entry
            end = funcs[order[i - 1]][0]; funcs[order[i - 1]][0] = a
        funcs[a] = [end, how]; order.insert(i, a)
        return True
    queue = []
    for name, _, typ, defined, value, _ in symbols:
        if defined and typ == 2 and add(value, 'export'): queue.append(value)
    for off, t, _, addend, _ in relocs:
        if t == R_RELATIVE and add(addend, 'pointer'): queue.append(addend)
    queue += [s for s in funcs if funcs[s][1] == 'fde']
    seen = set()
    while queue:
        s = queue.pop()
        if s in seen: continue
        seen.add(s)
        e, how = funcs[s]
        for ins in md.disasm(bytes(img[s:e]), s):
            m = ins.mnemonic
            if m in ('b', 'bl'):
                t = int(ins.op_str.rsplit('#', 1)[1], 0)
                if (m == 'bl' or not (s <= t < funcs[s][0])) and add(t, 'call'): queue.append(t)   # a bl into its own function's range is a call to a second entry (an outlined or merged body): it needs a symbol of its own
            if how != 'fde' and m in ('b', 'br', 'ret'): break
    return {s: (v[0], v[1]) for s, v in sorted(funcs.items())}


def describe(data, name, elf_name, build_id=''):
    """The manifest of an ELF module (a dict, JSON-able). Everything but name, elf and build_id is derived from the ELF alone"""
    loads, dyn, eh = parse_elf(data)
    img = image_of(data, loads)
    dynamic = read_dynamic(img, dyn)
    syms, relocs = read_symbols(img, dynamic), read_relocs(img, dynamic)
    text = next((s for s in loads if s['flags'] & 1), None)
    if text is None: raise ModuleError('no executable PT_LOAD')
    funcs = find_functions(img, text['vaddr'] + text['filesz'], syms, relocs)
    # the targets of the jump tables (switch statements) inside the functions: each becomes an entry of its own, lifted from inside its function (jump_tables.py)
    entries, jumps = set(funcs), {}
    for s_, (e_, how) in funcs.items():
        if how == 'call': continue   # a PLT stub or a tail-called fragment: no switch worth looking for
        for jt in jump_tables.find_jump_tables(img, s_, e_):
            for t in jt.targets:
                if t not in entries and t not in jumps: jumps[t] = dict(start=t, origin=s_, end=e_, br=jt.br)
    counts = {}
    for _, t, *_ in relocs: counts[RNAME.get(t, str(t))] = counts.get(RNAME.get(t, str(t)), 0) + 1
    return dict(format=FORMAT, name=name, elf=elf_name, build_id=build_id, sha256=hashlib.sha256(data).hexdigest(),
                segments=[dict(vaddr=s['vaddr'], filesz=s['filesz'], memsz=s['memsz'], perms=('r' if s['flags'] & 4 else '-') + ('w' if s['flags'] & 2 else '-') + ('x' if s['flags'] & 1 else '-')) for s in loads],
                size=len(img), dynamic=dynamic, eh_frame_hdr=eh,
                functions=[dict(start=s, end=e, how=h) for s, (e, h) in funcs.items()],
                jump_targets=[jumps[t] for t in sorted(jumps)],
                exports=[dict(name=n, addr=v, type='func' if t == 2 else 'object' if t == 1 else str(t), size=z) for n, b, t, d, v, z in syms[1:] if d],
                imports=[dict(name=n, weak=b == 2) for n, b, t, d, v, z in syms[1:] if not d],
                relocations=counts)


def convert_nso(nso_path, out_dir, name):
    """NSO -> `<name>.elf` + `<name>.manifest.json` in out_dir; returns both paths"""
    import nso
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    elf, man = out / f'{name}.elf', out / f'{name}.manifest.json'
    nso2elf.convert(str(nso_path), str(elf))
    m = describe(elf.read_bytes(), name, elf.name, nso.cli_read(str(nso_path)).build_id.hex())
    man.write_text(json.dumps(m, indent=1, sort_keys=True) + '\n')
    return elf, man


def load_manifest(path):
    """(manifest, ELF bytes), after checking that the manifest describes that ELF: its SHA-256 and everything derived from it"""
    path = Path(path)
    try: m = json.loads(path.read_text())
    except (OSError, ValueError) as e: raise ModuleError(f'{path}: {e}') from None
    if m.get('format') != FORMAT: raise ModuleError(f'{path}: format {m.get("format")!r}, not {FORMAT!r}')
    elf = path.parent / m['elf']
    try: data = elf.read_bytes()
    except OSError as e: raise ModuleError(f'{path}: {e}') from None
    if hashlib.sha256(data).hexdigest() != m['sha256']: raise ModuleError(f'{path}: {elf.name} is not the ELF this manifest was made from (SHA-256 differs)')
    want = describe(data, m['name'], m['elf'], m['build_id'])
    for k in want:
        if want[k] != m.get(k): raise ModuleError(f'{path}: "{k}" does not match what the ELF says')
    return m, data


# ---- the loader ----
class Loaded:
    def __init__(self, manifest, data, base):
        self.m, self.name, self.base = manifest, manifest['name'], base
        loads, _, _ = parse_elf(data)
        self.image = image_of(data, loads)
        self.size = page(len(self.image))
        self.exports = {e['name']: base + e['addr'] for e in manifest['exports']}
        self.functions = [(base + f['start'], base + f['end']) for f in manifest['functions']]
        self.jump_targets = [(base + j['start'], base + j['end'], base + j['origin']) for j in manifest.get('jump_targets', [])]   # (entry, end of its function, start of its function)
        self.relocs = read_relocs(self.image, manifest['dynamic'])
        self.symbols = read_symbols(self.image, manifest['dynamic'])


class ModuleSet:
    BASE = 0x08000000
    GAP = 0   # modules are contiguous, each starting where the previous one's bss ends (page aligned), as Horizon's rtld lays them out: a direct bl from main into sdk is encoded for that distance and lands in the wrong place with a gap
    STACK, STACK_SIZE = 0x01000000, 0x10000
    TLS, TLS_SIZE = 0x00100000, 0x10000   # the harness thread's thread-local region: tpidrro_el0 points at its start (Horizon's 0x200-byte region, then the thread object), tpidr_el0 a user TLS block after both
    IMPORT_BASE = 0x70000000
    STOP = STOP   # the x30 a thread the harness starts returns to

    def __init__(self, manifests, hle=None, base=BASE):
        """manifests: paths of `<name>.manifest.json` in load order. hle: {symbol name: fn(regs) or None}, the host functions an import can reach; fn gets x0..x30 as a list and changes it in place"""
        self.hle, self.modules, self.imports, self.aot = dict(hle or {}), [], {}, None
        for p in manifests:
            m, data = load_manifest(p)
            if any(x.name == m['name'] for x in self.modules): raise ModuleError(f'module {m["name"]} loaded twice')
            self.modules.append(Loaded(m, data, base)); base += page(len(self.modules[-1].image)) + self.GAP
        self.exports = {}
        for mod in self.modules:
            for n, a in mod.exports.items(): self.exports.setdefault(n, a)   # the first definition wins, as in loaded_sym (loader.c)
        self.log = []
        for mod in self.modules: self._relocate(mod)

    def _import_address(self, name):
        if name not in self.imports: self.imports[name] = self.IMPORT_BASE + 16 * len(self.imports)
        return self.imports[name]

    def _resolve(self, sym, rel_off, mod):
        name, binding, _, defined, value, _ = sym
        if defined and name not in self.exports: return mod.base + value
        if name in self.exports: return self.exports[name]
        if name in self.hle: return self._import_address(name)
        if binding == 2: self.log.append(f'{mod.name}: undefined weak {name} bound to 0'); return 0
        self.log.append(f'{mod.name}: {name} is not defined by any module nor by the host: its calls stop with its name'); return self._import_address(name)

    def _relocate(self, mod):
        n = len(mod.image)
        for off, t, si, add, _ in mod.relocs:
            if off % 4 or off + 8 > n: raise ModuleError(f'{mod.name}: relocation at {off:#x} is outside the module or not 4-byte aligned')   # packed structures put an 8-byte pointer at a 4-aligned offset (Xenoblade 2 has 48)
            if t == R_RELATIVE: v = mod.base + add
            elif t in (R_ABS64, R_GLOB_DAT, R_JUMP_SLOT):
                if si >= len(mod.symbols): raise ModuleError(f'{mod.name}: relocation at {off:#x} names symbol {si}, past the table')
                v = self._resolve(mod.symbols[si], off, mod) + (add if t != R_JUMP_SLOT else 0)
            else: raise ModuleError(f'{mod.name}: relocation type {t} at {off:#x} is not translated (only RELATIVE, ABS64, GLOB_DAT, JUMP_SLOT)')
            struct.pack_into('<Q', mod.image, off, v & (2 ** 64 - 1))

    def symbol(self, name):
        if name not in self.exports: raise ModuleError(f'no loaded module defines {name}')
        return self.exports[name]

    def _link_cross_module_calls(self):
        """A direct bl or b from one module into another (main into sdk, laid out back to back as Horizon's rtld does) is encoded for that distance. Its target is an entry the other module's own scan
        could not know: it lands inside a function, or on code its unwind data does not describe. Each such target becomes an entry (the function is split there, as find_functions does inside a module);
        returns how many were added"""
        import bisect, capstone
        md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM); md.detail = False; md.skipdata = True
        added = 0
        for mod in self.modules:
            targets = set()
            for a, e in mod.functions:
                for ins in md.disasm(bytes(mod.image[a - mod.base:e - mod.base]), a):
                    if ins.mnemonic in ('b', 'bl'):
                        t = int(ins.op_str.rsplit('#', 1)[1], 0)
                        if not mod.base <= t < mod.base + mod.size: targets.add(t)
            for t in sorted(targets):
                other = next((m for m in self.modules if m is not mod and m.base <= t < m.base + len(m.image)), None)
                if other is None or t % 4: continue   # outside every module: left to the linker's error, which names it
                starts = [f[0] for f in other.functions]
                i = bisect.bisect_right(starts, t) - 1
                if i >= 0 and other.functions[i][0] == t: continue
                if i >= 0 and t < other.functions[i][1]:
                    a, e = other.functions[i]
                    other.functions[i:i + 1] = [(a, t), (t, e)]
                else: other.functions.insert(i + 1, (t, starts[i + 1] if i + 1 < len(starts) else t + 4))
                added += 1
        return added

    def build(self, jit=False, overrides=None, **kw):
        """Lift every function, map the modules, the stack and the imports; returns the aot_lift.Aot.
        overrides: {exported symbol: fn(regs)}: functions the host replaces (the Mach host's HLE_FORCE): not lifted, the guest's calls to them run fn, which reads x0.. and sets x0 (like an import)"""
        over = {}
        for name, fn in (overrides or {}).items():
            if name not in self.exports: raise ModuleError(f'cannot replace {name}: no module exports it')
            over[self.exports[name]] = (name, fn)
        import aot_lift
        self.cross_entries = self._link_cross_module_calls()
        funcs = [(a, bytes(mod.image[a - mod.base:e - mod.base])) for mod in self.modules for a, e in mod.functions]
        funcs += [(a, bytes(mod.image[o - mod.base:e - mod.base]), o) for mod in self.modules for a, e, o in mod.jump_targets]   # the cases of a switch: lifted from their own address, with the function around them
        self.aot = aot_lift.Aot(funcs, jit=jit, stub_unliftable=True, overrides=set(over), **kw)
        for mod in self.modules: self.aot.map(mod.base, mod.size, bytes(mod.image) + bytes(mod.size - len(mod.image)))
        self.aot.map(self.STACK, self.STACK_SIZE)
        tls = bytearray(self.TLS_SIZE)   # what the kernel owns (host/thread.c): the ThreadLocalRegion, and right after it the current thread object that TLR[0x1f8] points at (the SDK reads its state through it)
        struct.pack_into('<Q', tls, 0x1f8, self.TLS + 0x200); struct.pack_into('<Q', tls, 0x110, 1)   # TLR[0x110]: the thread's ID
        self.aot.map(self.TLS, self.TLS_SIZE, bytes(tls))
        for addr, (name, fn) in over.items():
            if self.aot.register_import(addr, fn, name): raise ModuleError(f'{name} at {addr:#x} could not be replaced')
        for name, addr in self.imports.items():
            if self.aot.register_import(addr, self.hle.get(name), name): raise ModuleError(f'import {name} at {addr:#x} could not be registered')
        return self.aot

    def call(self, name_or_addr, regs=None, **kw):
        """Run an exported function (or an address) on the harness stack; the result of Aot.run"""
        a = name_or_addr if isinstance(name_or_addr, int) else self.symbol(name_or_addr)
        kw.setdefault('tpidrro_el0', self.TLS); kw.setdefault('tpidr_el0', self.TLS + 0x800)
        return self.aot.run(a, dict(regs or {}, x30=STOP), 0, self.STACK + self.STACK_SIZE, **kw)

    def run_inits(self):
        """DT_INIT, then each DT_INIT_ARRAY entry of each module in load order (host/loader.c run_module_inits); the first fault is returned, else None"""
        for mod in self.modules:
            d = mod.m['dynamic']
            todo = [mod.base + d['INIT']] if 'INIT' in d else []
            if 'INIT_ARRAY' in d:
                for i in range(d.get('INIT_ARRAYSZ', 0) // 8):
                    (a,) = struct.unpack_from('<Q', mod.image, d['INIT_ARRAY'] + 8 * i)
                    if a not in (0, 2 ** 64 - 1): todo.append(a)
            for a in todo:
                r = self.call(a)
                if 'fault' in r: return f'{mod.name}: init at {a:#x}: {r["fault"]}'
        return None


def main_build(cache, manifests, jit=True):
    import time
    t = time.time()
    ms = ModuleSet(manifests, {})
    aot = ms.build(jit=jit, cache=cache)
    print(f'{len(ms.modules)} module(s), {sum(len(m.functions) + len(m.jump_targets) for m in ms.modules)} entries (functions and switch cases): {aot.cache_hits} from the cache, {aot.cache_misses} built, {len(aot.unlifted)} left to the JIT; {time.time() - t:.1f} s')
    aot.close()


if __name__ == '__main__':
    if len(sys.argv) >= 4 and sys.argv[1] == 'build': main_build(sys.argv[2], sys.argv[3:]); sys.exit(0)
    if len(sys.argv) != 4: sys.exit(__doc__)
    elf, man = convert_nso(*sys.argv[1:4])
    print(elf, man)
