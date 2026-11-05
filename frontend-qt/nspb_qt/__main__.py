"""Entry point: python3 -m nspb_qt  (or run main.py)."""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from .platform_util import IS_LINUX, hide_console_for_children
from .style import STYLE, load_font


def main() -> int:
    # Before anything can spawn a child: on Windows every subprocess would otherwise open its own
    # console window (see the docstring — this is the "black window, nothing happens" report).
    hide_console_for_children()
    # High-DPI: let Qt scale with the screen. On a handheld (a small 720p/800p panel at
    # 125-150%) the default policy rounds the scale factor up and the window no longer fits;
    # PassThrough honours the system factor as-is and the font sizes below stay in points.
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName('NX2Linux' if IS_LINUX else 'NS Port Builder')
    app.setApplicationDisplayName(app.applicationName())
    app.setStyleSheet(STYLE)
    app.setStyle('Fusion')   # the same widget look on Windows and macOS
    family = load_font()     # Manrope, as the Mac launcher; the system font when it cannot load
    if family:
        font = app.font()
        font.setFamily(family)
        app.setFont(font)
    if IS_LINUX:
        from .linux_ui import LinuxWindow
        win = LinuxWindow()
    else:
        from .model import Model
        from .ui import MainWindow
        model = Model()
        win = MainWindow(model)
    win.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
