"""T6: ELF modules and a manifest instead of the Mach-O loader's inputs. A synthetic module (module_fixture.py: a PLT stub and GOT slot reaching a host function, a function-pointer table only relocations fill in,
exported data, an init_array entry, an undefined weak symbol, an import nobody implements) goes NSO -> ELF + manifest -> relocated and mapped -> lifted -> run, and equals the Unicorn ARM64 reference running the
same relocated image. The manifest is checked against its ELF; every kind of loading failure is an explicit error naming the module and the symbol or address.
Needs REMILL_BUILD, clang++, unicorn and capstone; the JIT tests also DYNARMIC_DIR; skipped otherwise."""
import json, os, struct, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent))
import aot_lift
import arm64_ref as ref
import elf_module as em
import module_fixture as mf
from test_aot_t3 import READY, same

try: import capstone  # noqa: F401
except ImportError: capstone = None
READY = READY and capstone
JIT_READY = READY and os.path.exists(os.path.join(os.environ.get('DYNARMIC_DIR', '/nonexistent'), 'build/src/dynarmic/libdynarmic.a'))
M = ref.MASK
HLE = {'hle_add': lambda r: r.__setitem__(0, (r[0] + r[1]) & M)}


def make_dir(*specs):
    d = tempfile.TemporaryDirectory()
    for sp in specs:
        nso, info = mf.build(sp)
        p = Path(d.name) / f'{sp["name"]}.nso'; p.write_bytes(nso); em.convert_nso(p, d.name, sp['name'])
    return d, [str(Path(d.name) / f'{sp["name"]}.manifest.json') for sp in specs]


def reference(ms, entry, regs_list):
    """Unicorn on the relocated images of ms, with the same host functions at the same import addresses"""
    mem = {m.base: bytes(m.image) + bytes(m.size - len(m.image)) for m in ms.modules}
    mem[ms.STACK] = bytes(ms.STACK_SIZE)
    imports = {a: ms.hle[n] for n, a in ms.imports.items() if ms.hle.get(n)}
    return ref.run_image(b'\0' * 0x1000, entry, [(dict(r, x30=em.STOP), 0, ms.STACK + ms.STACK_SIZE) for r in regs_list], mem, stop=em.STOP, imports=imports)


class Discovery(unittest.TestCase):
    def test_a_plt_covered_by_one_unwind_entry_is_split_at_each_called_stub(self):
        """GCC and the SDK give the whole PLT one FDE, and every stub is called on its own: a direct call into the middle of a function from another one is an entry"""
        d, paths = make_dir(mf.module_a())
        try:
            data = (Path(d.name) / 'a.elf').read_bytes()
            loads, dyn, _ = em.parse_elf(data); img = em.image_of(data, loads)
            dynamic = em.read_dynamic(img, dyn); syms, relocs = em.read_symbols(img, dynamic), em.read_relocs(img, dynamic)
            info = mf.build(mf.module_a())[1]
            plt0, plt1 = info['fn']['plt_hle_add'], info['fn']['plt_missing_fn']
            real = em.nso_code.fde_ranges
            fde = [r for r in real(img) if not plt0[0] <= r[0] < plt1[1]] + [(plt0[0], plt1[1])]   # one range over both stubs
            em.nso_code.fde_ranges = lambda _: sorted(fde)
            try: funcs = em.find_functions(img, loads[0]['filesz'], syms, relocs)
            finally: em.nso_code.fde_ranges = real
            self.assertEqual((funcs[plt0[0]][0], funcs[plt1[0]][0], funcs[plt1[0]][1]), (plt1[0], plt1[1], 'call'))   # split where mod_calls_missing / mod_entry call the second stub
        finally: d.cleanup()

    def test_an_exported_function_inside_one_unwind_entry_is_an_entry(self):
        """the SDK's svc wrappers (`svc #n ; ret`, one per exported function) share one unwind entry; each is called through the GOT and must be an entry of its own"""
        d, paths = make_dir(mf.module_a())
        try:
            data = (Path(d.name) / 'a.elf').read_bytes()
            loads, dyn, _ = em.parse_elf(data); img = em.image_of(data, loads)
            dynamic = em.read_dynamic(img, dyn); syms, relocs = em.read_symbols(img, dynamic), em.read_relocs(img, dynamic)
            exported = sorted(v for _, _, typ, defined, v, _ in syms if defined and typ == 2)
            self.assertGreater(len(exported), 1)
            real = em.nso_code.fde_ranges
            em.nso_code.fde_ranges = lambda _: [(exported[0], loads[0]['filesz'])]   # one range over the whole text
            try: funcs = em.find_functions(img, loads[0]['filesz'], syms, relocs)
            finally: em.nso_code.fde_ranges = real
            for v in exported: self.assertIn(v, funcs)
        finally: d.cleanup()


