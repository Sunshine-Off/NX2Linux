"""linux/docs/recompile-cost.md (issue #18): the command it gives runs (its options exist), and the variants and figures it names are the tool's. The figures themselves come from a run and are not recomputed."""
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / 'linux' / 'docs' / 'recompile-cost.md'
TOOLS = ROOT / 'engine' / 'dm' / 'tools'


@unittest.skipUnless((TOOLS / 'aot_cost.py').is_file(), 'engine/ is not synced (python3 tools/engine.py sync)')
class Doc(unittest.TestCase):
    def test_the_cache_stats_command_uses_options_the_tool_has(self):
        text = DOC.read_text()
        cmd = re.search(r'aot_cost\.py cache-stats [^\n]*', text).group(0)
        help_text = subprocess.run([sys.executable, '-I', str(TOOLS / 'aot_cost.py'), 'cache-stats', '--help'], capture_output=True, text=True, timeout=60).stdout
        for flag in re.findall(r'(--[a-z-]+)', cmd + ' --no-compress'): self.assertIn(flag, help_text, flag)

    def test_the_variants_in_the_table_are_the_ones_the_tool_measures(self):
        sys.path.insert(0, str(TOOLS))
        import aot_cost
        text = DOC.read_text()
        for word in ('no unwind tables', 'slim', 'thin LTO', '64 functions per object'): self.assertIn(word, text)
        self.assertEqual(aot_cost.GROUP, 64); self.assertIn('thin-lto', aot_cost.VARIANTS); self.assertIn('slim-Os', aot_cost.VARIANTS)

    def test_what_is_not_known_is_said_to_be_unknown_and_the_figures_say_where_they_come_from(self):
        text = DOC.read_text()
        self.assertIn('unknown', text); self.assertIn('Every figure is from this corpus, not from the game', text); self.assertIn('deduced', text)

    def test_no_game_file_or_key_is_named(self):
        for bad in ('prod.keys', '.nsp', '.xci'): self.assertNotIn(bad, DOC.read_text())


if __name__ == '__main__':
    unittest.main()
