from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from PySide6.QtCore import QSettings
from PySide6.QtNetwork import QAbstractSocket, QHostAddress, QUdpSocket
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QMessageBox,
    QPushButton,
)

from l2d_config_editor.update_discovery import DISCOVERY_PORT, UpdateHostDiscovery
from l2d_config_editor.update_host import (
    DEFAULT_PORT,
    HOST_RESTORE_AT_LOGIN_KEY,
    ReleaseHTTPServer,
    SETTINGS_PORT_KEY,
    UpdateHostWindow,
    default_data_root,
    firewall_powershell_command,
)
from l2d_config_editor.update_client import bundled_public_key_pem
from l2d_config_editor.main_window import MainWindow
from l2d_config_editor.update_manifest import (
    UpdateValidationError,
    canonical_manifest_bytes,
    import_release_bundle,
    resolve_same_origin,
    sign_manifest,
    validate_manifest,
    verify_artifact,
    verify_manifest_signature,
)
from scripts.release_tools import (
    build_bundle,
    checksums,
    collect_licenses,
    verify_keypair,
)

ROOT = Path(__file__).resolve().parents[1]


class IntegratedUpdateHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_host_window_is_an_embedded_tool_with_valid_chinese_labels(self):
        source = (
            ROOT / "l2d_config_editor" / "update_host.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("\ufffd", source)
        with tempfile.TemporaryDirectory() as directory:
            host = UpdateHostWindow(
                data_root=Path(directory),
                public_key_pem=bundled_public_key_pem(),
                parent=None,
            )

            visible_text = [host.windowTitle()]
            visible_text.extend(label.text() for label in host.findChildren(QLabel))
            visible_text.extend(button.text() for button in host.findChildren(QPushButton))
            self.assertNotIn("\ufffd", "".join(visible_text))
            self.assertFalse(hasattr(host, "tray"))
            self.assertEqual(QSettings.Format.IniFormat, host.settings.format())
            host.close()

    def test_firewall_rule_uses_the_hosts_configured_discovery_port(self):
        with tempfile.TemporaryDirectory() as directory:
            host = UpdateHostWindow(
                data_root=Path(directory),
                public_key_pem=bundled_public_key_pem(),
                discovery_port=54321,
            )
            fake_process = Mock()
            with (
                patch("l2d_config_editor.update_host.sys.platform", "win32"),
                patch(
                    "l2d_config_editor.update_host.sys.frozen",
                    True,
                    create=True,
                ),
                patch(
                    "l2d_config_editor.update_host.firewall_powershell_command",
                    return_value="Write-Output configured",
                ) as build_command,
                patch(
                    "l2d_config_editor.update_host.QProcess",
                    return_value=fake_process,
                ),
            ):
                host.create_firewall_rule()

            self.assertEqual(
                54321,
                build_command.call_args.kwargs["discovery_port"],
            )
            fake_process.start.assert_called_once()
            host.firewall_process = None
            host.close()

    def test_host_status_reports_exact_pid_and_restore_state(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(
                str(Path(directory) / "host.ini"),
                QSettings.Format.IniFormat,
            )
            settings.setValue(HOST_RESTORE_AT_LOGIN_KEY, True)
            host = UpdateHostWindow(
                data_root=Path(directory) / "data",
                public_key_pem=bundled_public_key_pem(),
                settings=settings,
            )
            with patch(
                "l2d_config_editor.update_host.login_startup_enabled",
                return_value=True,
            ):
                status = host.host_status()

            self.assertEqual(os.getpid(), status["pid"])
            self.assertTrue(status["process_running"])
            self.assertTrue(status["restore_at_login"])
            self.assertTrue(status["login_startup_enabled"])
            host.close()

    def test_embedded_window_close_stops_and_joins_the_server_thread(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            host = UpdateHostWindow(
                data_root=root,
                public_key_pem=bundled_public_key_pem(),
            )
            server = ReleaseHTTPServer(("127.0.0.1", 0), root / "releases")
            server_thread = threading.Thread(
                target=server.serve_forever,
                daemon=True,
            )
            host.server = server
            host.server_thread = server_thread
            server_thread.start()
            host.show()

            host.close()
            self.app.processEvents()

            self.assertIsNone(host.server)
            self.assertIsNone(host.server_thread)
            self.assertFalse(server_thread.is_alive())

    def test_access_log_keeps_only_the_most_recent_1000_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            host = UpdateHostWindow(
                data_root=Path(directory),
                public_key_pem=bundled_public_key_pem(),
            )

            for index in range(1005):
                host.logReceived.emit(f"request-{index}")

            self.assertEqual(1000, host.log_list.count())
            self.assertEqual("request-5", host.log_list.item(0).text())
            self.assertEqual("request-1004", host.log_list.item(999).text())
            host.close()

    def test_http_log_callback_is_bounded_until_the_gui_flushes_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            host = UpdateHostWindow(
                data_root=root / "host-data",
                public_key_pem=bundled_public_key_pem(),
            )
            host.port_box.setMinimum(0)
            host.port_box.setValue(0)
            host.start_server()
            try:
                self.assertIsNotNone(host.server)
                direct_log_count = host.log_list.count()

                def flood_server_callback() -> None:
                    assert host.server is not None
                    for index in range(100_000):
                        host.server.emit_log(f"request-{index}")

                worker = threading.Thread(target=flood_server_callback)
                worker.start()
                worker.join(timeout=5)
                self.assertFalse(worker.is_alive())

                self.assertEqual(1000, host.pending_access_log_count)
                self.assertEqual(99_000, host.dropped_access_log_count)
                self.assertEqual(direct_log_count, host.log_list.count())

                host.flush_pending_access_logs()

                self.assertEqual(0, host.pending_access_log_count)
                self.assertEqual(1000, host.log_list.count())
                self.assertIn("99000", host.log_list.item(999).text())
            finally:
                host.close()

    def test_running_host_is_discoverable_from_the_same_machine(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = root / "host-data" / "releases" / "1.2.0"
            release.mkdir(parents=True)
            (release.parent / "latest").write_text("1.2.0", encoding="utf-8")
            host = UpdateHostWindow(
                data_root=root / "host-data",
                public_key_pem=bundled_public_key_pem(),
                discovery_port=0,
                discovery_bind_address="127.0.0.1",
            )
            host.port_box.setMinimum(0)
            host.port_box.setValue(0)
            host.start_server()
            discovered = []
            self.assertIsNotNone(host.discovery_responder)
            discovery = UpdateHostDiscovery(
                discovery_port=host.discovery_responder.local_port,
                targets=("127.0.0.1",),
                timeout_ms=100,
            )
            discovery.finished.connect(discovered.append)
            try:
                discovery.start()
                for _ in range(40):
                    self.app.processEvents()
                    if discovered:
                        break
                    QTest.qWait(10)
                self.assertEqual(
                    [[f"http://127.0.0.1:{host.server.server_address[1]}"]],
                    discovered,
                )
                self.assertIn("自动发现已启用", host.discovery_label.text())
            finally:
                host.close()

            self.assertIsNone(host.discovery_responder)

    def test_discovery_port_collision_keeps_http_and_manual_address_available(self):
        blocker = QUdpSocket()
        self.assertTrue(
            blocker.bind(
                QHostAddress.SpecialAddress.LocalHost,
                0,
                QAbstractSocket.BindFlag.DontShareAddress,
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            host = UpdateHostWindow(
                data_root=Path(directory),
                public_key_pem=bundled_public_key_pem(),
                discovery_port=blocker.localPort(),
                discovery_bind_address="127.0.0.1",
            )
            host.port_box.setMinimum(0)
            host.port_box.setValue(0)
            try:
                host.start_server()

                self.assertIsNotNone(host.server)
                self.assertIsNone(host.discovery_responder)
                self.assertTrue(host.copy_button.isEnabled())
                self.assertIn("仍可复制地址手动配置", host.discovery_label.text())
            finally:
                host.close()
                blocker.close()

    def test_invalid_text_port_setting_falls_back_to_8765(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = QSettings(
                str(root / "settings.ini"),
                QSettings.Format.IniFormat,
            )
            settings.setValue(SETTINGS_PORT_KEY, "not-a-port")

            host = UpdateHostWindow(
                data_root=root / "host-data",
                public_key_pem=bundled_public_key_pem(),
                settings=settings,
            )

            self.assertEqual(DEFAULT_PORT, host.port_box.value())
            host.close()

    def test_out_of_range_port_setting_falls_back_to_8765(self):
        for configured_port in (80, 70000):
            with self.subTest(configured_port=configured_port):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    settings = QSettings(
                        str(root / "settings.ini"),
                        QSettings.Format.IniFormat,
                    )
                    settings.setValue(SETTINGS_PORT_KEY, configured_port)

                    host = UpdateHostWindow(
                        data_root=root / "host-data",
                        public_key_pem=bundled_public_key_pem(),
                        settings=settings,
                    )

                    self.assertEqual(DEFAULT_PORT, host.port_box.value())
                    host.close()

    def test_start_server_reports_when_release_directory_cannot_be_created(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            blocking_file = root / "not-a-directory"
            blocking_file.write_text("blocked", encoding="utf-8")
            host = UpdateHostWindow(
                data_root=root / "host-data",
                public_key_pem=bundled_public_key_pem(),
            )
            host.releases_root = blocking_file / "releases"

            with patch.object(QMessageBox, "critical") as critical:
                host.start_server()

            critical.assert_called_once()
            self.assertIsNone(host.server)
            self.assertIsNone(host.server_thread)
            host.close()

    def test_integrated_host_reuses_the_existing_release_cache_root(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"LOCALAPPDATA": directory},
        ):
            self.assertEqual(
                Path(directory) / "4S4H1" / "L2DUpdateHost",
                default_data_root(),
            )

    def test_integrated_host_imports_with_the_editor_embedded_public_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private_key = Ed25519PrivateKey.generate()
            private_path = root / "private.pem"
            private_path.write_bytes(
                private_key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
            public_key_pem = private_key.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            installer = root / "L2DConfigEditor-Setup-1.2.0-x64.exe"
            installer.write_bytes(b"signed editor installer")
            bundle = root / "release.l2dupdate"
            build_bundle(
                installer,
                private_path,
                bundle,
                "integration test",
                release_version="1.2.0",
            )
            host = UpdateHostWindow(
                data_root=root / "host-data",
                public_key_pem=public_key_pem,
            )

            with (
                patch.object(
                    QFileDialog,
                    "getOpenFileName",
                    return_value=(str(bundle), "L2D 更新包 (*.l2dupdate)"),
                ),
                patch.object(QMessageBox, "information") as information,
                patch.object(QMessageBox, "critical") as critical,
            ):
                host.import_bundle()

            critical.assert_not_called()
            information.assert_called_once()
            self.assertEqual("1.2.0", host.version_label.text())
            self.assertEqual(
                "1.2.0",
                (host.releases_root / "latest").read_text("utf-8").strip(),
            )
            self.assertFalse(
                (host.data_root / "release_public_key.pem").exists(),
                "integrated Host must trust the editor's embedded key",
            )
            host.close()

    def test_editor_wakes_independent_host_without_owning_its_lifecycle(self):
        with tempfile.TemporaryDirectory() as directory:
            editor = MainWindow(directory, prefer_saved_workspace=False)
            action_labels = [action.text() for action in editor.tools_menu.actions()]
            self.assertIn("局域网更新主机…", action_labels)
            manager = Mock()
            manager.show_or_start.return_value = True
            editor._update_host_manager = manager

            first = editor._open_update_host()
            second = editor._open_update_host()

            self.assertTrue(first)
            self.assertTrue(second)
            self.assertEqual(2, manager.show_or_start.call_count)
            editor._mark_saved_checkpoint(saved=True)
            editor.close()
            manager.prepare_for_update.assert_not_called()
            manager.shutdown_for_update.assert_not_called()

    def test_manual_check_discovers_without_prompting_for_an_address(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            editor = MainWindow(root, prefer_saved_workspace=False)
            editor.settings = QSettings(
                str(root / "settings.ini"),
                QSettings.Format.IniFormat,
            )
            client = Mock()
            try:
                with (
                    patch.object(
                        editor,
                        "_update_client_or_warn",
                        return_value=client,
                    ),
                    patch.object(
                        editor,
                        "_configure_update_host",
                        side_effect=AssertionError(
                            "自动发现不应先要求输入地址"
                        ),
                    ),
                ):
                    editor._check_for_updates(manual=True)

                client.check_automatically.assert_called_once_with("")
                self.assertIn("自动发现", editor.statusBar().currentMessage())
            finally:
                editor._mark_saved_checkpoint(saved=True)
                editor.close()

    def test_release_pipeline_builds_editor_and_companion_host_in_one_installer(self):
        build_script = (ROOT / "scripts" / "Build-Release.ps1").read_text("utf-8")
        project = (ROOT / "pyproject.toml").read_text("utf-8")

        self.assertIn("L2DUpdateHost.spec", build_script)
        self.assertIn("HOST_SOURCE_DIR", build_script)
        self.assertNotIn("host-installer.nsi", build_script)
        self.assertNotIn("L2DUpdateHost-Setup", build_script)
        self.assertIn("l2d-update-host", project)
        self.assertTrue((ROOT / "packaging" / "L2DUpdateHost.spec").exists())
        self.assertFalse((ROOT / "packaging" / "host-installer.nsi").exists())


def manifest_for(payload: bytes, version: str = "1.2.0") -> dict:
    return {
        "schema_version": 1,
        "product": "L2DConfigEditor",
        "channel": "stable",
        "version": version,
        "published_at": "2026-07-27T00:00:00+00:00",
        "minimum_supported_version": "1.0.0",
        "notes": "test",
        "key_id": "release-1",
        "artifact": {
            "platform": "windows",
            "arch": "x86_64",
            "url": f"L2DConfigEditor-Setup-{version}-x64.exe",
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
    }


class ReleaseLicenseTests(unittest.TestCase):
    def test_collector_includes_gnu_and_nsis_license_texts(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "licenses"
            collect_licenses(output)
            gpl = output / "GNU" / "GPL-3.0-only.txt"
            lgpl = output / "GNU" / "LGPL-3.0-only.txt"
            nsis = output / "NSIS" / "COPYING.txt"
            self.assertIn("GNU GENERAL PUBLIC LICENSE", gpl.read_text("utf-8"))
            self.assertIn(
                "GNU LESSER GENERAL PUBLIC LICENSE",
                lgpl.read_text("utf-8"),
            )
            self.assertIn("ZLIB/LIBPNG LICENSE", nsis.read_text("utf-8"))

    def test_checksum_manifest_recursively_covers_licenses(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "artifact.exe").write_bytes(b"artifact")
            (root / "licenses").mkdir()
            (root / "licenses" / "LGPL.txt").write_bytes(b"license")
            output = root / "SHA256SUMS.txt"
            checksums(root, output)
            entries = {
                line.split("  ", 1)[1]
                for line in output.read_text("ascii").splitlines()
            }
            self.assertEqual(
                {"artifact.exe", "licenses/LGPL.txt"},
                entries,
            )


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.private_key = Ed25519PrivateKey.generate()
        self.public_key = self.private_key.public_key()
        self.payload = b"fake installer bytes"

    def test_signature_covers_exact_raw_bytes(self):
        raw = canonical_manifest_bytes(manifest_for(self.payload))
        signature = sign_manifest(raw, self.private_key)
        verify_manifest_signature(raw, signature, self.public_key)
        with self.assertRaises(UpdateValidationError):
            verify_manifest_signature(raw + b" ", signature, self.public_key)
        with self.assertRaises(UpdateValidationError):
            verify_manifest_signature(raw, b"not base64!", self.public_key)

    def test_manifest_validation_and_artifact_hash(self):
        raw = canonical_manifest_bytes(manifest_for(self.payload))
        manifest, artifact = validate_manifest(raw, current_version="1.0.0")
        self.assertEqual(manifest["version"], "1.2.0")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / artifact.filename
            path.write_bytes(self.payload)
            verify_artifact(path, artifact)
            path.write_bytes(self.payload + b"tampered")
            with self.assertRaises(UpdateValidationError):
                verify_artifact(path, artifact)

    def test_rejects_downgrade_wrong_product_and_unsafe_url(self):
        candidate = manifest_for(self.payload, version="1.0.0")
        with self.assertRaisesRegex(UpdateValidationError, "降级"):
            validate_manifest(
                canonical_manifest_bytes(candidate), current_version="1.1.0"
            )
        candidate = manifest_for(self.payload)
        candidate["product"] = "Other"
        with self.assertRaisesRegex(UpdateValidationError, "产品"):
            validate_manifest(
                canonical_manifest_bytes(candidate), current_version="1.0.0"
            )
        for unsafe in (
            "../evil.exe",
            "nested/file.exe",
            r"..\evil.exe",
            "/absolute.exe",
            "http://evil.invalid/file.exe",
            "file.exe?x=1",
        ):
            with self.subTest(unsafe=unsafe):
                candidate = manifest_for(self.payload)
                candidate["artifact"]["url"] = unsafe
                with self.assertRaises(UpdateValidationError):
                    validate_manifest(
                        canonical_manifest_bytes(candidate), current_version="1.0.0"
                    )

    def test_requires_stable_semver_and_timezone_aware_timestamp(self):
        for bad_version in ("1.2", "01.2.3", "1.2.3rc1", "1.2.3-alpha.1"):
            with self.subTest(version=bad_version):
                candidate = manifest_for(self.payload)
                candidate["version"] = bad_version
                with self.assertRaises(UpdateValidationError):
                    validate_manifest(
                        canonical_manifest_bytes(candidate), current_version="1.0.0"
                    )
        candidate = manifest_for(self.payload)
        candidate["published_at"] = "2026-07-27T00:00:00"
        with self.assertRaisesRegex(UpdateValidationError, "时区"):
            validate_manifest(
                canonical_manifest_bytes(candidate), current_version="1.0.0"
            )

    def test_rejects_client_below_minimum_supported_version(self):
        candidate = manifest_for(self.payload, version="2.0.0")
        candidate["minimum_supported_version"] = "1.5.0"
        with self.assertRaisesRegex(UpdateValidationError, "最低版本"):
            validate_manifest(
                canonical_manifest_bytes(candidate),
                current_version="1.4.9",
            )

    def test_same_origin_resolution(self):
        self.assertEqual(
            resolve_same_origin(
                "http://host.local:8765/stable/manifest.json", "setup.exe"
            ),
            "http://host.local:8765/stable/setup.exe",
        )
        with self.assertRaises(UpdateValidationError):
            resolve_same_origin("file:///tmp/manifest.json", "setup.exe")


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.private_key = Ed25519PrivateKey.generate()
        self.public_key = self.private_key.public_key()

    def _bundle(self, root: Path, version: str, *, tamper=False) -> Path:
        payload = f"installer {version}".encode()
        manifest = manifest_for(payload, version)
        raw = canonical_manifest_bytes(manifest)
        signature = sign_manifest(raw, self.private_key)
        path = root / f"{version}.l2dupdate"
        with zipfile.ZipFile(path, "w") as bundle:
            bundle.writestr("manifest.json", raw)
            bundle.writestr("manifest.sig", signature)
            bundle.writestr(
                manifest["artifact"]["url"],
                payload + (b"x" if tamper else b""),
            )
        return path

    def test_atomic_import_and_retains_two_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            releases = root / "releases"
            for version in ("1.0.0", "1.1.0", "1.2.0"):
                imported = import_release_bundle(
                    self._bundle(root, version),
                    releases,
                    self.public_key,
                    retain=2,
                )
                self.assertEqual(imported, version)
            self.assertFalse((releases / "1.0.0").exists())
            self.assertTrue((releases / "1.1.0").is_dir())
            self.assertTrue((releases / "1.2.0").is_dir())
            self.assertEqual(
                (releases / "latest").read_text(encoding="utf-8"), "1.2.0"
            )

    def test_tampered_artifact_never_publishes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            releases = root / "releases"
            with self.assertRaises(UpdateValidationError):
                import_release_bundle(
                    self._bundle(root, "1.0.0", tamper=True),
                    releases,
                    self.public_key,
                )
            self.assertFalse((releases / "1.0.0").exists())
            self.assertFalse((releases / "latest").exists())

    def test_failed_latest_switch_preserves_old_release_and_allows_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            releases = root / "releases"
            import_release_bundle(self._bundle(root, "1.0.0"), releases, self.public_key)
            bundle = self._bundle(root, "1.1.0")
            replace = os.replace

            def fail_pointer(source, target):
                if Path(target).name == "latest":
                    raise PermissionError("latest is locked")
                return replace(source, target)

            with patch("l2d_config_editor.update_manifest.os.replace", side_effect=fail_pointer):
                with self.assertRaises(PermissionError):
                    import_release_bundle(bundle, releases, self.public_key, retain=1)
            self.assertEqual("1.0.0", (releases / "latest").read_text())
            self.assertTrue((releases / "1.0.0").is_dir())
            self.assertFalse((releases / "1.1.0").exists())
            self.assertEqual("1.1.0", import_release_bundle(bundle, releases, self.public_key))

    def test_locked_obsolete_cache_does_not_fail_new_release_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            releases = root / "releases"
            import_release_bundle(self._bundle(root, "1.0.0"), releases, self.public_key)
            bundle = self._bundle(root, "1.1.0")
            with patch("l2d_config_editor.update_manifest.shutil.rmtree", side_effect=PermissionError("cache in use")):
                self.assertEqual("1.1.0", import_release_bundle(bundle, releases, self.public_key, retain=1))
            self.assertEqual("1.1.0", (releases / "latest").read_text())
            self.assertTrue((releases / "1.1.0").is_dir())

    def test_import_rejects_downgrade_without_changing_latest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            releases = root / "releases"
            import_release_bundle(
                self._bundle(root, "1.2.0"),
                releases,
                self.public_key,
            )
            with self.assertRaisesRegex(UpdateValidationError, "旧版本"):
                import_release_bundle(
                    self._bundle(root, "1.1.0"),
                    releases,
                    self.public_key,
                )
            self.assertEqual(
                (releases / "latest").read_text(encoding="utf-8"), "1.2.0"
            )
            self.assertTrue((releases / "1.2.0").is_dir())
            self.assertFalse((releases / "1.1.0").exists())

    def test_manifest_size_is_bounded_before_zip_decompression(self):
        with tempfile.TemporaryDirectory() as directory:
            from l2d_config_editor.update_manifest import MAX_MANIFEST_BYTES

            root = Path(directory)
            path = root / "oversized.l2dupdate"
            with zipfile.ZipFile(
                path, "w", compression=zipfile.ZIP_DEFLATED
            ) as bundle:
                bundle.writestr("manifest.json", b"x" * (MAX_MANIFEST_BYTES + 1))
                bundle.writestr("manifest.sig", b"x")
            with self.assertRaisesRegex(UpdateValidationError, "清单过大"):
                import_release_bundle(path, root / "releases", self.public_key)

    def test_zip_traversal_and_duplicate_names_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "bad.l2dupdate"
            with zipfile.ZipFile(path, "w") as bundle:
                bundle.writestr("../manifest.json", b"{}")
            with self.assertRaises(UpdateValidationError):
                import_release_bundle(path, root / "releases", self.public_key)

            duplicate = root / "duplicate.l2dupdate"
            with self.assertWarns(UserWarning):
                with zipfile.ZipFile(duplicate, "w") as bundle:
                    bundle.writestr("manifest.json", b"one")
                    bundle.writestr("manifest.json", b"two")
            with self.assertRaises(UpdateValidationError):
                import_release_bundle(
                    duplicate, root / "releases", self.public_key
                )

    def test_release_bundle_allows_upgrade_from_declared_minimum(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installer = root / "L2DConfigEditor-Setup-1.0.1-x64.exe"
            installer.write_bytes(b"real bundle test")
            private_path = root / "release_private_key.pem"
            private_path.write_bytes(
                self.private_key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
            bundle_path = root / "1.0.1.l2dupdate"
            build_bundle(
                installer,
                private_path,
                bundle_path,
                "upgrade test",
                release_version="1.0.1",
                minimum_supported_version="1.0.0",
            )
            with zipfile.ZipFile(bundle_path) as bundle:
                raw = bundle.read("manifest.json")
                signature = bundle.read("manifest.sig")
            verify_manifest_signature(raw, signature, self.public_key)
            manifest, _artifact = validate_manifest(
                raw,
                current_version="1.0.0",
            )
            self.assertEqual("1.0.0", manifest["minimum_supported_version"])

    def test_release_bundle_rejects_invalid_or_impossible_minimum(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installer = root / "installer.exe"
            installer.write_bytes(b"installer")
            private_path = root / "private.pem"
            private_path.write_bytes(
                self.private_key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
            with self.assertRaises(SystemExit):
                build_bundle(
                    installer,
                    private_path,
                    root / "bad.l2dupdate",
                    "bad minimum",
                    release_version="1.0.1",
                    minimum_supported_version="1.1.0",
                )
            with self.assertRaises(SystemExit):
                build_bundle(
                    installer,
                    private_path,
                    root / "bad-semver.l2dupdate",
                    "bad semver",
                    release_version="1.0",
                    minimum_supported_version="1.0.0",
                )

    def test_keypair_audit_checks_external_and_embedded_public_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private_path = root / "private.pem"
            public_path = root / "public.pem"
            embedded_path = root / "embedded.pem"
            private_path.write_bytes(
                self.private_key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
            public_bytes = self.public_key.public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            public_path.write_bytes(public_bytes)
            embedded_path.write_bytes(public_bytes)
            verify_keypair(private_path, public_path, embedded_path)
            embedded_path.write_bytes(
                Ed25519PrivateKey.generate().public_key().public_bytes(
                    serialization.Encoding.PEM,
                    serialization.PublicFormat.SubjectPublicKeyInfo,
                )
            )
            with self.assertRaises(SystemExit):
                verify_keypair(private_path, public_path, embedded_path)


class HTTPServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        release = root / "1.2.0"
        release.mkdir()
        self.payload = b"0123456789abcdef"
        manifest = manifest_for(self.payload)
        (release / "manifest.json").write_bytes(canonical_manifest_bytes(manifest))
        (release / "manifest.sig").write_bytes(b"signature")
        (release / manifest["artifact"]["url"]).write_bytes(self.payload)
        (root / "latest").write_text("1.2.0", encoding="utf-8")
        self.server = ReleaseHTTPServer(("127.0.0.1", 0), root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, method: str, path: str, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        body = response.read()
        result = response.status, dict(response.getheaders()), body
        connection.close()
        return result

    def test_get_head_range_etag_and_read_only(self):
        status, headers, body = self.request(
            "GET", "/stable/L2DConfigEditor-Setup-1.2.0-x64.exe"
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, self.payload)
        self.assertIn("ETag", headers)

        status, headers, body = self.request(
            "HEAD", "/stable/L2DConfigEditor-Setup-1.2.0-x64.exe"
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertEqual(int(headers["Content-Length"]), len(self.payload))

        status, headers, body = self.request(
            "GET",
            "/stable/L2DConfigEditor-Setup-1.2.0-x64.exe",
            {"Range": "bytes=4-7"},
        )
        self.assertEqual(status, 206)
        self.assertEqual(body, b"4567")
        self.assertEqual(headers["Content-Range"], "bytes 4-7/16")

        self.assertEqual(self.request("POST", "/stable/manifest.json")[0], 405)
        self.assertIn(
            self.request("GET", "/stable/%2e%2e/manifest.json")[0], {400, 404}
        )
        self.assertEqual(self.request("GET", "/not-allowed")[0], 404)

    def test_invalid_range_returns_416(self):
        status, headers, body = self.request(
            "GET",
            "/stable/L2DConfigEditor-Setup-1.2.0-x64.exe",
            {"Range": "bytes=100-200"},
        )
        self.assertEqual(status, 416)
        self.assertEqual(headers["Content-Range"], "bytes */16")
        self.assertEqual(body, b"")

    def test_artifact_etag_uses_signed_manifest_digest_without_rehashing_file(self):
        with patch(
            "l2d_config_editor.update_host.sha256_file",
            side_effect=AssertionError("large artifact must not be rehashed"),
        ):
            status, headers, body = self.request(
                "HEAD",
                "/stable/L2DConfigEditor-Setup-1.2.0-x64.exe",
            )
        self.assertEqual(200, status)
        self.assertEqual(b"", body)
        self.assertEqual(
            f'"{hashlib.sha256(self.payload).hexdigest()}"',
            headers["ETag"],
        )


class FirewallCommandTests(unittest.TestCase):
    def test_rules_cover_http_and_discovery_with_the_same_lan_scope(self):
        command = firewall_powershell_command(
            8765, r"C:\Program Files\L2DConfigEditor\L2DConfigEditor.exe"
        )
        self.assertIn("-Protocol TCP -LocalPort 8765", command)
        self.assertIn(
            f"-Protocol UDP -LocalPort {DISCOVERY_PORT}",
            command,
        )
        self.assertEqual(2, command.count("-Profile Private,Domain"))
        self.assertEqual(2, command.count("-RemoteAddress LocalSubnet"))
        self.assertEqual(
            2,
            command.count("-Program 'C:\\Program Files\\L2DConfigEditor"),
        )
        self.assertIn("L2D Update Host HTTP (LocalSubnet)", command)
        self.assertIn("L2D Update Host Discovery (LocalSubnet)", command)
        self.assertIn("}catch{", command)
        self.assertIn(
            "$newRules|Remove-NetFirewallRule -ErrorAction SilentlyContinue",
            command,
        )
        self.assertIn(
            "$existing|Remove-NetFirewallRule -ErrorAction SilentlyContinue",
            command,
        )
        # Existing working rules stay active until both replacements exist.
        first_create = command.index(
            "$newRules+=@(New-NetFirewallRule -DisplayName "
            "'L2D Update Host HTTP (LocalSubnet)'"
        )
        second_create = command.index(
            "$newRules+=@(New-NetFirewallRule -DisplayName "
            "'L2D Update Host Discovery (LocalSubnet)'"
        )
        failure_cleanup = command.index("}catch{")
        old_rule_cleanup = command.index(
            "if($existing.Count -gt 0){$existing|Remove-NetFirewallRule"
        )
        self.assertLess(first_create, second_create)
        self.assertLess(second_create, failure_cleanup)
        self.assertLess(failure_cleanup, old_rule_cleanup)
        self.assertNotIn("Public", command)

    def test_rejects_privileged_or_invalid_port(self):
        for port in (0, 80, 65536):
            with self.assertRaises(ValueError):
                firewall_powershell_command(port, "host.exe")


if __name__ == "__main__":
    unittest.main()