class Manifest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir, cls.paths = make_dir(mf.module_a(), mf.module_b())

    @classmethod
    def tearDownClass(cls): cls.dir.cleanup()

    def test_manifest_lists_what_the_loader_needs(self):
        m = json.loads(Path(self.paths[0]).read_text())
        self.assertEqual(m['format'], em.FORMAT)
        self.assertEqual([s['perms'] for s in m['segments']], ['r-x', 'r--', 'rw-'])
        self.assertEqual({e['name'] for e in m['exports']}, {'mod_entry', 'mod_table_call', 'mod_value_get', 'mod_init', 'mod_weak_get', 'mod_calls_missing', 'mod_value'})
        self.assertEqual([(i['name'], i['weak']) for i in m['imports']], [('hle_add', False), ('missing_fn', False), ('weak_fn', True)])
        self.assertEqual(m['relocations'], {'GLOB_DAT': 1, 'JUMP_SLOT': 2, 'RELATIVE': 3})
        hows = {f['start']: f['how'] for f in m['functions']}
        info = mf.build(mf.module_a())[1]
        for stub in ('plt_hle_add', 'plt_missing_fn'): self.assertEqual(hows[info['fn'][stub][0]], 'call', stub)   # no unwind entry: found as call targets
        self.assertEqual(hows[info['fn']['mod_entry'][0]], 'fde')
        self.assertEqual(len(m['functions']), len(info['fn']))

    def test_the_manifest_is_reproducible(self):
        d2, p2 = make_dir(mf.module_a())
        try: self.assertEqual(Path(self.paths[0]).read_text(), Path(p2[0]).read_text())
        finally: d2.cleanup()

    def test_a_manifest_that_does_not_match_its_elf_is_refused(self):
        m = json.loads(Path(self.paths[0]).read_text())
        for key, value, what in (('exports', m['exports'][:-1], 'exports'), ('functions', m['functions'][1:], 'functions'), ('size', 1, 'size')):
            bad = Path(self.dir.name) / 'bad.manifest.json'
            bad.write_text(json.dumps(dict(m, **{key: value})))
            with self.assertRaisesRegex(em.ModuleError, f'"{what}" does not match'): em.load_manifest(bad)

    def test_an_elf_that_changed_is_refused(self):
        elf = Path(self.dir.name) / 'a.elf'; good = elf.read_bytes()
        try:
            elf.write_bytes(good[:-1] + bytes([good[-1] ^ 1]))
            with self.assertRaisesRegex(em.ModuleError, 'SHA-256 differs'): em.load_manifest(self.paths[0])
        finally: elf.write_bytes(good)

    def test_files_that_are_not_modules_are_explicit_errors(self):
        for data, why in ((b'', 'not a little-endian ELF64'), (b'\x7fELF\x02\x01' + bytes(100), 'shared object')):
            with self.assertRaisesRegex(em.ModuleError, why): em.parse_elf(data)
        junk = Path(self.dir.name) / 'junk.manifest.json'; junk.write_text('{"format": "other"}')
        with self.assertRaisesRegex(em.ModuleError, 'format'): em.load_manifest(junk)


