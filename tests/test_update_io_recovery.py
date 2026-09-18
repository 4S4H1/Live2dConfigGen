from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from PySide6.QtNetwork import QLocalServer, QLocalSocket, QNetworkReply
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from l2d_config_editor.single_instance import SingleInstance
from l2d_config_editor import main as application_main
from l2d_config_editor.update_client import UpdateClient
from l2d_config_editor.update_manifest import (
    canonical_manifest_bytes,
    sign_manifest,
    validate_manifest,
)


class UpdateIoRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.key = Ed25519PrivateKey.generate()
        cls.public_pem = cls.key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def make_client(self, cache: Path) -> UpdateClient:
        self.payload = b"verified installer"
        raw = canonical_manifest_bytes({
            "schema_version": 1,
            "product": "L2DConfigEditor",
            "channel": "stable",
            "version": "1.2.0",
            "published_at": "2026-09-19T00:00:00+00:00",
            "minimum_supported_version": "1.0.0",
            "notes": "test",
            "key_id": "release-1",
            "artifact": {
                "platform": "windows",
                "arch": "x86_64",
                "url": "setup.exe",
                "size": len(self.payload),
                "sha256": hashlib.sha256(self.payload).hexdigest(),
            },
        })
        client = UpdateClient(self.public_pem, current_version="1.0.0", cache_root=cache)
        _, client._artifact = validate_manifest(raw, current_version="1.0.0")
        client._artifact_url = "http://127.0.0.1:8765/stable/setup.exe"
        client._raw_manifest = raw
        client._manifest_signature = sign_manifest(raw, self.key)
        self.addCleanup(client.cancel_download)
        return client

    def test_unwritable_cache_emits_failure_instead_of_escaping(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache"
            cache.write_bytes(b"path is a file")
            client = self.make_client(cache)
            failures = []
            client.downloadFailed.connect(failures.append)

            client.download_available()

            self.assertEqual(1, len(failures))
            self.assertFalse(client._download_is_active())

    def test_locked_obsolete_cache_does_not_fail_verified_download(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            client = self.make_client(cache)
            for version in ("1.0.0", "1.1.0", "1.2.0"):
                (cache / version).mkdir()
            installer = cache / "1.2.0" / "setup.exe"
            installer.write_bytes(self.payload)
            finished, failures = [], []
            client.downloadFinished.connect(finished.append)
            client.downloadFailed.connect(failures.append)

            with (
                patch("l2d_config_editor.update_client.shutil.rmtree", side_effect=PermissionError("locked")),
                self.assertLogs("l2d_config_editor.update_client", level="WARNING"),
            ):
                client.download_available()

            self.assertEqual([str(installer)], finished)
            self.assertFalse(failures)
            self.assertTrue(client.verify_cached_installer(installer))

    def test_stream_open_failure_cancels_reply_and_reports_error(self):
        with tempfile.TemporaryDirectory() as directory:
            client = self.make_client(Path(directory))
            reply = Mock()
            reply.attribute.return_value = 200
            reply.rawHeader.return_value = b""
            client._download_reply = reply
            client._download_part = Path(directory) / "setup.exe.part"
            failures = []
            client.downloadFailed.connect(failures.append)

            with patch.object(Path, "open", side_effect=PermissionError("denied")):
                client._download_ready()

            self.assertEqual(1, len(failures))
            self.assertFalse(client._download_is_active())
            reply.abort.assert_called_once()

    def test_disk_full_during_write_cancels_reply_and_reports_error(self):
        with tempfile.TemporaryDirectory() as directory:
            client = self.make_client(Path(directory))
            reply, stream = Mock(), Mock()
            reply.readAll.return_value = b"installer bytes"
            stream.write.side_effect = OSError("disk full")
            # A buffered stream may raise the same error again while closing.
            stream.close.side_effect = OSError("disk full")
            client._download_reply = reply
            client._download_stream = stream
            client._download_initialized = True
            failures = []
            client.downloadFailed.connect(failures.append)

            with self.assertLogs("l2d_config_editor.update_client", level="WARNING"):
                client._download_ready()

            self.assertEqual(1, len(failures))
            self.assertFalse(client._download_is_active())
            reply.abort.assert_called_once()

    def test_flush_failure_finishes_with_one_error_and_releases_download(self):
        with tempfile.TemporaryDirectory() as directory:
            client = self.make_client(Path(directory))
            reply, stream = Mock(), Mock()
            reply.readAll.return_value = b""
            reply.error.return_value = QNetworkReply.NetworkError.NoError
            stream.tell.return_value = len(self.payload)
            stream.flush.side_effect = OSError("disk full")
            client._download_reply = reply
            client._download_stream = stream
            client._download_initialized = True
            failures = []
            client.downloadFailed.connect(failures.append)

            client._download_finished()

            self.assertEqual(1, len(failures))
            self.assertFalse(client._download_is_active())


class SingleInstanceFramingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_secondary_launch_resolves_files_before_sending_to_another_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "config.json"
            target.write_text("{}", encoding="utf-8")
            secondary = Mock(is_primary=False)
            secondary.normalized_file_arguments = SingleInstance.normalized_file_arguments
            secondary.send_to_primary.return_value = True
            original_cwd = Path.cwd()
            try:
                os.chdir(directory)
                with (
                    patch.object(sys, "argv", ["editor", "config.json"]),
                    patch.object(application_main, "QApplication"),
                    patch.object(application_main, "SingleInstance", return_value=secondary),
                ):
                    self.assertEqual(0, application_main.main())
                secondary.send_to_primary.assert_called_once_with([str(target.resolve())])
            finally:
                os.chdir(original_cwd)

    def test_fragmented_header_is_delivered_without_blocking_the_event_loop(self):
        name = f"L2D-fragmented-{uuid.uuid4().hex}"
        primary = SingleInstance(name)
        sender = QLocalSocket()
        received = []
        primary.messageReceived.connect(received.append)
        try:
            sender.connectToServer(name)
            self.assertTrue(sender.waitForConnected(1000))
            body = json.dumps({"args": ["C:/example.json"]}).encode()
            packet = len(body).to_bytes(4, "big") + body
            sender.write(packet[:2])
            sender.flush()
            QTest.qWait(20)
            sender.write(packet[2:7])
            sender.flush()
            QTest.qWait(20)
            sender.write(packet[7:])
            sender.flush()
            for _ in range(20):
                QTest.qWait(10)
                if received:
                    break
            self.assertEqual([["C:/example.json"]], received)
        finally:
            sender.abort()
            primary.server.close()
            QLocalServer.removeServer(name)

    def test_non_object_payload_is_ignored_without_an_exception(self):
        name = f"L2D-invalid-{uuid.uuid4().hex}"
        primary = SingleInstance(name)
        sender = QLocalSocket()
        received, errors = [], []
        primary.messageReceived.connect(received.append)
        try:
            sender.connectToServer(name)
            self.assertTrue(sender.waitForConnected(1000))
            sender.write(b"\x00\x00\x00\x02[]")
            sender.flush()
            with patch("sys.excepthook", side_effect=lambda *error: errors.append(error)):
                QTest.qWait(30)
            self.assertFalse(received)
            self.assertFalse(errors)
        finally:
            sender.abort()
            primary.server.close()
            QLocalServer.removeServer(name)
