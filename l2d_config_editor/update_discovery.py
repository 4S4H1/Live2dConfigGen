"""Small, bounded UDP protocol for discovering a LAN update host."""

from __future__ import annotations

import ipaddress
import re
import secrets
from collections.abc import Callable, Sequence

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import (
    QAbstractSocket,
    QHostAddress,
    QNetworkInterface,
    QUdpSocket,
)


DISCOVERY_PORT = 48765
MAX_DISCOVERY_DATAGRAM_BYTES = 128
_QUERY_PREFIX = b"L2DUPDATE-DISCOVERY/1 FIND L2DConfigEditor "
_RESPONSE_PREFIX = b"L2DUPDATE-DISCOVERY/1 HERE "
_NONCE_PATTERN = re.compile(r"[0-9a-f]{32}")


def _validated_nonce(nonce: str) -> str:
    if not _NONCE_PATTERN.fullmatch(nonce):
        raise ValueError("发现请求 nonce 必须是 32 位小写十六进制")
    return nonce


def build_discovery_query(nonce: str) -> bytes:
    """Return a fixed-format request whose size bounds any valid response."""

    return _QUERY_PREFIX + _validated_nonce(nonce).encode("ascii")


def parse_discovery_query(payload: bytes) -> str | None:
    """Return the request nonce only for the exact supported protocol."""

    if not isinstance(payload, bytes) or len(payload) > MAX_DISCOVERY_DATAGRAM_BYTES:
        return None
    if not payload.startswith(_QUERY_PREFIX):
        return None
    nonce_bytes = payload[len(_QUERY_PREFIX) :]
    try:
        nonce = nonce_bytes.decode("ascii")
        return _validated_nonce(nonce)
    except (UnicodeDecodeError, ValueError):
        return None


def build_discovery_response(nonce: str, http_port: int) -> bytes:
    """Return a nonce-correlated response containing only the HTTP port."""

    _validated_nonce(nonce)
    if not 1024 <= int(http_port) <= 65535:
        raise ValueError("HTTP 端口必须介于 1024 和 65535")
    payload = (
        _RESPONSE_PREFIX
        + nonce.encode("ascii")
        + b" "
        + str(int(http_port)).encode("ascii")
    )
    if len(payload) > MAX_DISCOVERY_DATAGRAM_BYTES:
        raise ValueError("发现响应过大")
    return payload


def parse_discovery_response(payload: bytes, expected_nonce: str) -> int | None:
    """Parse a valid response for this request, otherwise ignore it."""

    try:
        _validated_nonce(expected_nonce)
    except ValueError:
        return None
    if not isinstance(payload, bytes) or len(payload) > MAX_DISCOVERY_DATAGRAM_BYTES:
        return None
    prefix = _RESPONSE_PREFIX + expected_nonce.encode("ascii") + b" "
    if not payload.startswith(prefix):
        return None
    port_bytes = payload[len(prefix) :]
    if not port_bytes or len(port_bytes) > 5 or not port_bytes.isdigit():
        return None
    port = int(port_bytes)
    return port if 1024 <= port <= 65535 else None


def is_lan_address(value: str) -> bool:
    """Accept only non-global IPv4 sources reachable on a local interface."""

    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(
        address.version == 4
        and not address.is_global
        and not address.is_multicast
        and not address.is_unspecified
    )


def default_discovery_targets() -> tuple[str, ...]:
    """Return limited broadcast, adapter broadcasts, and explicit loopback."""

    targets = ["255.255.255.255"]
    for interface in QNetworkInterface.allInterfaces():
        if not (interface.flags() & QNetworkInterface.InterfaceFlag.IsUp):
            continue
        for entry in interface.addressEntries():
            if (
                entry.ip().protocol()
                != QAbstractSocket.NetworkLayerProtocol.IPv4Protocol
                or entry.broadcast().isNull()
            ):
                continue
            broadcast = entry.broadcast().toString()
            if broadcast and broadcast != "0.0.0.0":
                targets.append(broadcast)
    targets.append("127.0.0.1")
    return tuple(dict.fromkeys(targets))


