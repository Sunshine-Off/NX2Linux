import json
import argparse
import contextlib
import io
import os
import subprocess
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

    def test_relative_xdg_override_falls_back_like_the_gui(self):
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': 'relative'}, clear=False):
            self.assertEqual(nx2linux.xdg_dir('config'), Path.home() / '.config/nx2linux')

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


class VerifyTests(unittest.TestCase):
    def verify_report(self, xml, *, code=0, require=True):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'nx2linux_vulkan_demo').touch()
            args = argparse.Namespace(build_dir=str(directory), build=False, jobs=0,
                                      require_vulkan=require)
            def ctest(command, **kwargs):
                self.assertIn('--no-tests=error', command)
                self.assertNotIn('capture', kwargs)
                report = Path(command[command.index('--output-junit') + 1])
                if xml is not None:
                    report.write_text(xml)
                return subprocess.CompletedProcess(command, code)
            with patch.object(nx2linux, 'run', side_effect=ctest), contextlib.redirect_stderr(io.StringIO()):
                return nx2linux.verify(args)

    def test_both_required_tests_ran(self):
        self.assertEqual(self.verify_report('<testsuite>'
            '<testcase name="nx2linux_sdl_host_smoke" status="run"/>'
            '<testcase name="nx2linux_vulkan_triangle" status="run"/>'
            '</testsuite>'), 0)

    def test_empty_missing_and_malformed_reports_fail(self):
        for xml in [None, 'not XML', '<testsuite/>', '<testsuite><testcase/></testsuite>']:
            with self.subTest(xml=xml):
                self.assertNotEqual(self.verify_report(xml), 0)

    def test_vulkan_skipped_missing_or_failed_is_not_a_pass(self):
        for vulkan in ['', '<testcase name="nx2linux_vulkan_triangle" status="notrun"/>',
                       '<testcase name="nx2linux_vulkan_triangle" status="run"><skipped/></testcase>',
                       '<testcase name="nx2linux_vulkan_triangle" status="run"><failure/></testcase>']:
            with self.subTest(vulkan=vulkan):
                self.assertNotEqual(self.verify_report('<testsuite><testcase name="nx2linux_sdl_host_smoke" status="run"/>' + vulkan + '</testsuite>'), 0)

    def test_headless_mode_still_requires_sdl(self):
        self.assertEqual(self.verify_report('<testsuite><testcase name="nx2linux_sdl_host_smoke" status="run"/></testsuite>', require=False), 0)
        self.assertNotEqual(self.verify_report('<testsuite/>', require=False), 0)

    def test_ctest_failure_propagates(self):
        self.assertEqual(self.verify_report(None, code=8), 8)


if __name__ == '__main__':
    unittest.main()
