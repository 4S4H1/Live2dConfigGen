"""Standalone tray-process entry point for the LAN update Host."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

if __package__ in {None, ""}:
    package_root = Path(__file__).resolve().parents[1]
    if str(package_root) not in sys.path:
        sys.path.insert(0, str(package_root))
    from l2d_config_editor.host_process import HostIpcServer, request_host
    from l2d_config_editor.update_host import (
        HOST_RESTORE_AT_LOGIN_KEY,
        HOST_SERVICE_ENABLED_KEY,
        UpdateHostWindow,
        bundled_host_asset,
        create_host_settings,
        set_login_startup,
        setting_is_enabled,
    )
    from l2d_config_editor.version import PUBLISHER, VERSION
else:
    from .host_process import HostIpcServer, request_host
    from .update_host import (
        HOST_RESTORE_AT_LOGIN_KEY,
        HOST_SERVICE_ENABLED_KEY,
        UpdateHostWindow,
        bundled_host_asset,
        create_host_settings,
        set_login_startup,
        setting_is_enabled,
    )
    from .version import PUBLISHER, VERSION


def _arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--login", action="store_true")
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--restore-after-update", action="store_true")
    parser.add_argument("--restore-login", choices=("0", "1"))
    parser.add_argument("--restore-run", choices=("0", "1"))
    parser.add_argument("--restore-service", choices=("0", "1"))
    parser.add_argument("--restore-process", choices=("0", "1"), default="1")
    return parser.parse_known_args(argv)[0]


def _prepare_startup_state(
    args: argparse.Namespace,
    settings: QSettings,
) -> bool:
    """Apply login/update restoration and return whether to keep running."""

    if args.restore_after_update:
        restore_login = (
            args.restore_login == "1"
            if args.restore_login is not None
            else setting_is_enabled(
                settings,
                HOST_RESTORE_AT_LOGIN_KEY,
                False,
            )
        )
        restore_run = (
            args.restore_run == "1"
            if args.restore_run is not None
            else restore_login
        )
        restore_service = (
            args.restore_service == "1"
            if args.restore_service is not None
            else setting_is_enabled(
                settings,
                HOST_SERVICE_ENABLED_KEY,
                False,
            )
        )
        settings.setValue(HOST_RESTORE_AT_LOGIN_KEY, restore_login)
        settings.setValue(HOST_SERVICE_ENABLED_KEY, restore_service)
        settings.sync()
        set_login_startup(restore_run)
        return args.restore_process == "1"

    if args.login:
        # A stale Run entry must not resurrect a Host that the user explicitly
        # exited from the tray.
        if not setting_is_enabled(
            settings,
            HOST_RESTORE_AT_LOGIN_KEY,
            False,
        ):
            set_login_startup(False)
            return False
        set_login_startup(True)
    return True


def main(argv: list[str] | None = None) -> int:
    args = _arguments(list(sys.argv[1:] if argv is None else argv))
    app = QApplication([sys.argv[0]])
    app.setOrganizationName(PUBLISHER)
    app.setApplicationName("L2DUpdateHost")
    app.setApplicationDisplayName("L2D 局域网更新主机")
    app.setApplicationVersion(VERSION)
    app.setQuitOnLastWindowClosed(False)
    icon_path = bundled_host_asset("L2DUpdateHost.png")
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))

    # A duplicate executable invocation is only an IPC wake-up.
    probe_command = "show" if args.show or not args.login else "status"
    response = request_host(probe_command, timeout_ms=350)
    if response and bool(response.get("ok")):
        return 0

    settings = create_host_settings()
    if not _prepare_startup_state(args, settings):
        return 0

    window = UpdateHostWindow(
        public_key_pem=bundled_host_asset("release_public_key.pem").read_bytes(),
        settings=settings,
        standalone=True,
    )

    def handle_command(command: str) -> dict[str, object]:
        if command == "show":
            window.show_from_tray()
            return window.host_status()
        if command == "status":
            return window.host_status()
        if command == "shutdown_for_update":
            QTimer.singleShot(0, window.shutdown_for_update)
            return {"ok": True, "shutdown_started": True}
        return {"ok": False, "error": "UNSUPPORTED_COMMAND"}

    ipc = HostIpcServer(handle_command, parent=app)
    if not ipc.is_primary:
        # Lost a narrow startup race to another process.
        window.deleteLater()
        return 0 if request_host(probe_command) else 2

    first_manual_launch = (
        not args.login
        and not args.restore_after_update
        and not settings.contains(HOST_RESTORE_AT_LOGIN_KEY)
    )
    if not args.login and not args.restore_after_update:
        settings.setValue(HOST_RESTORE_AT_LOGIN_KEY, True)
        if first_manual_launch:
            settings.setValue(HOST_SERVICE_ENABLED_KEY, True)
        settings.sync()
        set_login_startup(True)

    if setting_is_enabled(settings, HOST_SERVICE_ENABLED_KEY, True):
        QTimer.singleShot(0, window.start_server)
    if args.show or (not args.login and not args.restore_after_update):
        window.show()
    app.aboutToQuit.connect(window.stop_server_for_exit)
    app.aboutToQuit.connect(ipc.close)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
