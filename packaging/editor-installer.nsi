Unicode True
RequestExecutionLevel user
SetCompressor /SOLID lzma

!include "MUI2.nsh"
!include "FileFunc.nsh"

!ifndef VERSION
  !error "VERSION define is required"
!endif
!ifndef SOURCE_DIR
  !error "SOURCE_DIR define is required"
!endif
!ifndef HOST_SOURCE_DIR
  ; Installer runtime fixtures may reuse one synthetic source tree.
  !define HOST_SOURCE_DIR "${SOURCE_DIR}"
!endif
!ifndef OUTPUT_DIR
  !error "OUTPUT_DIR define is required"
!endif

!ifndef LEGACY_HOST_GUID
  !define LEGACY_HOST_GUID "{B72C06D9-3E0B-4363-BE31-691845B77710}"
!endif
!ifndef LEGACY_HOST_PROCESS_NAME
  !define LEGACY_HOST_PROCESS_NAME "L2DUpdateHost"
!endif
!ifndef LEGACY_DEFAULT_INSTALL_DIR
  !define LEGACY_DEFAULT_INSTALL_DIR "$LOCALAPPDATA\Programs\L2DUpdateHost"
!endif
!ifndef HOST_INSTALL_DIR
  !ifdef INSTALLER_TEST_MODE
    !define HOST_INSTALL_DIR "$INSTDIR.__host"
  !else
    !define HOST_INSTALL_DIR "$LOCALAPPDATA\Programs\L2DUpdateHost"
  !endif
!endif
!ifndef LEGACY_PRODUCT_REG_KEY
  !define LEGACY_PRODUCT_REG_KEY "Software\4S4H1\L2DUpdateHost"
!endif
!ifndef LEGACY_UNINSTALL_REG_KEY
  !define LEGACY_UNINSTALL_REG_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\L2DUpdateHost"
!endif
!ifndef LEGACY_START_MENU_SHORTCUT
  !define LEGACY_START_MENU_SHORTCUT "$SMPROGRAMS\4S4H1\L2D 局域网更新主机.lnk"
!endif
!ifndef LEGACY_DESKTOP_SHORTCUT
  !define LEGACY_DESKTOP_SHORTCUT "$DESKTOP\L2D 局域网更新主机.lnk"
!endif
!ifndef LEGACY_STARTUP_SHORTCUT
  !define LEGACY_STARTUP_SHORTCUT "$SMSTARTUP\L2DUpdateHost.lnk"
!endif

Name "L2D 交互图表编辑器"
!define PRODUCT_GUID "{E12D3BB6-BC45-4CF0-88DE-3D1B7F228B48}"
!define OWNER_MARKER_NAME ".l2d-install-owner"
!define MANAGED_MANIFEST_NAME ".l2d-managed-files.txt"
!define MANAGED_SCRIPT_NAME ".l2d-managed-files.ps1"
!define EDITOR_OWNER_VALUE "L2DConfigEditor|${PRODUCT_GUID}"
!define HOST_OWNER_VALUE "L2DUpdateHost|${PRODUCT_GUID}"
OutFile "${OUTPUT_DIR}\L2DConfigEditor-Setup-${VERSION}-x64.exe"
InstallDir "$LOCALAPPDATA\Programs\L2DConfigEditor"
InstallDirRegKey HKCU "Software\4S4H1\L2DConfigEditor" "InstallDir"
VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=2052 "ProductName" "L2D 交互图表编辑器"
VIAddVersionKey /LANG=2052 "CompanyName" "4S4H1"
VIAddVersionKey /LANG=2052 "FileDescription" "L2D 交互图表编辑器安装程序"
VIAddVersionKey /LANG=2052 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=2052 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=2052 "LegalCopyright" "Copyright 4S4H1"
VIAddVersionKey /LANG=2052 "OriginalFilename" "L2DConfigEditor-Setup-${VERSION}-x64.exe"

!define MUI_ABORTWARNING
!ifndef INSTALLER_TEST_MODE
  !define MUI_ICON "..\build\icons\L2DConfigEditor.ico"
  !define MUI_UNICON "..\build\icons\L2DConfigEditor.ico"
!endif
!define MUI_STARTMENUPAGE_REGISTRY_ROOT "HKCU"
!define MUI_STARTMENUPAGE_REGISTRY_KEY "Software\4S4H1\L2DConfigEditor"
!define MUI_STARTMENUPAGE_REGISTRY_VALUENAME "StartMenuFolder"

Var StartMenuFolder
Var EditorHadOld
Var HostHadOld
Var HostInstallDir
Var RenameRetryCount
Var LegacyHostDetected
Var LegacyHostPreflightStatus
Var LegacyHostMigrationStatus
Var LegacyHostInstallDir
Var LegacyHostUpgradeCode
Var LegacyHostDisplayName
Var LegacyHostPublisher
Var LegacyHostUninstallString
Var LegacyHostExpectedUninstallString
Var OwnershipStatus
Var ManagedSource
Var ManagedTarget
Var LegacyHostQuarantineDir
Var LegacyHostWasQuarantined

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_STARTMENU Application $StartMenuFolder
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

; Windows may transiently retain handles while antivirus/indexing catches up.
; Every directory activation rename gets a bounded five-second retry window.
!macro RetryRename SOURCE TARGET PREFIX
  StrCpy $RenameRetryCount "0"
${PREFIX}_retry:
  ClearErrors
  Rename "${SOURCE}" "${TARGET}"
  IfErrors 0 ${PREFIX}_success
  IntOp $RenameRetryCount $RenameRetryCount + 1
  IntCmp $RenameRetryCount 20 ${PREFIX}_failed ${PREFIX}_sleep ${PREFIX}_failed
${PREFIX}_sleep:
  Sleep 250
  Goto ${PREFIX}_retry
${PREFIX}_failed:
  SetErrors
  Goto ${PREFIX}_done
${PREFIX}_success:
  ClearErrors
${PREFIX}_done:
!macroend

!macro DefineEditorOwnershipValidator NAME ROOT
Function ${NAME}
  StrCpy $OwnershipStatus "ok"
  IfFileExists "${ROOT}\*.*" 0 ${NAME}_done
  IfFileExists "${ROOT}\${OWNER_MARKER_NAME}" 0 ${NAME}_marker_missing
  ClearErrors
  FileOpen $0 "${ROOT}\${OWNER_MARKER_NAME}" r
  IfErrors ${NAME}_unknown
  FileRead $0 $1
  FileClose $0
  StrCmp $1 "${EDITOR_OWNER_VALUE}" ${NAME}_done ${NAME}_unknown
${NAME}_marker_missing:
!ifdef INSTALLER_TEST_MODE
  ; Runtime fixtures model the last marker-less release with payload.txt.
  IfFileExists "${ROOT}\payload.txt" ${NAME}_done ${NAME}_unknown
