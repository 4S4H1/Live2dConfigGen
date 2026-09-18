"""Application entry point."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

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
    from l2d_config_editor.app_settings import create_app_settings
    from l2d_config_editor.main_window import MainWindow, is_path_within_install_root
    from l2d_config_editor.single_instance import SingleInstance
    from l2d_config_editor.styles import APP_STYLE
    from l2d_config_editor.version import PRODUCT_ID, PRODUCT_NAME, PUBLISHER, VERSION
else:
    from .app_settings import create_app_settings
    from .main_window import MainWindow, is_path_within_install_root
    from .single_instance import SingleInstance
    from .styles import APP_STYLE
    from .version import PRODUCT_ID, PRODUCT_NAME, PUBLISHER, VERSION


def resolve_initial_workspace(
    default: str | Path,
    *,
    settings: QSettings | None = None,
    install_root: str | Path | None = None,
) -> Path | None:
    """Return the persisted workspace, or require a first-run selection."""

    app_settings = settings or create_app_settings()
    raw = app_settings.value(SETTINGS_WORKSPACE_ROOT)
    if raw not in (None, ""):
        candidate = Path(str(raw)).expanduser()
        if candidate.is_dir():
            candidate = candidate.resolve()
            if not is_path_within_install_root(candidate, install_root):
                return candidate
            QMessageBox.warning(
                None,
                "工作区位置不安全",
                "已保存的 JSON 工作区位于程序安装目录内，不能继续使用；"
                "为保护其中的数据，安装器会拒绝更新或卸载。"
                "\n请选择“文档”等安装目录以外的位置。",
            )
            app_settings.remove(SETTINGS_WORKSPACE_ROOT)
            app_settings.sync()

    while True:
        chosen = QFileDialog.getExistingDirectory(
            None,
            "选择 JSON 配置文件工作区",
            str(Path(default).resolve()),
        )
        if not chosen:
            return None
        workspace = Path(chosen).resolve()
        if is_path_within_install_root(workspace, install_root):
            QMessageBox.warning(
                None,
                "工作区位置不安全",
                "不能把 JSON 工作区放在程序安装目录内；"
                "为保护其中的数据，安装器会拒绝更新或卸载。"
                "\n请选择“文档”等安装目录以外的位置。",
            )
            continue
        app_settings.setValue(SETTINGS_WORKSPACE_ROOT, str(workspace))
        app_settings.sync()
        return workspace


def _default_workspace() -> Path:
    if not getattr(sys, "frozen", False):
        return PROJECT_ROOT
    documents = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.DocumentsLocation
    )
    return Path(documents) if documents else Path.home()


def _runtime_install_root() -> Path | None:
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve().parent


def _application_icon() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", PROJECT_ROOT)) / "assets" / "L2DConfigEditor.png"
    return PROJECT_ROOT / "build" / "icons" / "L2DConfigEditor.png"


def _consume_test_arguments() -> None:
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


def main() -> int:
    _consume_test_arguments()
    app = QApplication(sys.argv)
    app.setOrganizationName(PUBLISHER)
    app.setApplicationName(PRODUCT_ID)
    app.setApplicationDisplayName(PRODUCT_NAME)
    app.setApplicationVersion(VERSION)
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLE)
    icon = _application_icon()
    if icon.is_file():
        app.setWindowIcon(QIcon(str(icon)))

    instance = SingleInstance(parent=app)
    # Resolve relative arguments in the launching process, whose working
    # directory can differ from that of an already-running editor.
    file_arguments = instance.normalized_file_arguments(sys.argv[1:])
    if not instance.is_primary:
        return 0 if instance.send_to_primary(file_arguments) else 2

    initial_file = Path(file_arguments[0]) if file_arguments else None
    install_root = _runtime_install_root()
    if initial_file is not None and is_path_within_install_root(initial_file.resolve(), install_root):
        QMessageBox.warning(
            None,
            "文件位置不安全",
            "不能直接打开程序安装目录内的 JSON 文件；"
            "为保护其中的数据，安装器会拒绝更新或卸载。"
            "\n请先把文件移到“文档”等安装目录以外的位置。",
        )
        initial_file = None
    settings = create_app_settings()
    if initial_file is not None:
        workspace = initial_file.parent.resolve()
        settings.setValue(SETTINGS_WORKSPACE_ROOT, str(workspace))
        settings.sync()
    else:
        workspace = resolve_initial_workspace(
            _default_workspace(),
            settings=settings,
            install_root=install_root,
        )
        if workspace is None:
            return 0

    window = MainWindow(
        workspace,
        prefer_saved_workspace=False,
        install_root=install_root,
    )

    def deliver_external_arguments(arguments: list[str]) -> None:
        files = instance.normalized_file_arguments(arguments)
        if files:
            window.open_external_file(files[0])
        else:
            window.activate_from_external_request()

    instance.messageReceived.connect(deliver_external_arguments)
    window.show()
    if initial_file is not None:
        QTimer.singleShot(0, lambda: window.open_external_file(initial_file))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
