from __future__ import annotations

import configparser
import os
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAKENSIS = ROOT / ".tools" / "nsis-3.12" / "makensis.exe"
LEGACY_HOST_GUID = "{B72C06D9-3E0B-4363-BE31-691845B77710}"


LEGACY_HOST_FIXTURE_NSI = r"""
Unicode True
RequestExecutionLevel user
SilentInstall silent
!include "FileFunc.nsh"

!ifndef OUTPUT_DIR
  !error "OUTPUT_DIR is required"
!endif
!ifndef DEFAULT_INSTALL_DIR
  !error "DEFAULT_INSTALL_DIR is required"
!endif
!ifndef LEGACY_PRODUCT_REG_KEY
  !error "LEGACY_PRODUCT_REG_KEY is required"
!endif
!ifndef LEGACY_UNINSTALL_REG_KEY
  !error "LEGACY_UNINSTALL_REG_KEY is required"
!endif
!ifndef LEGACY_START_MENU_SHORTCUT
  !error "LEGACY_START_MENU_SHORTCUT is required"
!endif
!ifndef LEGACY_DESKTOP_SHORTCUT
  !error "LEGACY_DESKTOP_SHORTCUT is required"
!endif
!ifndef LEGACY_STARTUP_SHORTCUT
  !error "LEGACY_STARTUP_SHORTCUT is required"
!endif
!ifndef LEGACY_TEST_METADATA
  !error "LEGACY_TEST_METADATA is required"
!endif

Name "Legacy L2D Update Host fixture"
OutFile "${OUTPUT_DIR}\LegacyHostFixture.exe"
InstallDir "${DEFAULT_INSTALL_DIR}"

Section
  SetOutPath "$INSTDIR"
  FileOpen $0 "$INSTDIR\L2DUpdateHost.exe" w
  FileWrite $0 "legacy host payload"
  FileClose $0
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteINIStr "${LEGACY_TEST_METADATA}" "legacy_product" "InstallDir" "$INSTDIR"
  WriteINIStr "${LEGACY_TEST_METADATA}" "legacy_product" "UpgradeCode" "${LEGACY_HOST_GUID}"
  WriteINIStr "${LEGACY_TEST_METADATA}" "legacy_product" "service_enabled" "true"
  WriteINIStr "${LEGACY_TEST_METADATA}" "host" "restore_at_login" "true"
  WriteINIStr "${LEGACY_TEST_METADATA}" "legacy_uninstall" "DisplayName" "L2D 局域网更新主机"
  WriteINIStr "${LEGACY_TEST_METADATA}" "legacy_uninstall" "Publisher" "4S4H1"
  WriteINIStr "${LEGACY_TEST_METADATA}" "legacy_uninstall" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  CreateDirectory "$INSTDIR\legacy-runtime"
  FileOpen $0 "$INSTDIR\legacy-runtime\stale.dll" w
  FileWrite $0 "legacy runtime"
  FileClose $0
  ${GetParent} "${LEGACY_START_MENU_SHORTCUT}" $0
  CreateDirectory "$0"
  CreateShortcut "${LEGACY_START_MENU_SHORTCUT}" "$INSTDIR\L2DUpdateHost.exe"
  ${GetParent} "${LEGACY_DESKTOP_SHORTCUT}" $0
  CreateDirectory "$0"
  CreateShortcut "${LEGACY_DESKTOP_SHORTCUT}" "$INSTDIR\L2DUpdateHost.exe"
  ${GetParent} "${LEGACY_STARTUP_SHORTCUT}" $0
  CreateDirectory "$0"
  CreateShortcut "${LEGACY_STARTUP_SHORTCUT}" "$INSTDIR\L2DUpdateHost.exe"
SectionEnd

Section "Uninstall"
  ReadINIStr $0 "${LEGACY_TEST_METADATA}" "legacy_product" "InstallDir"
  StrCmp $0 "" +2
  StrCpy $INSTDIR $0
  Delete "${LEGACY_START_MENU_SHORTCUT}"
  Delete "${LEGACY_DESKTOP_SHORTCUT}"
  Delete "${LEGACY_STARTUP_SHORTCUT}"
  RMDir /r "$INSTDIR"
  DeleteINISec "${LEGACY_TEST_METADATA}" "legacy_product"
  DeleteINISec "${LEGACY_TEST_METADATA}" "legacy_uninstall"
SectionEnd
"""


