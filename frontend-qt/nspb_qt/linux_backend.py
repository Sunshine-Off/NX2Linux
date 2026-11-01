"""Asynchronous Linux CLI operations for the Qt launcher (Qt 6.7 or later)."""
from __future__ import annotations

import codecs
import json
import os
from pathlib import Path
import signal
import sys
import tempfile

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from .platform_util import xdg_dir as linux_dir


class LinuxBackend(QObject):
    output = Signal(str)
    busyChanged = Signal(bool)
    completed = Signal(str, bool, str)
    diagnostics = Signal(dict)

    def __init__(self, parent=None, *, log_directory: Path | None = None):
        super().__init__(parent)
        self.busy = False
        self.action = ''
        self.log_path: Path | None = None
        self._log_directory = log_directory
        self._log = None
        self._group = 0
        self._cancelled = False
        self._report = ''
        self._decoder = codecs.getincrementaldecoder('utf-8')('replace')
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.started.connect(self._started)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        self._kill_timer = QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.setInterval(1500)
        self._kill_timer.timeout.connect(self._kill)

    def start(self, action: str, repository: str, build_directory: str,
              backend: str = 'auto', config: str = 'Debug') -> bool:
        if self.busy:
            return False
        self.log_path = None
        try:
            if action not in ('doctor', 'build', 'verify', 'run'):
                raise ValueError('Unknown Linux operation')
            if backend not in ('auto', 'wayland', 'x11') or config not in ('Debug', 'Release'):
                raise ValueError('Invalid Linux build or display selection')
            root = Path(repository).expanduser().resolve()
            cli = root / 'linux/cli/nx2linux.py'
            if not cli.is_file() or not (root / 'linux/host/CMakeLists.txt').is_file():
                raise ValueError('Select an NX2Linux checkout containing linux/cli and linux/host')
            if not build_directory.strip():
                raise ValueError('Choose a build directory')
            directory = Path(build_directory).expanduser()
            directory = (root / directory).resolve() if not directory.is_absolute() else directory
            if not hasattr(QProcess, 'UnixProcessFlag'):
                raise ValueError('The Linux interface requires PySide6 6.7 or later')
            self.process.setUnixProcessParameters(QProcess.UnixProcessFlag.CreateNewSession)
            args = ['-u', str(cli), action]
            if action == 'doctor':
                args += ['--json']
            else:
                args += ['--build-dir', str(directory)]
            if action == 'build':
                args += ['--config', config, '-j', str(min(os.cpu_count() or 1, 8))]
            elif action == 'verify':
                args += ['--require-vulkan']
            elif action == 'run':
                args += ['--backend', backend]
            env = QProcessEnvironment.systemEnvironment()
            if backend != 'auto':
                env.insert('SDL_VIDEODRIVER', backend)
            env.insert('PYTHONUNBUFFERED', '1')
            env.insert('PYTHONIOENCODING', 'utf-8')
            self.process.setProcessEnvironment(env)
            self.process.setWorkingDirectory(str(root))
            logs = self._log_directory or linux_dir('state') / 'logs'
            logs.mkdir(parents=True, exist_ok=True)
            self._log = tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                                    prefix=f'qt-{action}-', suffix='.log',
                                                    dir=logs, delete=False)
            self.log_path = Path(self._log.name)
        except (ValueError, OSError, AttributeError) as error:
            self.completed.emit(action, False, str(error))
            return False
        self.action = action
        self._cancelled = False
        self._report = ''
        self._decoder.reset()
        self.busy = True
        self.busyChanged.emit(True)
        self._append(f'[{action}] Linux host demo workflow\nLog: {self.log_path}\n')
        self.process.start(sys.executable, args)
        return True

    def _append(self, text: str):
        if self._log:
            try:
                self._log.write(text)
                self._log.flush()
            except OSError:
                self._close_log()
                self.output.emit('Warning: cannot write the operation log\n')
        self.output.emit(text)

    def _close_log(self):
        log, self._log = self._log, None
        if log:
            try:
                log.close()
            except OSError:
                self.output.emit('Warning: cannot close the operation log\n')

    def _started(self):
        self._group = int(self.process.processId())
        if self._cancelled:
            self._signal_group(signal.SIGTERM)

    def _read(self):
        text = self._decoder.decode(bytes(self.process.readAllStandardOutput()))
        if self.action == 'doctor':
            self._report += text
        self._append(text)

    def _signal_group(self, sig):
        if self._group:
            try:
                os.killpg(self._group, sig)
            except ProcessLookupError:
                pass

    def cancel(self):
        if self.busy and not self._cancelled:
            self._cancelled = True
            self._append('Stopping operation and child processes...\n')
            self._signal_group(signal.SIGTERM)
            self._kill_timer.start()

    def _kill(self):
        self._signal_group(signal.SIGKILL)

    def _error(self, error):
        if error == QProcess.FailedToStart:
            self._complete(False, f'Cannot start Python: {self.process.errorString()}')

    def _finished(self, code, status):
        self._read()
        self._append(self._decoder.decode(b'', final=True))
        ok = code == 0 and status == QProcess.NormalExit and not self._cancelled
        note = 'Cancelled' if self._cancelled else ('Completed' if ok else f'Failed (exit {code})')
        if self.action == 'doctor' and not self._cancelled:
            try:
                report = json.loads(self._report)
                if not isinstance(report, dict) or not report:
                    raise ValueError('Empty diagnostic report')
                for entry in report.values():
                    if (not isinstance(entry, dict) or type(entry.get('ok')) is not bool
                            or not isinstance(entry.get('detail'), str)):
                        raise ValueError('Invalid diagnostic entry')
                self.diagnostics.emit(report)
            except (ValueError, TypeError) as error:
                ok, note = False, f'Invalid diagnostic report: {error}'
        self._complete(ok, note)

    def _complete(self, ok: bool, note: str):
        if not self.busy:
            return
        self._kill_timer.stop()
        # The CLI owns a new process group. Do not leave compilers or the demo behind.
        self._kill()
        self._group = 0
        self._append(f'[{self.action}] {note}\n')
        self._close_log()
        self.busy = False
        self.busyChanged.emit(False)
        self.completed.emit(self.action, ok, note)