@unittest.skipUnless(READY, 'needs REMILL_BUILD, clang++, unicorn and capstone')
class Loading(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir, cls.paths = make_dir(mf.module_a(), mf.module_b())
        cls.ms = em.ModuleSet(cls.paths, HLE)
        cls.aot = cls.ms.build()

    @classmethod
    def tearDownClass(cls): cls.aot.close(); cls.dir.cleanup()

    def run_both(self, name, cases):
        entry = self.ms.symbol(name)
        want = reference(self.ms, entry, cases)
        for regs, w in zip(cases, want):
            self.aot.reset()
            self.assertNotIn('fault', w, f'{name} {regs}: the reference faulted: {w.get("fault")}')
            same(self, self.ms.call(name, regs), w, f'{name} {regs}')

    def test_nothing_needed_the_jit_or_the_stubs(self):
        self.assertEqual(self.aot.unlifted, {})

    def test_a_plt_stub_reaches_the_host_function(self):
        self.run_both('mod_entry', [{'x0': x} for x in (0, 7, M, 1 << 63)])
        self.aot.reset(); self.assertEqual(self.ms.call('mod_entry', {'x0': 7})['x0'], 13)
        self.assertGreater(self.aot.stat('import_calls'), 0)

    def test_relocated_function_pointers_are_called(self):
        self.run_both('mod_table_call', [{'x0': i, 'x1': v} for i in (0, 1) for v in (0, 21, M)])
        self.aot.reset(); self.assertEqual([self.ms.call('mod_table_call', {'x0': i, 'x1': 21})['x0'] for i in (0, 1)], [42, 121])

    def test_exported_data_and_the_init_array(self):
        self.run_both('mod_value_get', [{}])
        self.aot.reset()
        self.assertEqual(self.ms.call('mod_value_get')['x0'], 0x1111)
        self.assertIsNone(self.ms.run_inits())
        self.assertEqual(self.ms.call('mod_value_get')['x0'], 0x1234)   # mod_init ran from the init_array the RELATIVE relocation filled in
        self.aot.reset()

    def test_a_weak_symbol_nobody_defines_is_bound_to_zero(self):
        self.run_both('mod_weak_get', [{}])
        self.assertEqual(self.ms.call('mod_weak_get')['x0'], 0)
        self.assertIn('a: undefined weak weak_fn bound to 0', self.ms.log)

    def test_another_modules_export_is_bound_before_the_host(self):
        """b imports mod_entry, which a defines: the GOT slot holds a's function, not an import address"""
        b = self.ms.modules[1]
        slot = mf.build(mf.module_b())[1]['got']['mod_entry']
        self.assertEqual(struct.unpack_from('<Q', b.image, slot)[0], self.ms.symbol('mod_entry'))
        self.run_both('b_call', [{'x0': x} for x in (0, 3, M)])
        self.aot.reset(); self.assertEqual(self.ms.call('b_call', {'x0': 3})['x0'], 3 + 5 + 1 + 1000)

    def test_an_import_nobody_implements_stops_with_its_name(self):
        got = self.ms.call('mod_calls_missing')
        self.assertIn('unresolved import missing_fn', got['fault'])
        self.assertIn('missing_fn is not defined by any module nor by the host', ' '.join(self.ms.log))

    def test_the_symbols_modules_define_are_the_first_definitions(self):
        self.assertEqual(self.ms.symbol('mod_value'), self.ms.modules[0].base + 0x2018)
        with self.assertRaisesRegex(em.ModuleError, 'no loaded module defines nothing_here'): self.ms.symbol('nothing_here')


@unittest.skipUnless(READY, 'needs REMILL_BUILD, clang++, unicorn and capstone')
class Overrides(unittest.TestCase):
    """The host replaces an exported SDK function (the Mach host's HLE_FORCE): b's call to a's mod_entry runs the host function, not the lifted code"""
    def test_a_replaced_function_is_not_lifted_and_its_callers_reach_the_host(self):
        d, paths = make_dir(mf.module_a(), mf.module_b())
        try:
            ms = em.ModuleSet(paths, HLE)
            aot = ms.build(overrides={'mod_entry': lambda r: r.__setitem__(0, (r[0] * 3) & M)})
            try:
                self.assertEqual(ms.call('b_call', {'x0': 7})['x0'], 1021)   # 7 * 3 + 1000; the lifted mod_entry gives 13 + 1000
                self.assertIn(ms.symbol('mod_entry'), aot.unlifted)
            finally: aot.close()
        finally: d.cleanup()

    def test_replacing_a_symbol_nobody_exports_is_an_explicit_error(self):
        d, paths = make_dir(mf.module_a())
        try:
            with self.assertRaisesRegex(em.ModuleError, 'cannot replace nope'): em.ModuleSet(paths, HLE).build(overrides={'nope': lambda r: None})
        finally: d.cleanup()


@unittest.skipUnless(JIT_READY, 'needs REMILL_BUILD, DYNARMIC_DIR, clang++, unicorn and capstone')
class LoadingWithJit(unittest.TestCase):
    def test_a_function_remill_cannot_lift_runs_in_the_jit_and_still_reaches_the_host_function(self):
        d, paths = make_dir(mf.module_c())
        try:
            ms = em.ModuleSet(paths, HLE)
            aot = ms.build(jit=True)
            try:
                entry = ms.symbol('c_main')
                self.assertEqual(list(aot.unlifted), [entry]); self.assertIn('ccmn', aot.unlifted[entry])
                cases = [{'x0': x, 'x1': y} for x in (0, 5, M) for y in (0, 1, 1 << 63)]
                for regs, w in zip(cases, reference(ms, entry, cases)):
                    aot.reset(); same(self, ms.call('c_main', regs), w, f'{regs}')
                self.assertEqual(aot.stat('jit_entries'), len(cases)); self.assertGreaterEqual(aot.stat('import_calls'), len(cases))
            finally: aot.close()
        finally: d.cleanup()


class Switch(unittest.TestCase):
    def test_the_cases_of_a_switch_are_entries_and_a_dispatch_stays_in_the_recompiled_code(self):
        d, paths = make_dir(mf.module_d())
        try:
            m = json.loads(Path(paths[0]).read_text())
            start = m['functions'][0]['start']
            self.assertEqual([j['start'] for j in m['jump_targets']], [start + 32 + 8 * k for k in range(4)])
            self.assertEqual({j['origin'] for j in m['jump_targets']}, {start})
            ms = em.ModuleSet(paths, HLE)
            aot = ms.build()
            try:
                entry = ms.symbol('d_switch')
                cases = [{'x0': x} for x in (0, 1, 2, 3, 4, 7, M)]
                for regs, w in zip(cases, reference(ms, entry, cases)):
                    aot.reset(); same(self, ms.call('d_switch', regs), w, f'{regs}')
                self.assertEqual(ms.call('d_switch', {'x0': 2})['x0'], 30)
                self.assertEqual(aot.stat('fallback_requests'), 0)   # no JIT here, and no error: the jump went to a lifted case
                self.assertGreaterEqual(aot.stat('indirect_jumps'), 4)
            finally: aot.close()
        finally: d.cleanup()


class Errors(unittest.TestCase):
    def test_an_unsupported_relocation_type_is_an_explicit_error(self):
        d, paths = make_dir(mf.module_a())
        try:
            elf = Path(d.name) / 'a.elf'; data = bytearray(elf.read_bytes())
            m = json.loads(Path(paths[0]).read_text())
            rela = m['dynamic']['RELA'] + 0x1000   # the file offset of the image is 0x1000 (nso2elf.HDR)
            struct.pack_into('<Q', data, rela + 8, (0 << 32) | 1024)   # R_AARCH64_COPY in the first relocation
            elf.write_bytes(data)
            m['sha256'] = __import__('hashlib').sha256(bytes(data)).hexdigest(); m['relocations'] = {'1024': 1, 'GLOB_DAT': 0, 'JUMP_SLOT': 2, 'RELATIVE': 3}
            m['relocations'] = em.describe(bytes(data), 'a', 'a.elf', m['build_id'])['relocations']
            Path(paths[0]).write_text(json.dumps(m))
            with self.assertRaisesRegex(em.ModuleError, 'relocation type 1024'): em.ModuleSet(paths, HLE)
        finally: d.cleanup()

    def _first_relocation_at(self, off):
        """a module whose first relocation (a RELATIVE one) is moved to image offset `off`"""
        d, paths = make_dir(mf.module_a())
        elf = Path(d.name) / 'a.elf'; data = bytearray(elf.read_bytes())
        m = json.loads(Path(paths[0]).read_text())
        rela = m['dynamic']['RELA'] + 0x1000
        for i in range(m['relocations']['RELATIVE'] + m['relocations']['GLOB_DAT'] + m['relocations']['JUMP_SLOT']):
            if struct.unpack_from('<Q', data, rela + 24 * i + 8)[0] & 0xFFFFFFFF == 1027: break
        struct.pack_into('<Q', data, rela + 24 * i, off)
        elf.write_bytes(data)
        m['sha256'] = __import__('hashlib').sha256(bytes(data)).hexdigest()
        Path(paths[0]).write_text(json.dumps(m))
        return d, paths

    def test_a_relocation_at_a_four_byte_aligned_offset_is_applied(self):
        """packed structures hold a pointer at such an offset (Xenoblade 2 has 48)"""
        d, paths = self._first_relocation_at(0x2004)
        try:
            ms = em.ModuleSet(paths, HLE)
            self.assertNotEqual(struct.unpack_from('<Q', ms.modules[0].image, 0x2004)[0], 0)
        finally: d.cleanup()

    def test_a_relocation_off_the_four_byte_grid_is_an_explicit_error(self):
        d, paths = self._first_relocation_at(0x2002)
        try:
            with self.assertRaisesRegex(em.ModuleError, 'not 4-byte aligned'): em.ModuleSet(paths, HLE)
        finally: d.cleanup()

    def test_two_modules_of_one_name_are_refused(self):
        d, paths = make_dir(mf.module_a())
        try:
            with self.assertRaisesRegex(em.ModuleError, 'loaded twice'): em.ModuleSet(paths + paths, HLE)
        finally: d.cleanup()


if __name__ == '__main__': unittest.main()
