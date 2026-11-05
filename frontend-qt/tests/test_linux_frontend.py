"""Linux GUI integration tests with real QProcess children and isolated settings."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

try:
    from PySide6.QtCore import QEvent, QSettings
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
except ImportError:
    QApplication = None


@unittest.skipUnless(sys.platform == 'linux' and QApplication is not None, 'Linux and PySide6 required')
class LinuxFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from nspb_qt.linux_backend import LinuxBackend
        self.tmp = tempfile.TemporaryDirectory(prefix='nx2linux qt ')
        self.root = Path(self.tmp.name)
        self.cli = self.root / 'linux/cli/nx2linux.py'
        self.cli.parent.mkdir(parents=True)
        host = self.root / 'linux/host'
        host.mkdir()
        (host / 'CMakeLists.txt').write_text('# fixture\n')
        self.backend = LinuxBackend(log_directory=self.root / 'logs')
        self.output, self.results, self.reports = [], [], []
        self.backend.output.connect(self.output.append)
        self.backend.completed.connect(lambda *result: self.results.append(result))
        self.backend.diagnostics.connect(self.reports.append)
        self.window = None

    def tearDown(self):
        self.backend.cancel()
        self.wait_for(lambda: not self.backend.busy)
        if self.window:
            self.window.close()
            self.window.deleteLater()
        self.backend.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        self.tmp.cleanup()

    def wait_for(self, predicate, seconds=6):
        deadline = time.monotonic() + seconds
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(10)
        self.assertTrue(predicate(), ''.join(self.output))

    def script(self, source):
        self.cli.write_text(source, encoding='utf-8')

    def start(self, action='build', **kwargs):
        return self.backend.start(action, str(self.root), 'build with spaces', **kwargs)

    def make_window(self):
        from nspb_qt.linux_ui import LinuxWindow
        settings = QSettings(str(self.root / 'settings.ini'), QSettings.IniFormat)
        self.window = LinuxWindow(self.backend, settings)
        self.window.repository.setText(str(self.root))
        self.window.show()
        return self.window

    def test_live_output_busy_guard_and_persistent_log(self):
        self.script("import time\nprint('first é', flush=True)\ntime.sleep(.3)\nprint('last')\n")
        self.assertTrue(self.start())
        self.assertFalse(self.start())
        self.wait_for(lambda: 'first é' in ''.join(self.output))
        self.assertTrue(self.backend.busy)
        self.assertIn('first é', self.backend.log_path.read_text())
        self.wait_for(lambda: not self.backend.busy)
        self.assertEqual(self.results, [('build', True, 'Completed')])
        self.assertIn('last', self.backend.log_path.read_text())

    def test_arguments_paths_and_display_are_passed_without_a_shell(self):
        self.script("import json, os, sys\nprint(json.dumps([sys.argv[1:], os.getcwd(), os.getenv('SDL_VIDEODRIVER')]))\n")
        self.assertTrue(self.start(config='Release', backend='wayland'))
        self.wait_for(lambda: not self.backend.busy)
        line = next(line for line in ''.join(self.output).splitlines() if line.startswith('[['))
        args, cwd, driver = json.loads(line)
        self.assertEqual(args[:5], ['build-host', '--build-dir', str(self.root / 'build with spaces'), '--config', 'Release'])
        self.assertEqual(cwd, str(self.root))
        self.assertEqual(driver, 'wayland')

    def test_verify_always_requires_vulkan_and_run_selects_backend(self):
        self.script("import sys\nprint(repr(sys.argv))\n")
        for action, expected in [('verify', '--require-vulkan'), ('run', '--backend')]:
            with self.subTest(action=action):
                self.output.clear()
                self.assertTrue(self.start(action, backend='x11'))
                self.wait_for(lambda: not self.backend.busy)
                self.assertIn(expected, ''.join(self.output))
                self.assertTrue(self.results[-1][1])

    def test_doctor_renders_required_and_optional_failures(self):
        report = {'sdl3': {'ok': False, 'detail': 'not installed'},
                  'vulkaninfo': {'ok': False, 'required': False, 'detail': 'optional'}}
        self.script(f'import sys\nprint({json.dumps(report)!r})\nsys.exit(1)\n')
        w = self.make_window()
        w._start('doctor')
        self.assertFalse(w.buttons['build'].isEnabled())
        self.assertTrue(w.stop.isEnabled())
        self.wait_for(lambda: not self.backend.busy)
        self.assertEqual(self.reports, [report])
        self.assertEqual(w.report.item(0, 1).text(), 'Missing')
        self.assertEqual(w.report.item(1, 1).text(), 'Optional / unavailable')
        self.assertIn('Failed', w.status.text())
        self.assertTrue(w.buttons['build'].isEnabled())
        self.assertFalse(w.stop.isEnabled())

    def test_valid_doctor_succeeds(self):
        self.script('print(\'{"cmake": {"ok": true, "detail": "available"}}\')\n')
        self.start('doctor')
        self.wait_for(lambda: not self.backend.busy)
        self.assertTrue(self.results[-1][1])
        self.assertEqual(len(self.reports), 1)

    def test_malformed_doctor_is_not_success(self):
        for report in ['not JSON', '{}', '[]', '{"cmake": {"ok": "true", "detail": "bad"}}']:
            with self.subTest(report=report):
                self.script(f'print({report!r})\n')
                self.start('doctor')
                self.wait_for(lambda: not self.backend.busy)
                self.assertFalse(self.results[-1][1])
                self.assertIn('Invalid diagnostic report', self.results[-1][2])

    def test_nonzero_exit_is_not_success(self):
        self.script("import sys\nprint('compiler failed', file=sys.stderr)\nsys.exit(9)\n")
        self.start()
        self.wait_for(lambda: not self.backend.busy)
        self.assertEqual(self.results[-1], ('build', False, 'Failed (exit 9)'))
        self.assertIn('compiler failed', ''.join(self.output))

    def test_invalid_checkout_does_not_start_a_process(self):
        self.assertFalse(self.backend.start('build', str(self.root / 'missing'), 'build'))
        self.assertFalse(self.backend.busy)
        self.assertFalse(self.results[-1][1])

    def test_failed_python_start_is_reported(self):
        self.script('pass\n')
        with patch('nspb_qt.linux_backend.sys.executable', str(self.root / 'no-python')):
            self.start()
            self.wait_for(lambda: not self.backend.busy)
        self.assertFalse(self.results[-1][1])
        self.assertIn('Cannot start Python', self.results[-1][2])

    def test_unwritable_log_destination_is_reported(self):
        self.script('pass\n')
        destination = self.root / 'not-a-directory'
        destination.write_text('occupied')
        self.backend._log_directory = destination
        self.assertFalse(self.start())
        self.assertFalse(self.backend.busy)
        self.assertFalse(self.results[-1][1])

    def test_log_io_failure_does_not_leave_the_operation_busy(self):
        from unittest.mock import Mock
        self.script("import time\ntime.sleep(.1)\nprint('finished')\n")
        self.start()
        self.backend._log.close()
        broken = Mock()
        broken.write.side_effect = OSError('disk full')
        broken.close.side_effect = OSError('disk full')
        self.backend._log = broken
        self.wait_for(lambda: not self.backend.busy)
        self.assertTrue(self.results[-1][1])
        self.assertIn('Warning: cannot write', ''.join(self.output))

    def test_application_entrypoint_selects_linux_without_apple_model(self):
        source = textwrap.dedent('''
            import sys
            from PySide6.QtCore import QTimer
            from PySide6.QtWidgets import QApplication
            from nspb_qt.__main__ import main
            original = QApplication.exec
            def checked_exec(app):
                assert app.applicationName() == 'NX2Linux'
                assert 'nspb_qt.model' not in sys.modules
                windows = app.topLevelWidgets()
                assert any(type(w).__name__ == 'LinuxWindow' for w in windows)
                QTimer.singleShot(50, app.closeAllWindows)
                return original()
            QApplication.exec = checked_exec
            raise SystemExit(main())
        ''')
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen',
                   XDG_CONFIG_HOME=str(self.root / 'config'),
                   PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        result = subprocess.run([sys.executable, '-c', source], env=env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cancel_immediately_after_start(self):
        self.script('import time\ntime.sleep(60)\n')
        self.start()
        self.backend.cancel()
        self.wait_for(lambda: not self.backend.busy)
        self.assertEqual(self.results[-1], ('build', False, 'Cancelled'))

    def test_split_utf8_output_is_preserved(self):
        self.script("import os,time\nos.write(1, b'\\xc3')\ntime.sleep(.05)\nos.write(1, b'\\xa9\\n')\n")
        self.start()
        self.wait_for(lambda: not self.backend.busy)
        self.assertIn('é\n', ''.join(self.output))
        self.assertNotIn('\ufffd', ''.join(self.output))

    def test_cancellation_kills_a_stubborn_child_group(self):
        child = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('child-ready',flush=True); time.sleep(60)"
        self.script("import signal, subprocess, sys, time\n"
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                    f"p = subprocess.Popen([sys.executable, '-u', '-c', {child!r}])\n"
                    "print('child-pid=' + str(p.pid), flush=True)\ntime.sleep(60)\n")
        self.start()
        self.wait_for(lambda: 'child-ready' in ''.join(self.output))
        pid = int(next(line.split('=')[1] for line in ''.join(self.output).splitlines() if line.startswith('child-pid=')))
        self.backend.cancel()
        self.wait_for(lambda: not self.backend.busy)
        self.assertEqual(self.results[-1], ('build', False, 'Cancelled'))
        def child_stopped():
            try:
                # A killed orphan may briefly remain a zombie until PID 1 reaps it.
                return Path(f'/proc/{pid}/stat').read_text().split(') ')[1].split()[0] == 'Z'
            except FileNotFoundError:
                return True
        self.wait_for(child_stopped)

    def test_close_cancels_then_closes_without_destroyed_object_access(self):
        self.script("import time\nprint('ready', flush=True)\ntime.sleep(60)\n")
        w = self.make_window()
        w._start('run')
        self.wait_for(lambda: 'ready' in ''.join(self.output))
        w.close()
        self.wait_for(lambda: not self.backend.busy and not w.isVisible())
        self.assertFalse(self.results[-1][1])

    def test_settings_persist_and_xdg_ignores_relative_paths(self):
        from nspb_qt.linux_backend import linux_dir
        w = self.make_window()
        w.display.setCurrentText('x11')
        w.build_directory.setText('build custom')
        w.close()
        saved = QSettings(str(self.root / 'settings.ini'), QSettings.IniFormat)
        self.assertEqual(saved.value('display'), 'x11')
        self.assertEqual(saved.value('build_directory'), 'build custom')
        with patch.dict(os.environ, {'XDG_STATE_HOME': 'relative'}):
            self.assertEqual(linux_dir('state'), Path.home() / '.local/state/nx2linux')
        with patch.dict(os.environ, {'XDG_STATE_HOME': str(self.root)}):
            self.assertEqual(linux_dir('state'), self.root / 'nx2linux')


if __name__ == '__main__':
    unittest.main()
