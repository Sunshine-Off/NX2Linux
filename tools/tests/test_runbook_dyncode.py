"""linux/docs/measure-dynamic-code.md (issue #17): the options in its commands exist, and the SVC names and report fields it explains are the ones the code has."""
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / 'linux' / 'docs' / 'measure-dynamic-code.md'
TOOLS = ROOT / 'engine' / 'dm' / 'tools'


@unittest.skipUnless((TOOLS / 'horizon_run.py').is_file(), 'engine/ is not synced (python3 tools/engine.py sync)')
class Runbook(unittest.TestCase):
    def test_every_option_in_the_commands_exists(self):
        help_text = subprocess.run([sys.executable, '-I', str(TOOLS / 'horizon_run.py'), '--help'], capture_output=True, text=True, timeout=120).stdout
        used = set()
        for block in re.findall(r'```sh\n(.*?)```', DOC.read_text(), re.S):
            for command in block.replace('\\\n', ' ').splitlines():
                if 'horizon_run.py' in command: used |= set(re.findall(r'(?<![\w-])(--[a-z][a-z-]*)', command))
        self.assertTrue(used >= {'--modules', '--romfs', '--title', '--jit', '--seconds', '--cache', '--report-json'}, used)
        for flag in sorted(used): self.assertIn(flag, help_text, flag)

    def test_the_svc_names_the_text_lists_are_recorded_by_horizon(self):
        sys.path.insert(0, str(TOOLS))
        import horizon_hle
        listed = re.search(r'Horizon now names and records (.*?)\. A call with', DOC.read_text(), re.S).group(1)
        names = re.findall(r'`(\w+)`', listed)
        self.assertEqual(set(names), set(horizon_hle.CODE_SVCS))

    def test_the_report_fields_the_text_names_exist(self):
        sys.path.insert(0, str(TOOLS))
        import aot_lift
        text = DOC.read_text()
        for name in re.findall(r'`runtime\.(\w+)`', text): self.assertIn(name, aot_lift.STATS, name)
        for name in ('extra.jit_targets', 'jit_fallback', 'fallback_requests'): self.assertIn(name, text)
        src = (TOOLS / 'horizon_run.py').read_text()
        self.assertIn("extra['jit_targets']", src); self.assertIn("'dynamic_code'", src)

    def test_no_game_file_or_key_is_named(self):
        for bad in ('prod.keys', '.nsp', '.xci'): self.assertNotIn(bad, DOC.read_text())


if __name__ == '__main__':
    unittest.main()