!else
  ; Compatibility with a legitimate pre-marker editor install requires both
  ; its registered product identity and its expected executable.
  ReadRegStr $0 HKCU "Software\4S4H1\L2DConfigEditor" "InstallDir"
  StrCmp $0 $INSTDIR 0 ${NAME}_unknown
  ReadRegStr $0 HKCU "Software\4S4H1\L2DConfigEditor" "UpgradeCode"
  StrCmp $0 "${PRODUCT_GUID}" 0 ${NAME}_unknown
  IfFileExists "${ROOT}\L2DConfigEditor.exe" ${NAME}_done ${NAME}_unknown
!endif
${NAME}_unknown:
  StrCpy $OwnershipStatus "unknown"
${NAME}_done:
FunctionEnd
!macroend

!macro DefineHostOwnershipValidator NAME ROOT ALLOW_LEGACY
Function ${NAME}
  StrCpy $OwnershipStatus "ok"
  IfFileExists "${ROOT}\*.*" 0 ${NAME}_done
  IfFileExists "${ROOT}\${OWNER_MARKER_NAME}" 0 ${NAME}_marker_missing
  ClearErrors
  FileOpen $0 "${ROOT}\${OWNER_MARKER_NAME}" r
  IfErrors ${NAME}_unknown
  FileRead $0 $1
  FileClose $0
  StrCmp $1 "${HOST_OWNER_VALUE}" ${NAME}_done ${NAME}_unknown
${NAME}_marker_missing:
!ifdef INSTALLER_TEST_MODE
  IfFileExists "${ROOT}\payload.txt" ${NAME}_done 0
!endif
  ; The validated legacy default installation is allowed to become the new
  ; companion root in-place. Arbitrary executable-looking directories are not.
  StrCmp "${ALLOW_LEGACY}" "1" 0 ${NAME}_registered_companion
  StrCmp $LegacyHostPreflightStatus "ready" 0 ${NAME}_registered_companion
  StrCmp $LegacyHostInstallDir $HostInstallDir ${NAME}_done
${NAME}_registered_companion:
!ifndef INSTALLER_TEST_MODE
  ReadRegStr $0 HKCU "Software\4S4H1\L2DConfigEditor" "HostInstallDir"
  StrCmp $0 $HostInstallDir 0 ${NAME}_unknown
  ReadRegStr $0 HKCU "Software\4S4H1\L2DConfigEditor" "UpgradeCode"
  StrCmp $0 "${PRODUCT_GUID}" 0 ${NAME}_unknown
  IfFileExists "${ROOT}\L2DUpdateHost.exe" ${NAME}_done ${NAME}_unknown
!else
  Goto ${NAME}_unknown
!endif
${NAME}_unknown:
  StrCpy $OwnershipStatus "unknown"
${NAME}_done:
FunctionEnd
!macroend

!insertmacro DefineEditorOwnershipValidator ValidateEditorOwnership "$INSTDIR"
!insertmacro DefineEditorOwnershipValidator ValidateEditorRecoveryOwnership "$INSTDIR.__old"
!insertmacro DefineEditorOwnershipValidator ValidateEditorStagingOwnership "$INSTDIR.__new"
!insertmacro DefineHostOwnershipValidator ValidateHostOwnership "$HostInstallDir" "1"
!insertmacro DefineHostOwnershipValidator ValidateHostRecoveryOwnership "$HostInstallDir.__old" "0"
!insertmacro DefineHostOwnershipValidator ValidateHostStagingOwnership "$HostInstallDir.__new" "0"

; Installation manifests are written from the freshly staged payload. Upgrade
; and uninstall delete only paths listed in that manifest. Everything else is
; user-owned and is moved back into the live root (or simply left in place).
!macro DefineManagedPayloadHelpers PREFIX SCRIPT
Function ${PREFIX}WriteManagedManifest
  Exch $R0
  System::Call 'kernel32::SetEnvironmentVariableW(w "L2D_MANIFEST_ROOT", w "$R0") i.r1'
  System::Call 'kernel32::SetEnvironmentVariableW(w "L2D_MANIFEST_NAME", w "${MANAGED_MANIFEST_NAME}") i.r1'
  nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "${SCRIPT}" -Mode WriteManifest'
  Pop $R1
  Pop $R2
  System::Call 'kernel32::SetEnvironmentVariableW(w "L2D_MANIFEST_ROOT", w "") i.r3'
  System::Call 'kernel32::SetEnvironmentVariableW(w "L2D_MANIFEST_NAME", w "") i.r3'
  StrCmp $R1 "0" 0 managed_manifest_failed
  ClearErrors
  Goto managed_manifest_done
managed_manifest_failed:
  SetErrors
managed_manifest_done:
  Exch $R0
FunctionEnd

Function ${PREFIX}MergeUnknownFiles
  System::Call 'kernel32::SetEnvironmentVariableW(w "L2D_MERGE_SOURCE", w "$ManagedSource") i.r2'
  System::Call 'kernel32::SetEnvironmentVariableW(w "L2D_MERGE_TARGET", w "$ManagedTarget") i.r2'
  System::Call 'kernel32::SetEnvironmentVariableW(w "L2D_MANIFEST_NAME", w "${MANAGED_MANIFEST_NAME}") i.r2'
  System::Call 'kernel32::SetEnvironmentVariableW(w "L2D_KNOWN_FILES", w "${OWNER_MARKER_NAME}|Uninstall.exe|L2DConfigEditor.exe|L2DUpdateHost.exe") i.r2'
  System::Call 'kernel32::SetEnvironmentVariableW(w "L2D_PRESERVE_SUFFIX", w ".user-preserved") i.r2'
  nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "${SCRIPT}" -Mode MergeUnknown'
  Pop $R2
  Pop $R3
  System::Call 'kernel32::SetEnvironmentVariableW(w "L2D_MERGE_SOURCE", w "") i.r4'
  System::Call 'kernel32::SetEnvironmentVariableW(w "L2D_MERGE_TARGET", w "") i.r4'
  System::Call 'kernel32::SetEnvironmentVariableW(w "L2D_MANIFEST_NAME", w "") i.r4'
  System::Call 'kernel32::SetEnvironmentVariableW(w "L2D_KNOWN_FILES", w "") i.r4'
  System::Call 'kernel32::SetEnvironmentVariableW(w "L2D_PRESERVE_SUFFIX", w "") i.r4'
  StrCmp $R2 "0" 0 merge_unknown_failed
  ClearErrors
  Goto merge_unknown_done
merge_unknown_failed:
  SetErrors
merge_unknown_done:
FunctionEnd

