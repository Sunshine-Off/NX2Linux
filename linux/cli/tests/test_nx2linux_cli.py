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

    def test_worker_python_prefers_nx2_python_then_the_venv(self):
        with patch.dict(os.environ, {'NX2_PYTHON': '/opt/py/bin/python'}, clear=False):
            self.assertEqual(nx2linux.worker_python(), '/opt/py/bin/python')
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'bin').mkdir()
            (Path(tmp) / 'bin' / 'python').write_text('')
            env = {k: v for k, v in os.environ.items() if k != 'NX2_PYTHON'}
            with patch.dict(os.environ, dict(env, NX2_VENV=tmp), clear=True):
                self.assertEqual(nx2linux.worker_python(), str(Path(tmp) / 'bin' / 'python'))
            with patch.dict(os.environ, dict(env, NX2_VENV=str(Path(tmp) / 'none'), HOME=tmp), clear=True):
                self.assertEqual(nx2linux.worker_python(), sys.executable)

    def test_doctor_reports_the_worker_modules(self):
        with patch.object(nx2linux, 'missing_worker_modules', return_value=['capstone']):
            check = nx2linux.doctor_report()['worker-python']
        self.assertFalse(check['ok'])
        self.assertIn('capstone', check['detail'])
        self.assertIn('setup_toolchain.sh', check['detail'])
        with patch.object(nx2linux, 'missing_worker_modules', return_value=[]):
            self.assertTrue(nx2linux.doctor_report()['worker-python']['ok'])

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
            '<testcase name="nx2linux_gl_on_vulkan" status="run"/>'
            '</testsuite>'), 0)

    def test_gl_on_vulkan_skipped_is_not_a_pass_when_vulkan_is_required(self):
        base = ('<testsuite><testcase name="nx2linux_sdl_host_smoke" status="run"/>'
                '<testcase name="nx2linux_vulkan_triangle" status="run"/>')
        self.assertNotEqual(self.verify_report(base + '<testcase name="nx2linux_gl_on_vulkan" status="run"><skipped/></testcase></testsuite>'), 0)
        self.assertNotEqual(self.verify_report(base + '</testsuite>'), 0)

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


class ExtractAndSmokeTests(unittest.TestCase):
    def test_extract_refuses_an_output_folder_inside_the_repository(self):
        with tempfile.TemporaryDirectory() as temporary:
            nsp, keys = Path(temporary) / 'g.nsp', Path(temporary) / 'prod.keys'
            nsp.touch(); keys.touch()
            args = argparse.Namespace(nsp=str(nsp), keys=str(keys), out=str(ROOT / 'extracted'))
            with patch.object(nx2linux.shutil, 'which', return_value='/usr/bin/dotnet'), \
                    patch.object(nx2linux, 'NX_EXTRACT', ROOT / 'linux' / 'cli' / 'tests' / 'fake'), \
                    contextlib.redirect_stderr(io.StringIO()) as err:
                # nx_extract is checked before the output folder, so give it a project file that exists
                fake = ROOT / 'linux' / 'cli' / 'tests' / 'fake'
                fake.mkdir(exist_ok=True)
                (fake / 'nx_extract.csproj').touch()
                try:
                    code = nx2linux.extract(args)
                finally:
                    (fake / 'nx_extract.csproj').unlink()
                    fake.rmdir()
            self.assertEqual(code, 2)
            self.assertIn('inside the repository', err.getvalue())

    def test_extract_accepts_the_keys_folder_and_reports_a_missing_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = argparse.Namespace(nsp=str(Path(temporary) / 'missing.nsp'), keys=temporary, out=None)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(nx2linux.extract(args), 2)

    def smoke_args(self, script, runs=3):
        return argparse.Namespace(runs=runs, timeout=20, title_id='0100000000000000', game_version='1', profile='p',
                                  log_dir=None, command_line=[sys.executable, '-c', script])

    def test_smoke_passes_when_every_run_exits_cleanly_without_red(self):
        script = 'print("gpu: llvmpipe"); print("driver: lavapipe")'
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(nx2linux.smoke(self.smoke_args(script)), 0)
        report = json.loads(out.getvalue())
        self.assertEqual((report['gpu'], report['driver'], len(report['runs'])), ('llvmpipe', 'lavapipe', 3))

    def test_smoke_fails_on_a_red_line_or_a_bad_exit(self):
        for script in ('print("[RED] unexplained")', 'raise SystemExit(3)'):
            with self.subTest(script=script), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(nx2linux.smoke(self.smoke_args(script)), 1)

    def test_survey_hands_its_arguments_to_the_engine_script(self):
        with patch.object(nx2linux.subprocess, 'run', return_value=subprocess.CompletedProcess([], 3)) as run:
            code = nx2linux.main(['survey', '--', '--modules', 'm', '--title', '0100000000000000'])
        self.assertEqual(code, 3)
        self.assertEqual(run.call_args.args[0], [sys.executable, str(nx2linux.SURVEY), '--modules', 'm', '--title', '0100000000000000'])

    def test_smoke_without_a_command_is_a_usage_error(self):
        args = self.smoke_args('')
        args.command_line = []
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(nx2linux.smoke(args), 2)


if __name__ == '__main__':
    unittest.main()
