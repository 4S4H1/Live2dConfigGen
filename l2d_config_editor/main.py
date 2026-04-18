"""Application entry point."""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import QSettings
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
    from l2d_config_editor.logic import set_runtime_theme_mode
    from l2d_config_editor.main_window import MainWindow
    from l2d_config_editor.styles import build_app_style, normalize_theme_mode
else:
    from .logic import set_runtime_theme_mode
    from .main_window import MainWindow
    from .styles import build_app_style, normalize_theme_mode


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("L2D Config Editor")
    app.setStyle("Fusion")
    settings = QSettings("OpenAI", "L2DConfigEditor")
    theme_mode = normalize_theme_mode(settings.value("ui/theme_mode", "light"))
    set_runtime_theme_mode(theme_mode)
    app.setStyleSheet(build_app_style(theme_mode))
    window = MainWindow(PROJECT_ROOT)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
