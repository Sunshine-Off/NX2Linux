"""`nx2linux.py sanitize` (issue #12): what it refuses, what it skips and why, how it judges a run, and that the normal build is left alone. None of this runs a sanitizer: that is the command itself."""
import argparse
import contextlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'linux' / 'cli'))

import nx2linux
import nx2linux_sanitize as san


def args(**kw):
    base = dict(sanitizers='address,undefined', build_dir=None, skip_build=True, jobs=0, only=None, vulkan='default', leaks=False, timeout=60)
    return argparse.Namespace(**dict(base, **kw))


def run_quietly(namespace):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = san.run(namespace, ROOT, sys.executable)
    return code, out.getvalue(), err.getvalue()


class Judging(unittest.TestCase):
    def test_a_clean_exit_is_clean(self):
        self.assertEqual(san.classify(0, 'Ran 5 tests in 1s\n\nOK\n'), ('clean', 0))

    def test_a_failed_test_without_a_report_is_failed_not_a_finding(self):
        self.assertEqual(san.classify(1, 'FAILED (failures=2)\n'), ('FAILED', 0))

    def test_every_sanitizer_report_is_a_finding_even_when_the_exit_code_is_zero(self):
        for text in ('==12==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x1\n', '==12==ERROR: LeakSanitizer: detected memory leaks\n',
                     'gpu.c:10:5: runtime error: shift exponent 40 is too large for 32-bit type\n', 'SUMMARY: UndefinedBehaviorSanitizer: undefined-behavior gpu.c:10:5\n'):
            with self.subTest(text[:40]):
                self.assertEqual(san.classify(0, text)[0], 'SANITIZER'); self.assertEqual(san.classify(1, text)[0], 'SANITIZER')

    def test_a_report_wins_over_a_failed_test(self):
        self.assertEqual(san.classify(1, 'ERROR: AddressSanitizer: x\nFAILED\n'), ('SANITIZER', 1))

    def test_text_that_only_mentions_a_sanitizer_is_not_a_finding(self):
        self.assertEqual(san.classify(0, 'test_a_sanitizer_report_is_a_finding ... ok\nrunning with AddressSanitizer on\n'), ('clean', 0))


class Environment(unittest.TestCase):
    def env(self, **kw):
        return san.sanitizer_environment(Path('/rt/libclang_rt.asan.so'), 'address,undefined', Path('/b'), Path('/sc'), base={'PATH': '/bin', 'PYTHONPATH': '/old'}, **kw)

    def test_the_sanitizer_environment_is_for_the_child_only(self):
        before = dict(os.environ)
        env = self.env()
        self.assertEqual(env['LD_PRELOAD'], '/rt/libclang_rt.asan.so')
        self.assertEqual(dict(os.environ), before)   # a preload in this process would break every tool it starts

    def test_the_libraries_come_from_the_sanitizer_build_and_the_runtime_is_built_with_it(self):
        env = self.env()
        self.assertEqual((env['NX2LINUX_GPU_LIB'], env['NX2LINUX_AUDIO_LIB'], env['NX2LINUX_DISPLAY_LIB']), ('/b/libnx2linux_gpu.so', '/b/libnx2linux_audio.so', '/b/libnx2linux_display.so'))
        self.assertEqual(env['AOT_SANITIZE'], 'address,undefined')
        self.assertEqual(env['PYTHONPATH'], os.pathsep.join(['/sc', '/old']))

    def test_a_report_stops_the_process_and_leaks_are_off_unless_asked(self):
        self.assertIn('halt_on_error=1', self.env()['UBSAN_OPTIONS'])
        self.assertIn('detect_leaks=0', self.env()['ASAN_OPTIONS']); self.assertIn('detect_leaks=1', self.env(leaks=True)['ASAN_OPTIONS'])

    def test_llvmpipe_only_when_asked_and_present(self):
        self.assertNotIn('VK_DRIVER_FILES', self.env())
        with patch.object(Path, 'is_file', return_value=True): self.assertTrue(self.env(soft_vulkan=True)['VK_DRIVER_FILES'].endswith('lvp_icd.x86_64.json'))
        with patch.object(Path, 'is_file', return_value=False): self.assertNotIn('VK_DRIVER_FILES', self.env(soft_vulkan=True))

    def test_the_site_customization_removes_only_the_deepbind_request(self):
        """capstone opens libcapstone with RTLD_DEEPBIND, which ends a sanitized process: the test process drops the request"""
        done = subprocess.run([sys.executable, '-c', san.SITECUSTOMIZE + 'import os; print(hasattr(os, "RTLD_DEEPBIND"), hasattr(os, "RTLD_LAZY"))'], capture_output=True, text=True)
        self.assertEqual(done.stdout.split(), ['False', 'True'])


