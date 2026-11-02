#!/usr/bin/env python3
"""Validation of the AArch64 -> x86_64 recompilation on a real module (NX2Linux T7). Nothing of the game is stored or printed beyond addresses, counts and mnemonics: the module is read from a folder of NSOs
(the ExeFS nx_extract writes) and everything derived from it goes to a scratch folder you name.
  coverage  how much of the module Remill can lift: every function (or a sample), in parallel; the failures by first unliftable instruction and by mnemonic
  census    every distinct instruction (register numbers masked) of the module, whether Remill lifts it, and how many times and in how many functions the unliftable ones occur: the list of what to implement next
  diff      leaf functions (no call, no indirect branch, no svc, no system register) lifted, compiled and run on random inputs next to the Unicorn ARM64 core on the same relocated module image:
            registers, NZCV and the writable memory must be identical. Both-fault and step-budget cases prove nothing and are counted apart.
Usage: module_validate.py census <exefs dir> <work dir> <module>[,<module>...] [--top N]
       module_validate.py coverage <exefs dir> <work dir> <module> [--sample N] [--jobs J]
       module_validate.py diff <exefs dir> <work dir> <module> [--functions N] [--cases K] [--jobs J] [--seed S]
Needs REMILL_BUILD, clang++, capstone and unicorn, like the tests."""
import argparse, collections, concurrent.futures as cf, json, os, random, struct, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE / 'tests'))
import elf_module as em

SCRATCH, SCRATCH_SIZE, STACK, STACK_SIZE, STOP = 0x20000000, 0x10000, 0x30000000, 0x10000, 0xFFFF0000
MASK = (1 << 64) - 1
REJECT = {'bl', 'blr', 'br', 'svc', 'mrs', 'msr', 'brk', 'hlt', 'hvc', 'smc', 'udf', 'eret'}   # a leaf function: nothing that leaves it or reads the machine


def module(exefs, work, name):
    """(manifest path, relocated module, ModuleSet): converts the NSO once"""
    man = Path(work) / f'{name}.manifest.json'
    if not man.exists(): em.convert_nso(Path(exefs) / name, work, name)
    ms = em.ModuleSet([str(man)], {})
    return man, ms.modules[0], ms


# ---- coverage ----
def lift_one(args):
    """(start, None) when the function lifts, (start, 'address: word (text)') for the first instruction that does not"""
    import aot_lift
    start, code, base, tmp = args
    try: aot_lift.lift_function(code, base + start, os.path.join(tmp, f'c{start:x}.ll')); err = None
    except aot_lift.LiftError as e: err = str(e)
    try: os.unlink(os.path.join(tmp, f'c{start:x}.ll'))
    except OSError: pass
    return start, err


def mnemonics(code, base):
    import capstone
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM); md.skipdata = True
    return collections.Counter(i.mnemonic for i in md.disasm(code, base))


def cmd_coverage(a):
    import aot_lift
    man, mod, _ = module(a.exefs, a.work, a.module)
    funcs = [(f['start'], bytes(mod.image[f['start']:f['end']])) for f in json.loads(man.read_text())['functions']]
    if a.sample and a.sample < len(funcs): funcs = random.Random(a.seed).sample(funcs, a.sample)
    tmp = Path(a.work) / 'lift'; tmp.mkdir(exist_ok=True)
    t = time.time()
    with cf.ProcessPoolExecutor(a.jobs) as ex:
        res = list(ex.map(lift_one, [(s, c, mod.base, str(tmp)) for s, c in funcs], chunksize=8))
    dt = time.time() - t
    bad = {s: e for s, e in res if e}
    first = collections.Counter(e.split('(')[1].split(' ')[0] if '(' in e else '?' for e in bad.values())
    print(f'{a.module}: {len(funcs)} functions{" (sample)" if a.sample else ""}, {len(funcs) - len(bad)} lift ({100 * (len(funcs) - len(bad)) / len(funcs):.1f}%), {len(bad)} do not; {dt:.0f} s on {a.jobs} jobs = {1000 * dt * a.jobs / len(funcs):.0f} ms per function per job')
    print('first unliftable instruction, by mnemonic:', ', '.join(f'{m} {n}' for m, n in first.most_common(25)))
    # which mnemonics block how many functions: test one representative word per mnemonic, then count the functions that use an unliftable mnemonic
    reps = {}
    import capstone
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM); md.skipdata = True
    for s, code in funcs:
        for i in md.disasm(code, mod.base + s): reps.setdefault(i.mnemonic, struct.unpack('<I', bytes(i.bytes))[0])
    reps.pop('.byte', None)
    out = collections.Counter()
    ok_m = set()
    for m, w in reps.items():
        try: aot_lift.lift(w, str(tmp / 'rep.ll')); ok_m.add(m)
        except aot_lift.LiftError: pass
        except Exception: pass
    blocked = collections.Counter()
    for s, code in funcs:
        for m in set(mnemonics(code, mod.base + s)) - ok_m - {'.byte'}: blocked[m] += 1
    print(f'{len(reps)} distinct mnemonics, {len(ok_m)} lift in their representative form (a variant may still fail)')
    print('functions each unliftable mnemonic appears in:', ', '.join(f'{m} {n}' for m, n in blocked.most_common(30)))
    (Path(a.work) / f'{a.module}.coverage.json').write_text(json.dumps(dict(functions=len(funcs), lift=len(funcs) - len(bad), unliftable={hex(mod.base + s): e for s, e in sorted(bad.items())}), indent=1))


