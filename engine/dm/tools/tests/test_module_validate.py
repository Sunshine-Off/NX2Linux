"""T7's validation tool (module_validate.py) on a synthetic module, so it is tested without game data: coverage counts what lifts, and diff runs the leaf functions next to Unicorn and reports equality.
Needs REMILL_BUILD, clang++, unicorn and capstone; skipped otherwise."""
import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent))
import module_fixture as mf
from test_elf_module import READY
import module_validate as mv

TOOL = HERE.parent / 'module_validate.py'


@unittest.skipUnless(READY, 'needs REMILL_BUILD, clang++, unicorn and capstone')
class Validate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cls.exefs, cls.work = Path(cls.dir.name) / 'ExeFS', Path(cls.dir.name) / 'work'
        cls.exefs.mkdir()
        (cls.exefs / 'a').write_bytes(mf.build(mf.module_a())[0])

    @classmethod
    def tearDownClass(cls): cls.dir.cleanup()

    def run_tool(self, *args):
        return subprocess.run([sys.executable, '-I', str(TOOL), args[0], str(self.exefs), str(self.work), 'a', *args[1:]], capture_output=True, text=True,
                              env=dict(os.environ, PYTHONPATH=f'{TOOL.parent}:{HERE}'), timeout=300)

    def test_coverage_counts_what_lifts(self):
        p = self.run_tool('coverage', '--jobs', '2')
        self.assertEqual(p.returncode, 0, p.stderr[-500:])
        self.assertIn('a: 10 functions, 10 lift (100.0%), 0 do not', p.stdout)
        self.assertEqual(json.loads((self.work / 'a.coverage.json').read_text())['unliftable'], {})

    def test_leaf_functions_are_selected_and_equal_the_reference(self):
        man = self.work / 'a.manifest.json'
        _, mod, _ = mv.module(self.exefs, self.work, 'a')
        leaves = {mod.base + s for s in mv.candidates(man, mod)}
        info = mf.build(mf.module_a())[1]
        want = {mod.base + info['fn'][n][0] for n in ('fa', 'fb', 'mod_value_get', 'mod_weak_get', 'mod_init')}
        self.assertEqual(leaves, want)   # the PLT stubs end in br, mod_entry and the others call: not leaves
        p = self.run_tool('diff', '--functions', '10', '--cases', '6', '--batch', '2', '--jobs', '2')
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr[-500:])
        rows = json.loads((self.work / 'a.diff.json').read_text())
        self.assertEqual({r['status'] for r in rows} - {'equal', 'inconclusive'}, set(), rows)
        self.assertGreaterEqual(sum(1 for r in rows if r['status'] == 'equal'), 3)


if __name__ == '__main__': unittest.main()
