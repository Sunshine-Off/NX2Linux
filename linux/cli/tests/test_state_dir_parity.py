"""The folder of the host logs and crash reports is the one the Qt front end and `nx2linux.py` use (issue #23): $XDG_STATE_HOME/nx2linux/logs, else ~/.local/state/nx2linux/logs.
Three implementations of one rule (the engine cannot import the front end), so a test holds them together. Needs the engine (tools/engine.py sync) and Qt's helper module, which is plain Python."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'linux' / 'cli')); sys.path.insert(0, str(ROOT / 'frontend-qt'))
TOOLS = ROOT / 'engine' / 'dm' / 'tools'
sys.path.insert(0, str(TOOLS))

import nx2linux

try:
    import horizon_crash
except ImportError:
    horizon_crash = None
try:
    from nspb_qt import platform_util
except ImportError:
    platform_util = None


@unittest.skipIf(horizon_crash is None, 'the engine is not synced (python3 tools/engine.py sync) or has no horizon_crash.py')
class StateDirParity(unittest.TestCase):
    CASES = ({'XDG_STATE_HOME': '/var/lib/someone'}, {'XDG_STATE_HOME': 'relative/path'}, {'XDG_STATE_HOME': ''}, {})

    def test_the_cli_and_the_host_agree(self):
        for env in self.CASES:
            with patch.dict(os.environ, env, clear=False):
                if 'XDG_STATE_HOME' not in env: os.environ.pop('XDG_STATE_HOME', None)
                self.assertEqual(horizon_crash.state_dir(), nx2linux.xdg_dir('state'), env)

    @unittest.skipIf(platform_util is None or not platform_util.IS_LINUX, 'the Qt helper module needs Linux here')
    def test_the_front_end_and_the_host_agree_on_the_logs_folder(self):
        for env in self.CASES:
            with patch.dict(os.environ, env, clear=False):
                if 'XDG_STATE_HOME' not in env: os.environ.pop('XDG_STATE_HOME', None)
                self.assertEqual(horizon_crash.logs_dir(), platform_util.log_dir(), env)


if __name__ == '__main__':
    unittest.main()
