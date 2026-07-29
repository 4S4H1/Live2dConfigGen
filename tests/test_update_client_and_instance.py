from __future__ import annotations

import os
import hashlib
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from PySide6.QtCore import QLockFile, QSettings, QStandardPaths
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtNetwork import QAbstractSocket, QLocalServer, QNetworkInterface

from l2d_config_editor.single_instance import SingleInstance, default_server_name
from l2d_config_editor.host_process import HostIpcServer, HostProcessManager
from l2d_config_editor.update_client import UpdateClient
from l2d_config_editor.update_discovery import (
    UpdateDiscoveryResponder,
    UpdateHostDiscovery,
    build_discovery_query,
    build_discovery_response,
    default_discovery_targets,
    parse_discovery_response,
)
from l2d_config_editor.update_host import ReleaseHTTPServer
from l2d_config_editor.update_host_main import (
    _arguments as host_arguments,
    _prepare_startup_state,
)
from l2d_config_editor.update_installer import (
    installer_handoff_command,
    launch_installer_after_exit,
)
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

    def test_discovery_response_is_nonce_correlated_and_not_amplifying(self):
        nonce = "0123456789abcdef0123456789abcdef"
        query = build_discovery_query(nonce)
        response = build_discovery_response(nonce, 8765)

        self.assertLessEqual(len(response), len(query))
        self.assertEqual(8765, parse_discovery_response(response, nonce))
        self.assertIsNone(
            parse_discovery_response(
                response,
                "ffffffffffffffffffffffffffffffff",
            )
        )

    def test_discovers_a_running_host_without_a_configured_address(self):
        responder = UpdateDiscoveryResponder(
            http_port=8765,
            release_available=lambda: True,
            discovery_port=0,
            bind_address="127.0.0.1",
        )
        self.assertTrue(responder.start())
        discovered = []
        discovery = UpdateHostDiscovery(
            discovery_port=responder.local_port,
            targets=("127.0.0.1",),
            timeout_ms=150,
        )
        discovery.finished.connect(discovered.append)
        started = time.monotonic()
        try:
            discovery.start()
            for _ in range(60):
                self.app.processEvents()
                if discovered:
                    break
                QTest.qWait(10)
        finally:
            responder.stop()

        self.assertEqual([["http://127.0.0.1:8765"]], discovered)
        self.assertLess(time.monotonic() - started, 1.0)

    def test_default_discovery_targets_cover_each_ipv4_adapter_broadcast(self):
        targets = default_discovery_targets()

        self.assertIn("127.0.0.1", targets)
        self.assertIn("255.255.255.255", targets)
        expected = set()
        for interface in QNetworkInterface.allInterfaces():
            if not (
                interface.flags()
                & QNetworkInterface.InterfaceFlag.IsUp
            ):
                continue
            for entry in interface.addressEntries():
                if (
                    entry.ip().protocol()
                    == QAbstractSocket.NetworkLayerProtocol.IPv4Protocol
                    and not entry.broadcast().isNull()
                ):
                    expected.add(entry.broadcast().toString())
        self.assertTrue(expected.issubset(set(targets)))
        self.assertEqual(len(targets), len(set(targets)))

    def test_cache_root_is_explicit_and_version_is_configurable(self):
        with tempfile.TemporaryDirectory() as directory:
            client = UpdateClient(
                self.public_pem,
                current_version="9.8.7",
                cache_root=Path(directory),
            )
            self.assertEqual(client.current_version, "9.8.7")
            self.assertEqual(client.cache_root, Path(directory).resolve())

    def test_new_check_is_rejected_without_resetting_an_active_download(self):
        with tempfile.TemporaryDirectory() as directory:
            client = UpdateClient(
                self.public_pem,
                current_version="1.0.0",
                cache_root=Path(directory),
            )
            artifact = object()
            active_reply = object()
            client._artifact = artifact
            client._raw_manifest = b"signed manifest snapshot"
            client._download_reply = active_reply
            failures = []
            client.checkFailed.connect(failures.append)
            try:
                client.check("http://127.0.0.1:8765")
                client.check_automatically()

                self.assertEqual(2, len(failures))
                self.assertTrue(all("正在下载" in message for message in failures))
                self.assertIs(artifact, client._artifact)
                self.assertEqual(
                    b"signed manifest snapshot",
                    client._raw_manifest,
                )
                self.assertIs(active_reply, client._download_reply)
            finally:
                client._download_reply = None

    def test_cancel_stops_a_queued_range_fallback_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            client = UpdateClient(
                self.public_pem,
                current_version="1.0.0",
                cache_root=Path(directory),
            )
            failures = []
            client.downloadFailed.connect(failures.append)
            client._download_retry_cache_only = False
            client._download_retry_timer.start(0)

            client.cancel_download()
            self.app.processEvents()

            self.assertFalse(client._download_retry_timer.isActive())
            self.assertIsNone(client._download_retry_cache_only)
            self.assertFalse(failures)

    def test_finished_reply_stops_after_stream_setup_replaces_it(self):
        with tempfile.TemporaryDirectory() as directory:
            client = UpdateClient(
                self.public_pem,
                current_version="1.0.0",
                cache_root=Path(directory),
            )
            old_reply = Mock()
            client._download_reply = old_reply

            def replace_reply_during_ready() -> None:
                client._download_reply = None

            client._download_ready = replace_reply_during_ready
            client._download_finished()

            old_reply.error.assert_not_called()
            old_reply.deleteLater.assert_not_called()

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
                restart_host_executable=Path(directory) / "L2DUpdateHost.exe",
                host_pid=5678,
                restore_host_process=True,
                restore_host_service=False,
                restore_host_login=True,
                restore_host_run_entry=True,
            )
            self.assertIn("Wait-Process -Id 1234", command)
            self.assertIn("Wait-Process -Id 5678", command)
            self.assertLess(
                command.index("Wait-Process -Id 5678"),
                command.index("Start-Process -FilePath $installer"),
            )
            self.assertIn("setup''s copy.exe", command)
            self.assertIn("Start-Process -FilePath $installer", command)
            self.assertIn("$restartHost=", command)
            self.assertIn("'--restore-after-update'", command)
            self.assertIn("'--restore-login','1'", command)
            self.assertIn("'--restore-run','1'", command)
            self.assertIn("'--restore-service','0'", command)
            self.assertIn("'--restore-process','1'", command)
            self.assertGreaterEqual(
                command.count("-WorkingDirectory $workingDirectory"),
                3,
            )

    def test_host_shutdown_waits_for_the_exact_reported_pid(self):
        manager = HostProcessManager(
            executable=Path.cwd() / "L2DUpdateHost.exe",
            server_name=f"host-test-{uuid.uuid4().hex}",
        )
        status = {
            "ok": True,
            "process_running": True,
            "pid": 43210,
            "service_running": True,
        }
        with (
            patch.object(manager, "status", return_value=status),
            patch(
                "l2d_config_editor.host_process.request_host",
                return_value={"ok": True, "shutdown_started": True},
            ),
            patch.object(
                manager,
                "_wait_for_process_exit",
                return_value=True,
            ) as wait_for_exit,
        ):
            self.assertTrue(manager.shutdown_for_update(timeout_ms=3210))

        wait_for_exit.assert_called_once_with(43210, 3210)

    def test_host_shutdown_refuses_unsafe_pidless_status(self):
        manager = HostProcessManager(
            executable=Path.cwd() / "L2DUpdateHost.exe",
            server_name=f"host-test-{uuid.uuid4().hex}",
        )
        with (
            patch.object(
                manager,
                "status",
                return_value={"ok": True, "process_running": True},
            ),
            patch(
                "l2d_config_editor.host_process.request_host"
            ) as request,
        ):
            self.assertFalse(manager.shutdown_for_update())
        request.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "exact PID waiting is Windows-only")
    def test_exact_host_pid_waits_for_process_handle_signal(self):
        process = subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Start-Sleep -Milliseconds 300",
            ],
            cwd=Path(__file__).resolve().parents[1],
        )
        try:
            self.assertFalse(
                HostProcessManager._wait_for_process_exit(
                    process.pid,
                    25,
                )
            )
            self.assertTrue(
                HostProcessManager._wait_for_process_exit(
                    process.pid,
                    2000,
                )
            )
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=2)

    def test_host_update_preparation_preserves_restore_state(self):
        manager = HostProcessManager(
            executable=Path.cwd() / "L2DUpdateHost.exe",
            server_name=f"host-test-{uuid.uuid4().hex}",
        )
        status = {
            "ok": True,
            "process_running": True,
            "pid": 8765,
            "service_running": False,
            "restore_at_login": True,
            "login_startup_enabled": False,
        }
        with (
            patch.object(manager, "status", return_value=status),
            patch.object(manager, "shutdown_for_update", return_value=True),
        ):
            preparation = manager.prepare_for_update()

        self.assertTrue(preparation.was_running)
        self.assertFalse(preparation.service_was_running)
        self.assertEqual(8765, preparation.pid)
        self.assertTrue(preparation.restore_at_login)
        self.assertFalse(preparation.login_startup_enabled)

    def test_login_start_respects_disabled_restore_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(
                str(Path(directory) / "host.ini"),
                QSettings.Format.IniFormat,
            )
            settings.setValue("host/restore_at_login", False)
            settings.sync()
            args = host_arguments(["--login"])
            with patch(
                "l2d_config_editor.update_host_main.set_login_startup",
                return_value=True,
            ) as set_startup:
                self.assertFalse(_prepare_startup_state(args, settings))
            set_startup.assert_called_once_with(False)

    def test_update_restore_applies_run_service_and_process_state(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(
                str(Path(directory) / "host.ini"),
                QSettings.Format.IniFormat,
            )
            args = host_arguments(
                [
                    "--restore-after-update",
                    "--restore-login",
                    "0",
                    "--restore-run",
                    "1",
                    "--restore-service",
                    "0",
                    "--restore-process",
                    "0",
                ]
            )
            with patch(
                "l2d_config_editor.update_host_main.set_login_startup",
                return_value=True,
            ) as set_startup:
                self.assertFalse(_prepare_startup_state(args, settings))
            set_startup.assert_called_once_with(True)
            self.assertFalse(
                settings.value("host/restore_at_login", type=bool)
            )
            self.assertFalse(
                settings.value("host/service_enabled", type=bool)
            )

    def test_host_ipc_recovers_a_lock_left_by_a_crashed_process(self):
        server_name = f"L2DUpdateHost-stale-{uuid.uuid4().hex}"
        lock_digest = hashlib.sha256(server_name.encode("utf-8")).hexdigest()
        lock_path = (
            Path(
                QStandardPaths.writableLocation(
                    QStandardPaths.StandardLocation.TempLocation
                )
            )
            / f"{lock_digest}.lock"
        )
        code = (
            "import os;"
            "from PySide6.QtCore import QLockFile;"
            f"lock=QLockFile({str(lock_path)!r});"
            "lock.setStaleLockTime(0);"
            "raise SystemExit(4) if not lock.tryLock(0) else os._exit(0)"
        )
        crashed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(__file__).resolve().parents[1],
            check=False,
            timeout=5,
        )
        self.assertEqual(0, crashed.returncode)
        self.assertTrue(lock_path.is_file())

        server = HostIpcServer(
            lambda _command: {"ok": True},
            server_name=server_name,
        )
        try:
            self.assertTrue(server.is_primary)
        finally:
            server.close()

    def test_host_ipc_never_removes_a_live_startup_lock(self):
        server_name = f"L2DUpdateHost-live-{uuid.uuid4().hex}"
        lock_digest = hashlib.sha256(server_name.encode("utf-8")).hexdigest()
        lock_path = (
            Path(
                QStandardPaths.writableLocation(
                    QStandardPaths.StandardLocation.TempLocation
                )
            )
            / f"{lock_digest}.lock"
        )
        live_lock = QLockFile(str(lock_path))
        live_lock.setStaleLockTime(30_000)
        self.assertTrue(live_lock.tryLock(0))
        server = HostIpcServer(
            lambda _command: {"ok": True},
            server_name=server_name,
        )
        try:
            self.assertFalse(server.is_primary)
            self.assertTrue(live_lock.isLocked())
            self.assertTrue(lock_path.is_file())
        finally:
            server.close()
            live_lock.unlock()

    def test_installer_handoff_runs_from_the_external_update_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installer = root / "updates" / "1.2.3" / "setup.exe"
            installer.parent.mkdir(parents=True)
            installer.write_bytes(b"MZ")
            with (
                patch("l2d_config_editor.update_installer.sys.platform", "win32"),
                patch(
                    "l2d_config_editor.update_installer.QProcess.startDetached",
                    return_value=(True, 1234),
                ) as start_detached,
            ):
                self.assertTrue(
                    launch_installer_after_exit(
                        installer,
                        current_pid=4321,
                    )
                )

            self.assertEqual(
                str(installer.parent.resolve()),
                start_detached.call_args.args[2],
            )

    def test_auto_discovery_signed_check_and_range_resume_download(self):
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
            responder = UpdateDiscoveryResponder(
                http_port=server.server_address[1],
                release_available=lambda: True,
                discovery_port=0,
                bind_address="127.0.0.1",
            )
            self.assertTrue(responder.start())
            cache = root / "cache"
            part = cache / "1.0.1" / f"{installer_name}.part"
            part.parent.mkdir(parents=True)
            part.write_bytes(payload[:777])
            client = UpdateClient(
                self.public_pem,
                current_version="1.0.0",
                cache_root=cache,
                discovery=UpdateHostDiscovery(
                    discovery_port=responder.local_port,
                    targets=("127.0.0.1",),
                    timeout_ms=100,
                ),
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
                client.check_automatically()
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
                responder.stop()
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_manual_address_is_used_after_discovery_times_out(self):
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
                discovery=UpdateHostDiscovery(
                    discovery_port=65534,
                    targets=("127.0.0.1",),
                    timeout_ms=50,
                ),
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
                client.check_automatically(
                    f"http://127.0.0.1:{server.server_address[1]}"
                )
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

    def test_manifest_check_timeout_is_bounded(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(0.05)
        accepted = []
        stopped = threading.Event()

        def accept_without_replying() -> None:
            while not stopped.is_set():
                try:
                    connection, _address = listener.accept()
                except TimeoutError:
                    continue
                except OSError:
                    return
                accepted.append(connection)

        worker = threading.Thread(target=accept_without_replying, daemon=True)
        worker.start()
        client = UpdateClient(
            self.public_pem,
            current_version="1.0.0",
            check_timeout_ms=100,
        )
        failures = []
        client.checkFailed.connect(failures.append)
        started = time.monotonic()
        try:
            client.check(f"http://127.0.0.1:{listener.getsockname()[1]}")
            for _ in range(80):
                self.app.processEvents()
                if failures:
                    break
                QTest.qWait(10)
            self.assertTrue(failures)
            self.assertIn("超时", failures[0])
            self.assertLess(time.monotonic() - started, 1.0)
        finally:
            stopped.set()
            listener.close()
            for connection in accepted:
                connection.close()
            worker.join(timeout=1)
            # Aborted QNetworkReply objects must not leave Python callbacks that
            # crash the next GUI event-loop turn.
            self.app.processEvents()


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