class Skipping(unittest.TestCase):
    def case(self, group, module='test_x'): return san.Case(group, module)

    def test_the_aot_cases_need_remill_and_say_so(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('REMILL_BUILD', None)
            self.assertIn('REMILL_BUILD', san.skip_reason(self.case('aot'), Path('/b')))
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'REMILL_BUILD': d}):
            self.assertEqual(san.skip_reason(self.case('aot'), Path('/b')), '')

    def test_the_jit_cases_need_dynarmic_as_well(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'REMILL_BUILD': d}):
            os.environ.pop('DYNARMIC_DIR', None)
            self.assertIn('DYNARMIC_DIR', san.skip_reason(self.case('jit'), Path('/b')))
            lib = Path(d) / 'build/src/dynarmic/libdynarmic.a'; lib.parent.mkdir(parents=True); lib.write_bytes(b'')
            with patch.dict(os.environ, {'DYNARMIC_DIR': d}): self.assertEqual(san.skip_reason(self.case('jit'), Path('/b')), '')

    def test_the_host_and_c_cases_need_neither(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('REMILL_BUILD', None)
            self.assertEqual(san.skip_reason(self.case('host'), Path('/b')), ''); self.assertEqual(san.skip_reason(san.Case('c', None), Path('/b')), '')

    def test_a_skipped_case_is_reported_with_its_reason_not_run(self):
        with patch.dict(os.environ, {}, clear=False), patch.object(subprocess, 'run') as run:
            os.environ.pop('REMILL_BUILD', None)
            got = san.run_case(self.case('aot'), ROOT, sys.executable, {}, Path('/b'), Path('/l'), 5)
        self.assertEqual(got.status, 'skipped'); self.assertIn('REMILL_BUILD', got.detail); run.assert_not_called()


class JitCase(unittest.TestCase):
    def test_the_jit_case_runs_without_an_alternate_signal_stack_and_the_others_keep_it(self):
        seen = {}
        def fake(command, cwd, env, **kw):
            seen[command[-1]] = env['ASAN_OPTIONS']; return subprocess.CompletedProcess(command, 0, 'OK', '')
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'REMILL_BUILD': d, 'DYNARMIC_DIR': d}), patch.object(subprocess, 'run', side_effect=fake):
            lib = Path(d) / 'build/src/dynarmic/libdynarmic.a'; lib.parent.mkdir(parents=True); lib.write_bytes(b'')
            for case in (san.Case('jit', 'test_j'), san.Case('aot', 'test_a')):
                san.run_case(case, ROOT, sys.executable, {'ASAN_OPTIONS': 'detect_leaks=0'}, Path(d), Path(d), 5)
        self.assertIn('use_sigaltstack=0', seen['test_j']); self.assertNotIn('use_sigaltstack', seen['test_a'])


class Cases(unittest.TestCase):
    def test_the_list_is_well_formed(self):
        names = [c.name for c in san.CASES]
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue({c.group for c in san.CASES} <= set(san.GROUPS))
        self.assertTrue(san.CASES[0].module is None)   # ctest first: it is the cheapest and says at once whether the build works

    def test_every_listed_module_exists_in_the_engine_tests(self):
        tests = ROOT / 'engine' / 'dm' / 'tools' / 'tests'
        if not tests.is_dir():
            self.skipTest('engine/ is not synced (python3 tools/engine.py sync)')
        for case in san.CASES:
            if case.module: self.assertTrue((tests / f'{case.module}.py').is_file(), case.module)

    def test_the_sigsegv_handler_of_the_inline_memory_accesses_is_in_the_list(self):
        inline = [c for c in san.CASES if dict(c.env).get('AOT_INLINE_MEM') == '1']
        self.assertIn('test_aot_svc', [c.module for c in inline])   # the test that makes the guest touch an unmapped page and expects the handler's error