# ---- census ----
REGS = ~((0x1F << 16) | (0x1F << 5) | 0x1F) & 0xFFFFFFFF   # Rd, Rn, Rm (and Rt, Rt2's neighbour): the register numbers do not change what an instruction is


def census_lift(w):
    import aot_lift, tempfile
    with tempfile.TemporaryDirectory() as d:
        try: aot_lift.lift(w, os.path.join(d, 'c.ll'), 0x1000); return w, None
        except aot_lift.LiftError as e: return w, str(e)
        except Exception as e: return w, f'{type(e).__name__}: {e}'


def cmd_census(a):
    import capstone
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    count, funcs, example = collections.Counter(), collections.Counter(), {}
    total = 0
    for name in a.module.split(','):
        man, mod, _ = module(a.exefs, a.work, name)
        for f in json.loads(man.read_text())['functions']:
            if f['how'] == 'call': continue
            seen = set()
            for (w,) in struct.iter_unpack('<I', bytes(mod.image[f['start']:f['end']])):
                k = w & REGS; count[k] += 1; total += 1; seen.add(k); example.setdefault(k, w)
            for k in seen: funcs[k] += 1
    valid = {}
    for k, w in example.items():
        i = next(md.disasm(struct.pack('<I', w), 0), None)
        if i: valid[k] = (w, i.mnemonic, f'{i.mnemonic} {i.op_str}')
    print(f'{total} words, {len(count)} distinct classes, {len(valid)} decode as instructions', flush=True)
    with cf.ProcessPoolExecutor(a.jobs) as ex: res = dict(ex.map(census_lift, [w for w, _, _ in valid.values()], chunksize=16))
    bad = {k: v for k, v in valid.items() if res[v[0]]}
    nbad = sum(count[k] for k in bad); nall = sum(count[k] for k in valid)
    print(f'{len(bad)} classes are not lifted: {nbad} of {nall} instructions ({100 * nbad / nall:.2f}%)')
    by_m = collections.defaultdict(lambda: [0, 0, 0, ''])
    for k, (w, m, text) in bad.items():
        r = by_m[m]; r[0] += count[k]; r[1] += funcs[k]; r[2] += 1; r[3] = r[3] or text
    print('mnemonic: instructions / (sum over classes of) functions / classes   example')
    for m, (n, fn, cl, ex_) in sorted(by_m.items(), key=lambda kv: -kv[1][0])[:a.top]: print(f'  {m:10} {n:8} {fn:8} {cl:4}   {ex_}')
    (Path(a.work) / f'{a.module.replace(",", "+")}.census.json').write_text(json.dumps({m: dict(count=v[0], classes=v[2], example=v[3]) for m, v in by_m.items()}, indent=1))


