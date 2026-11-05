"""nx2linux build on a synthetic package (engine/dm/tools/tests/nsp_fixture.py: random bytes, fake keys, made in a temporary folder). No game file or real key is read.
The full chain runs for real up to the build manifest (nx_extract, then one ELF module per NSO). The recompilation needs Remill: its command line is checked with the process stubbed."""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'linux' / 'cli'))
TESTS = ROOT / 'engine' / 'dm' / 'tools' / 'tests'
sys.path.insert(0, str(TESTS)); sys.path.insert(0, str(ROOT / 'engine' / 'dm' / 'tools'))

import nx2linux
import nx2linux_build as nb

try:
    import nsp_fixture as fx
except ImportError:
    fx = None

WHY = None
if fx is None: WHY = 'engine/ is not synced (python3 tools/engine.py sync)'
elif not shutil.which('dotnet'): WHY = 'dotnet is not installed'
elif nx2linux.missing_worker_modules(nx2linux.worker_python()): WHY = 'the worker Python lacks capstone, unicorn or lz4 (tools/setup_toolchain.sh)'


def cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = nx2linux.main(list(argv))
    return rc, out.getvalue(), err.getvalue()


@unittest.skipIf(WHY, WHY)
class Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix='nx2linux-build-')); cls.addClassCleanup(shutil.rmtree, cls.tmp, True)
        cls.pkg = fx.make(cls.tmp / 'pkg', 'nsp')

    def args(self, out, *more): return ['build', str(self.pkg.path), '--keys', str(self.pkg.keys), '--out', str(out), '--skip-recompile', *more]

    def test_build_directory_and_manifest(self):
        out = self.tmp / 'a'
        rc, text, err = cli(*self.args(out))
        self.assertEqual(rc, 0, text + err)
        root = out / f'{fx.TITLE_ID:016x}'
        self.assertIn(f'build directory: {root}', text)
        b = json.loads((root / 'build.json').read_text())
        self.assertEqual((b['format'], b['title_id']), (nb.FORMAT, f'{fx.TITLE_ID:016x}'))
        self.assertEqual(sorted(b['modules']), ['main'])   # rtld is the host's, main.npdm is not a module
        self.assertTrue((root / 'RomFS.img').is_file() and (root / 'ExeFS' / 'main').is_file())
        self.assertTrue((root / 'modules' / b['modules']['main']['elf']).is_file())
        self.assertNotIn('recompile', b['steps'])

    def test_second_run_skips_every_step_and_force_redoes_them(self):
        out = self.tmp / 'b'
        cli(*self.args(out))
        rc, text, _ = cli(*self.args(out))
        self.assertEqual(rc, 0)
        self.assertIn('[extract] skipped (unchanged)', text); self.assertIn('[modules] skipped (unchanged)', text)
        rc, text, _ = cli(*self.args(out, '--force'))
        self.assertNotIn('skipped (unchanged)', text)

    def test_a_deleted_output_is_rebuilt(self):
        out = self.tmp / 'c'; cli(*self.args(out))
        root = out / f'{fx.TITLE_ID:016x}'; (root / 'modules' / 'main.manifest.json').unlink()
        rc, text, _ = cli(*self.args(out))
        self.assertEqual(rc, 0); self.assertIn('[modules] start', text); self.assertIn('[extract] skipped', text)

    def test_json_events(self):
        rc, text, _ = cli(*self.args(self.tmp / 'd', '--json'))
        events = [json.loads(l) for l in text.splitlines() if l.startswith('{')]
        self.assertEqual([e['step'] for e in events if e['status'] == 'done'], ['check', 'extract', 'modules', 'manifest'])

    def test_stop_after(self):
        out = self.tmp / 'e'
        rc, text, _ = cli('build', str(self.pkg.path), '--keys', str(self.pkg.keys), '--out', str(out), '--stop-after', 'extract')
        self.assertEqual(rc, 0)
        self.assertFalse((out / f'{fx.TITLE_ID:016x}' / 'modules').exists())

    def test_recompile_command_line(self):
        out = self.tmp / 'f'; fake = self.tmp / 'remill'; (fake / 'bin/lift').mkdir(parents=True, exist_ok=True); (fake / 'bin/lift/remill-lift-22').write_text('')
        seen = {}
        class P:
            stdout = iter(['lifted\n']); returncode = 0
            def wait(self): return 0
        def popen(cmd, **kw): seen['cmd'] = cmd; return P()
        real = nb.subprocess.Popen   # the extraction and conversion run for real: only the recompile call is stubbed
        def pick(cmd, **kw): return popen(cmd, **kw) if 'horizon_run.py' in ' '.join(map(str, cmd)) else real(cmd, **kw)
        with patch.dict(os.environ, {'REMILL_BUILD': str(fake)}), patch.object(nb.subprocess, 'Popen', side_effect=pick):
            rc, text, err = cli('build', str(self.pkg.path), '--keys', str(self.pkg.keys), '--out', str(out))
        self.assertEqual(rc, 0, text + err)
        cmd = seen['cmd']
        self.assertIn('--build-only', cmd); self.assertIn('--order', cmd); self.assertEqual(cmd[cmd.index('--title') + 1], f'{fx.TITLE_ID:016x}')
        self.assertNotIn('--jit', cmd)
        self.assertEqual(json.loads((out / f'{fx.TITLE_ID:016x}' / 'build.json').read_text())['steps']['recompile']['cache'].endswith('aot-cache'), True)


