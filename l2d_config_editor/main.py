"""Application entry point."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication

PACKAGE_DIR = Path(__file__).resolve().parent


def _project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return PACKAGE_DIR.parent


PROJECT_ROOT = _project_root()

if __package__ in {None, ""}:
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from l2d_config_editor.main_window import MainWindow
    from l2d_config_editor.styles import APP_STYLE
else:
    from .main_window import MainWindow
    from .styles import APP_STYLE


def main() -> int:
    if "--no-close-prompt" in sys.argv:
        os.environ["L2D_CONFIG_EDITOR_NO_CLOSE_PROMPT"] = "1"
        sys.argv.remove("--no-close-prompt")
    for argument in list(sys.argv):
        if argument.startswith("--test-close-policy="):
            os.environ["L2D_CONFIG_EDITOR_TEST_CLOSE_POLICY"] = argument.split("=", 1)[1].strip().lower()
            sys.argv.remove(argument)
    if "--auto-discard-on-close" in sys.argv:
        os.environ["L2D_CONFIG_EDITOR_TEST_CLOSE_POLICY"] = "discard"
        sys.argv.remove("--auto-discard-on-close")
    if "--auto-save-on-close" in sys.argv:
        os.environ["L2D_CONFIG_EDITOR_TEST_CLOSE_POLICY"] = "save"
        sys.argv.remove("--auto-save-on-close")
    app = QApplication(sys.argv)
    app.setApplicationName("L2D Config Editor")
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLE)
    window = MainWindow(PROJECT_ROOT)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
