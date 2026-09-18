from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "packaging" / "managed-files.ps1"
MANIFEST = ".l2d-managed-files.txt"


@unittest.skipUnless(os.name == "nt", "installer helper requires Windows PowerShell")
class ManagedFilesEncodingTests(unittest.TestCase):
    def run_helper(self, mode: str, **values: str) -> None:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(HELPER), "-Mode", mode],
            env={**os.environ, "L2D_MANIFEST_NAME": MANIFEST, **values},
            capture_output=True,
            timeout=15,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_uninstall_removes_unicode_managed_files_and_preserves_user_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            managed = root / "程序资源.txt"
            managed.write_text("managed", encoding="utf-8")
            self.run_helper("WriteManifest", L2D_MANIFEST_ROOT=str(root))
            user_file = root / "用户配置.json"
            user_file.write_text("{}", encoding="utf-8")

            self.run_helper("DeleteManaged", L2D_DELETE_ROOT=str(root))

            self.assertFalse(managed.exists())
            self.assertEqual("{}", user_file.read_text(encoding="utf-8"))

    def test_upgrade_does_not_misclassify_unicode_program_files_as_user_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source, target = root / "old", root / "new"
            source.mkdir()
            target.mkdir()
            (source / "程序资源.txt").write_text("old", encoding="utf-8")
            self.run_helper("WriteManifest", L2D_MANIFEST_ROOT=str(source))
            (source / "用户配置.json").write_text("{}", encoding="utf-8")
            (target / "程序资源.txt").write_text("new", encoding="utf-8")

            self.run_helper(
                "MergeUnknown", L2D_MERGE_SOURCE=str(source), L2D_MERGE_TARGET=str(target),
                L2D_PRESERVE_SUFFIX=".user-preserved",
            )

            self.assertFalse(source.exists())
            self.assertEqual({"程序资源.txt", "用户配置.json"}, {path.name for path in target.iterdir()})
            self.assertEqual("new", (target / "程序资源.txt").read_text(encoding="utf-8"))
            self.assertEqual("{}", (target / "用户配置.json").read_text(encoding="utf-8"))