Function ${PREFIX}DeleteManagedFiles
  Exch $R0
  System::Call 'kernel32::SetEnvironmentVariableW(w "L2D_DELETE_ROOT", w "$R0") i.r1'
  System::Call 'kernel32::SetEnvironmentVariableW(w "L2D_MANIFEST_NAME", w "${MANAGED_MANIFEST_NAME}") i.r1'
  System::Call 'kernel32::SetEnvironmentVariableW(w "L2D_KNOWN_FILES", w "${OWNER_MARKER_NAME}|Uninstall.exe|L2DConfigEditor.exe|L2DUpdateHost.exe") i.r1'
  nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "${SCRIPT}" -Mode DeleteManaged'
  Pop $R1
  Pop $R2
  System::Call 'kernel32::SetEnvironmentVariableW(w "L2D_DELETE_ROOT", w "") i.r3'
  System::Call 'kernel32::SetEnvironmentVariableW(w "L2D_MANIFEST_NAME", w "") i.r3'
  System::Call 'kernel32::SetEnvironmentVariableW(w "L2D_KNOWN_FILES", w "") i.r3'
  Exch $R0
FunctionEnd
!macroend

!insertmacro DefineManagedPayloadHelpers "" "$PLUGINSDIR\l2d-managed-files.ps1"
!insertmacro DefineManagedPayloadHelpers "un." "$INSTDIR\${MANAGED_SCRIPT_NAME}"

Function ValidateInstallRootRelationship
  ; Pass paths through inherited environment variables so even apostrophes and
  ; other shell metacharacters never become PowerShell source text.
  System::Call 'kernel32::SetEnvironmentVariableW(w "L2D_EDITOR_INSTALL_ROOT", w "$INSTDIR") i.r0'
  System::Call 'kernel32::SetEnvironmentVariableW(w "L2D_HOST_INSTALL_ROOT", w "$HostInstallDir") i.r0'
  nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -Command "$$cmp=[StringComparison]::OrdinalIgnoreCase; $$a=[IO.Path]::GetFullPath($$env:L2D_EDITOR_INSTALL_ROOT).TrimEnd([char]92); $$b=[IO.Path]::GetFullPath($$env:L2D_HOST_INSTALL_ROOT).TrimEnd([char]92); if([string]::IsNullOrWhiteSpace($$a) -or [string]::IsNullOrWhiteSpace($$b)){exit 73}; if($$a.Equals($$b,$$cmp) -or $$a.StartsWith($$b+[char]92,$$cmp) -or $$b.StartsWith($$a+[char]92,$$cmp)){exit 69}; $$broad=@([IO.Path]::GetPathRoot($$a),[IO.Path]::GetPathRoot($$b),[Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile),[Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData),[Environment]::GetFolderPath([Environment+SpecialFolder]::ApplicationData),[IO.Path]::GetTempPath()); foreach($$candidate in $$broad){if(-not [string]::IsNullOrWhiteSpace($$candidate)){ $$candidate=$$candidate.TrimEnd([char]92); if($$a.Equals($$candidate,$$cmp) -or $$b.Equals($$candidate,$$cmp)){exit 73}}}; exit 0"'
  Pop $0
  Pop $1
  System::Call 'kernel32::SetEnvironmentVariableW(w "L2D_EDITOR_INSTALL_ROOT", w "") i.r1'
  System::Call 'kernel32::SetEnvironmentVariableW(w "L2D_HOST_INSTALL_ROOT", w "") i.r1'
  StrCmp $0 "0" install_roots_valid
  StrCmp $0 "69" install_roots_overlap
  StrCpy $OwnershipStatus "broad"
  Return
install_roots_overlap:
  StrCpy $OwnershipStatus "overlap"
  Return
install_roots_valid:
  StrCpy $OwnershipStatus "ok"
FunctionEnd

Function PreflightLegacyHost
  StrCpy $LegacyHostDetected "0"
  StrCpy $LegacyHostPreflightStatus "none"
  StrCpy $LegacyHostInstallDir ""
  StrCpy $LegacyHostUpgradeCode ""
  StrCpy $LegacyHostDisplayName ""
  StrCpy $LegacyHostPublisher ""
  StrCpy $LegacyHostUninstallString ""
!ifdef LEGACY_MIGRATION_TEST
  ReadINIStr $LegacyHostInstallDir "${LEGACY_TEST_METADATA}" "legacy_product" "InstallDir"
  ReadINIStr $LegacyHostUpgradeCode "${LEGACY_TEST_METADATA}" "legacy_product" "UpgradeCode"
  ReadINIStr $LegacyHostDisplayName "${LEGACY_TEST_METADATA}" "legacy_uninstall" "DisplayName"
  ReadINIStr $LegacyHostPublisher "${LEGACY_TEST_METADATA}" "legacy_uninstall" "Publisher"
  ReadINIStr $LegacyHostUninstallString "${LEGACY_TEST_METADATA}" "legacy_uninstall" "UninstallString"
!else
  ReadRegStr $LegacyHostInstallDir HKCU "${LEGACY_PRODUCT_REG_KEY}" "InstallDir"
  ReadRegStr $LegacyHostUpgradeCode HKCU "${LEGACY_PRODUCT_REG_KEY}" "UpgradeCode"
  ReadRegStr $LegacyHostDisplayName HKCU "${LEGACY_UNINSTALL_REG_KEY}" "DisplayName"
  ReadRegStr $LegacyHostPublisher HKCU "${LEGACY_UNINSTALL_REG_KEY}" "Publisher"
  ReadRegStr $LegacyHostUninstallString HKCU "${LEGACY_UNINSTALL_REG_KEY}" "UninstallString"
!endif

  ; A legacy uninstaller left UpgradeCode behind in some releases. With no
  ; install directory or uninstall command there is no standalone app to remove.
  StrCmp $LegacyHostInstallDir "" legacy_host_no_install_dir
  StrCpy $LegacyHostDetected "1"
  StrCmp $LegacyHostUpgradeCode "${LEGACY_HOST_GUID}" 0 legacy_host_invalid_guid
  StrCmp $LegacyHostDisplayName "L2D 局域网更新主机" 0 legacy_host_invalid_name
  StrCmp $LegacyHostPublisher "4S4H1" 0 legacy_host_invalid_publisher
!ifdef LEGACY_MIGRATION_TEST
  ; Windows INI APIs remove surrounding quotation marks from values.
  StrCpy $LegacyHostExpectedUninstallString "$LegacyHostInstallDir\Uninstall.exe"
!else
  StrCpy $LegacyHostExpectedUninstallString "$\"$LegacyHostInstallDir\Uninstall.exe$\""
!endif
  StrCmp $LegacyHostUninstallString $LegacyHostExpectedUninstallString 0 legacy_host_invalid_command

