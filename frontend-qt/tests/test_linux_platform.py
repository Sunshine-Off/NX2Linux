"""Linux platform paths for the NX2Linux frontend foundation."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'frontend-qt'))

from nspb_qt import platform_util as pu  # noqa: E402


class LinuxPlatformTest(unittest.TestCase):
    def test_xdg_directories_use_environment(self) -> None:
        with mock.patch.object(pu, 'IS_LINUX', True), mock.patch.dict(os.environ, {
            'XDG_CONFIG_HOME': '/tmp/config',
            'XDG_CACHE_HOME': '/tmp/cache',
            'XDG_STATE_HOME': '/tmp/state',
            'XDG_DATA_HOME': '/tmp/data',
        }, clear=False):
            self.assertEqual(pu.xdg_dir('config'), Path('/tmp/config/nx2linux'))
            self.assertEqual(pu.xdg_dir('cache'), Path('/tmp/cache/nx2linux'))
            self.assertEqual(pu.xdg_dir('state'), Path('/tmp/state/nx2linux'))
            self.assertEqual(pu.xdg_dir('data'), Path('/tmp/data/nx2linux'))

    def test_xdg_directories_have_linux_defaults(self) -> None:
        with mock.patch.object(pu, 'IS_LINUX', True), mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(Path, 'home', return_value=Path('/home/test')):
            self.assertEqual(pu.xdg_dir('state'), Path('/home/test/.local/state/nx2linux'))
            self.assertEqual(pu.log_dir(), Path('/home/test/.local/state/nx2linux/logs'))

    def test_linux_venv_is_in_xdg_data(self) -> None:
        with mock.patch.object(pu, 'IS_LINUX', True), \
                mock.patch.object(pu, 'xdg_dir', return_value=Path('/tmp/data/nx2linux')):
            self.assertEqual(pu.venv_dir(), Path('/tmp/data/nx2linux/NX2Linux/venv'))

    def test_unknown_xdg_kind_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            pu.xdg_dir('runtime')


if __name__ == '__main__':
    unittest.main()
