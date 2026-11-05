"""Opt-in desktop/GPU acceptance check, never part of headless unittest discovery.

Run from the root with:
QT_QPA_PLATFORM=offscreen python3 frontend-qt/tests/check_linux_workflow.py --backend wayland
Qt can be offscreen, but SDL/Vulkan still need a real display and GPU access.
"""
import argparse
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'frontend-qt'))

from PySide6.QtCore import QEvent, QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from nspb_qt.linux_backend import LinuxBackend
from nspb_qt.linux_ui import LinuxWindow
from nspb_qt.style import STYLE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=['auto', 'wayland', 'x11'], default='auto')
    parser.add_argument('--build-dir', default='build/s6-qt-validation')
    parser.add_argument('--config', choices=['Debug', 'Release'], default='Debug')
    parser.add_argument('--screenshot', type=Path)
    args = parser.parse_args()
    app = QApplication([])
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    with tempfile.TemporaryDirectory(prefix='nx2linux-qt-check-') as temporary:
        backend = LinuxBackend(log_directory=ROOT / args.build_dir / 'qt-test-logs')
        window = LinuxWindow(backend, QSettings(str(Path(temporary) / 'settings.ini'), QSettings.IniFormat))
        window.repository.setText(str(ROOT))
        window.build_directory.setText(args.build_dir)
        window.display.setCurrentText(args.backend)
        window.config.setCurrentText(args.config)
        results = []
        backend.completed.connect(lambda *result: results.append(result))
        backend.output.connect(lambda text: print(text, end='', flush=True))
        window.show()
        try:
            for action in ['doctor', 'build', 'verify', 'run']:
                results.clear()
                window.buttons[action].click()
                assert backend.busy, f'{action}: did not start: {results}'
                assert not window.buttons['build'].isEnabled()
                deadline = time.monotonic() + 120
                while backend.busy and time.monotonic() < deadline:
                    app.processEvents()
                    QTest.qWait(10)
                assert not backend.busy, f'{action}: timeout'
                assert len(results) == 1 and results[0][1], results
                assert window.buttons['build'].isEnabled()
                assert backend.log_path.is_file()
                assert 'Completed' in backend.log_path.read_text()
                if action == 'doctor':
                    assert window.report.rowCount() > 0
                print(f'PASS: Qt -> CLI -> {action}', flush=True)
            if args.screenshot:
                assert window.grab().save(str(args.screenshot)), 'Screenshot could not be saved'
            return 0
        finally:
            backend.cancel()
            deadline = time.monotonic() + 5
            while backend.busy and time.monotonic() < deadline:
                app.processEvents()
                QTest.qWait(10)
            window.close()
            window.deleteLater()
            backend.deleteLater()
            app.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == '__main__':
    raise SystemExit(main())