# ---- differential run ----
def candidates(man, mod, switches=False):
    """Starts of the leaf functions: no call, no indirect branch, no svc or system register, every direct branch inside the function.
    switches: only functions with a jump table, whose `br` is allowed (the other instructions stay as strict)"""
    import jump_tables
    import capstone
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM); md.skipdata = True
    out = []
    for f in json.loads(man.read_text())['functions']:
        s, e = f['start'], f['end']
        ok = e - s >= 8
        tables = {t.br + mod.base for t in jump_tables.find_jump_tables(mod.image, s, e)} if switches else set()
        if switches and not tables: continue
        for i in md.disasm(bytes(mod.image[s:e]), mod.base + s):
            if i.address in tables: continue
            if i.mnemonic in REJECT or i.mnemonic == '.byte': ok = False; break
            if i.mnemonic == 'b' or i.mnemonic.startswith('b.') or i.mnemonic in ('cbz', 'cbnz', 'tbz', 'tbnz'):
                t = int(i.op_str.rsplit('#', 1)[1], 0)
                if not (mod.base + s <= t < mod.base + e): ok = False; break
        if ok: out.append(s)
    return out


def _nan(x):
    return ((x >> 52) & 0x7FF == 0x7FF and x & ((1 << 52) - 1)) or any((x >> sh & 0xFFFFFFFF) >> 23 & 0xFF == 0xFF and (x >> sh & 0x7FFFFF) for sh in (0, 32))


VPOOL = [v for v in ([struct.unpack('<Q', struct.pack('<d', f))[0] for f in (0.0, -0.0, 1.0, -1.0, 0.5, 1.5, -2.5, 3.7, 100.25, 1e10, -1e10, 1e-10, 2.0 ** 31, 2.0 ** 63, 1e300)] +
                      [struct.unpack('<I', struct.pack('<f', f))[0] | struct.unpack('<I', struct.pack('<f', g))[0] << 32 for f, g in ((1.0, 2.0), (-3.5, 0.25), (1e10, -1e-10), (7.0, 0.0))] +
                      [0, 1, 0xFFFFFFFFFFFFFFFF, 0x7FFFFFFF, 0x80000000, 0x0123456789ABCDEF, 0x00FF00FF00FF00FF]) if not _nan(v)]   # no NaN: payloads and priorities differ between x86 and ARM


