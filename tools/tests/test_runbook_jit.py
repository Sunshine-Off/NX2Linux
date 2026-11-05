"""linux/docs/measure-jit-fallback.md (issue #14) names commands the maintainer runs on the real game. The commands are not run here, but the options in them must exist, and the report fields the text explains must be
the ones aot_report.py writes: a runbook that names a flag that was renamed is a day lost on a 25-minute build."""
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / 'linux' / 'docs' / 'measure-jit-fallback.md'
TOOLS = ROOT / 'engine' / 'dm' / 'tools'


@unittest.skipUnless((TOOLS / 'horizon_run.py').is_file(), 'engine/ is not synced (python3 tools/engine.py sync)')
class Runbook(unittest.TestCase):
    def blocks(self):
        return re.findall(r'```sh\n(.*?)```', DOC.read_text(), re.S)

    def test_every_option_of_horizon_run_in_the_runbook_exists(self):
        help_text = subprocess.run([sys.executable, '-I', str(TOOLS / 'horizon_run.py'), '--help'], capture_output=True, text=True, timeout=120).stdout
        used = set()
        for block in self.blocks():
            for command in re.split(r'(?<!\\)\n(?=\S)', block.replace('\\\n', ' ')):
                if 'horizon_run.py' in command: used |= set(re.findall(r'(?<![\w-])(--[a-z][a-z-]*)', command))
        self.assertTrue(used >= {'--modules', '--romfs', '--title', '--jit', '--seconds', '--cache', '--report-json'}, used)
        for flag in sorted(used): self.assertIn(flag, help_text, f'{flag} is in the runbook and not in horizon_run.py --help')

    def test_the_aot_report_commands_are_the_ones_the_tool_has(self):
        text = '\n'.join(self.blocks())
        for sub in re.findall(r'aot_report\.py (\w+)', text): self.assertIn(sub, ('show', 'compare'))
        self.assertIn('aot_report.py compare', text)

    def test_the_fields_the_text_explains_are_written_by_the_report(self):
        sys.path.insert(0, str(TOOLS))
        import aot_lift
        doc = DOC.read_text()
        for name in re.findall(r'`runtime\.(\w+)`', doc): self.assertIn(name, aot_lift.STATS, name)
        for name in re.findall(r'`derived\.(\w+)`', doc): self.assertIn(name, ('aot_function_share', 'fallback_share_of_indirect_transfers'), name)

    def test_no_game_file_or_key_is_named(self):
        text = DOC.read_text()
        for bad in ('prod.keys', '.nsp', '.xci'): self.assertNotIn(bad, text)


if __name__ == '__main__':
    unittest.main()