legacy_host_validate_program_path:
  ; Never execute an old uninstaller from the editor directory or the persistent
  ; release-cache directory. Both registered and registry-less orphan installs
  ; pass through this shared validation after the final $INSTDIR is known.
  StrCmp $LegacyHostInstallDir $INSTDIR legacy_host_invalid_editor_dir
  StrCmp $LegacyHostInstallDir "$LOCALAPPDATA\4S4H1\L2DUpdateHost" legacy_host_invalid_data_dir
  IfFileExists "$LegacyHostInstallDir\L2DUpdateHost.exe" 0 legacy_host_invalid_host_file
  IfFileExists "$LegacyHostInstallDir\Uninstall.exe" 0 legacy_host_invalid_uninstaller

legacy_host_check_running:
  nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -Command "if (Get-Process -Name ${LEGACY_HOST_PROCESS_NAME} -ErrorAction SilentlyContinue) { exit 10 } else { exit 0 }"'
  Pop $0
  Pop $1
  StrCmp $0 "0" legacy_host_ready
  StrCpy $LegacyHostPreflightStatus "running"
  Return

legacy_host_no_install_dir:
  StrCmp $LegacyHostUninstallString "" 0 legacy_host_invalid
  IfFileExists "${LEGACY_DEFAULT_INSTALL_DIR}\L2DUpdateHost.exe" 0 legacy_host_none
  ; A companion Host installed by 1.2.0 intentionally has no second
  ; uninstaller. It still participates in the running-process preflight.
  IfFileExists "${LEGACY_DEFAULT_INSTALL_DIR}\Uninstall.exe" 0 legacy_host_companion
!ifndef LEGACY_MIGRATION_TEST
  ; Orphan fallback is intentionally limited to the exact historical default
  ; directory and two executable-looking PE files. The released Host did not
  ; consistently carry Win32 version resources, so an MZ-header check is used.
  ClearErrors
  FileOpen $0 "${LEGACY_DEFAULT_INSTALL_DIR}\L2DUpdateHost.exe" r
  IfErrors legacy_host_invalid
  FileReadByte $0 $1
  FileReadByte $0 $2
  FileClose $0
  StrCmp $1 "77" 0 legacy_host_invalid
  StrCmp $2 "90" 0 legacy_host_invalid
  ClearErrors
  FileOpen $0 "${LEGACY_DEFAULT_INSTALL_DIR}\Uninstall.exe" r
  IfErrors legacy_host_invalid
  FileReadByte $0 $1
  FileReadByte $0 $2
  FileClose $0
  StrCmp $1 "77" 0 legacy_host_invalid
  StrCmp $2 "90" 0 legacy_host_invalid
!endif
  StrCpy $LegacyHostInstallDir "${LEGACY_DEFAULT_INSTALL_DIR}"
  StrCpy $LegacyHostDetected "1"
  Goto legacy_host_validate_program_path
legacy_host_companion:
  StrCpy $LegacyHostInstallDir "${LEGACY_DEFAULT_INSTALL_DIR}"
  StrCpy $LegacyHostDetected "0"
  Goto legacy_host_check_running
legacy_host_none:
  StrCpy $LegacyHostPreflightStatus "none"
  Return
legacy_host_ready:
  ; The historical default root becomes the new companion root in-place.
  ; Replace it atomically below instead of executing its old uninstaller.
  StrCmp $LegacyHostInstallDir "${HOST_INSTALL_DIR}" 0 +2
  StrCpy $LegacyHostDetected "0"
  StrCpy $LegacyHostPreflightStatus "ready"
  Return
legacy_host_invalid_guid:
  Goto legacy_host_invalid
legacy_host_invalid_name:
  Goto legacy_host_invalid
legacy_host_invalid_publisher:
  Goto legacy_host_invalid
legacy_host_invalid_command:
  Goto legacy_host_invalid
legacy_host_invalid_editor_dir:
  Goto legacy_host_invalid
legacy_host_invalid_data_dir:
  Goto legacy_host_invalid
legacy_host_invalid_host_file:
  Goto legacy_host_invalid
legacy_host_invalid_uninstaller:
  Goto legacy_host_invalid
legacy_host_invalid:
  ; Incomplete or mismatched legacy metadata is not authority to delete an old
  ; root. Leave it untouched, warn in interactive mode, and continue installing.
  StrCpy $LegacyHostDetected "0"
  StrCpy $LegacyHostPreflightStatus "skipped"
FunctionEnd

Function MigrateLegacyHost
  StrCpy $LegacyHostMigrationStatus "ok"
  StrCpy $LegacyHostWasQuarantined "0"
  StrCpy $LegacyHostQuarantineDir ""
  StrCmp $LegacyHostPreflightStatus "skipped" legacy_host_migration_skipped
  StrCmp $LegacyHostDetected "1" 0 legacy_host_cleanup_integration

  ; A validated custom legacy root is first moved aside as one reversible
  ; unit. Its old uninstaller is never executed after the new roots are live.
  StrCpy $LegacyHostQuarantineDir "$LegacyHostInstallDir.__l2d_legacy_old"
  IfFileExists "$LegacyHostQuarantineDir\*.*" legacy_host_migration_failed
  !insertmacro RetryRename "$LegacyHostInstallDir" "$LegacyHostQuarantineDir" rr_legacy_host_quarantine
  IfErrors legacy_host_migration_failed
  StrCpy $LegacyHostWasQuarantined "1"
!ifdef FORCE_LEGACY_MIGRATION_FAILURE
  Goto legacy_host_migration_restore
!endif

legacy_host_cleanup_integration:
  ; Remove exact legacy integration points even when only dead shortcuts or
  ; stale uninstall metadata remain. Host QSettings and release cache are
  ; deliberately untouched.
!ifdef LEGACY_MIGRATION_TEST
  Delete "${LEGACY_START_MENU_SHORTCUT}"
  Delete "${LEGACY_DESKTOP_SHORTCUT}"
  Delete "${LEGACY_STARTUP_SHORTCUT}"
  DeleteINIStr "${LEGACY_TEST_METADATA}" "legacy_product" "InstallDir"
  DeleteINIStr "${LEGACY_TEST_METADATA}" "legacy_product" "UpgradeCode"
  DeleteINISec "${LEGACY_TEST_METADATA}" "legacy_uninstall"
!else
!ifndef INSTALLER_TEST_MODE
  Delete "${LEGACY_START_MENU_SHORTCUT}"
  Delete "${LEGACY_DESKTOP_SHORTCUT}"
  Delete "${LEGACY_STARTUP_SHORTCUT}"
  DeleteRegKey HKCU "${LEGACY_UNINSTALL_REG_KEY}"
  DeleteRegValue HKCU "${LEGACY_PRODUCT_REG_KEY}" "InstallDir"
  DeleteRegValue HKCU "${LEGACY_PRODUCT_REG_KEY}" "UpgradeCode"
  RMDir "$SMPROGRAMS\4S4H1"
!endif
!endif
  Return

legacy_host_migration_skipped:
  Return

