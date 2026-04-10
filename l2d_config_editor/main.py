"""Application entry point."""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from .main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow(Path.cwd())
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
