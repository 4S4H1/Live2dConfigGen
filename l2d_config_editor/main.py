"""Application entry point."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QFileDialog

PACKAGE_DIR = Path(__file__).resolve().parent


def _project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return PACKAGE_DIR.parent


PROJECT_ROOT = _project_root()
SETTINGS_WORKSPACE_ROOT = "workspace_root"

if __package__ in {None, ""}:
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from l2d_config_editor.main_window import MainWindow
    from l2d_config_editor.styles import APP_STYLE
else:
    from .main_window import MainWindow
    from .styles import APP_STYLE


def resolve_initial_workspace(default: str | Path) -> Path | None:
    """Return the persisted workspace, or require a first-run selection."""

    settings = QSettings("OpenAI", "L2DConfigEditor")
    raw = settings.value(SETTINGS_WORKSPACE_ROOT)
    if raw not in (None, ""):
        candidate = Path(str(raw)).expanduser()
        if candidate.is_dir():
            return candidate.resolve()

    chosen = QFileDialog.getExistingDirectory(
        None,
        "选择 JSON 配置文件工作区",
        str(Path(default).resolve()),
    )
    if not chosen:
        return None
    workspace = Path(chosen).resolve()
    settings.setValue(SETTINGS_WORKSPACE_ROOT, str(workspace))
    settings.sync()
    return workspace


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
    workspace = resolve_initial_workspace(PROJECT_ROOT)
    if workspace is None:
        return 0
    window = MainWindow(workspace, prefer_saved_workspace=False)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
