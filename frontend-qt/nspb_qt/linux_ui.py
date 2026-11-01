"""Linux host controls backed by the S5 CLI, separate from the Apple pipeline."""
from pathlib import Path

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMainWindow, QPlainTextEdit, QProgressBar, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .linux_backend import LinuxBackend, linux_dir


class LinuxWindow(QMainWindow):
    def __init__(self, backend=None, settings=None):
        super().__init__()
        self.setWindowTitle('NX2Linux — Linux host')
        self.resize(900, 720)
        self.backend = backend or LinuxBackend(self)
        self.settings = settings or QSettings(str(linux_dir('config') / 'frontend.ini'), QSettings.IniFormat)
        self._closing = False
        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        note = QLabel('Linux host tools • Synthetic Vulkan demo\n'
                      'Game recompilation and game launch await the AOT/runtime integration.')
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.repository = QLineEdit(self.settings.value('repository', str(Path(__file__).resolve().parents[2]), type=str))
        repo_row = QHBoxLayout()
        repo_row.addWidget(self.repository)
        self.browse = QPushButton('Choose checkout…')
        self.browse.clicked.connect(self._browse)
        repo_row.addWidget(self.browse)
        form.addRow('Repository', repo_row)
        self.build_directory = QLineEdit(self.settings.value('build_directory', 'build/linux-host', type=str))
        form.addRow('Build directory', self.build_directory)
        self.display = QComboBox()
        self.display.addItems(['auto', 'wayland', 'x11'])
        self.display.setCurrentText(self.settings.value('display', 'auto', type=str))
        form.addRow('Display backend', self.display)
        self.config = QComboBox()
        self.config.addItems(['Debug', 'Release'])
        self.config.setCurrentText(self.settings.value('config', 'Debug', type=str))
        form.addRow('Build configuration', self.config)
        layout.addLayout(form)
        row = QHBoxLayout()
        self.buttons = {}
        for action, title in [('doctor', 'Diagnose'), ('build', 'Build host'),
                              ('verify', 'Verify with Vulkan'), ('run', 'Run Vulkan demo')]:
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, a=action: self._start(a))
            self.buttons[action] = button
            row.addWidget(button)
        self.stop = QPushButton('Stop')
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self.backend.cancel)
        row.addWidget(self.stop)
        layout.addLayout(row)
        self.status = QLabel('Ready')
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        self.report = QTableWidget(0, 3)
        self.report.setHorizontalHeaderLabels(['Check', 'Status', 'Details'])
        self.report.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.report.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.report, 1)
        self.log_path = QLabel('Logs are saved under XDG_STATE_HOME/nx2linux/logs')
        self.log_path.setWordWrap(True)
        layout.addWidget(self.log_path)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        layout.addWidget(self.log, 2)
        self.backend.output.connect(self._output)
        self.backend.busyChanged.connect(self._busy)
        self.backend.completed.connect(self._completed)
        self.backend.diagnostics.connect(self._diagnostics)

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, 'NX2Linux checkout', self.repository.text())
        if folder:
            self.repository.setText(folder)

    def _save(self):
        for key, value in [('repository', self.repository.text()), ('build_directory', self.build_directory.text()),
                           ('display', self.display.currentText()), ('config', self.config.currentText())]:
            self.settings.setValue(key, value)
        self.settings.sync()

    def _start(self, action):
        if self.backend.busy:
            return
        self._save()
        self.log.clear()
        if action == 'doctor':
            self.report.setRowCount(0)
        self.status.setText(f'{action}: running')
        self.backend.start(action, self.repository.text(), self.build_directory.text(),
                           self.display.currentText(), self.config.currentText())
        if self.backend.log_path:
            self.log_path.setText(f'Log: {self.backend.log_path}')
        else:
            self.log_path.setText('No log file created')

    def _output(self, text):
        cursor = self.log.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text)
        self.log.setTextCursor(cursor)
        self.log.ensureCursorVisible()

    def _busy(self, busy):
        for widget in [*self.buttons.values(), self.repository, self.build_directory,
                       self.browse, self.display, self.config]:
            widget.setEnabled(not busy)
        self.stop.setEnabled(busy)
        self.progress.setRange(0, 0 if busy else 1)

    def _completed(self, action, ok, message):
        self.status.setText(f'{action}: {message}')
        self.progress.setValue(1 if ok else 0)
        if self._closing:
            QTimer.singleShot(0, self.close)

    def _diagnostics(self, report):
        self.report.setRowCount(len(report))
        for row, (name, check) in enumerate(report.items()):
            state = 'OK' if check['ok'] else ('Missing' if check.get('required', True) else 'Optional / unavailable')
            for col, value in enumerate((name, state, check['detail'])):
                self.report.setItem(row, col, QTableWidgetItem(str(value)))

    def closeEvent(self, event):
        if self.backend.busy:
            self._closing = True
            self.status.setText('Stopping operation before closing…')
            self.backend.cancel()
            event.ignore()
            return
        self._save()
        event.accept()