@unittest.skipUnless(
    os.name == "nt" and MAKENSIS.is_file(),
    "NSIS installer runtime tests require the locked Windows toolchain",
)
class InstallerRuntimeTests(unittest.TestCase):
    def _compile_nsis(
        self,
        script: Path,
        *,
        defines: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        command = [str(MAKENSIS)]
        command.extend(f"/D{key}={value}" for key, value in defines.items())
        command.append(str(script))
        return subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=60,
        )

    @staticmethod
    def _metadata_value_exists(
        metadata_path: Path,
        section: str,
        value_name: str,
    ) -> bool:
        parser = configparser.ConfigParser()
        parser.optionxform = str
        parser.read(metadata_path, encoding="mbcs")
        return parser.has_option(section, value_name)

    def _legacy_fixture_defines(
        self,
        root: Path,
        *,
        registry_root: str,
    ) -> dict[str, str]:
        shortcuts = root / "legacy-shortcuts"
        return {
            "OUTPUT_DIR": str(root),
            "DEFAULT_INSTALL_DIR": str(root / "legacy-default"),
            "LEGACY_PRODUCT_REG_KEY": f"{registry_root}\\L2DUpdateHost",
            "LEGACY_UNINSTALL_REG_KEY": (
                f"{registry_root}\\Uninstall\\L2DUpdateHost"
            ),
            "LEGACY_HOST_GUID": LEGACY_HOST_GUID,
            "LEGACY_START_MENU_SHORTCUT": str(
                shortcuts / "Programs" / "4S4H1" / "L2D Host.lnk"
            ),
            "LEGACY_DESKTOP_SHORTCUT": str(
                shortcuts / "Desktop" / "L2D Host.lnk"
            ),
            "LEGACY_STARTUP_SHORTCUT": str(
                shortcuts / "Startup" / "L2DUpdateHost.lnk"
            ),
            "LEGACY_TEST_METADATA": str(root / "legacy-install.ini"),
            "LEGACY_DEFAULT_INSTALL_DIR": str(root / "legacy-default"),
        }

    def _compile_editor_migration_fixture(
        self,
        root: Path,
        *,
        legacy_defines: dict[str, str],
        process_name: str,
        extra_defines: dict[str, str] | None = None,
    ) -> Path:
        source = root / "editor-source"
        output = root / "editor-output"
        source.mkdir(exist_ok=True)
        output.mkdir(exist_ok=True)
        (source / "payload.txt").write_text("integrated editor", encoding="utf-8")
        defines = {
            "INSTALLER_TEST_MODE": "1",
            "LEGACY_MIGRATION_TEST": "1",
            "LEGACY_HOST_PROCESS_NAME": process_name,
            "VERSION": "1.0.0",
            "SOURCE_DIR": str(source),
            "OUTPUT_DIR": str(output),
            **{
                key: value
                for key, value in legacy_defines.items()
                if key.startswith("LEGACY_")
            },
            **(extra_defines or {}),
        }
        compiled = self._compile_nsis(
            ROOT / "packaging" / "editor-installer.nsi",
            defines=defines,
        )
        self.assertEqual(0, compiled.returncode, compiled.stdout + compiled.stderr)
        installer = output / "L2DConfigEditor-Setup-1.0.0-x64.exe"
        self.assertTrue(installer.is_file())
        return installer

    def _compile_editor_test_installer(
        self,
        root: Path,
        *,
        extra_defines: dict[str, str] | None = None,
    ) -> Path:
        source = root / "editor-source"
        output = root / "editor-output"
        source.mkdir(exist_ok=True)
        output.mkdir(exist_ok=True)
        (source / "payload.txt").write_text("integrated editor", encoding="utf-8")
        compiled = self._compile_nsis(
            ROOT / "packaging" / "editor-installer.nsi",
            defines={
                "INSTALLER_TEST_MODE": "1",
                "VERSION": "1.0.0",
                "SOURCE_DIR": str(source),
                "OUTPUT_DIR": str(output),
                **(extra_defines or {}),
            },
        )
        self.assertEqual(0, compiled.returncode, compiled.stdout + compiled.stderr)
        installer = output / "L2DConfigEditor-Setup-1.0.0-x64.exe"
        self.assertTrue(installer.is_file())
        return installer

    def _assert_clean_install_and_upgrade(
        self,
        *,
        script_name: str,
        installer_name: str,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="l2d-installer-runtime-",
            dir=ROOT / "build",
        ) as directory:
            root = Path(directory)
            source = root / "source"
            output = root / "output"
            target = root / "installed"
            source.mkdir()
            output.mkdir()
            (source / "payload.txt").write_text("release payload", encoding="utf-8")

            command = [
                str(MAKENSIS),
                "/DINSTALLER_TEST_MODE=1",
                "/DVERSION=1.0.0",
                f"/DSOURCE_DIR={source}",
                f"/DOUTPUT_DIR={output}",
            ]
            command.append(str(ROOT / "packaging" / script_name))
            compiled = subprocess.run(
                command,
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=60,
            )
            self.assertEqual(
                0,
                compiled.returncode,
                compiled.stdout + compiled.stderr,
            )
            installer = output / installer_name
            self.assertTrue(installer.is_file())

            for pass_index in range(2):
                installed = subprocess.run(
                    [str(installer), "/S", f"/D={target}"],
                    cwd=root,
                    capture_output=True,
                    check=False,
                    timeout=60,
                )
                self.assertEqual(
                    0,
                    installed.returncode,
                    f"installer pass {pass_index + 1} failed",
                )
                self.assertEqual(
                    "release payload",
                    (target / "payload.txt").read_text(encoding="utf-8"),
                )
                self.assertTrue((target / "Uninstall.exe").is_file())
                self.assertEqual(
                    "L2DConfigEditor|{E12D3BB6-BC45-4CF0-88DE-3D1B7F228B48}",
                    (target / ".l2d-install-owner").read_text(
                        encoding="utf-8"
                    ),
                )
                self.assertEqual(
                    "L2DUpdateHost|{E12D3BB6-BC45-4CF0-88DE-3D1B7F228B48}",
                    (
                        Path(f"{target}.__host")
                        / ".l2d-install-owner"
                    ).read_text(encoding="utf-8"),
                )
                self.assertFalse(Path(f"{target}.__new").exists())
                self.assertFalse(Path(f"{target}.__old").exists())
                if pass_index == 0:
                    (target / "stale.txt").write_text("old", encoding="utf-8")

            self.assertEqual(
                "old",
                (target / "stale.txt").read_text(encoding="utf-8"),
                "unknown files must survive an upgrade outside the managed manifest",
            )

    def test_editor_installer_activates_staged_directory(self) -> None:
        self._assert_clean_install_and_upgrade(
            script_name="editor-installer.nsi",
            installer_name="L2DConfigEditor-Setup-1.0.0-x64.exe",
        )

    def test_installer_rejects_parent_child_program_roots(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="l2d-installer-overlap-",
            dir=ROOT / "build",
        ) as directory:
            root = Path(directory)
            target = root / "installed"
            host_target = target / "host"
            installer = self._compile_editor_test_installer(
                root,
                extra_defines={"HOST_INSTALL_DIR": str(host_target)},
            )

            blocked = subprocess.run(
                [str(installer), "/S", f"/D={target}"],
                cwd=root,
                capture_output=True,
                check=False,
                timeout=30,
            )

            self.assertEqual(69, blocked.returncode)
            self.assertFalse(target.exists())
            self.assertFalse(host_target.exists())

    def test_installer_rejects_an_overbroad_editor_root(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="l2d-installer-broad-",
            dir=ROOT / "build",
        ) as directory:
            root = Path(directory)
            installer = self._compile_editor_test_installer(root)
            broad_target = Path(tempfile.gettempdir()).resolve()

            blocked = subprocess.run(
                [str(installer), "/S", f"/D={broad_target}"],
                cwd=root,
                capture_output=True,
                check=False,
                timeout=30,
            )

            self.assertEqual(73, blocked.returncode)

            host_broad_installer = self._compile_editor_test_installer(
                root,
                extra_defines={
                    "HOST_INSTALL_DIR": str(broad_target),
                },
            )
            host_blocked = subprocess.run(
                [
                    str(host_broad_installer),
                    "/S",
                    f"/D={root / 'safe-editor-root'}",
                ],
                cwd=root,
                capture_output=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(73, host_blocked.returncode)
            self.assertFalse((root / "safe-editor-root").exists())

    def test_installer_rejects_unknown_editor_root_contents(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="l2d-installer-editor-owner-",
            dir=ROOT / "build",
        ) as directory:
            root = Path(directory)
            target = root / "installed"
            target.mkdir()
            unknown = target / "unrelated.dll"
            unknown.write_bytes(b"not owned")
            installer = self._compile_editor_test_installer(root)

            blocked = subprocess.run(
                [str(installer), "/S", f"/D={target}"],
                cwd=root,
                capture_output=True,
                check=False,
                timeout=30,
            )

            self.assertEqual(72, blocked.returncode)
            self.assertEqual(b"not owned", unknown.read_bytes())
            self.assertFalse(Path(f"{target}.__new").exists())

    def test_installer_rejects_unknown_host_root_contents(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="l2d-installer-host-owner-",
            dir=ROOT / "build",
        ) as directory:
            root = Path(directory)
            target = root / "installed"
            host_target = root / "host-installed"
            host_target.mkdir()
            unknown = host_target / "unrelated.dll"
            unknown.write_bytes(b"not owned")
            installer = self._compile_editor_test_installer(
                root,
                extra_defines={"HOST_INSTALL_DIR": str(host_target)},
            )

            blocked = subprocess.run(
                [str(installer), "/S", f"/D={target}"],
                cwd=root,
                capture_output=True,
                check=False,
                timeout=30,
            )

            self.assertEqual(74, blocked.returncode)
            self.assertEqual(b"not owned", unknown.read_bytes())
            self.assertFalse(target.exists())

    def test_uninstaller_rejects_a_tampered_companion_owner_marker(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="l2d-uninstaller-host-owner-",
            dir=ROOT / "build",
        ) as directory:
            root = Path(directory)
            target = root / "installed"
            installer = self._compile_editor_test_installer(root)
            installed = subprocess.run(
                [str(installer), "/S", f"/D={target}"],
                cwd=root,
                capture_output=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(0, installed.returncode)
            host_target = Path(f"{target}.__host")
            host_marker = host_target / ".l2d-install-owner"
            host_marker.write_text("another-product", encoding="utf-8")

            blocked = subprocess.run(
                [
                    str(target / "Uninstall.exe"),
                    "/S",
                    f"_?={target}",
                ],
                cwd=root,
                capture_output=True,
                check=False,
                timeout=30,
            )

            self.assertEqual(75, blocked.returncode)
            self.assertTrue((target / "payload.txt").is_file())
            self.assertTrue((host_target / "payload.txt").is_file())
            self.assertEqual(
                "another-product",
                host_marker.read_text(encoding="utf-8"),
            )

    def test_clean_install_host_activation_failure_isolated_before_cleanup(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="l2d-installer-clean-rollback-",
            dir=ROOT / "build",
        ) as directory:
            root = Path(directory)
            target = root / "installed"
            installer = self._compile_editor_test_installer(
                root,
                extra_defines={"FORCE_HOST_ACTIVATE_FAILURE": "1"},
            )

            failed = subprocess.run(
                [str(installer), "/S", f"/D={target}"],
                cwd=root,
                capture_output=True,
                check=False,
                timeout=30,
            )

            self.assertEqual(56, failed.returncode)
            self.assertFalse(target.exists())
            self.assertFalse(Path(f"{target}.__new").exists())
            self.assertFalse(Path(f"{target}.__old").exists())
            self.assertFalse(Path(f"{target}.__host").exists())
            self.assertFalse(Path(f"{target}.__host.__new").exists())

    def test_editor_upgrade_retries_a_transient_install_directory_cwd_lock(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="l2d-installer-cwd-retry-",
            dir=ROOT / "build",
        ) as directory:
            root = Path(directory)
            installer = self._compile_editor_test_installer(root)
            target = root / "installed"
            installed = subprocess.run(
                [str(installer), "/S", f"/D={target}"],
                cwd=root,
                check=False,
                timeout=30,
            )
            self.assertEqual(0, installed.returncode)

            holder = subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "Start-Sleep -Milliseconds 1200",
                ],
                cwd=target,
            )
            try:
                time.sleep(0.15)
                started = time.monotonic()
                upgraded = subprocess.run(
                    [str(installer), "/S", f"/D={target}"],
                    cwd=root,
                    check=False,
                    timeout=15,
                )
                elapsed = time.monotonic() - started
            finally:
                holder.wait(timeout=5)

            self.assertEqual(0, upgraded.returncode)
            self.assertGreaterEqual(elapsed, 0.75)
            # Include managed-manifest PowerShell startup time on slower
            # Windows hosts while retaining a bound below the 15 s timeout.
            self.assertLess(elapsed, 9.0)
            self.assertTrue((target / "payload.txt").is_file())
            self.assertFalse(Path(f"{target}.__old").exists())

    def test_editor_upgrade_keeps_the_old_install_on_a_permanent_cwd_lock(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="l2d-installer-cwd-blocked-",
            dir=ROOT / "build",
        ) as directory:
            root = Path(directory)
            installer = self._compile_editor_test_installer(root)
            target = root / "installed"
            installed = subprocess.run(
                [str(installer), "/S", f"/D={target}"],
                cwd=root,
                check=False,
                timeout=30,
            )
            self.assertEqual(0, installed.returncode)
            marker = target / "old-install.marker"
            marker.write_bytes(b"must survive")

            holder = subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "Start-Sleep -Seconds 10",
                ],
                cwd=target,
            )
            try:
                time.sleep(0.15)
                blocked = subprocess.run(
                    [str(installer), "/S", f"/D={target}"],
                    cwd=root,
                    check=False,
                    timeout=12,
                )
            finally:
                holder.terminate()
                holder.wait(timeout=5)

            self.assertEqual(51, blocked.returncode)
            self.assertEqual(b"must survive", marker.read_bytes())
            self.assertFalse(Path(f"{target}.__old").exists())
            self.assertFalse(Path(f"{target}.__new").exists())

    def test_host_activation_failure_rolls_back_both_program_roots(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="l2d-installer-dual-rollback-",
            dir=ROOT / "build",
        ) as directory:
            root = Path(directory)
            installer = self._compile_editor_test_installer(root)
            target = root / "installed"
            installed = subprocess.run(
                [str(installer), "/S", f"/D={target}"],
                cwd=root,
                check=False,
                timeout=30,
            )
            self.assertEqual(0, installed.returncode)
            host_target = Path(f"{target}.__host")
            editor_marker = target / "editor-old.marker"
            host_marker = host_target / "host-old.marker"
            editor_marker.write_bytes(b"editor old")
            host_marker.write_bytes(b"host old")

            failing_installer = self._compile_editor_test_installer(
                root,
                extra_defines={"FORCE_HOST_ACTIVATE_FAILURE": "1"},
            )
            failed = subprocess.run(
                [str(failing_installer), "/S", f"/D={target}"],
                cwd=root,
                check=False,
                timeout=30,
            )

            self.assertEqual(56, failed.returncode)
            self.assertEqual(b"editor old", editor_marker.read_bytes())
            self.assertEqual(b"host old", host_marker.read_bytes())
            self.assertFalse(Path(f"{target}.__old").exists())
            self.assertFalse(Path(f"{target}.__new").exists())
            self.assertFalse(Path(f"{host_target}.__old").exists())
            self.assertFalse(Path(f"{host_target}.__new").exists())

    def test_plain_installer_test_mode_never_touches_legacy_integration(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="l2d-installer-isolation-",
            dir=ROOT / "build",
        ) as directory:
            root = Path(directory)
            shortcut_paths = {
                "LEGACY_START_MENU_SHORTCUT": root / "legacy-menu.lnk",
                "LEGACY_DESKTOP_SHORTCUT": root / "legacy-desktop.lnk",
                "LEGACY_STARTUP_SHORTCUT": root / "legacy-startup.lnk",
            }
            for shortcut in shortcut_paths.values():
                shortcut.write_bytes(b"must remain outside migration tests")
            installer = self._compile_editor_test_installer(
                root,
                extra_defines={
                    name: str(path)
                    for name, path in shortcut_paths.items()
                },
            )

            installed = subprocess.run(
                [str(installer), "/S", f"/D={root / 'installed'}"],
                cwd=root,
                capture_output=True,
                check=False,
                timeout=60,
            )

            self.assertEqual(0, installed.returncode)
            for shortcut in shortcut_paths.values():
                self.assertEqual(
                    b"must remain outside migration tests",
                    shortcut.read_bytes(),
                )

    def test_directory_page_change_is_rechecked_before_any_files_are_removed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="l2d-installer-directory-page-",
            dir=ROOT / "build",
        ) as directory:
            root = Path(directory)
            initial_directory = root / "safe-on-init"
            selected_directory = root / "selected-after-on-init"
            stale_staging = Path(f"{selected_directory}.__new")
            selected_directory.mkdir()
            stale_staging.mkdir()
            selected_work_file = selected_directory / "project.json"
            staging_work_file = stale_staging / "reference.png"
            selected_work_file.write_bytes(b'{"must": "survive"}')
            staging_work_file.write_bytes(b"must also survive")
            installer = self._compile_editor_test_installer(
                root,
                extra_defines={
                    "TEST_DIRECTORY_SELECTED_INSTALL_DIR": str(
                        selected_directory
                    ),
                },
            )

            blocked = subprocess.run(
                [str(installer), "/S", f"/D={initial_directory}"],
                cwd=root,
                capture_output=True,
                check=False,
                timeout=60,
            )

            self.assertEqual(72, blocked.returncode)
            self.assertEqual(b'{"must": "survive"}', selected_work_file.read_bytes())
            self.assertEqual(b"must also survive", staging_work_file.read_bytes())
            self.assertFalse(initial_directory.exists())

    def test_editor_installer_reports_failed_rollback_separately(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="l2d-installer-rollback-",
            dir=ROOT / "build",
        ) as directory:
            root = Path(directory)
            source = root / "source"
            output = root / "output"
            target = root / "installed"
            source.mkdir()
            output.mkdir()
            target.mkdir()
            (source / "payload.txt").write_text("new", encoding="utf-8")
            (target / "payload.txt").write_text("old", encoding="utf-8")
            compiled = self._compile_nsis(
                ROOT / "packaging" / "editor-installer.nsi",
                defines={
                    "INSTALLER_TEST_MODE": "1",
                    "FORCE_EDITOR_ACTIVATE_FAILURE": "1",
                    "FORCE_EDITOR_ROLLBACK_FAILURE": "1",
                    "VERSION": "1.0.0",
                    "SOURCE_DIR": str(source),
                    "OUTPUT_DIR": str(output),
                },
            )
            self.assertEqual(
                0,
                compiled.returncode,
                compiled.stdout + compiled.stderr,
            )
            installer = output / "L2DConfigEditor-Setup-1.0.0-x64.exe"
            failed = subprocess.run(
                [str(installer), "/S", f"/D={target}"],
                cwd=root,
                capture_output=True,
                check=False,
                timeout=60,
            )
            self.assertEqual(53, failed.returncode)
            recovery_directory = Path(f"{target}.__old")
            self.assertEqual(
                "old",
                (recovery_directory / "payload.txt").read_text("utf-8"),
            )
            self.assertFalse(Path(f"{target}.__new").exists())

    def test_editor_upgrade_and_uninstall_preserve_work_files_in_install_dir(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="l2d-installer-work-files-",
            dir=ROOT / "build",
        ) as directory:
            root = Path(directory)
            installer = self._compile_editor_test_installer(root)
            target = root / "installed"
            installed = subprocess.run(
                [str(installer), "/S", f"/D={target}"],
                cwd=root,
                capture_output=True,
                check=False,
                timeout=60,
            )
            self.assertEqual(0, installed.returncode)
            project = target / "user-project"
            project.mkdir()
            work_files = {
                project / "user.json": b'{"user": true}',
                project / "nodes.csv": b"name,value\nidle,1\n",
                target / "reference.PNG": b"not really a png",
            }
            for path, payload in work_files.items():
                path.write_bytes(payload)

            upgraded = subprocess.run(
                [str(installer), "/S", f"/D={target}"],
                cwd=root,
                capture_output=True,
                check=False,
                timeout=60,
            )
            self.assertEqual(0, upgraded.returncode)
            for path, payload in work_files.items():
                self.assertEqual(payload, path.read_bytes())

            recovery_directory = Path(f"{target}.__old")
            recovery_directory.mkdir()
            recovery_work_file = recovery_directory / "recover.json"
            recovery_work_file.write_text('{"keep": true}', encoding="utf-8")

            uninstalled = subprocess.run(
                [
                    str(target / "Uninstall.exe"),
                    "/S",
                    f"_?={target}",
                ],
                cwd=root,
                capture_output=True,
                check=False,
                timeout=60,
            )
            self.assertEqual(0, uninstalled.returncode)
            self.assertFalse((target / "payload.txt").is_file())
            for path, payload in work_files.items():
                self.assertEqual(payload, path.read_bytes())
            self.assertEqual(
                '{"keep": true}',
                recovery_work_file.read_text("utf-8"),
            )

    def test_work_file_guard_skips_only_root_internal_directory(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="l2d-installer-internal-guard-",
            dir=ROOT / "build",
        ) as directory:
            root = Path(directory)
            installer = self._compile_editor_test_installer(root)
            target = root / "installed"
            installed = subprocess.run(
                [str(installer), "/S", f"/D={target}"],
                cwd=root,
                capture_output=True,
                check=False,
                timeout=60,
            )
            self.assertEqual(0, installed.returncode)
            installer_internal = target / "_internal"
            installer_internal.mkdir()
            (installer_internal / "runtime.json").write_text(
                '{"owned": true}',
                encoding="utf-8",
            )
            allowed = subprocess.run(
                [str(installer), "/S", f"/D={target}"],
                cwd=root,
                capture_output=True,
                check=False,
                timeout=60,
            )
            self.assertEqual(0, allowed.returncode)

            nested_internal = target / "workspace" / "_internal"
            nested_internal.mkdir(parents=True)
            protected = nested_internal / "user.json"
            protected.write_text('{"user": true}', encoding="utf-8")
            allowed_nested = subprocess.run(
                [str(installer), "/S", f"/D={target}"],
                cwd=root,
                capture_output=True,
                check=False,
                timeout=60,
            )
            self.assertEqual(0, allowed_nested.returncode)
            self.assertEqual('{"user": true}', protected.read_text("utf-8"))

    def test_editor_installer_restores_stale_recovery_before_work_file_guard(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="l2d-installer-stale-recovery-",
            dir=ROOT / "build",
        ) as directory:
            root = Path(directory)
            target = root / "installed"
            target.mkdir()
            (target / "payload.txt").write_text("old", encoding="utf-8")
            failing_installer = self._compile_editor_test_installer(
                root,
                extra_defines={
                    "FORCE_EDITOR_ACTIVATE_FAILURE": "1",
                    "FORCE_EDITOR_ROLLBACK_FAILURE": "1",
                },
            )
            failed = subprocess.run(
                [str(failing_installer), "/S", f"/D={target}"],
                cwd=root,
                capture_output=True,
                check=False,
                timeout=60,
            )
            self.assertEqual(53, failed.returncode)
            recovery_directory = Path(f"{target}.__old")
            protected_file = recovery_directory / "user.json"
            protected_file.write_text('{"recover": true}', encoding="utf-8")

            normal_installer = self._compile_editor_test_installer(root)
            retried = subprocess.run(
                [str(normal_installer), "/S", f"/D={target}"],
                cwd=root,
                capture_output=True,
                check=False,
                timeout=60,
            )
            self.assertEqual(0, retried.returncode)
            self.assertFalse(recovery_directory.exists())
            self.assertEqual(
                '{"recover": true}',
                (target / "user.json").read_text("utf-8"),
            )
            self.assertEqual(
                "integrated editor",
                (target / "payload.txt").read_text("utf-8"),
            )

    def test_editor_installer_preserves_ambiguous_stale_recovery(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="l2d-installer-stale-conflict-",
            dir=ROOT / "build",
        ) as directory:
            root = Path(directory)
            target = root / "installed"
            recovery_directory = Path(f"{target}.__old")
            target.mkdir()
            recovery_directory.mkdir()
            current_file = target / "current.txt"
            protected_file = recovery_directory / "user.csv"
            current_file.write_text("current", encoding="utf-8")
            protected_file.write_text("protected", encoding="utf-8")
            installer = self._compile_editor_test_installer(root)

            blocked = subprocess.run(
                [str(installer), "/S", f"/D={target}"],
                cwd=root,
                capture_output=True,
                check=False,
                timeout=60,
            )
            self.assertEqual(68, blocked.returncode)
            self.assertEqual("current", current_file.read_text("utf-8"))
            self.assertEqual("protected", protected_file.read_text("utf-8"))

    def test_editor_upgrade_removes_default_and_custom_legacy_host_installations(
        self,
    ) -> None:
        for installation_kind in ("default", "custom"):
            with self.subTest(installation_kind=installation_kind), (
                tempfile.TemporaryDirectory(
                    prefix=f"l2d-legacy-{installation_kind}-",
                    dir=ROOT / "build",
                )
            ) as directory:
                root = Path(directory)
                registry_root = (
                    "Software\\4S4H1\\L2DInstallerTests\\"
                    f"{uuid.uuid4().hex}"
                )
                legacy_defines = self._legacy_fixture_defines(
                    root,
                    registry_root=registry_root,
                )
                fixture_script = root / "legacy-host-fixture.nsi"
                fixture_script.write_text(
                    LEGACY_HOST_FIXTURE_NSI,
                    encoding="utf-8-sig",
                )
                compiled = self._compile_nsis(
                    fixture_script,
                    defines=legacy_defines,
                )
                self.assertEqual(
                    0,
                    compiled.returncode,
                    compiled.stdout + compiled.stderr,
                )
                legacy_installer = root / "LegacyHostFixture.exe"
                legacy_dir = (
                    root / "legacy-default"
                    if installation_kind == "default"
                    else root / "legacy-custom" / "nested"
                )
                install_command = [str(legacy_installer), "/S"]
                if installation_kind == "custom":
                    install_command.append(f"/D={legacy_dir}")
                installed = subprocess.run(
                    install_command,
                    cwd=root,
                    capture_output=True,
                    check=False,
                    timeout=60,
                )
                self.assertEqual(0, installed.returncode)

                cache = root / "data" / "4S4H1" / "L2DUpdateHost" / "releases"
                cache.mkdir(parents=True)
                cache_payload = cache / "1.0.0.l2dupdate"
                cache_payload.write_bytes(b"verified legacy release")
                for shortcut_key in (
                    "LEGACY_START_MENU_SHORTCUT",
                    "LEGACY_DESKTOP_SHORTCUT",
                    "LEGACY_STARTUP_SHORTCUT",
                ):
                    self.assertTrue(Path(legacy_defines[shortcut_key]).is_file())
                self.assertTrue((legacy_dir / "Uninstall.exe").is_file())

                editor_installer = self._compile_editor_migration_fixture(
                    root,
                    legacy_defines=legacy_defines,
                    process_name=f"L2DHostAbsent{uuid.uuid4().hex[:8]}",
                )
                editor_target = root / "editor-installed"
                migrated = subprocess.run(
                    [str(editor_installer), "/S", f"/D={editor_target}"],
                    cwd=root,
                    capture_output=True,
                    check=False,
                    timeout=60,
                )
                self.assertEqual(0, migrated.returncode)
                self.assertFalse(legacy_dir.exists())
                for shortcut_key in (
                    "LEGACY_START_MENU_SHORTCUT",
                    "LEGACY_DESKTOP_SHORTCUT",
                    "LEGACY_STARTUP_SHORTCUT",
                ):
                    self.assertFalse(Path(legacy_defines[shortcut_key]).exists())
                metadata_path = Path(legacy_defines["LEGACY_TEST_METADATA"])
                self.assertFalse(
                    self._metadata_value_exists(
                        metadata_path,
                        "legacy_product",
                        "InstallDir",
                    )
                )
                self.assertFalse(
                    self._metadata_value_exists(
                        metadata_path,
                        "legacy_product",
                        "UpgradeCode",
                    )
                )
                self.assertTrue(
                    self._metadata_value_exists(
                        metadata_path,
                        "legacy_product",
                        "service_enabled",
                    )
                )
                self.assertTrue(
                    self._metadata_value_exists(
                        metadata_path,
                        "host",
                        "restore_at_login",
                    )
                )
                self.assertFalse(
                    self._metadata_value_exists(
                        metadata_path,
                        "legacy_uninstall",
                        "UninstallString",
                    )
                )
                self.assertEqual(
                    b"verified legacy release",
                    cache_payload.read_bytes(),
                )

    def test_custom_legacy_host_migration_failure_restores_original_root(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="l2d-legacy-reversible-",
            dir=ROOT / "build",
        ) as directory:
            root = Path(directory)
            legacy_defines = self._legacy_fixture_defines(
                root,
                registry_root=(
                    "Software\\4S4H1\\L2DInstallerTests\\"
                    f"{uuid.uuid4().hex}"
                ),
            )
            fixture_script = root / "legacy-host-fixture.nsi"
            fixture_script.write_text(
                LEGACY_HOST_FIXTURE_NSI,
                encoding="utf-8-sig",
            )
            compiled = self._compile_nsis(
                fixture_script,
                defines=legacy_defines,
            )
            self.assertEqual(
                0,
                compiled.returncode,
                compiled.stdout + compiled.stderr,
            )
            legacy_dir = root / "legacy-custom" / "nested"
            installed = subprocess.run(
                [
                    str(root / "LegacyHostFixture.exe"),
                    "/S",
                    f"/D={legacy_dir}",
                ],
                cwd=root,
                capture_output=True,
                check=False,
                timeout=60,
            )
            self.assertEqual(0, installed.returncode)
            legacy_payload = (
                legacy_dir / "L2DUpdateHost.exe"
            ).read_bytes()
            editor_installer = self._compile_editor_migration_fixture(
                root,
                legacy_defines=legacy_defines,
                process_name=f"L2DHostAbsent{uuid.uuid4().hex[:8]}",
                extra_defines={"FORCE_LEGACY_MIGRATION_FAILURE": "1"},
            )
            editor_target = root / "editor-installed"

            failed = subprocess.run(
                [str(editor_installer), "/S", f"/D={editor_target}"],
                cwd=root,
                capture_output=True,
                check=False,
                timeout=60,
            )

            self.assertEqual(64, failed.returncode)
            self.assertEqual(
                legacy_payload,
                (legacy_dir / "L2DUpdateHost.exe").read_bytes(),
            )
            self.assertTrue((legacy_dir / "Uninstall.exe").is_file())
            self.assertFalse(
                Path(f"{legacy_dir}.__l2d_legacy_old").exists()
            )
            self.assertFalse(editor_target.exists())
            self.assertFalse(Path(f"{editor_target}.__host").exists())

    def test_editor_upgrade_removes_orphaned_default_legacy_host(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="l2d-legacy-orphan-",
            dir=ROOT / "build",
        ) as directory:
            root = Path(directory)
            registry_root = (
                "Software\\4S4H1\\L2DInstallerTests\\"
                f"{uuid.uuid4().hex}"
            )
            legacy_defines = self._legacy_fixture_defines(
                root,
                registry_root=registry_root,
            )
            fixture_script = root / "legacy-host-fixture.nsi"
            fixture_script.write_text(
                LEGACY_HOST_FIXTURE_NSI,
                encoding="utf-8-sig",
            )
            compiled = self._compile_nsis(
                fixture_script,
                defines=legacy_defines,
            )
            self.assertEqual(
                0,
                compiled.returncode,
                compiled.stdout + compiled.stderr,
            )
            legacy_dir = Path(legacy_defines["LEGACY_DEFAULT_INSTALL_DIR"])
            installed = subprocess.run(
                [str(root / "LegacyHostFixture.exe"), "/S"],
                cwd=root,
                capture_output=True,
                check=False,
                timeout=60,
            )
            self.assertEqual(0, installed.returncode)
            Path(legacy_defines["LEGACY_TEST_METADATA"]).unlink()
            cache_file = (
                root
                / "data"
                / "4S4H1"
                / "L2DUpdateHost"
                / "releases"
                / "verified.bundle"
            )
            cache_file.parent.mkdir(parents=True)
            cache_file.write_bytes(b"keep")

            editor_installer = self._compile_editor_migration_fixture(
                root,
                legacy_defines=legacy_defines,
                process_name=f"L2DHostAbsent{uuid.uuid4().hex[:8]}",
            )
            migrated = subprocess.run(
                [
                    str(editor_installer),
                    "/S",
                    f"/D={root / 'editor-installed'}",
                ],
                cwd=root,
                capture_output=True,
                check=False,
                timeout=60,
            )
            self.assertEqual(0, migrated.returncode)
            self.assertFalse(legacy_dir.exists())
            for shortcut_key in (
                "LEGACY_START_MENU_SHORTCUT",
                "LEGACY_DESKTOP_SHORTCUT",
                "LEGACY_STARTUP_SHORTCUT",
            ):
                self.assertFalse(Path(legacy_defines[shortcut_key]).exists())
            self.assertEqual(b"keep", cache_file.read_bytes())

    def test_directory_page_cannot_select_orphaned_legacy_host_directory(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="l2d-legacy-orphan-conflict-",
            dir=ROOT / "build",
        ) as directory:
            root = Path(directory)
            legacy_defines = self._legacy_fixture_defines(
                root,
                registry_root=(
                    "Software\\4S4H1\\L2DInstallerTests\\"
                    f"{uuid.uuid4().hex}"
                ),
            )
            fixture_script = root / "legacy-host-fixture.nsi"
            fixture_script.write_text(
                LEGACY_HOST_FIXTURE_NSI,
                encoding="utf-8-sig",
            )
            compiled = self._compile_nsis(
                fixture_script,
                defines=legacy_defines,
            )
            self.assertEqual(
                0,
                compiled.returncode,
                compiled.stdout + compiled.stderr,
            )
            legacy_directory = Path(
                legacy_defines["LEGACY_DEFAULT_INSTALL_DIR"]
            )
            installed = subprocess.run(
                [str(root / "LegacyHostFixture.exe"), "/S"],
                cwd=root,
                capture_output=True,
                check=False,
                timeout=60,
            )
            self.assertEqual(0, installed.returncode)
            Path(legacy_defines["LEGACY_TEST_METADATA"]).unlink()
            original_host = (
                legacy_directory / "L2DUpdateHost.exe"
            ).read_bytes()
            editor_installer = self._compile_editor_migration_fixture(
                root,
                legacy_defines=legacy_defines,
                process_name=f"L2DHostAbsent{uuid.uuid4().hex[:8]}",
                extra_defines={
                    "TEST_DIRECTORY_SELECTED_INSTALL_DIR": str(
                        legacy_directory
                    ),
                },
            )

            blocked = subprocess.run(
                [
                    str(editor_installer),
                    "/S",
                    f"/D={root / 'safe-on-init'}",
                ],
                cwd=root,
                capture_output=True,
                check=False,
                timeout=60,
            )

            self.assertEqual(72, blocked.returncode)
            self.assertEqual(
                original_host,
                (legacy_directory / "L2DUpdateHost.exe").read_bytes(),
            )
            self.assertTrue((legacy_directory / "Uninstall.exe").is_file())
            self.assertFalse(
                Path(f"{legacy_directory}.__new").exists()
            )
            self.assertFalse(
                Path(f"{legacy_directory}.__old").exists()
            )

    def test_editor_install_removes_dead_legacy_host_shortcuts(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="l2d-legacy-shortcuts-",
            dir=ROOT / "build",
        ) as directory:
            root = Path(directory)
            legacy_defines = self._legacy_fixture_defines(
                root,
                registry_root=(
                    "Software\\4S4H1\\L2DInstallerTests\\"
                    f"{uuid.uuid4().hex}"
                ),
            )
            shortcut_paths: list[Path] = []
            for shortcut_key in (
                "LEGACY_START_MENU_SHORTCUT",
                "LEGACY_DESKTOP_SHORTCUT",
                "LEGACY_STARTUP_SHORTCUT",
            ):
                shortcut = Path(legacy_defines[shortcut_key])
                shortcut.parent.mkdir(parents=True, exist_ok=True)
                shortcut.write_bytes(b"dead shortcut")
                shortcut_paths.append(shortcut)
            editor_installer = self._compile_editor_migration_fixture(
                root,
                legacy_defines=legacy_defines,
                process_name=f"L2DHostAbsent{uuid.uuid4().hex[:8]}",
            )

            installed = subprocess.run(
                [
                    str(editor_installer),
                    "/S",
                    f"/D={root / 'editor-installed'}",
                ],
                cwd=root,
                capture_output=True,
                check=False,
                timeout=60,
            )
            self.assertEqual(0, installed.returncode)
            for shortcut in shortcut_paths:
                self.assertFalse(shortcut.exists())

    def test_editor_upgrade_aborts_while_legacy_host_is_running(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="l2d-legacy-running-",
            dir=ROOT / "build",
        ) as directory:
            root = Path(directory)
            registry_root = (
                "Software\\4S4H1\\L2DInstallerTests\\"
                f"{uuid.uuid4().hex}"
            )
            legacy_defines = self._legacy_fixture_defines(
                root,
                registry_root=registry_root,
            )
            fixture_script = root / "legacy-host-fixture.nsi"
            fixture_script.write_text(
                LEGACY_HOST_FIXTURE_NSI,
                encoding="utf-8-sig",
            )
            process_name = f"L2DHost{uuid.uuid4().hex[:8]}"
            sleeper_executable = root / f"{process_name}.exe"
            sleeper: subprocess.Popen[bytes] | None = None
            try:
                compiled = self._compile_nsis(
                    fixture_script,
                    defines=legacy_defines,
                )
                self.assertEqual(
                    0,
                    compiled.returncode,
                    compiled.stdout + compiled.stderr,
                )
                legacy_installer = root / "LegacyHostFixture.exe"
                legacy_dir = root / "legacy-default"
                installed = subprocess.run(
                    [str(legacy_installer), "/S"],
                    cwd=root,
                    capture_output=True,
                    check=False,
                    timeout=60,
                )
                self.assertEqual(0, installed.returncode)

                editor_installer = self._compile_editor_migration_fixture(
                    root,
                    legacy_defines=legacy_defines,
                    process_name=process_name,
                )
                shutil.copy2(
                    Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe",
                    sleeper_executable,
                )
                sleeper = subprocess.Popen(
                    [
                        str(sleeper_executable),
                        "/d",
                        "/q",
                        "/k",
                    ],
                    cwd=root,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                editor_target = root / "editor-installed"
                blocked = subprocess.run(
                    [str(editor_installer), "/S", f"/D={editor_target}"],
                    cwd=root,
                    capture_output=True,
                    check=False,
                    timeout=60,
                )
                self.assertNotEqual(0, blocked.returncode)
                self.assertFalse(editor_target.exists())
                self.assertTrue((legacy_dir / "L2DUpdateHost.exe").is_file())
                self.assertTrue(
                    self._metadata_value_exists(
                        Path(legacy_defines["LEGACY_TEST_METADATA"]),
                        "legacy_product",
                        "InstallDir",
                    )
                )
            finally:
                if sleeper is not None:
                    if sleeper.stdin is not None:
                        sleeper.stdin.close()
                    if sleeper.poll() is None:
                        sleeper.terminate()
                    sleeper.wait(timeout=10)

    def test_editor_upgrade_never_recursively_deletes_untrusted_registry_path(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="l2d-legacy-untrusted-",
            dir=ROOT / "build",
        ) as directory:
            root = Path(directory)
            registry_root = (
                "Software\\4S4H1\\L2DInstallerTests\\"
                f"{uuid.uuid4().hex}"
            )
            legacy_defines = self._legacy_fixture_defines(
                root,
                registry_root=registry_root,
            )
            guard = root / "must-survive"
            guard.mkdir()
            sentinel = guard / "user-data.txt"
            sentinel.write_text("do not delete", encoding="utf-8")
            metadata = configparser.ConfigParser()
            metadata.optionxform = str
            metadata["legacy_product"] = {
                "InstallDir": str(guard),
                "UpgradeCode": LEGACY_HOST_GUID,
            }
            metadata["legacy_uninstall"] = {
                "DisplayName": "L2D Update Host",
                "Publisher": "4S4H1",
                "UninstallString": f'"{guard / "Uninstall.exe"}"',
            }
            with Path(legacy_defines["LEGACY_TEST_METADATA"]).open(
                "w",
                encoding="utf-8",
            ) as metadata_file:
                metadata.write(metadata_file)

            editor_installer = self._compile_editor_migration_fixture(
                root,
                legacy_defines=legacy_defines,
                process_name=f"L2DHostAbsent{uuid.uuid4().hex[:8]}",
            )
            attempted = subprocess.run(
                [
                    str(editor_installer),
                    "/S",
                    f"/D={root / 'editor-installed'}",
                ],
                cwd=root,
                capture_output=True,
                check=False,
                timeout=60,
            )
            self.assertEqual(0, attempted.returncode)
            self.assertEqual("do not delete", sentinel.read_text("utf-8"))

if __name__ == "__main__":
    unittest.main()