class Failures(unittest.TestCase):
    def test_no_package(self):
        rc, _, err = cli('build'); self.assertEqual(rc, 2); self.assertIn('build-host', err)

    def test_no_keys_option(self):
        rc, _, err = cli('build', 'x.nsp'); self.assertEqual(rc, 2); self.assertIn('--keys is required', err)

    def test_missing_package_and_keys_files(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'g.nsp').write_bytes(b'x')
            rc, _, err = cli('build', str(Path(d) / 'g.nsp'), '--keys', str(Path(d) / 'nope.keys'), '--out', d + '/o')
            self.assertEqual(rc, 3); self.assertIn('keys: no keys file at', err); self.assertIn('--keys', err)
            rc, _, err = cli('build', str(Path(d) / 'absent.nsp'), '--keys', d, '--out', d + '/o')
            self.assertEqual(rc, 2); self.assertIn('game package not found', err)

    def test_missing_remill_is_named(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'g.nsp').write_bytes(b'x'); (Path(d) / 'prod.keys').write_text('')
            env = {k: v for k, v in os.environ.items() if k not in ('REMILL_BUILD', 'DYNARMIC_DIR')}
            with patch.dict(os.environ, env, clear=True):
                rc, _, err = cli('build', str(Path(d) / 'g.nsp'), '--keys', d, '--out', d + '/o')
        self.assertEqual(rc, 3, err); self.assertIn('remill: REMILL_BUILD is not set', err); self.assertIn('setup_toolchain.sh', err)

    def test_jit_needs_dynarmic(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'g.nsp').write_bytes(b'x'); (Path(d) / 'prod.keys').write_text('')
            lift = Path(d) / 'r/bin/lift'; lift.mkdir(parents=True); (lift / 'remill-lift-22').write_text('')
            env = {k: v for k, v in os.environ.items() if k != 'DYNARMIC_DIR'}; env['REMILL_BUILD'] = str(Path(d) / 'r')
            with patch.dict(os.environ, env, clear=True):
                rc, _, err = cli('build', str(Path(d) / 'g.nsp'), '--keys', d, '--out', d + '/o', '--jit')
        self.assertEqual(rc, 3, err); self.assertIn('dynarmic: DYNARMIC_DIR is not set', err)

    def test_output_inside_the_repository_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'g.nsp').write_bytes(b'x'); (Path(d) / 'prod.keys').write_text('')
            rc, _, err = cli('build', str(Path(d) / 'g.nsp'), '--keys', d, '--out', str(ROOT / 'build' / 'game'), '--skip-recompile')
        self.assertEqual(rc, 2); self.assertIn('inside the repository', err)

    def test_doctor_reports_the_same_gaps(self):
        env = {k: v for k, v in os.environ.items() if k not in ('REMILL_BUILD', 'DYNARMIC_DIR')}
        with patch.dict(os.environ, env, clear=True):
            report = nx2linux.doctor_report()
            checks = {c.name: c for c in nb.prerequisites()}
        for name in ('remill', 'dynarmic'):
            self.assertFalse(report[name]['ok']); self.assertFalse(checks[name].ok)
            self.assertIn(checks[name].detail, report[name]['detail'])
        self.assertEqual(report['dotnet']['ok'], checks['dotnet'].ok)


if __name__ == '__main__':
    unittest.main()