!ifdef FORCE_LEGACY_MIGRATION_FAILURE
legacy_host_migration_restore:
  StrCmp $LegacyHostWasQuarantined "1" 0 legacy_host_migration_failed
  !insertmacro RetryRename "$LegacyHostQuarantineDir" "$LegacyHostInstallDir" rr_legacy_host_restore
  IfErrors legacy_host_migration_failed
  StrCpy $LegacyHostWasQuarantined "0"
!endif
legacy_host_migration_failed:
  StrCpy $LegacyHostMigrationStatus "failed"
FunctionEnd

Function .onInit
  StrCpy $HostInstallDir "${HOST_INSTALL_DIR}"
!ifndef INSTALLER_TEST_MODE
  nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -Command "if (Get-Process -Name L2DConfigEditor -ErrorAction SilentlyContinue) { exit 10 } else { exit 0 }"'
  Pop $0
  Pop $1
  StrCmp $0 "0" editor_init_done
  IfSilent editor_init_abort
  MessageBox MB_ICONEXCLAMATION|MB_OK "L2D 交互图表编辑器仍在运行。请先退出程序，再重新安装。"
editor_init_abort:
  Abort
editor_init_done:
!endif
FunctionEnd

; This preflight must run from the Core section, after the Directory page has
; committed its final $INSTDIR and before any staging/recovery directory is
; removed. Running it from .onInit would let an interactive directory change
; bypass both the user-data guard and the legacy Host path validation.
Function PreflightFinalInstallDirectory
  StrCpy $HostInstallDir "${HOST_INSTALL_DIR}"
  Call ValidateInstallRootRelationship
  StrCmp $OwnershipStatus "overlap" editor_host_directory_conflict
  StrCmp $OwnershipStatus "broad" editor_install_directory_too_broad
  StrCpy $LegacyHostDetected "0"
  StrCpy $LegacyHostPreflightStatus "none"
!ifndef INSTALLER_TEST_MODE
  Call PreflightLegacyHost
!else
!ifdef LEGACY_MIGRATION_TEST
  Call PreflightLegacyHost
!endif
!endif
  StrCmp $LegacyHostPreflightStatus "skipped" legacy_host_init_skipped
  StrCmp $LegacyHostPreflightStatus "running" legacy_host_init_running
  Call ValidateHostOwnership
  StrCmp $OwnershipStatus "unknown" host_ownership_unknown
  Call ValidateHostRecoveryOwnership
  StrCmp $OwnershipStatus "unknown" host_ownership_unknown
  Call ValidateHostStagingOwnership
  StrCmp $OwnershipStatus "unknown" host_ownership_unknown
  Goto legacy_host_init_done

editor_host_directory_conflict:
  SetErrorLevel 69
  IfSilent legacy_host_init_abort
  MessageBox MB_ICONSTOP|MB_OK "编辑器与独立 Host 必须安装在不同目录。请选择其他编辑器目录。"
  Goto legacy_host_init_abort
editor_install_directory_too_broad:
  SetErrorLevel 73
  IfSilent legacy_host_init_abort
  MessageBox MB_ICONSTOP|MB_OK "所选编辑器目录范围过宽。不能直接使用磁盘根目录、用户目录、AppData 或临时目录。"
  Goto legacy_host_init_abort
host_ownership_unknown:
  SetErrorLevel 74
  IfSilent legacy_host_init_abort
  MessageBox MB_ICONSTOP|MB_OK "Host 安装或恢复目录包含不属于本产品的内容。安装已中止，未删除任何文件。"
  Goto legacy_host_init_abort

legacy_host_init_skipped:
  IfSilent legacy_host_init_skipped_silent
  MessageBox MB_ICONEXCLAMATION|MB_OK "检测到旧版 L2D 更新主机元数据不完整或不匹配。安装将继续，但不会清理旧 Host 目录或集成信息。"
legacy_host_init_skipped_silent:
  Call ValidateHostOwnership
  StrCmp $OwnershipStatus "unknown" host_ownership_unknown
  Call ValidateHostRecoveryOwnership
  StrCmp $OwnershipStatus "unknown" host_ownership_unknown
  Call ValidateHostStagingOwnership
  StrCmp $OwnershipStatus "unknown" host_ownership_unknown
  Goto legacy_host_init_done
legacy_host_init_running:
  SetErrorLevel 63
  IfSilent legacy_host_init_abort
  MessageBox MB_ICONEXCLAMATION|MB_OK "旧版 L2D 局域网更新主机仍在运行。请先从托盘退出，再重新安装。"
legacy_host_init_abort:
  Abort
legacy_host_init_done:
  ; Recover a previous failed rollback before any staging cleanup. Never
  ; discard the only old installation, and never discard protected work files
  ; from an ambiguous recovery directory.
  SetOutPath "$TEMP"
  IfFileExists "$HostInstallDir.__old\*.*" host_stale_recovery_found editor_recovery_begin
host_stale_recovery_found:
  IfFileExists "$HostInstallDir\*.*" host_stale_recovery_conflict host_restore_stale_recovery
host_restore_stale_recovery:
  !insertmacro RetryRename "$HostInstallDir.__old" "$HostInstallDir" rr_host_stale_restore
  IfErrors host_stale_restore_failed
  Goto editor_recovery_begin
host_stale_recovery_conflict:
  SetErrorLevel 70
  IfSilent editor_preflight_abort
  MessageBox MB_ICONSTOP|MB_OK "当前 Host 与恢复目录同时存在。安装已中止，请先人工整理：$HostInstallDir.__old"
  Goto editor_preflight_abort
host_stale_restore_failed:
  SetErrorLevel 71
  IfSilent editor_preflight_abort
  MessageBox MB_ICONSTOP|MB_OK "检测到上次安装留下的 Host 恢复目录，但无法恢复：$HostInstallDir.__old。"
  Goto editor_preflight_abort
editor_recovery_begin:
  IfFileExists "$INSTDIR.__old\*.*" editor_stale_recovery_found editor_scan_current_install
editor_stale_recovery_found:
  IfFileExists "$INSTDIR\*.*" editor_stale_recovery_conflict editor_restore_stale_recovery
editor_restore_stale_recovery:
  ClearErrors
  Rename "$INSTDIR.__old" "$INSTDIR"
  IfErrors editor_stale_restore_failed
  Goto editor_scan_current_install
editor_stale_recovery_conflict:
  SetErrorLevel 68
  IfSilent editor_preflight_abort
  MessageBox MB_ICONSTOP|MB_OK "当前安装与恢复目录同时存在。为避免破坏上次回滚状态，安装已中止，请先人工整理：$INSTDIR.__old"
  Goto editor_preflight_abort

editor_scan_current_install:
  Goto editor_validate_ownership

editor_validate_ownership:
  Call ValidateEditorOwnership
  StrCmp $OwnershipStatus "unknown" editor_ownership_unknown
  Call ValidateEditorRecoveryOwnership
  StrCmp $OwnershipStatus "unknown" editor_ownership_unknown
  Call ValidateEditorStagingOwnership
  StrCmp $OwnershipStatus "unknown" editor_ownership_unknown
  Goto editor_preflight_done

