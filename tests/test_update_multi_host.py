from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QTimer, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from l2d_config_editor.update_client import UpdateClient
from l2d_config_editor.update_host import ReleaseHTTPServer
from l2d_config_editor.update_manifest import canonical_manifest_bytes, sign_manifest


class StaticHostDiscovery(QObject):
    finished = Signal(list)

    def __init__(self, urls: list[str]):
        super().__init__()
        self.urls = list(urls)

    def start(self) -> None:
        QTimer.singleShot(0, lambda: self.finished.emit(list(self.urls)))

    def cancel(self) -> None:
        pass


class MultiHostUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.key = Ed25519PrivateKey.generate()
        cls.public_pem = cls.key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def _start_signed_host(
        self,
        root: Path,
        version: str,
    ) -> tuple[ReleaseHTTPServer, threading.Thread, list[str]]:
        releases = root / "releases"
        release = releases / version
        release.mkdir(parents=True)
        payload = f"installer {version}".encode()
        installer_name = f"L2DConfigEditor-Setup-{version}-x64.exe"
        manifest = {
            "schema_version": 1,
            "product": "L2DConfigEditor",
            "channel": "stable",
            "version": version,
            "published_at": "2026-07-27T00:00:00+00:00",
            "minimum_supported_version": "1.0.0",
            "notes": f"release {version}",
            "key_id": "release-1",
            "artifact": {
                "platform": "windows",
                "arch": "x86_64",
                "url": installer_name,
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            },
        }
        raw = canonical_manifest_bytes(manifest)
        (release / "manifest.json").write_bytes(raw)
        (release / "manifest.sig").write_bytes(sign_manifest(raw, self.key))
        (release / installer_name).write_bytes(payload)
        (releases / "latest").write_text(version, encoding="utf-8")
        logs: list[str] = []
        server = ReleaseHTTPServer(("127.0.0.1", 0), releases, logs.append)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread, logs

    def _dispose_client(self, client: UpdateClient) -> None:
        client.cancel_check()
        client.network.clearConnectionCache()
        client.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def test_auto_check_skips_current_host_and_finds_newer_host(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current, current_thread, _current_logs = self._start_signed_host(
                root / "current",
                "1.0.0",
            )
            newer, newer_thread, newer_logs = self._start_signed_host(
                root / "newer",
                "1.1.0",
            )
            client = UpdateClient(
                self.public_pem,
                current_version="1.0.0",
                cache_root=root / "cache",
                discovery=StaticHostDiscovery(
                    [
                        f"http://127.0.0.1:{current.server_address[1]}",
                        f"http://127.0.0.1:{newer.server_address[1]}",
                    ]
                ),
            )
            available = []
            no_update = []
            failures = []
            client.updateAvailable.connect(
                lambda manifest, url: available.append((manifest, url))
            )
            client.noUpdate.connect(lambda: no_update.append(True))
            client.checkFailed.connect(failures.append)
            try:
                client.check_automatically()
                for _ in range(200):
                    self.app.processEvents()
                    if available or no_update or failures:
                        break
                    QTest.qWait(10)
                self.assertFalse(failures)
                self.assertFalse(no_update)
                self.assertEqual("1.1.0", available[0][0]["version"])
                self.assertTrue(newer_logs)
            finally:
                self._dispose_client(client)
                for server, thread in (
                    (current, current_thread),
                    (newer, newer_thread),
                ):
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=2)

    def test_auto_check_emits_no_update_only_after_all_valid_hosts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, first_thread, _first_logs = self._start_signed_host(
                root / "first",
                "1.0.0",
            )
            second, second_thread, second_logs = self._start_signed_host(
                root / "second",
                "1.0.0",
            )
            client = UpdateClient(
                self.public_pem,
                current_version="1.0.0",
                cache_root=root / "cache",
                discovery=StaticHostDiscovery(
                    [
                        f"http://127.0.0.1:{first.server_address[1]}",
                        f"http://127.0.0.1:{second.server_address[1]}",
                    ]
                ),
            )
            available = []
            no_update = []
            failures = []
            client.updateAvailable.connect(
                lambda manifest, url: available.append((manifest, url))
            )
            client.noUpdate.connect(lambda: no_update.append(True))
            client.checkFailed.connect(failures.append)
            try:
                client.check_automatically()
                for _ in range(200):
                    self.app.processEvents()
                    if no_update or failures:
                        break
                    QTest.qWait(10)
                self.assertFalse(available)
                self.assertFalse(failures)
                self.assertEqual([True], no_update)
                self.assertTrue(second_logs)
                cached = []
                cache_failures = []
                client.cacheFinished.connect(cached.append)
                client.cacheFailed.connect(cache_failures.append)
                self.assertTrue(client.cache_current_release())
                for _ in range(200):
                    self.app.processEvents()
                    if cached or cache_failures:
                        break
                    QTest.qWait(10)
                self.assertFalse(cache_failures)
                self.assertTrue(cached)
            finally:
                self._dispose_client(client)
                for server, thread in (
                    (first, first_thread),
                    (second, second_thread),
                ):
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
