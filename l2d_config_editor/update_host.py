"""LAN update host and tray application."""

from __future__ import annotations

import html
import os
import socket
import sys
import threading
import base64
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from collections.abc import Callable
from typing import BinaryIO
from urllib.parse import unquote, urlsplit

from PySide6.QtCore import QProcess, QSettings, Qt, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .update_manifest import import_release_bundle, load_public_key, sha256_file
from .version import PRODUCT_NAME, PUBLISHER

DEFAULT_PORT = 8765
SETTINGS_ORGANIZATION = PUBLISHER
SETTINGS_APPLICATION = "L2DUpdateHost"


def bundled_asset(name: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return root / "assets" / name


def default_data_root() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "4S4H1" / "L2DUpdateHost"
    return Path.home() / ".l2d-update-host"


def local_ipv4_addresses() -> list[str]:
    addresses: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if not address.startswith("127."):
                addresses.add(address)
    except OSError:
        pass
    return sorted(addresses)


def _powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def firewall_powershell_command(port: int, program: str) -> str:
    """Build the exact elevated firewall operation used by the Host UI."""

    if not 1024 <= port <= 65535:
        raise ValueError("端口必须介于 1024 和 65535")
    if not program:
        raise ValueError("缺少 Host 程序路径")
    rule_name = "L2D Update Host (LocalSubnet)"
    name = _powershell_quote(rule_name)
    executable = _powershell_quote(str(Path(program).resolve()))
    return (
        "$ErrorActionPreference='Stop';"
        f"$existing=Get-NetFirewallRule -DisplayName {name} "
        "-ErrorAction SilentlyContinue;"
        "if($null -ne $existing){$existing|Remove-NetFirewallRule};"
        f"New-NetFirewallRule -DisplayName {name} -Direction Inbound "
        f"-Action Allow -Protocol TCP -LocalPort {port} -Program {executable} "
        "-RemoteAddress LocalSubnet -Profile Private,Domain|Out-Null"
    )


def _read_latest(releases_root: Path) -> Path | None:
    try:
        version = (releases_root / "latest").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not version or "/" in version or "\\" in version or version in {".", ".."}:
        return None
    candidate = (releases_root / version).resolve()
    try:
        candidate.relative_to(releases_root.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_dir() else None


class ReleaseRequestHandler(BaseHTTPRequestHandler):
    """Strict read-only handler exposing only the latest stable release."""

    server_version = "L2DUpdateHost/1.0"
    protocol_version = "HTTP/1.1"

    @property
    def release_server(self) -> "ReleaseHTTPServer":
        return self.server  # type: ignore[return-value]

    def log_message(self, fmt: str, *args: object) -> None:
        self.release_server.emit_log(f"{self.client_address[0]} - {fmt % args}")

    def do_GET(self) -> None:
        self._serve(send_body=True)

    def do_HEAD(self) -> None:
        self._serve(send_body=False)

    def do_POST(self) -> None:
        self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)

    do_PUT = do_POST
    do_DELETE = do_POST
    do_PATCH = do_POST

    def _serve(self, *, send_body: bool) -> None:
        parsed = urlsplit(self.path)
        if parsed.query or parsed.fragment:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            path = unquote(parsed.path, errors="strict")
        except (UnicodeDecodeError, ValueError):
            self.send_error(HTTPStatus.BAD_REQUEST)
            return
        if "\\" in path or "\x00" in path or ".." in path.split("/"):
            self.send_error(HTTPStatus.BAD_REQUEST)
            return
        latest = _read_latest(self.release_server.releases_root)
        if path == "/":
            self._serve_index(latest, send_body=send_body)
            return
        prefix = "/stable/"
        name = path[len(prefix) :] if path.startswith(prefix) else ""
        if latest is None or not name or "/" in name:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        allowed = {"manifest.json", "manifest.sig"}
        try:
            import json

            manifest = json.loads((latest / "manifest.json").read_text(encoding="utf-8"))
            artifact_name = str(manifest["artifact"]["url"])
            allowed.add(artifact_name)
        except (OSError, KeyError, TypeError, ValueError):
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE)
            return
        if name not in allowed:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        file_path = latest / name
        if not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self._serve_file(file_path, send_body=send_body)

    def _serve_index(self, latest: Path | None, *, send_body: bool) -> None:
        version = latest.name if latest is not None else "尚未发布"
        download = ""
        if latest is not None:
            try:
                import json

                manifest = json.loads(
                    (latest / "manifest.json").read_text(encoding="utf-8")
                )
                artifact = str(manifest["artifact"]["url"])
                if (
                    artifact
                    and "/" not in artifact
                    and "\\" not in artifact
                    and (latest / artifact).is_file()
                ):
                    download = (
                        "<p><a href='/stable/"
                        + html.escape(artifact, quote=True)
                        + "'>下载当前安装程序</a></p>"
                    )
            except (OSError, KeyError, TypeError, ValueError):
                pass
        body = (
            "<!doctype html><meta charset='utf-8'><title>L2D Update Host</title>"
            "<style>body{font:16px system-ui;max-width:720px;margin:4rem auto;"
            "padding:0 1rem;color:#172033}code{background:#eef2f8;padding:.2rem .4rem}</style>"
            f"<h1>{html.escape(PRODUCT_NAME)} 更新主机</h1>"
            f"<p>当前稳定版本：<strong>{html.escape(version)}</strong></p>"
            f"{download}"
            "<p>编辑器可使用本站地址检查更新。清单路径："
            "<code>/stable/manifest.json</code></p>"
        ).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if send_body:
            self.wfile.write(body)

    @staticmethod
    def _parse_range(value: str, size: int) -> tuple[int, int] | None:
        if not value.startswith("bytes=") or "," in value:
            return None
        spec = value[6:].strip()
        if "-" not in spec:
            return None
        first, last = spec.split("-", 1)
        try:
            if first:
                start = int(first)
                end = int(last) if last else size - 1
            elif last:
                suffix = int(last)
                if suffix <= 0:
                    return None
                start = max(0, size - suffix)
                end = size - 1
            else:
                return None
        except ValueError:
            return None
        if start < 0 or start >= size or end < start:
            return None
        return start, min(end, size - 1)

    def _serve_file(self, path: Path, *, send_body: bool) -> None:
        size = path.stat().st_size
        etag = self.release_server.etag_for(path)
        if self.headers.get("If-None-Match") == etag:
            self.send_response(HTTPStatus.NOT_MODIFIED)
            self.send_header("ETag", etag)
            self.end_headers()
            return

        range_header = self.headers.get("Range")
        byte_range = self._parse_range(range_header, size) if range_header else None
        if range_header and byte_range is None:
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        start, end = byte_range or (0, size - 1)
        length = max(0, end - start + 1)
        content_type = (
            "application/json"
            if path.name == "manifest.json"
            else "application/octet-stream"
        )
        self.send_response(
            HTTPStatus.PARTIAL_CONTENT if byte_range else HTTPStatus.OK
        )
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("ETag", etag)
        self.send_header("X-Content-Type-Options", "nosniff")
        if byte_range:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if not send_body or length == 0:
            return
        with path.open("rb") as stream:
            stream.seek(start)
            self._copy_exact(stream, self.wfile, length)

    @staticmethod
    def _copy_exact(source: BinaryIO, target: BinaryIO, remaining: int) -> None:
        while remaining:
            chunk = source.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            target.write(chunk)
            remaining -= len(chunk)


class ReleaseHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 32
    max_concurrent_requests = 16

    def __init__(
        self,
        address: tuple[str, int],
        releases_root: Path,
        log_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.releases_root = releases_root.resolve()
        self._log_callback = log_callback
        self._request_slots = threading.BoundedSemaphore(
            self.max_concurrent_requests
        )
        self._etag_cache: dict[tuple[str, int, int], str] = {}
        self._etag_lock = threading.Lock()
        super().__init__(address, ReleaseRequestHandler)

    def emit_log(self, message: str) -> None:
        if self._log_callback:
            self._log_callback(message)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(30.0)
        return request, address

    def process_request(self, request, client_address) -> None:
        if not self._request_slots.acquire(blocking=False):
            try:
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\n"
                    b"Connection: close\r\nContent-Length: 0\r\n\r\n"
                )
            finally:
                self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()

    def etag_for(self, path: Path) -> str:
        """Use the signed artifact digest and cache hashes for small metadata."""

        try:
            import json

            manifest = json.loads(
                (path.parent / "manifest.json").read_text(encoding="utf-8")
            )
            artifact = manifest["artifact"]
            digest = str(artifact["sha256"]).lower()
            if (
                path.name == str(artifact["url"])
                and path.stat().st_size == int(artifact["size"])
                and len(digest) == 64
                and all(character in "0123456789abcdef" for character in digest)
            ):
                return f'"{digest}"'
        except (OSError, KeyError, TypeError, ValueError):
            pass
        stat = path.stat()
        key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
        with self._etag_lock:
            cached = self._etag_cache.get(key)
        if cached is not None:
            return cached
        result = f'"{sha256_file(path)}"'
        with self._etag_lock:
            self._etag_cache = {
                existing: value
                for existing, value in self._etag_cache.items()
                if existing[0] != key[0]
            }
            self._etag_cache[key] = result
        return result


