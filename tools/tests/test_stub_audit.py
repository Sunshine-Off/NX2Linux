"""tools/stub_audit.py (NX2Linux issue 20): families from mangled names, merging runs, ranking, the text. No game: audit files written here."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import stub_audit as sa

HID = '_ZN2nn3hid13GetNpadStatesEPNS0_17NpadHandheldStateEiRKj'
TOUCH = '_ZN2nn3hid20GetTouchScreenStatesILm16EEEiPNS0_16TouchScreenStateIXT_EEEi'
ACC = '_ZN2nn7account12AsyncContext9GetResultEv'
CTOR = '_ZN2nn7account12AsyncContextC1Ev'
AUDIO = '_ZN2nn5audio16AcquireVoiceSlotEPNS0_19AudioRendererConfigEPNS0_9VoiceTypeEiiNS0_12SampleFormatEiPKvm'
C = 'nndiagStartup'


def doc(calls, stubbed, title='0100e95004038000', seconds=3600, frames=1000):
    return {'schema': 1, 'title': title, 'seconds': seconds, 'frames': frames, 'stubbed_exported': stubbed,
            'calls': {s: {'count': n, 'first_site': site, 'first_s': 1.0} for s, (n, site) in calls.items()}}


class Names(unittest.TestCase):
    def test_components(self):
        self.assertEqual(sa.components(HID), ['nn', 'hid', 'GetNpadStates']); self.assertEqual(sa.components(TOUCH), ['nn', 'hid', 'GetTouchScreenStates'])
        self.assertEqual(sa.components(ACC), ['nn', 'account', 'AsyncContext', 'GetResult']); self.assertEqual(sa.components(CTOR), ['nn', 'account', 'AsyncContext'])
        self.assertEqual(sa.components('_ZN2nn2oe10InitializeEv'), ['nn', 'oe', 'Initialize']); self.assertEqual(sa.components('_Z3fooi'), ['foo'])

    def test_qualifiers_are_skipped_and_garbage_gives_nothing(self):
        self.assertEqual(sa.components('_ZNK2nn3hid3FooEv'), ['nn', 'hid', 'Foo'])
        for bad in ('nndiagStartup', '', '_ZN', '_ZN99xE', 'main'): self.assertEqual(sa.components(bad), [], bad)

    def test_family_and_pretty(self):
        self.assertEqual(sa.family(HID), 'nn::hid'); self.assertEqual(sa.family(ACC), 'nn::account'); self.assertEqual(sa.family(C), 'other')
        self.assertEqual(sa.family('_ZN2nn3FooEv'), 'nn'); self.assertEqual(sa.family('_Z3fooi'), 'global')
        self.assertEqual(sa.pretty(HID), 'nn::hid::GetNpadStates'); self.assertEqual(sa.pretty(C), C)


class Merge(unittest.TestCase):
    def test_counts_are_added_and_the_first_site_is_the_first_run_that_has_one(self):
        a = doc({HID: (10, 'main+0x10'), ACC: (2, None)}, [HID, ACC, AUDIO]); b = doc({HID: (5, 'main+0x99'), ACC: (1, 'main+0x50')}, [HID, ACC, AUDIO], seconds=1800, frames=500)
        m = sa.merge([a, b])
        self.assertEqual(m['runs'], 2); self.assertEqual(m['seconds'], 5400); self.assertEqual(m['frames'], 1500)
        self.assertEqual(m['calls'][HID], {'count': 15, 'runs': 2, 'first_site': 'main+0x10'}); self.assertEqual(m['calls'][ACC]['first_site'], 'main+0x50')
        self.assertEqual(sa.never(m), [AUDIO])

    def test_different_titles_are_refused(self):
        with self.assertRaises(SystemExit) as cm: sa.merge([doc({}, [], title='a'), doc({}, [], title='b')])
        self.assertIn('different titles', str(cm.exception))

    def test_stubs_that_only_one_file_lists_are_in_the_union(self):
        m = sa.merge([doc({}, [HID]), doc({}, [AUDIO])]); self.assertEqual(m['stubbed'], sorted([HID, AUDIO]))

    def test_the_ranking_is_by_count_then_name(self):
        m = sa.merge([doc({HID: (5, 'x'), ACC: (50, 'y'), AUDIO: (5, 'z')}, [HID, ACC, AUDIO])])
        self.assertEqual([s for s, _ in sa.ranked(m)], [ACC, AUDIO, HID])

    def test_families(self):
        m = sa.merge([doc({HID: (5, 'x'), TOUCH: (1, 'x'), ACC: (50, 'y')}, [HID, TOUCH, ACC, CTOR, AUDIO, C])])
        f = sa.families(m)
        self.assertEqual(f['nn::hid'], {'stubbed': 2, 'called': 2, 'calls': 6}); self.assertEqual(f['nn::account'], {'stubbed': 2, 'called': 1, 'calls': 50})
        self.assertEqual(f['nn::audio'], {'stubbed': 1, 'called': 0, 'calls': 0}); self.assertEqual(f['other']['stubbed'], 1)


class Text(unittest.TestCase):
    def merged(self):
        return sa.merge([doc({HID: (600, 'main+0x10'), ACC: (3, 'subsdk0+0x8')}, [HID, ACC, AUDIO, C], seconds=3600, frames=64000)])

    def test_the_summary_lines(self):
        out = sa.text(self.merged())
        self.assertIn('1 run(s), 1.00 h, 64000 frames presented', out); self.assertIn('exports: 4, called in at least one run: 2, never called: 2', out)
        self.assertIn('nn::hid::GetNpadStates  first from main+0x10', out); self.assertIn('10.0/min', out); self.assertIn('in 1/1 runs', out)

    def test_never_called_are_listed_by_family_only_when_asked(self):
        m = self.merged(); self.assertNotIn('never called (', sa.text(m)); out = sa.text(m, show_never=True)
        self.assertIn('never called (2):', out); self.assertIn('    nn::audio::AcquireVoiceSlot', out); self.assertIn('  other\n    nndiagStartup', out)

    def test_top_limits_the_list(self):
        out = sa.text(self.merged(), top=1); self.assertIn('GetNpadStates', out); self.assertNotIn('AsyncContext::GetResult  first', out)

    def test_a_run_that_called_nothing(self):
        out = sa.text(sa.merge([doc({}, [HID])])); self.assertIn('  none', out); self.assertIn('never called: 1', out)

    def test_a_call_not_in_the_list_is_counted_and_said(self):
        out = sa.text(sa.merge([doc({HID: (1, 'x')}, [])])); self.assertIn('not in the exported list', out)


class Command(unittest.TestCase):
    def run_tool(self, *args):
        return subprocess.run([sys.executable, '-I', str(TOOLS / 'stub_audit.py'), *args], capture_output=True, text=True)

    def test_two_files_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b, out = Path(tmp) / 'a.json', Path(tmp) / 'b.json', Path(tmp) / 'm.json'
            a.write_text(json.dumps(doc({HID: (4, 'main+0x1')}, [HID, AUDIO]))); b.write_text(json.dumps(doc({HID: (6, 'main+0x1')}, [HID, AUDIO])))
            r = self.run_tool(str(a), str(b), '--never', '--json', str(out))
            merged = json.loads(out.read_text())
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn('2 run(s)', r.stdout); self.assertIn('nn::audio::AcquireVoiceSlot', r.stdout); self.assertEqual(merged['calls'][HID]['count'], 10)

    def test_a_file_that_is_not_an_audit_is_a_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'x.json'; p.write_text('{"a": 1}')
            r = self.run_tool(str(p)); r2 = self.run_tool(str(Path(tmp) / 'missing.json'))
        for res in (r, r2): self.assertNotEqual(res.returncode, 0); self.assertNotIn('Traceback', res.stderr)
        self.assertIn('not a stub audit file', r.stderr); self.assertIn('cannot read', r2.stderr)

    def test_it_reads_what_the_engine_writes(self):
        engine_tools = TOOLS.parent / 'engine' / 'dm' / 'tools'
        if not (engine_tools / 'horizon_stubs.py').is_file(): self.skipTest('the engine is not synchronised here')
        code = ("import sys, types, tempfile, os, json; sys.path.insert(0, %r); import horizon_stubs as hs, horizon_overrides as ho, time; "
                "mods=[types.SimpleNamespace(name='main', base=0x8000000, size=0x100000, exports={})]; "
                "hz=types.SimpleNamespace(ms=types.SimpleNamespace(modules=mods), stub_calls={}, start=time.monotonic(), log=lambda m: None, frames_presented=3); "
                "r=[0]*31; r[30]=0x8000100; ho._stub(%r)(hz, r); "
                "a=hs.Audit(hz, sys.argv[1], 'T', stubbed=[%r]); a.write()") % (str(engine_tools), HID, HID)
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'a.json'
            subprocess.run([sys.executable, '-I', '-c', code, str(p)], check=True)
            m = sa.merge([sa.load(str(p))])
        self.assertEqual(m['calls'][HID], {'count': 1, 'runs': 1, 'first_site': 'main+0x100'})


if __name__ == '__main__': unittest.main()