editor_stale_restore_failed:
  SetErrorLevel 67
  IfSilent editor_preflight_abort
  MessageBox MB_ICONSTOP|MB_OK "检测到上次安装留下的恢复目录，但无法恢复：$INSTDIR.__old。安装已中止，文件保持不变。"
  Goto editor_preflight_abort
editor_ownership_unknown:
  SetErrorLevel 72
  IfSilent editor_preflight_abort
  MessageBox MB_ICONSTOP|MB_OK "编辑器安装或恢复目录包含不属于本产品的内容。安装已中止，未删除任何文件。"
editor_preflight_abort:
  Abort
editor_preflight_done:
FunctionEnd

Section "L2D 交互图表编辑器（必选）" Core
  SectionIn RO
!ifdef TEST_DIRECTORY_SELECTED_INSTALL_DIR
  ; Runtime-test hook: model a user changing $INSTDIR on the Directory page,
  ; which happens after .onInit but before this section begins.
  StrCpy $INSTDIR "${TEST_DIRECTORY_SELECTED_INSTALL_DIR}"
!endif
  Call PreflightFinalInstallDirectory
  InitPluginsDir
  SetOutPath "$PLUGINSDIR"
  File /oname=l2d-managed-files.ps1 "managed-files.ps1"

  ; Preserve unknown files from an interrupted staging root, then remove only
  ; the files listed by its installer manifest.
  IfFileExists "$INSTDIR.__new\*.*" 0 editor_staging_root_ready
  StrCpy $ManagedSource "$INSTDIR.__new"
  StrCpy $ManagedTarget "$INSTDIR"
  Call MergeUnknownFiles
  ; MergeUnknown removes the staging root after preserving its unknown files.
  ; Avoid launching a second helper process for a root that no longer exists.
  RMDir "$INSTDIR.__new"
editor_staging_root_ready:
  SetOutPath "$INSTDIR.__new"
  ClearErrors
  File /r "${SOURCE_DIR}\*.*"
  IfErrors editor_stage_failed
  File /oname=${MANAGED_SCRIPT_NAME} "managed-files.ps1"
  IfErrors editor_stage_failed
  ClearErrors
  FileOpen $0 "$INSTDIR.__new\${OWNER_MARKER_NAME}" w
  IfErrors editor_stage_failed
  FileWrite $0 "${EDITOR_OWNER_VALUE}"
  FileClose $0
  IfErrors editor_stage_failed
  WriteUninstaller "$INSTDIR.__new\Uninstall.exe"
  IfErrors editor_stage_failed
  Push "$INSTDIR.__new"
  Call WriteManagedManifest
  Pop $0
  IfErrors editor_stage_failed
  IfFileExists "$HostInstallDir.__new\*.*" 0 host_staging_root_ready
  StrCpy $ManagedSource "$HostInstallDir.__new"
  StrCpy $ManagedTarget "$HostInstallDir"
  Call MergeUnknownFiles
  RMDir "$HostInstallDir.__new"
host_staging_root_ready:
  SetOutPath "$HostInstallDir.__new"
  ClearErrors
  File /r "${HOST_SOURCE_DIR}\*.*"
  IfErrors host_stage_failed
  ClearErrors
  FileOpen $0 "$HostInstallDir.__new\${OWNER_MARKER_NAME}" w
  IfErrors host_stage_failed
  FileWrite $0 "${HOST_OWNER_VALUE}"
  FileClose $0
  IfErrors host_stage_failed
  Push "$HostInstallDir.__new"
  Call WriteManagedManifest
  Pop $0
  IfErrors host_stage_failed

  ; SetOutPath also changes the process working directory. Windows refuses
  ; to rename a directory while this installer has it as its current folder.
  SetOutPath "$TEMP"
  RMDir /r "$INSTDIR.__old"
  RMDir /r "$HostInstallDir.__old"
  IfFileExists "$INSTDIR\*.*" editor_has_old editor_no_old
editor_has_old:
  StrCpy $EditorHadOld "1"
  !insertmacro RetryRename "$INSTDIR" "$INSTDIR.__old" rr_editor_replace
  IfErrors editor_replace_failed
  Goto host_replace_begin
editor_no_old:
  StrCpy $EditorHadOld "0"
  RMDir "$INSTDIR"
host_replace_begin:
  IfFileExists "$HostInstallDir\*.*" host_has_old host_no_old
host_has_old:
  StrCpy $HostHadOld "1"
  !insertmacro RetryRename "$HostInstallDir" "$HostInstallDir.__old" rr_host_replace
  IfErrors host_replace_failed
  Goto editor_activate
host_no_old:
  StrCpy $HostHadOld "0"
  RMDir "$HostInstallDir"

editor_activate:
!ifdef FORCE_EDITOR_ACTIVATE_FAILURE
  SetErrors
!else
  !insertmacro RetryRename "$INSTDIR.__new" "$INSTDIR" rr_editor_activate
!endif
  IfErrors editor_activate_failed
!ifdef FORCE_HOST_ACTIVATE_FAILURE
  SetErrors
!else
  !insertmacro RetryRename "$HostInstallDir.__new" "$HostInstallDir" rr_host_activate
!endif
  IfErrors host_activate_failed

  ; A custom legacy standalone Host is removed only after both companion roots
  ; are live. The historical default root was replaced in place above.
  Call MigrateLegacyHost
  StrCmp $LegacyHostMigrationStatus "failed" editor_legacy_migration_failed
  StrCpy $ManagedSource "$INSTDIR.__old"
  StrCpy $ManagedTarget "$INSTDIR"
  Call MergeUnknownFiles
  StrCpy $ManagedSource "$HostInstallDir.__old"
  StrCpy $ManagedTarget "$HostInstallDir"
  Call MergeUnknownFiles
  ; Successful MergeUnknown calls already removed both old managed roots.
  RMDir "$INSTDIR.__old"
  RMDir "$HostInstallDir.__old"
  SetOutPath "$TEMP"
  Goto editor_files_ready

host_replace_failed:
  ; Host was not changed; restore the editor root already handed aside.
  StrCmp $EditorHadOld "1" 0 host_replace_cleanup
!ifdef FORCE_EDITOR_ROLLBACK_FAILURE
  SetErrors
!else
  !insertmacro RetryRename "$INSTDIR.__old" "$INSTDIR" rr_editor_restore_after_host_replace
!endif
  IfErrors coordinated_rollback_failed
host_replace_cleanup:
  RMDir /r "$INSTDIR.__new"
  RMDir /r "$HostInstallDir.__new"
  SetErrorLevel 55
!ifndef INSTALLER_TEST_MODE
  MessageBox MB_ICONSTOP|MB_OK "无法安全替换旧 Host 文件。编辑器原安装已恢复；请确认托盘 Host 完全退出后重试。"
