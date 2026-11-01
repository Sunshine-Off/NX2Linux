import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'linux' / 'cli'))

import nx2linux


class CliTests(unittest.TestCase):
    def test_xdg_paths_use_environment(self):
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': '/tmp/nx-config'}, clear=False):
            self.assertEqual(nx2linux.xdg_dir('config'), Path('/tmp/nx-config/nx2linux'))

    def test_doctor_json_is_serializable(self):
        report = nx2linux.doctor_report()
        json.dumps(report)
        self.assertIn('vulkan', report)
        self.assertIn('xdg-state', report)

    def test_build_dir_resolves_relative_to_repository(self):
        self.assertEqual(nx2linux.build_dir('build/test-cli'), ROOT / 'build' / 'test-cli')

    def test_parser_has_the_four_s5_commands(self):
        self.assertEqual(nx2linux.parser().parse_args(['doctor']).command, 'doctor')
        self.assertEqual(nx2linux.parser().parse_args(['build']).command, 'build')
        self.assertEqual(nx2linux.parser().parse_args(['run']).command, 'run')
        self.assertTrue(nx2linux.parser().parse_args(['verify', '--require-vulkan']).require_vulkan)


if __name__ == '__main__':
    unittest.main()
