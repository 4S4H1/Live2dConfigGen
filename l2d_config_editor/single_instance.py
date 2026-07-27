"""Single-instance coordination using Qt local sockets."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, QStandardPaths, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .version import PRODUCT_ID, PUBLISHER


def default_server_name() -> str:
    user = os.environ.get("USERNAME") or os.environ.get("USER") or "default"
    digest = hashlib.sha256(f"{PUBLISHER}:{PRODUCT_ID}:{user}".encode()).hexdigest()[:16]
    return f"{PRODUCT_ID}-{digest}"


class SingleInstance(QObject):
    """Own a local server or hand arguments to the already-running process."""

    messageReceived = Signal(list)

    def __init__(self, server_name: str | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.server_name = server_name or default_server_name()
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._accept_connections)
        lock_root = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.TempLocation
        )
        lock_digest = hashlib.sha256(self.server_name.encode("utf-8")).hexdigest()
        self._lock = QLockFile(str(Path(lock_root) / f"{lock_digest}.lock"))
        self._lock.setStaleLockTime(0)
        owns_lock = self._lock.tryLock(0)
        self.is_primary = False
        if owns_lock:
            # Only the lock owner may remove a stale local-socket endpoint.
            QLocalServer.removeServer(self.server_name)
            self.is_primary = self.server.listen(self.server_name)
            if not self.is_primary:
                self._lock.unlock()

    def send_to_primary(self, arguments: list[str], timeout_ms: int = 1500) -> bool:
        if self.is_primary:
            return False
        socket = QLocalSocket()
        socket.connectToServer(self.server_name)
        if not socket.waitForConnected(timeout_ms):
            return False
        payload = json.dumps({"args": arguments}, ensure_ascii=False).encode("utf-8")
        if len(payload) > 64 * 1024:
            return False
        socket.write(len(payload).to_bytes(4, "big") + payload)
        if not socket.waitForBytesWritten(timeout_ms):
            return False
        socket.disconnectFromServer()
        return True

    def _accept_connections(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if socket is None:
                continue
            socket.waitForReadyRead(500)
            data = bytes(socket.readAll())
            if len(data) < 4:
                socket.disconnectFromServer()
                continue
            size = int.from_bytes(data[:4], "big")
            if size > 64 * 1024:
                socket.disconnectFromServer()
                continue
            body = data[4:]
            while len(body) < size and socket.waitForReadyRead(250):
                body += bytes(socket.readAll())
            if size != len(body):
                socket.disconnectFromServer()
                continue
            try:
                message = json.loads(body.decode("utf-8"))
                arguments = message["args"]
                if not isinstance(arguments, list) or not all(
                    isinstance(item, str) for item in arguments
                ):
                    raise ValueError
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError, ValueError):
                socket.disconnectFromServer()
                continue
            self.messageReceived.emit(arguments)
            socket.disconnectFromServer()

    @staticmethod
    def normalized_file_arguments(arguments: list[str]) -> list[str]:
        return [
            str(Path(argument).expanduser().resolve())
            for argument in arguments
            if argument and not argument.startswith("-") and Path(argument).is_file()
        ]