!endif
  Abort

editor_activate_failed:
  ; Neither new root is active yet. Restore both old roots as one rollback.
  StrCmp $HostHadOld "1" 0 editor_activate_restore_editor
!ifdef FORCE_HOST_ROLLBACK_FAILURE
  SetErrors
!else
  !insertmacro RetryRename "$HostInstallDir.__old" "$HostInstallDir" rr_host_restore_after_editor_activate
!endif
  IfErrors coordinated_rollback_failed
editor_activate_restore_editor:
  StrCmp $EditorHadOld "1" 0 editor_activate_cleanup
!ifdef FORCE_EDITOR_ROLLBACK_FAILURE
  SetErrors
!else
  !insertmacro RetryRename "$INSTDIR.__old" "$INSTDIR" rr_editor_restore_after_activate
!endif
  IfErrors coordinated_rollback_failed
editor_activate_cleanup:
  RMDir /r "$INSTDIR.__new"
  RMDir /r "$HostInstallDir.__new"
  SetErrorLevel 54
!ifndef INSTALLER_TEST_MODE
  MessageBox MB_ICONSTOP|MB_OK "无法激活新编辑器；编辑器与 Host 原安装均已恢复。"
!endif
  Abort

host_activate_failed:
  ; The new editor is active but Host activation failed. Move the new editor
  ; back out of the way, then restore both old roots.
  StrCmp $EditorHadOld "1" 0 host_activate_remove_new_editor
  !insertmacro RetryRename "$INSTDIR" "$INSTDIR.__new" rr_editor_deactivate_after_host_failure
  IfErrors coordinated_rollback_failed
  Goto host_activate_restore_host
host_activate_remove_new_editor:
  !insertmacro RetryRename "$INSTDIR" "$INSTDIR.__new" rr_editor_isolate_after_host_failure
  IfErrors coordinated_rollback_failed
  IfFileExists "$INSTDIR\*.*" coordinated_rollback_failed
host_activate_restore_host:
  StrCmp $HostHadOld "1" 0 host_activate_restore_editor
!ifdef FORCE_HOST_ROLLBACK_FAILURE
  SetErrors
!else
  !insertmacro RetryRename "$HostInstallDir.__old" "$HostInstallDir" rr_host_restore_after_host_activate
!endif
  IfErrors coordinated_rollback_failed
host_activate_restore_editor:
  StrCmp $EditorHadOld "1" 0 host_activate_cleanup
!ifdef FORCE_EDITOR_ROLLBACK_FAILURE
  SetErrors
!else
  !insertmacro RetryRename "$INSTDIR.__old" "$INSTDIR" rr_editor_restore_after_host_activate
!endif
  IfErrors coordinated_rollback_failed
host_activate_cleanup:
  RMDir /r "$INSTDIR.__new"
  RMDir /r "$HostInstallDir.__new"
  SetErrorLevel 56
!ifndef INSTALLER_TEST_MODE
  MessageBox MB_ICONSTOP|MB_OK "无法激活新 Host；编辑器与 Host 原安装均已恢复。"
!endif
  Abort

editor_replace_failed:
  RMDir /r "$INSTDIR.__new"
  RMDir /r "$HostInstallDir.__new"
!ifdef INSTALLER_TEST_MODE
  SetErrorLevel 51
!else
  MessageBox MB_ICONSTOP|MB_OK "无法安全替换旧程序文件。原安装已保留；请确认程序完全退出后重试。"
!endif
  Abort
editor_stage_failed:
  RMDir /r "$INSTDIR.__new"
  RMDir /r "$HostInstallDir.__new"
!ifdef INSTALLER_TEST_MODE
  SetErrorLevel 52
!else
  MessageBox MB_ICONSTOP|MB_OK "无法完整暂存新程序文件，安装已中止。"
!endif
  Abort

host_stage_failed:
  RMDir /r "$INSTDIR.__new"
  RMDir /r "$HostInstallDir.__new"
  SetErrorLevel 57
!ifndef INSTALLER_TEST_MODE
  MessageBox MB_ICONSTOP|MB_OK "无法完整暂存新 Host 文件，安装已中止；现有安装未改变。"
!endif
  Abort

coordinated_rollback_failed:
  SetOutPath "$TEMP"
  ; Staged/new payloads are disposable; never delete the .__old recovery roots.
  RMDir /r "$INSTDIR.__new"
  RMDir /r "$HostInstallDir.__new"
  SetErrorLevel 53
!ifndef INSTALLER_TEST_MODE
  MessageBox MB_ICONSTOP|MB_OK "双程序升级失败且无法完整自动回滚。旧文件均保留在 .__old 恢复目录中，请勿删除并联系维护者。"
!endif
  Abort

editor_legacy_migration_failed:
  ; Undo both newly activated roots before returning an error. A custom legacy
  ; Host that could not be removed remains untouched in its original directory.
  SetOutPath "$TEMP"
  StrCmp $EditorHadOld "1" 0 migration_remove_new_editor
  !insertmacro RetryRename "$INSTDIR" "$INSTDIR.__new" rr_editor_deactivate_after_migration
  IfErrors coordinated_rollback_failed
  Goto migration_deactivate_host
migration_remove_new_editor:
  !insertmacro RetryRename "$INSTDIR" "$INSTDIR.__new" rr_editor_isolate_after_migration
  IfErrors coordinated_rollback_failed
  IfFileExists "$INSTDIR\*.*" coordinated_rollback_failed
migration_deactivate_host:
  StrCmp $HostHadOld "1" 0 migration_remove_new_host
  !insertmacro RetryRename "$HostInstallDir" "$HostInstallDir.__new" rr_host_deactivate_after_migration
  IfErrors coordinated_rollback_failed
  Goto migration_restore_host
migration_remove_new_host:
  !insertmacro RetryRename "$HostInstallDir" "$HostInstallDir.__new" rr_host_isolate_after_migration
  IfErrors coordinated_rollback_failed
  IfFileExists "$HostInstallDir\*.*" coordinated_rollback_failed
migration_restore_host:
  StrCmp $HostHadOld "1" 0 migration_restore_editor
  !insertmacro RetryRename "$HostInstallDir.__old" "$HostInstallDir" rr_host_restore_after_migration
  IfErrors coordinated_rollback_failed
migration_restore_editor:
  StrCmp $EditorHadOld "1" 0 migration_cleanup
  !insertmacro RetryRename "$INSTDIR.__old" "$INSTDIR" rr_editor_restore_after_migration
  IfErrors coordinated_rollback_failed
migration_cleanup:
  RMDir /r "$INSTDIR.__new"
  RMDir /r "$HostInstallDir.__new"
  SetErrorLevel 64
