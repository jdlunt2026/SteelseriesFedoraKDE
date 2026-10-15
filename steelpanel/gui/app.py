from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from ..backends import default_backends
from ..profiles import ProfileStore
from .main_window import MainWindow, app_icon


def run(start_hidden: bool = False) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("SteelPanel")
    app.setDesktopFileName("steelpanel")
    app.setWindowIcon(app_icon())
    window = MainWindow(default_backends(), ProfileStore())
    # With a tray icon, closing the window keeps the app running.
    app.setQuitOnLastWindowClosed(window.tray is None)
    if not (start_hidden and window.tray is not None):
        window.show()
    return app.exec()
