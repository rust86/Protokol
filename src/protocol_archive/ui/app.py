from __future__ import annotations

import sys
from pathlib import Path

from .. import APP_NAME


def run(db_path: Path) -> int:
    from PySide6.QtWidgets import QApplication, QMessageBox

    from ..db import SchemaVersionError
    from .main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    try:
        window = MainWindow(db_path)
    except SchemaVersionError as exc:
        QMessageBox.critical(None, APP_NAME, str(exc))
        return 2
    window.show()
    return app.exec()
