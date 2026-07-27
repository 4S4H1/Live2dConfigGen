from __future__ import annotations

import base64
import hashlib
import http.client
import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from l2d_config_editor.update_host import (
    ReleaseHTTPServer,
    firewall_powershell_command,
)
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
    def test_rule_is_scoped_to_program_port_profiles_and_local_subnet(self):
        command = firewall_powershell_command(
            8765, r"C:\Program Files\L2DUpdateHost\L2DUpdateHost.exe"
        )
        self.assertIn("-LocalPort 8765", command)
        self.assertIn("-Profile Private,Domain", command)
        self.assertIn("-RemoteAddress LocalSubnet", command)
        self.assertIn("-Program 'C:\\Program Files\\L2DUpdateHost", command)
        self.assertIn("L2D Update Host (LocalSubnet)", command)
        self.assertNotIn("Public", command)
        self.assertNotIn("Any", command)

    def test_rejects_privileged_or_invalid_port(self):
        for port in (0, 80, 65536):
            with self.assertRaises(ValueError):
                firewall_powershell_command(port, "host.exe")


if __name__ == "__main__":
    unittest.main()
