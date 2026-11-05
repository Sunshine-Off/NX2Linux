"""linux/docs/validate-modules-against-unicorn.md (issue #15): the options in its commands exist, and the columns it explains are the ones validate_game.py prints. The commands themselves need the game and are not run."""
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / 'linux' / 'docs' / 'validate-modules-against-unicorn.md'
TOOLS = ROOT / 'engine' / 'dm' / 'tools'


@unittest.skipUnless((TOOLS / 'validate_game.py').is_file(), 'engine/ is not synced (python3 tools/engine.py sync)')
class Runbook(unittest.TestCase):
    def test_every_option_in_the_commands_exists(self):
        help_text = subprocess.run([sys.executable, '-I', str(TOOLS / 'validate_game.py'), '--help'], capture_output=True, text=True, timeout=120).stdout
        used = set()
        for block in re.findall(r'```sh\n(.*?)```', DOC.read_text(), re.S):
            for command in block.replace('\\\n', ' ').splitlines():
                if 'validate_game.py' in command: used |= set(re.findall(r'(?<![\w-])(--[a-z][a-z-]*)', command))
        self.assertTrue(used >= {'--functions', '--cases', '--seeds', '--coverage-sample', '--md'}, used)
        for flag in sorted(used): self.assertIn(flag, help_text, flag)

    def test_the_columns_the_text_explains_are_printed_by_the_tool(self):
        sys.path.insert(0, str(TOOLS))
        import validate_game as vg
        import tempfile, json
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'p').mkdir(); (Path(d) / 'p' / 'm.diff.json').write_text(json.dumps([{'function': '0x1', 'status': 'equal', 'counts': {'equal': 1}}]))
            header = vg.summarize(d, ['m'])[0].splitlines()[0]
        for column in re.findall(r'^\| ([a-zA-Z, ]+?) \|', DOC.read_text(), re.M)[1:]:
            for part in (p.strip() for p in column.split(',')):
                self.assertIn(part, header, f'"{part}" is explained in the runbook and is not a column')

    def test_no_game_file_or_key_is_named_and_the_work_folder_is_outside_the_clone(self):
        text = DOC.read_text()
        for bad in ('prod.keys', '.nsp', '.xci'): self.assertNotIn(bad, text)
        self.assertIn('outside the repository', text)


if __name__ == '__main__':
    unittest.main()