class UpdateHostWindow(QMainWindow):
    logReceived = Signal(str)

    def __init__(self, data_root: Path | None = None) -> None:
        super().__init__()
        self.data_root = (data_root or default_data_root()).resolve()
        self.releases_root = self.data_root / "releases"
        self.settings = QSettings(SETTINGS_ORGANIZATION, SETTINGS_APPLICATION)
        self.server: ReleaseHTTPServer | None = None
        self.server_thread: threading.Thread | None = None
        self.firewall_process: QProcess | None = None
        self.first_url = ""
        self.setWindowTitle("L2D 局域网更新主机")
        self.resize(640, 420)
        self._build_ui()
        self.logReceived.connect(self.log_list.addItem)
        self._build_tray()

    def _build_ui(self) -> None:
        central = QWidget(self)
        layout = QVBoxLayout(central)
        form = QFormLayout()
        self.port_box = QSpinBox()
        self.port_box.setRange(1024, 65535)
        self.port_box.setValue(int(self.settings.value("server/port", DEFAULT_PORT)))
        self.url_label = QLabel("未启动")
        self.url_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        latest = _read_latest(self.releases_root)
        self.version_label = QLabel(latest.name if latest is not None else "尚未发布")
        form.addRow("端口", self.port_box)
        form.addRow("当前版本", self.version_label)
        form.addRow("局域网地址", self.url_label)
        layout.addLayout(form)
        row = QHBoxLayout()
        self.start_button = QPushButton("启动服务")
        self.start_button.clicked.connect(self.toggle_server)
        import_button = QPushButton("导入签名更新包…")
        import_button.clicked.connect(self.import_bundle)
        self.copy_button = QPushButton("复制地址")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self.copy_address)
        self.firewall_button = QPushButton("创建局域网防火墙规则…")
        self.firewall_button.setVisible(
            sys.platform == "win32" and bool(getattr(sys, "frozen", False))
        )
        self.firewall_button.clicked.connect(self.create_firewall_rule)
        row.addWidget(self.start_button)
        row.addWidget(import_button)
        row.addWidget(self.copy_button)
        row.addWidget(self.firewall_button)
        layout.addLayout(row)
        layout.addWidget(QLabel("访问日志"))
        self.log_list = QListWidget()
        layout.addWidget(self.log_list)
        self.setCentralWidget(central)

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(QApplication.windowIcon())
        self.tray.setToolTip("L2D 局域网更新主机")
        menu = self.tray.contextMenu()
        if menu is None:
            from PySide6.QtWidgets import QMenu

            menu = QMenu()
            self.tray.setContextMenu(menu)
        show_action = QAction("显示", self)
        show_action.triggered.connect(self.showNormal)
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(QApplication.instance().quit)
        menu.addAction(show_action)
        menu.addAction(quit_action)
        self.tray.activated.connect(
            lambda reason: self.showNormal()
            if reason == QSystemTrayIcon.ActivationReason.DoubleClick
            else None
        )
        self.tray.show()

    def toggle_server(self) -> None:
        if self.server is None:
            self.start_server()
        else:
            self.stop_server()

    def start_server(self) -> None:
        self.releases_root.mkdir(parents=True, exist_ok=True)
        port = self.port_box.value()
        try:
            server = ReleaseHTTPServer(
                ("0.0.0.0", port),
                self.releases_root,
                self.logReceived.emit,
            )
        except OSError as exc:
            QMessageBox.critical(self, "无法启动", str(exc))
            return
        self.server = server
        self.server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        self.server_thread.start()
        self.settings.setValue("server/port", port)
        urls = [f"http://{ip}:{port}" for ip in local_ipv4_addresses()]
        self.first_url = urls[0] if urls else f"http://127.0.0.1:{port}"
        self.url_label.setText("\n".join(urls) or self.first_url)
        self.copy_button.setEnabled(True)
        self.start_button.setText("停止服务")
        self.port_box.setEnabled(False)
        self.logReceived.emit(f"服务已启动，端口 {port}")

    def stop_server(self) -> None:
        server, self.server = self.server, None
        if server is not None:
            server.shutdown()
            server.server_close()
        self.server_thread = None
        self.url_label.setText("未启动")
        self.first_url = ""
        self.copy_button.setEnabled(False)
        self.start_button.setText("启动服务")
        self.port_box.setEnabled(True)
        self.logReceived.emit("服务已停止")

    def import_bundle(self) -> None:
        bundle, _ = QFileDialog.getOpenFileName(
            self,
            "导入 L2D 更新包",
            "",
            "L2D 更新包 (*.l2dupdate)",
        )
        if not bundle:
            return
        key_path = self.data_root / "release_public_key.pem"
        if not key_path.is_file():
            QMessageBox.critical(
                self,
                "缺少发布公钥",
                f"请将 release_public_key.pem 放到：\n{key_path}",
            )
            return
        try:
            key = load_public_key(key_path.read_bytes())
            version = import_release_bundle(
                Path(bundle), self.releases_root, key, retain=2
            )
        except Exception as exc:
            QMessageBox.critical(self, "导入失败", str(exc))
            return
        self.logReceived.emit(f"已发布版本 {version}")
        self.version_label.setText(version)
        QMessageBox.information(self, "发布成功", f"稳定版本已更新为 {version}")

    def copy_address(self) -> None:
        if not self.first_url:
            return
        QApplication.clipboard().setText(self.first_url)
        self.logReceived.emit(f"已复制地址：{self.first_url}")

    def create_firewall_rule(self) -> None:
        if sys.platform != "win32" or not getattr(sys, "frozen", False):
            QMessageBox.information(self, "不可用", "防火墙入口只在安装后的 Windows Host 中启用。")
            return
        if self.firewall_process is not None:
            return
        command = firewall_powershell_command(self.port_box.value(), sys.executable)
        encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
        outer = (
            "$p=Start-Process -FilePath 'powershell.exe' "
            "-ArgumentList @('-NoProfile','-NonInteractive','-EncodedCommand',"
            f"'{encoded}') -Verb RunAs -WindowStyle Hidden -Wait -PassThru;"
            "exit $p.ExitCode"
        )
        process = QProcess(self)
        process.finished.connect(self._firewall_finished)
        process.errorOccurred.connect(self._firewall_start_error)
        self.firewall_process = process
        self.firewall_button.setEnabled(False)
        self.logReceived.emit("正在等待管理员确认防火墙规则…")
        process.start(
            "powershell.exe",
            ["-NoProfile", "-NonInteractive", "-Command", outer],
        )

    def _firewall_finished(
        self, exit_code: int, _status: QProcess.ExitStatus
    ) -> None:
        process, self.firewall_process = self.firewall_process, None
        if process is not None:
            process.deleteLater()
        self.firewall_button.setEnabled(True)
        if exit_code == 0:
            message = (
                f"已创建规则：TCP {self.port_box.value()}，"
                "Private/Domain，LocalSubnet，仅限本 Host 程序。"
            )
            self.logReceived.emit(message)
            QMessageBox.information(self, "防火墙规则已创建", message)
        else:
            self.logReceived.emit("防火墙规则未创建或管理员确认被取消")
            QMessageBox.warning(
                self,
                "未创建规则",
                "操作失败或管理员确认被取消。未扩大任何网络访问范围。",
            )

    def _firewall_start_error(self, _error: QProcess.ProcessError) -> None:
        if self.firewall_process is None:
            return
        try:
            self.firewall_process.finished.disconnect(self._firewall_finished)
        except (RuntimeError, TypeError):
            pass
        self.firewall_process.deleteLater()
        self.firewall_process = None
        self.firewall_button.setEnabled(True)
        self.logReceived.emit("无法启动防火墙配置程序")
        QMessageBox.warning(self, "无法启动", "无法启动 PowerShell 防火墙配置程序。")

    def closeEvent(self, event: object) -> None:
        if self.tray.isVisible():
            self.hide()
            event.ignore()  # type: ignore[attr-defined]
            self.tray.showMessage("L2D 更新主机", "程序仍在托盘中运行")
        else:
            self.stop_server()
            event.accept()  # type: ignore[attr-defined]


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("L2D Update Host")
    app.setOrganizationName(PUBLISHER)
    icon_path = bundled_asset("L2DUpdateHost.png")
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    app.setQuitOnLastWindowClosed(False)
    window = UpdateHostWindow()
    window.show()
    window.start_server()
    app.aboutToQuit.connect(window.stop_server)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
