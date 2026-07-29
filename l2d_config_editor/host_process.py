"""Process and local-IPC helpers for the standalone update Host.

The editor deliberately talks only to this narrow command surface.  It never
owns the Host window or its HTTP/UDP threads, so closing the editor cannot stop
the publishing service.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, QProcess, QStandardPaths, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .version import PUBLISHER

HOST_APPLICATION_ID = "L2DUpdateHost"
HOST_EXECUTABLE_NAME = "L2DUpdateHost.exe"
MAX_IPC_BYTES = 16 * 1024
HOST_LOCK_STALE_TIMEOUT_MS = 30_000


def host_server_name() -> str:
    """Return a per-user, product-stable Qt local-server name."""

    user = os.environ.get("USERNAME") or os.environ.get("USER") or "default"
    digest = hashlib.sha256(
        f"{PUBLISHER}:{HOST_APPLICATION_ID}:{user}".encode("utf-8")
    ).hexdigest()[:16]
    return f"{HOST_APPLICATION_ID}-{digest}"


def default_host_install_root() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "Programs" / HOST_APPLICATION_ID
    return Path.home() / "AppData" / "Local" / "Programs" / HOST_APPLICATION_ID


def default_host_executable() -> Path:
    overridden = str(os.environ.get("L2D_UPDATE_HOST_EXE") or "").strip()
    if overridden:
        return Path(overridden).expanduser().resolve()
    return default_host_install_root() / HOST_EXECUTABLE_NAME


def _encode_message(message: dict[str, object]) -> bytes:
    body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    if len(body) > MAX_IPC_BYTES:
        raise ValueError("Host IPC message is too large")
    return len(body).to_bytes(4, "big") + body


def _read_message(socket: QLocalSocket, timeout_ms: int) -> dict[str, object] | None:
    if socket.bytesAvailable() < 4 and not socket.waitForReadyRead(timeout_ms):
        return None
    data = bytes(socket.readAll())
    deadline = time.monotonic() + max(timeout_ms, 1) / 1000.0
    while len(data) < 4:
        remaining = max(1, int((deadline - time.monotonic()) * 1000))
        if remaining <= 1 or not socket.waitForReadyRead(remaining):
            return None
        data += bytes(socket.readAll())
    size = int.from_bytes(data[:4], "big")
    if size < 0 or size > MAX_IPC_BYTES:
        return None
    body = data[4:]
    while len(body) < size:
        remaining = max(1, int((deadline - time.monotonic()) * 1000))
        if remaining <= 1 or not socket.waitForReadyRead(remaining):
            return None
        body += bytes(socket.readAll())
    if len(body) != size:
        return None
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return decoded if isinstance(decoded, dict) else None


def request_host(
    command: str,
    *,
    timeout_ms: int = 1500,
    server_name: str | None = None,
) -> dict[str, object] | None:
    """Send one allow-listed command to the running Host."""

    if command not in {"show", "status", "shutdown_for_update"}:
        raise ValueError(f"unsupported Host command: {command}")
    socket = QLocalSocket()
    socket.connectToServer(server_name or host_server_name())
    if not socket.waitForConnected(timeout_ms):
        return None
    payload = _encode_message({"command": command})
    if socket.write(payload) != len(payload):
        socket.abort()
        return None
    socket.flush()
    response = _read_message(socket, timeout_ms)
    socket.disconnectFromServer()
    return response


class HostIpcServer(QObject):
    """Single-instance owner and request/reply server for the Host process."""

    commandReceived = Signal(str)

    def __init__(
        self,
        handler: Callable[[str], dict[str, object]],
        *,
        server_name: str | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.handler = handler
        self.server_name = server_name or host_server_name()
        self.server = QLocalServer(self)
        if hasattr(QLocalServer, "SocketOption"):
            try:
                self.server.setSocketOptions(
                    QLocalServer.SocketOption.UserAccessOption
                )
            except (AttributeError, TypeError):
                pass
        self.server.newConnection.connect(self._accept_connections)
        self._buffers: dict[QLocalSocket, bytearray] = {}
        lock_root = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.TempLocation
        )
        lock_digest = hashlib.sha256(self.server_name.encode("utf-8")).hexdigest()
        self._lock = QLockFile(str(Path(lock_root) / f"{lock_digest}.lock"))
        # A crashed process must not permanently strand the per-user Host.
        # QLockFile also checks whether the recorded PID is still alive before
        # declaring a lock stale, so a slow but valid startup remains protected.
        self._lock.setStaleLockTime(HOST_LOCK_STALE_TIMEOUT_MS)
        self.is_primary = False
        acquired = self._lock.tryLock(0)
        if not acquired:
            # update_host_main has already probed the IPC endpoint before
            # constructing this server.  At this point a non-responsive lock
            # can only be recovered when QLockFile verifies that it is stale.
            if self._lock.removeStaleLockFile():
                acquired = self._lock.tryLock(0)
        if acquired:
            QLocalServer.removeServer(self.server_name)
            self.is_primary = self.server.listen(self.server_name)
            if not self.is_primary:
                self._lock.unlock()

    def close(self) -> None:
        if self.server.isListening():
            self.server.close()
            QLocalServer.removeServer(self.server_name)
        if self._lock.isLocked():
            self._lock.unlock()

    def _accept_connections(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if socket is None:
                continue
            self._buffers[socket] = bytearray()
            socket.readyRead.connect(
                lambda socket=socket: self._socket_ready(socket)
            )
            socket.disconnected.connect(
                lambda socket=socket: self._discard_socket(socket)
            )
            if socket.bytesAvailable():
                self._socket_ready(socket)

    def _discard_socket(self, socket: QLocalSocket) -> None:
        self._buffers.pop(socket, None)
        try:
            socket.deleteLater()
        except RuntimeError:
            pass

    def _socket_ready(self, socket: QLocalSocket) -> None:
        buffer = self._buffers.get(socket)
        if buffer is None:
            return
        buffer.extend(bytes(socket.readAll()))
        if len(buffer) < 4:
            return
        size = int.from_bytes(buffer[:4], "big")
        if size < 0 or size > MAX_IPC_BYTES:
            socket.abort()
            return
        if len(buffer) < 4 + size:
            return
        if len(buffer) != 4 + size:
            socket.abort()
            return
        try:
            request = json.loads(bytes(buffer[4:]).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            request = None
        command = (
            str(request.get("command") or "")
            if isinstance(request, dict)
            else ""
        )
        if command not in {"show", "status", "shutdown_for_update"}:
            response: dict[str, object] = {
                "ok": False,
                "error": "UNSUPPORTED_COMMAND",
            }
        else:
            try:
                response = dict(self.handler(command))
                response.setdefault("ok", True)
                self.commandReceived.emit(command)
            except Exception:
                response = {"ok": False, "error": "HOST_COMMAND_FAILED"}
        self._buffers.pop(socket, None)
        socket.write(_encode_message(response))
        socket.flush()
        socket.disconnectFromServer()


@dataclass(frozen=True)
class HostUpdatePreparation:
    was_running: bool
    service_was_running: bool
    stopped: bool
    restart_executable: str | None
    pid: int | None = None
    restore_at_login: bool = False
    login_startup_enabled: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class HostProcessManager:
    """Editor-side facade for detached Host lifecycle operations."""

    def __init__(
        self,
        *,
        executable: str | Path | None = None,
        server_name: str | None = None,
    ) -> None:
        self.executable = (
            Path(executable).expanduser().resolve()
            if executable is not None
            else default_host_executable()
        )
        self.server_name = server_name or host_server_name()

    def status(self, timeout_ms: int = 750) -> dict[str, object]:
        response = request_host(
            "status", timeout_ms=timeout_ms, server_name=self.server_name
        )
        if response is None:
            return {
                "ok": True,
                "process_running": False,
                "service_running": False,
            }
        return dict(response)

    def show_or_start(self) -> bool:
        response = request_host(
            "show", timeout_ms=500, server_name=self.server_name
        )
        if response and bool(response.get("ok")):
            return True
        program, arguments, working_directory = self._launch_command()
        result = QProcess.startDetached(program, arguments, working_directory)
        return bool(result[0] if isinstance(result, tuple) else result)

    def shutdown_for_update(
        self,
        timeout_ms: int = 5000,
        *,
        _reported_status: dict[str, object] | None = None,
    ) -> bool:
        started = time.monotonic()
        status = (
            dict(_reported_status)
            if _reported_status is not None
            else self.status()
        )
        if not bool(status.get("process_running")):
            return True
        try:
            pid = int(status.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        # An IPC endpoint disappearing is not proof that Windows has released
        # the executable/directory handles.  Refuse an unsafe upgrade when the
        # running Host cannot identify the exact process to wait for.
        if pid <= 0:
            return False
        response = request_host(
            "shutdown_for_update",
            timeout_ms=min(timeout_ms, 1500),
            server_name=self.server_name,
        )
        if not response or not bool(response.get("ok")):
            return False
        elapsed_ms = int((time.monotonic() - started) * 1000)
        remaining_ms = max(1, int(timeout_ms) - elapsed_ms)
        return self._wait_for_process_exit(pid, remaining_ms)

    def prepare_for_update(self, timeout_ms: int = 5000) -> HostUpdatePreparation:
        status = self.status()
        was_running = bool(status.get("process_running"))
        service_was_running = bool(status.get("service_running"))
        try:
            pid = int(status.get("pid") or 0) if was_running else 0
        except (TypeError, ValueError):
            pid = 0
        stopped = (
            self.shutdown_for_update(
                timeout_ms,
                _reported_status=status,
            )
            if was_running
            else True
        )
        restart = str(self.executable) if was_running and stopped else None
        return HostUpdatePreparation(
            was_running=was_running,
            service_was_running=service_was_running,
            stopped=stopped,
            restart_executable=restart,
            pid=pid or None,
            restore_at_login=bool(status.get("restore_at_login")),
            login_startup_enabled=bool(status.get("login_startup_enabled")),
        )

    @staticmethod
    def _wait_for_process_exit(pid: int, timeout_ms: int) -> bool:
        """Wait for one exact PID, not merely for its IPC endpoint to vanish."""

        if pid <= 0:
            return False
        bounded_timeout = max(1, int(timeout_ms))
        if sys.platform == "win32":
            from ctypes import WinDLL, get_last_error, wintypes

            synchronize = 0x00100000
            process_query_limited_information = 0x1000
            wait_object_0 = 0x00000000
            error_invalid_parameter = 87
            kernel32 = WinDLL("kernel32", use_last_error=True)
            open_process = kernel32.OpenProcess
            open_process.argtypes = [
                wintypes.DWORD,
                wintypes.BOOL,
                wintypes.DWORD,
            ]
            open_process.restype = wintypes.HANDLE
            wait_for_single_object = kernel32.WaitForSingleObject
            wait_for_single_object.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            wait_for_single_object.restype = wintypes.DWORD
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = [wintypes.HANDLE]
            close_handle.restype = wintypes.BOOL
            handle = open_process(
                synchronize | process_query_limited_information,
                False,
                pid,
            )
            if not handle:
                return get_last_error() == error_invalid_parameter
            try:
                return wait_for_single_object(handle, bounded_timeout) == wait_object_0
            finally:
                close_handle(handle)

        deadline = time.monotonic() + bounded_timeout / 1000.0
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            except PermissionError:
                return False
            time.sleep(0.02)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        return False

    def _launch_command(self) -> tuple[str, list[str], str]:
        if self.executable.is_file():
            return str(self.executable), ["--show"], str(self.executable.parent)
        if not getattr(sys, "frozen", False):
            project_root = Path(__file__).resolve().parents[1]
            return (
                sys.executable,
                ["-m", "l2d_config_editor.update_host_main", "--show"],
                str(project_root),
            )
        return str(self.executable), ["--show"], str(self.executable.parent)