!ifndef INSTALLER_TEST_MODE
  MessageBox MB_ICONSTOP|MB_OK "旧版独立 Host 未能安全迁移；编辑器与伴随 Host 原安装均已恢复，发布缓存保持不变。"
!endif
  Abort

editor_files_ready:
!ifndef INSTALLER_TEST_MODE
  WriteRegStr HKCU "Software\4S4H1\L2DConfigEditor" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\4S4H1\L2DConfigEditor" "HostInstallDir" "$HostInstallDir"
  WriteRegStr HKCU "Software\4S4H1\L2DConfigEditor" "UpgradeCode" "${PRODUCT_GUID}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\L2DConfigEditor" "DisplayName" "L2D 交互图表编辑器"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\L2DConfigEditor" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\L2DConfigEditor" "Publisher" "4S4H1"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\L2DConfigEditor" "DisplayIcon" "$INSTDIR\L2DConfigEditor.exe,0"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\L2DConfigEditor" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "Software\Classes\Applications\L2DConfigEditor.exe" "FriendlyAppName" "L2D 交互图表编辑器"
  WriteRegStr HKCU "Software\Classes\Applications\L2DConfigEditor.exe\DefaultIcon" "" "$INSTDIR\L2DConfigEditor.exe,0"
  WriteRegStr HKCU "Software\Classes\Applications\L2DConfigEditor.exe\SupportedTypes" ".json" ""
  WriteRegStr HKCU "Software\Classes\Applications\L2DConfigEditor.exe\shell\open\command" "" '"$INSTDIR\L2DConfigEditor.exe" "%1"'
  !insertmacro MUI_STARTMENU_WRITE_BEGIN Application
    CreateDirectory "$SMPROGRAMS\$StartMenuFolder"
    CreateShortcut "$SMPROGRAMS\$StartMenuFolder\L2D 交互图表编辑器.lnk" "$INSTDIR\L2DConfigEditor.exe"
    CreateShortcut "$SMPROGRAMS\$StartMenuFolder\L2D 局域网更新主机.lnk" "$HostInstallDir\L2DUpdateHost.exe"
    CreateShortcut "$SMPROGRAMS\$StartMenuFolder\卸载.lnk" "$INSTDIR\Uninstall.exe"
  !insertmacro MUI_STARTMENU_WRITE_END
!endif
  ; The install transaction is committed. The reversible custom-legacy
  ; quarantine is now disposable; a transient deletion failure merely leaves
  ; the clearly named backup for manual cleanup.
  StrCmp $LegacyHostWasQuarantined "1" 0 legacy_quarantine_cleanup_done
  RMDir /r "$LegacyHostQuarantineDir"
legacy_quarantine_cleanup_done:
SectionEnd

!ifndef INSTALLER_TEST_MODE
Section /o "桌面快捷方式" DesktopShortcut
  CreateShortcut "$DESKTOP\L2D 交互图表编辑器.lnk" "$INSTDIR\L2DConfigEditor.exe"
SectionEnd
!endif

Function un.onInit
  StrCpy $HostInstallDir "${HOST_INSTALL_DIR}"
!ifndef INSTALLER_TEST_MODE
  nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -Command "if (Get-Process -Name L2DUpdateHost -ErrorAction SilentlyContinue) { exit 10 } else { exit 0 }"'
  Pop $0
  Pop $1
  StrCmp $0 "0" host_uninstall_not_running
  IfSilent host_uninstall_running_abort
  MessageBox MB_ICONEXCLAMATION|MB_OK "L2D 局域网更新主机仍在运行。请先从托盘选择“退出”，再重新卸载。发布缓存不会被删除。"
host_uninstall_running_abort:
  Abort
host_uninstall_not_running:
!endif
  ; Never let this uninstaller recursively remove a root whose ownership
  ; marker was replaced or stripped after installation.
  IfFileExists "$INSTDIR\${OWNER_MARKER_NAME}" 0 editor_uninstall_owner_invalid
  ClearErrors
  FileOpen $0 "$INSTDIR\${OWNER_MARKER_NAME}" r
  IfErrors editor_uninstall_owner_invalid
  FileRead $0 $1
  FileClose $0
  StrCmp $1 "${EDITOR_OWNER_VALUE}" 0 editor_uninstall_owner_invalid
  IfFileExists "$HostInstallDir\*.*" 0 editor_uninstall_owner_valid
  IfFileExists "$HostInstallDir\${OWNER_MARKER_NAME}" 0 editor_uninstall_owner_invalid
  ClearErrors
  FileOpen $0 "$HostInstallDir\${OWNER_MARKER_NAME}" r
  IfErrors editor_uninstall_owner_invalid
  FileRead $0 $1
  FileClose $0
  StrCmp $1 "${HOST_OWNER_VALUE}" 0 editor_uninstall_owner_invalid
editor_uninstall_owner_valid:
  Goto editor_uninstall_preflight_done
editor_uninstall_owner_invalid:
  SetErrorLevel 75
  IfSilent editor_uninstall_owner_abort
  MessageBox MB_ICONSTOP|MB_OK "程序目录所有权标记无效。卸载已中止，未删除任何文件。"
editor_uninstall_owner_abort:
  Abort
editor_uninstall_preflight_done:
FunctionEnd

Section "Uninstall"
!ifndef INSTALLER_TEST_MODE
  Delete "$DESKTOP\L2D 交互图表编辑器.lnk"
  !insertmacro MUI_STARTMENU_GETFOLDER Application $StartMenuFolder
  RMDir /r "$SMPROGRAMS\$StartMenuFolder"
!endif
  Push "$HostInstallDir"
  Call un.DeleteManagedFiles
  Pop $0
  Push "$HostInstallDir.__new"
  Call un.DeleteManagedFiles
  Pop $0
  Push "$HostInstallDir.__old"
  Call un.DeleteManagedFiles
  Pop $0
  RMDir "$HostInstallDir"
  RMDir "$HostInstallDir.__new"
  RMDir "$HostInstallDir.__old"
  Push "$INSTDIR.__new"
  Call un.DeleteManagedFiles
  Pop $0
  Push "$INSTDIR.__old"
  Call un.DeleteManagedFiles
  Pop $0
  Push "$INSTDIR"
  Call un.DeleteManagedFiles
  Pop $0
  RMDir "$INSTDIR.__new"
  RMDir "$INSTDIR.__old"
  RMDir "$INSTDIR"
!ifndef INSTALLER_TEST_MODE
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "L2DUpdateHost"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\L2DConfigEditor"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\L2DUpdateHost"
  DeleteRegKey HKCU "Software\4S4H1\L2DUpdateHost"
  DeleteRegKey HKCU "Software\Classes\Applications\L2DConfigEditor.exe"
  DeleteRegValue HKCU "Software\4S4H1\L2DConfigEditor" "InstallDir"
  DeleteRegValue HKCU "Software\4S4H1\L2DConfigEditor" "HostInstallDir"
!endif
SectionEnd