class UpdateDiscoveryResponder(QObject):
    """Answer correlated LAN discovery queries while the HTTP Host is active."""

    def __init__(
        self,
        *,
        http_port: int,
        release_available: Callable[[], bool],
        discovery_port: int = DISCOVERY_PORT,
        bind_address: str = "0.0.0.0",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.http_port = int(http_port)
        self.release_available = release_available
        self.discovery_port = int(discovery_port)
        self.bind_address = bind_address
        self._socket: QUdpSocket | None = None

    @property
    def local_port(self) -> int:
        return int(self._socket.localPort()) if self._socket is not None else 0

    @property
    def error_string(self) -> str:
        return self._socket.errorString() if self._socket is not None else ""

    def start(self) -> bool:
        self.stop()
        if not 1024 <= self.http_port <= 65535:
            return False
        udp_socket = QUdpSocket(self)
        mode = (
            QAbstractSocket.BindFlag.ShareAddress
            | QAbstractSocket.BindFlag.ReuseAddressHint
        )
        if not udp_socket.bind(
            QHostAddress(self.bind_address),
            self.discovery_port,
            mode,
        ):
            self._socket = udp_socket
            return False
        self._socket = udp_socket
        udp_socket.readyRead.connect(self._read_pending_datagrams)
        return True

    def stop(self) -> None:
        udp_socket, self._socket = self._socket, None
        if udp_socket is None:
            return
        udp_socket.close()
        udp_socket.deleteLater()

    def _read_pending_datagrams(self) -> None:
        udp_socket = self._socket
        if udp_socket is None:
            return
        while udp_socket.hasPendingDatagrams():
            pending_size = int(udp_socket.pendingDatagramSize())
            data, sender, sender_port = udp_socket.readDatagram(
                min(max(pending_size, 0), MAX_DISCOVERY_DATAGRAM_BYTES + 1)
            )
            if pending_size > MAX_DISCOVERY_DATAGRAM_BYTES:
                continue
            sender_text = sender.toString()
            if not is_lan_address(sender_text):
                continue
            nonce = parse_discovery_query(bytes(data))
            if nonce is None:
                continue
            try:
                available = bool(self.release_available())
            except Exception:
                available = False
            if not available:
                continue
            response = build_discovery_response(nonce, self.http_port)
            udp_socket.writeDatagram(response, sender, int(sender_port))


class UpdateHostDiscovery(QObject):
    """Discover all responsive LAN Hosts within a short, fixed deadline."""

    finished = Signal(list)

    def __init__(
        self,
        *,
        discovery_port: int = DISCOVERY_PORT,
        targets: Sequence[str] | None = None,
        timeout_ms: int = 900,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.discovery_port = int(discovery_port)
        configured_targets = (
            default_discovery_targets() if targets is None else targets
        )
        self.targets = tuple(
            dict.fromkeys(str(target) for target in configured_targets)
        )
        self.timeout_ms = max(50, min(int(timeout_ms), 5000))
        self._socket: QUdpSocket | None = None
        self._nonce = ""
        self._urls: list[str] = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._finish)

    def start(self) -> None:
        self.cancel()
        self._nonce = secrets.token_hex(16)
        self._urls = []
        udp_socket = QUdpSocket(self)
        mode = (
            QAbstractSocket.BindFlag.ShareAddress
            | QAbstractSocket.BindFlag.ReuseAddressHint
        )
        if not udp_socket.bind(QHostAddress.SpecialAddress.AnyIPv4, 0, mode):
            udp_socket.deleteLater()
            self.finished.emit([])
            return
        self._socket = udp_socket
        udp_socket.readyRead.connect(self._read_pending_datagrams)
        query = build_discovery_query(self._nonce)
        for target in self.targets:
            udp_socket.writeDatagram(
                query,
                QHostAddress(target),
                self.discovery_port,
            )
        self._timer.start(self.timeout_ms)

    def cancel(self) -> None:
        self._timer.stop()
        udp_socket, self._socket = self._socket, None
        if udp_socket is not None:
            udp_socket.close()
            udp_socket.deleteLater()
        self._nonce = ""
        self._urls = []

    def _read_pending_datagrams(self) -> None:
        udp_socket = self._socket
        if udp_socket is None:
            return
        while udp_socket.hasPendingDatagrams():
            pending_size = int(udp_socket.pendingDatagramSize())
            data, sender, sender_port = udp_socket.readDatagram(
                min(max(pending_size, 0), MAX_DISCOVERY_DATAGRAM_BYTES + 1)
            )
            if (
                pending_size > MAX_DISCOVERY_DATAGRAM_BYTES
                or int(sender_port) != self.discovery_port
            ):
                continue
            address = sender.toString()
            if not is_lan_address(address):
                continue
            http_port = parse_discovery_response(bytes(data), self._nonce)
            if http_port is None:
                continue
            url = f"http://{address}:{http_port}"
            if url not in self._urls:
                self._urls.append(url)

    def _finish(self) -> None:
        urls = list(self._urls)
        self.cancel()
        self.finished.emit(urls)