def inputs(rng, mod, k):
    data_lo = mod.base + next(sg['vaddr'] for sg in mod.m['segments'] if sg['perms'] == 'rw-')
    data_sz = len(mod.image) - (data_lo - mod.base)
    def one():
        c = rng.random()
        if c < .30: return rng.randrange(0, 17)
        if c < .45: return rng.getrandbits(64)
        if c < .60: return SCRATCH + rng.randrange(0, SCRATCH_SIZE // 2) * 8 % (SCRATCH_SIZE // 2)
        if c < .70: return SCRATCH + rng.randrange(0, SCRATCH_SIZE // 2)
        if c < .80: return data_lo + rng.randrange(0, max(1, data_sz - 64))
        if c < .85: return MASK
        if c < .90: return 1 << 63
        return rng.randrange(0, 1 << 16)
    return [dict({f'x{i}': one() for i in range(8)}, nzcv=rng.randrange(16), v={f'v{i}': rng.choice(VPOOL) | rng.choice(VPOOL) << 64 for i in range(32)}) for _ in range(k)]


def worker(a):
    """Run a batch of functions: AOT next to Unicorn; prints one JSON line per function"""
    import aot_lift
    from unicorn import Uc, UcError, UC_ARCH_ARM64, UC_MODE_ARM
    from unicorn.arm64_const import UC_ARM64_REG_SP, UC_ARM64_REG_NZCV, UC_ARM64_REG_PC, UC_ARM64_REG_Q0, UC_ARM64_REG_CPACR_EL1, UC_ARM64_REG_FPCR, UC_ARM64_REG_FPSR
    import arm64_ref as ref
    man, mod, _ = module(a.exefs, a.work, a.module)
    starts = [int(x, 16) for x in a.starts.split(',')]
    fe = {f['start']: f['end'] for f in json.loads(man.read_text())['functions']}
    rng = random.Random(a.seed * 1000003 + starts[0])
    rw = next(sg['vaddr'] for sg in mod.m['segments'] if sg['perms'] == 'rw-')
    img = bytes(mod.image) + bytes(mod.size - len(mod.image))
    scratch = bytes(rng.getrandbits(8) for _ in range(SCRATCH_SIZE))
    cases = [(mod.base + j['start'], bytes(mod.image[j['origin']:j['end']]), mod.base + j['origin']) for j in json.loads(man.read_text()).get('jump_targets', []) if j['origin'] in starts]
    aot = aot_lift.Aot([(mod.base + s, bytes(mod.image[s:fe[s]])) for s in starts] + cases, stub_unliftable=True, jit=a.jit)
    aot.lib.aot_map(mod.base, rw); aot.lib.aot_poke(mod.base, img[:rw], rw)   # text and ro: never written by a leaf function
    aot.map(mod.base + rw, mod.size - rw, img[rw:]); aot.map(SCRATCH, SCRATCH_SIZE, scratch); aot.map(STACK, STACK_SIZE)
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    uc.mem_map(mod.base, mod.size); uc.mem_write(mod.base, img); uc.mem_map(SCRATCH, SCRATCH_SIZE); uc.mem_map(STACK, STACK_SIZE); uc.mem_map(STOP, 0x1000)
    uc.reg_write(UC_ARM64_REG_CPACR_EL1, 3 << 20)   # floating point and vectors on
    for s in starts:
        r = dict(function=hex(mod.base + s), status='', cases=0, detail='')
        r['jit'] = bool(a.jit and mod.base + s in aot.unlifted)   # an unliftable function runs in the JIT when there is one
        if mod.base + s in aot.unlifted and not a.jit: r['status'] = 'unliftable'; r['detail'] = aot.unlifted[mod.base + s][:200]; print(json.dumps(r), flush=True); continue
        counts = collections.Counter(); j0 = aot.stat('indirect_jumps'); f0 = aot.stat('fallback_requests')
        for inp in inputs(rng, mod, a.cases):
            nz = inp.pop('nzcv'); vr = inp.pop('v')
            uc.mem_write(mod.base + rw, img[rw:]); uc.mem_write(SCRATCH, scratch); uc.mem_write(STACK, bytes(STACK_SIZE))
            for i in range(31): uc.reg_write(ref.xreg(i), inp.get(f'x{i}', 0))
            for i in range(32): uc.reg_write(UC_ARM64_REG_Q0 + i, vr[f'v{i}'])
            uc.reg_write(UC_ARM64_REG_FPCR, 0); uc.reg_write(UC_ARM64_REG_FPSR, 0)
            uc.reg_write(ref.xreg(30), STOP); uc.reg_write(UC_ARM64_REG_SP, STACK + STACK_SIZE - 0x100); uc.reg_write(UC_ARM64_REG_NZCV, nz << 28)
            try: uc.emu_start(mod.base + s, STOP, count=200000); fault = None
            except UcError as e: fault = str(e)
            if fault is None and uc.reg_read(UC_ARM64_REG_PC) != STOP: counts['budget'] += 1; continue
            aot.reset()
            got = aot.run(mod.base + s, dict(inp, x30=STOP), nz, STACK + STACK_SIZE - 0x100, vregs=vr)
            if fault is not None or 'fault' in got:
                if fault is not None and 'fault' in got: counts['both-fault'] += 1
                else: counts['fault-differs'] += 1; r['detail'] = f'inputs {inp} nzcv {nz:04b}: unicorn {fault!r}, aot {got.get("fault")!r}'
                continue
            want = {f'x{i}': uc.reg_read(ref.xreg(i)) for i in range(31)}; want['sp'] = uc.reg_read(UC_ARM64_REG_SP); want['nzcv'] = uc.reg_read(UC_ARM64_REG_NZCV) >> 28
            want.update({f'v{i}': uc.reg_read(UC_ARM64_REG_Q0 + i) for i in range(32)})
            diff = [k for k in want if got[k] != want[k]]
            for b, size in ((mod.base + rw, mod.size - rw), (SCRATCH, SCRATCH_SIZE), (STACK, STACK_SIZE)):
                if bytes(uc.mem_read(b, size)) != got['mem'][b]: diff.append(f'memory@{b:#x}')
            if diff: counts['MISMATCH'] += 1; r['detail'] = f'inputs {inp} nzcv {nz:04b}: differs in {diff[:6]}; ' + ', '.join(f'{k} aot {got[k]:#x} unicorn {want[k]:#x}' for k in diff[:6] if k in want)
            else: counts['equal'] += 1
            r['cases'] += 1
        counts['table-jumps'] = aot.stat('indirect_jumps') - j0; counts['jit-or-error-requests'] = aot.stat('fallback_requests') - f0   # indirect jumps taken (the tables') and those no AOT entry took
        r['counts'] = dict(counts)
        if r['jit']: counts['jit'] = 0
        r['status'] = 'mismatch' if counts['MISMATCH'] or counts['fault-differs'] else 'equal' if counts['equal'] else 'inconclusive'
        print(json.dumps(r), flush=True)
    aot.close()


def run_batch(a, starts):
    cmd = [sys.executable, '-I', str(HERE / 'module_validate.py'), '_worker', a.exefs, a.work, a.module, '--starts', ','.join(f'{s:x}' for s in starts), '--cases', str(a.cases), '--seed', str(a.seed)] + (['--jit'] if a.jit else []) + (['--only-unliftable'] if a.only_unliftable else []) + (['--switches'] if a.switches else [])
    try: p = subprocess.run(cmd, capture_output=True, text=True, timeout=a.timeout, env=dict(os.environ, PYTHONPATH=f'{HERE}:{HERE / "tests"}'))
    except subprocess.TimeoutExpired: return [dict(function=hex(s), status='timeout') for s in starts]
    rows = [json.loads(l) for l in p.stdout.splitlines() if l.startswith('{')]
    done = {r['function'] for r in rows}
    return rows + [dict(function=f'{s:#x}', status='worker-died', detail=p.stderr[-300:]) for s in starts if f'{s:#x}' not in done and False] if rows or p.returncode == 0 else [dict(function='batch', status='worker-died', detail=p.stderr[-400:])]


def cmd_diff(a):
    man, mod, _ = module(a.exefs, a.work, a.module)
    cand = candidates(man, mod, a.switches)
    rng = random.Random(a.seed)
    if a.only_unliftable:
        bad = {int(k, 16) - mod.base for k in json.loads((Path(a.work) / f'{a.module}.coverage.json').read_text())['unliftable']}
        cand = [c for c in cand if c in bad]
    pick = sorted(rng.sample(cand, min(a.functions, len(cand))))
    print(f'{a.module}: {len(cand)} leaf candidates of {len(json.loads(man.read_text())["functions"])} functions; testing {len(pick)} with {a.cases} random cases each', flush=True)
    batches = [pick[i:i + a.batch] for i in range(0, len(pick), a.batch)]
    rows, t = [], time.time()
    with cf.ThreadPoolExecutor(a.jobs) as ex:
        for part in ex.map(lambda b: run_batch(a, b), batches): rows += part
    for b in [r for r in rows if r['status'] == 'timeout']:   # a hung batch: its functions one by one, so only the guilty one is lost
        pass
    st = collections.Counter(r['status'] for r in rows)
    print(f'{len(rows)} results in {time.time() - t:.0f} s: ' + ', '.join(f'{k} {v}' for k, v in st.most_common()))
    cases = collections.Counter()
    for r in rows:
        for k, v in r.get('counts', {}).items(): cases[k] += v
    print('cases:', dict(cases))
    for r in rows:
        if r['status'] in ('mismatch', 'timeout', 'worker-died'): print(' ', r['status'], r.get('function'), r.get('detail', ''))
    (Path(a.work) / f'{a.module}.diff.json').write_text(json.dumps(rows, indent=1))
    return 1 if st['mismatch'] else 0


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('cmd', choices=['coverage', 'census', 'diff', '_worker']); p.add_argument('exefs'); p.add_argument('work'); p.add_argument('module')
    p.add_argument('--sample', type=int, default=0); p.add_argument('--jobs', type=int, default=max(1, (os.cpu_count() or 2) - 2)); p.add_argument('--seed', type=int, default=1)
    p.add_argument('--functions', type=int, default=200); p.add_argument('--cases', type=int, default=12); p.add_argument('--batch', type=int, default=8); p.add_argument('--timeout', type=int, default=240)
    p.add_argument('--top', type=int, default=60); p.add_argument('--starts', default=''); p.add_argument('--jit', action='store_true', help='also build the dynarmic fallback: unliftable functions then run in it (needs DYNARMIC_DIR)')
    p.add_argument('--switches', action='store_true', help='diff: test the functions that contain a jump table (switch) instead of the plain leaves')
    p.add_argument('--only-unliftable', action='store_true', help='diff: test only the functions coverage found unliftable (needs a previous coverage run of the same module)')
    a = p.parse_args()
    Path(a.work).mkdir(parents=True, exist_ok=True)
    if a.cmd == 'coverage': cmd_coverage(a)
    elif a.cmd == 'census': cmd_census(a)
    elif a.cmd == 'diff': sys.exit(cmd_diff(a))
    else: worker(a)


if __name__ == '__main__': main()