class Refusals(unittest.TestCase):
    def test_a_bad_sanitizer_list_is_refused(self):
        for bad in ('address;rm', 'Address', '', 'address,'):
            with self.subTest(bad):
                code, _, err = run_quietly(args(sanitizers=bad)); self.assertEqual(code, 2); self.assertIn('comma-separated', err)

    def test_a_missing_tool_is_named(self):
        with patch.object(shutil, 'which', side_effect=lambda t: None if t == 'clang++' else '/usr/bin/' + t):
            code, _, err = run_quietly(args())
        self.assertEqual(code, 2); self.assertIn('clang++ is not installed', err)

    def test_a_missing_sanitizer_runtime_is_named(self):
        with patch.object(shutil, 'which', return_value='/usr/bin/x'), patch.object(san, 'runtime_library', return_value=None):
            code, _, err = run_quietly(args())
        self.assertEqual(code, 2); self.assertIn('compiler-rt', err)

    def test_the_normal_build_directory_is_refused(self):
        with patch.object(shutil, 'which', return_value='/usr/bin/x'), patch.object(san, 'runtime_library', return_value=Path('/rt/x.so')):
            code, _, err = run_quietly(args(build_dir=str(ROOT / 'build' / 'linux-host')))
        self.assertEqual(code, 2); self.assertIn('normal build directory', err)

    def test_a_build_that_has_no_sanitizer_in_it_is_refused_not_run(self):
        """a plain build would give a green run that proves nothing"""
        with tempfile.TemporaryDirectory() as d:
            for name in san.LIBRARIES: (Path(d) / name).write_bytes(b'')
            with patch.object(shutil, 'which', return_value='/usr/bin/x'), patch.object(san, 'runtime_library', return_value=Path('/rt/x.so')), \
                    patch.object(san, 'has_sanitizer', return_value=False), patch.object(san, 'run_case') as run_case:
                code, _, err = run_quietly(args(build_dir=d))
        self.assertEqual(code, 1); self.assertIn('no sanitizer in it', err); run_case.assert_not_called()

    def test_a_library_that_was_not_built_is_named(self):
        with tempfile.TemporaryDirectory() as d, patch.object(shutil, 'which', return_value='/usr/bin/x'), patch.object(san, 'runtime_library', return_value=Path('/rt/x.so')):
            code, _, err = run_quietly(args(build_dir=d))
        self.assertEqual(code, 1); self.assertIn('libnx2linux_gpu.so', err)

    def test_the_exit_code_follows_the_table(self):
        results = [san.Result(san.Case('host', 'a'), 'clean'), san.Result(san.Case('aot', 'b'), 'skipped', 'needs REMILL_BUILD')]
        with tempfile.TemporaryDirectory() as d:
            for name in san.LIBRARIES: (Path(d) / name).write_bytes(b'')
            for bad, want in (('clean', 0), ('FAILED', 1), ('SANITIZER', 1)):
                one = [san.Result(san.Case('host', 'z'), bad)]
                with patch.object(shutil, 'which', return_value='/usr/bin/x'), patch.object(san, 'runtime_library', return_value=Path('/rt/x.so')), \
                        patch.object(san, 'has_sanitizer', return_value=True), patch.object(san, 'run_case', side_effect=lambda *a, **k: one[0]), patch.object(san, 'CASES', (san.Case('host', 'z'),)):
                    self.assertEqual(run_quietly(args(build_dir=d))[0], want, bad)
        self.assertIn('skipped: 1', san.summary(results))


