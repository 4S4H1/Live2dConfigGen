from __future__ import annotations

import os
import hashlib
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtNetwork import QLocalServer

from l2d_config_editor.single_instance import SingleInstance, default_server_name
from l2d_config_editor.update_client import UpdateClient
from l2d_config_editor.update_host import ReleaseHTTPServer
from l2d_config_editor.update_installer import installer_handoff_command
from l2d_config_editor.update_manifest import (
    UpdateValidationError,
    canonical_manifest_bytes,
    sign_manifest,
)


class UpdateClientPrimitiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.key = Ed25519PrivateKey.generate()
        cls.public_pem = cls.key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def test_normalizes_http_host(self):
        self.assertEqual(
            UpdateClient.normalize_base_url("http://host.local:8765"),
            "http://host.local:8765/",
        )
        for unsafe in (
            "file:///tmp",
            "http://user:pass@host/",
            "http://host/?query=1",
            "not a url",
        ):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(UpdateValidationError):
                    UpdateClient.normalize_base_url(unsafe)

    def test_cache_root_is_explicit_and_version_is_configurable(self):
        with tempfile.TemporaryDirectory() as directory:
            client = UpdateClient(
                self.public_pem,
                current_version="9.8.7",
                cache_root=Path(directory),
            )
            self.assertEqual(client.current_version, "9.8.7")
            self.assertEqual(client.cache_root, Path(directory).resolve())

    def test_cached_installer_requires_original_signature_and_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            version_dir = root / "1.0.1"
            version_dir.mkdir()
            installer = version_dir / "L2DConfigEditor-Setup-1.0.1-x64.exe"
            installer.write_bytes(b"signed installer bytes")
            manifest = {
                "schema_version": 1,
                "product": "L2DConfigEditor",
                "channel": "stable",
                "version": "1.0.1",
                "published_at": "2026-07-27T00:00:00+00:00",
                "minimum_supported_version": "1.0.0",
                "notes": "test",
                "key_id": "release-1",
                "artifact": {
                    "platform": "windows",
                    "arch": "x86_64",
                    "url": installer.name,
                    "size": installer.stat().st_size,
                    "sha256": hashlib.sha256(installer.read_bytes()).hexdigest(),
                },
            }
            raw = canonical_manifest_bytes(manifest)
            (version_dir / "manifest.json").write_bytes(raw)
            (version_dir / "manifest.sig").write_bytes(sign_manifest(raw, self.key))
            client = UpdateClient(
                self.public_pem,
                current_version="1.0.0",
                cache_root=root,
            )
            self.assertEqual(
                [("1.0.1", installer.resolve())],
                client.verified_cached_installers(),
            )
            self.assertTrue(client.verify_cached_installer(installer))
            installer.write_bytes(b"tampered")
            self.assertEqual([], client.verified_cached_installers())

    def test_installer_handoff_quotes_paths_and_waits_for_current_process(self):
        with tempfile.TemporaryDirectory() as directory:
            installer = Path(directory) / "setup's copy.exe"
            installer.write_bytes(b"MZ")
            command = installer_handoff_command(
                installer,
                current_pid=1234,
                restart_executable=Path(directory) / "Editor.exe",
            )
            self.assertIn("Wait-Process -Id 1234", command)
            self.assertIn("setup''s copy.exe", command)
            self.assertIn("Start-Process -FilePath $installer", command)

    def test_async_signed_check_and_range_resume_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            releases = root / "releases"
            release = releases / "1.0.1"
            release.mkdir(parents=True)
            payload = b"MZ" + bytes(range(256)) * 16
            installer_name = "L2DConfigEditor-Setup-1.0.1-x64.exe"
            manifest = {
                "schema_version": 1,
                "product": "L2DConfigEditor",
                "channel": "stable",
                "version": "1.0.1",
                "published_at": "2026-07-27T00:00:00+00:00",
                "minimum_supported_version": "1.0.0",
                "notes": "integration",
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
            (releases / "latest").write_text("1.0.1", encoding="utf-8")
            server = ReleaseHTTPServer(("127.0.0.1", 0), releases)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            cache = root / "cache"
            part = cache / "1.0.1" / f"{installer_name}.part"
            part.parent.mkdir(parents=True)
            part.write_bytes(payload[:777])
            client = UpdateClient(
                self.public_pem,
                current_version="1.0.0",
                cache_root=cache,
            )
            available = []
            finished = []
            failures = []
            client.updateAvailable.connect(lambda data, url: available.append((data, url)))
            client.downloadFinished.connect(finished.append)
            client.checkFailed.connect(failures.append)
            client.downloadFailed.connect(failures.append)
            app = QApplication.instance()
            try:
                client.check(f"http://127.0.0.1:{server.server_address[1]}")
                for _ in range(120):
                    app.processEvents()
                    if available or failures:
                        break
                    QTest.qWait(10)
                self.assertFalse(failures)
                self.assertEqual("1.0.1", available[0][0]["version"])
                client.download_available()
                for _ in range(200):
                    app.processEvents()
                    if finished or failures:
                        break
                    QTest.qWait(10)
                self.assertFalse(failures)
                installed = Path(finished[0])
                self.assertEqual(payload, installed.read_bytes())
                self.assertTrue(client.verify_cached_installer(installed))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_equal_signed_release_is_silently_cached_for_future_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            releases = root / "releases"
            release = releases / "1.0.0"
            release.mkdir(parents=True)
            payload = b"installed version setup"
            installer_name = "L2DConfigEditor-Setup-1.0.0-x64.exe"
            manifest = {
                "schema_version": 1,
                "product": "L2DConfigEditor",
                "channel": "stable",
                "version": "1.0.0",
                "published_at": "2026-07-27T00:00:00+00:00",
                "minimum_supported_version": "1.0.0",
                "notes": "first release",
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
            (releases / "latest").write_text("1.0.0", encoding="utf-8")
            server = ReleaseHTTPServer(("127.0.0.1", 0), releases)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            client = UpdateClient(
                self.public_pem,
                current_version="1.0.0",
                cache_root=root / "cache",
            )
            no_update = []
            cached = []
            failures = []
            client.noUpdate.connect(lambda: no_update.append(True))
            client.cacheFinished.connect(cached.append)
            client.checkFailed.connect(failures.append)
            client.cacheFailed.connect(failures.append)
            app = QApplication.instance()
            try:
                client.check(f"http://127.0.0.1:{server.server_address[1]}")
                for _ in range(120):
                    app.processEvents()
                    if no_update or failures:
                        break
                    QTest.qWait(10)
                self.assertEqual([True], no_update)
                self.assertTrue(client.cache_current_release())
                for _ in range(200):
                    app.processEvents()
                    if cached or failures:
                        break
                    QTest.qWait(10)
                self.assertFalse(failures)
                self.assertEqual(
                    [("1.0.0", Path(cached[0]).resolve())],
                    client.verified_cached_installers(),
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


class SingleInstancePrimitiveTests(unittest.TestCase):
    def test_server_name_is_stable_and_scoped(self):
        self.assertEqual(default_server_name(), default_server_name())
        self.assertTrue(default_server_name().startswith("L2DConfigEditor-"))

    def test_file_argument_normalization_ignores_flags_and_missing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text("{}", encoding="utf-8")
            result = SingleInstance.normalized_file_arguments(
                ["--no-close-prompt", str(path), str(path.with_name("missing.json"))]
            )
            self.assertEqual(result, [str(path.resolve())])

    def test_second_instance_delivers_arguments_to_primary(self):
        app = QApplication.instance() or QApplication([])
        name = f"L2DConfigEditor-test-{uuid.uuid4().hex}"
        primary = SingleInstance(name)
        received = []
        primary.messageReceived.connect(received.append)
        try:
            self.assertTrue(primary.is_primary)
            code = (
                "from l2d_config_editor.single_instance import SingleInstance;"
                f"instance=SingleInstance({name!r});"
                "raise SystemExit(3 if instance.is_primary else "
                "(0 if instance.send_to_primary(['C:/example.json']) else 4))"
            )
            sender = subprocess.Popen(
                [sys.executable, "-c", code],
                cwd=Path(__file__).resolve().parents[1],
            )
            for _ in range(80):
                app.processEvents()
                if received and sender.poll() is not None:
                    break
                QTest.qWait(25)
            self.assertEqual(0, sender.wait(timeout=2))
            self.assertEqual([["C:/example.json"]], received)
        finally:
            primary.server.close()
            QLocalServer.removeServer(name)


if __name__ == "__main__":
    unittest.main()