@unittest.skipUnless(shutil.which('clang') and shutil.which('nm'), 'needs clang and nm')
class Instrumentation(unittest.TestCase):
    def test_a_library_built_with_the_sanitizer_is_told_from_a_plain_one(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / 'a.c'; src.write_text('int f(int *p) { return *p + 1; }\n')
            plain, instrumented = Path(d) / 'plain.so', Path(d) / 'san.so'
            for out, flags in ((plain, []), (instrumented, ['-fsanitize=address'])):
                done = subprocess.run(['clang', '-shared', '-fPIC', *flags, str(src), '-o', str(out)], capture_output=True, text=True)
                if done.returncode: self.skipTest('clang cannot build a sanitized library here: ' + done.stderr[-200:])
            self.assertFalse(san.has_sanitizer(plain)); self.assertTrue(san.has_sanitizer(instrumented))


@unittest.skipUnless(shutil.which('clang') and san.runtime_library(), 'needs clang with its sanitizer runtime')
class Detection(unittest.TestCase):
    """The pipeline can fail: a library with a heap overflow and a shift by 40, loaded by Python with the runtime preloaded as the command does, is judged SANITIZER"""
    def test_a_real_overflow_and_a_real_undefined_shift_are_findings(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / 'bad.c'
            src.write_text('#include <stdlib.h>\nint over(void) { char *p = malloc(4); p[4] = 1; int r = p[4]; free(p); return r; }\nint shift(int n) { return 1 << n; }\n')
            lib = Path(d) / 'bad.so'
            done = subprocess.run(['clang', '-shared', '-fPIC', '-fsanitize=address,undefined', '-shared-libsan', '-g', str(src), '-o', str(lib)], capture_output=True, text=True)
            if done.returncode: self.skipTest('cannot build: ' + done.stderr[-200:])
            sc = Path(d) / 'sc'; sc.mkdir(); (sc / 'sitecustomize.py').write_text(san.SITECUSTOMIZE)
            env = san.sanitizer_environment(san.runtime_library(), 'address,undefined', Path(d), sc)
            for call, kind in (('over()', 'AddressSanitizer'), ('shift(40)', 'runtime error')):
                with self.subTest(call):
                    done = subprocess.run([sys.executable, '-c', f'import ctypes; print(ctypes.CDLL({str(lib)!r}).{call.split("(")[0]}' + ('()' if call == 'over()' else '(40)') + ')'], env=env, capture_output=True, text=True)
                    status, n = san.classify(done.returncode, done.stdout + done.stderr)
                    self.assertEqual(status, 'SANITIZER', done.stderr[-300:]); self.assertIn(kind, done.stderr)


class NormalBuild(unittest.TestCase):
    """The sanitizer flags are behind an option that is empty by default: the normal build has none of them"""
    def setUp(self): self.text = (ROOT / 'linux' / 'host' / 'CMakeLists.txt').read_text()

    def test_the_option_is_empty_by_default(self):
        self.assertRegex(self.text, r'set\(NX2LINUX_SANITIZE "" CACHE STRING')

    def test_every_sanitizer_flag_is_inside_the_if_of_the_option(self):
        lines = self.text.splitlines()
        start = next(i for i, l in enumerate(lines) if l.strip() == 'if(NX2LINUX_SANITIZE)')
        end = next(i for i in range(start, len(lines)) if lines[i].strip() == 'endif()')
        for i, line in enumerate(lines):
            if '-fsanitize=' in line and not line.lstrip().startswith('#'):
                self.assertTrue(start < i < end, f'line {i + 1} sets a sanitizer flag outside the option: {line.strip()}')

    @unittest.skipUnless(shutil.which('cmake'), 'needs cmake')
    def test_a_default_configure_has_no_sanitizer_flag(self):
        with tempfile.TemporaryDirectory() as d:
            done = subprocess.run(['cmake', '-S', str(ROOT / 'linux' / 'host'), '-B', d], capture_output=True, text=True)
            if done.returncode: self.skipTest('the host does not configure here (SDL3, Vulkan, EGL or glslc missing): ' + done.stderr[-160:])
            cache = (Path(d) / 'CMakeCache.txt').read_text()
            self.assertRegex(cache, r'(?m)^NX2LINUX_SANITIZE:STRING=$')
            self.assertNotIn('fsanitize', ''.join(p.read_text(errors='replace') for p in Path(d).rglob('flags.make')))


class Parser(unittest.TestCase):
    def test_the_command_is_registered_with_its_defaults(self):
        got = nx2linux.parser().parse_args(['sanitize'])
        self.assertEqual((got.command, got.sanitizers, got.vulkan, got.leaks, got.skip_build, got.only), ('sanitize', 'address,undefined', 'default', False, False, None))

    def test_only_takes_known_groups(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit): nx2linux.parser().parse_args(['sanitize', '--only', 'gpu'])


if __name__ == '__main__':
    unittest.main()
