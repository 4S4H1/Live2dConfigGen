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
Var LegacyHostDetected
Var LegacyHostPreflightStatus
Var LegacyHostMigrationStatus
Var LegacyHostInstallDir
Var LegacyHostUpgradeCode
Var LegacyHostDisplayName
Var LegacyHostPublisher
Var LegacyHostUninstallString
Var LegacyHostExpectedUninstallString
Var WorkScanFound
Var WorkScanRoot

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_STARTMENU Application $StartMenuFolder
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

!macro DefineWorkFileScanner PREFIX
Function ${PREFIX}ScanForUserWorkFiles
  Exch $R0
  Push $R1
  Push $R2
  Push $R3
  FindFirst $R1 $R2 "$R0\*.*"
work_scan_loop:
  StrCmp $R2 "" work_scan_done
  StrCmp $R2 "." work_scan_next
  StrCmp $R2 ".." work_scan_next
  IfFileExists "$R0\$R2\*.*" work_scan_directory work_scan_file
work_scan_directory:
  StrCmp $R2 "_internal" 0 work_scan_recurse
  StrCmp $R0 $WorkScanRoot work_scan_next
work_scan_recurse:
  Push "$R0\$R2"
  Call ${PREFIX}ScanForUserWorkFiles
  Pop $R3
  StrCmp $WorkScanFound "1" work_scan_done
  Goto work_scan_next
work_scan_file:
  ${GetFileExt} "$R0\$R2" $R3
  StrCmp $R3 "json" work_scan_found
  StrCmp $R3 "csv" work_scan_found
  StrCmp $R3 "png" work_scan_found
  StrCmp $R3 "jpg" work_scan_found
  StrCmp $R3 "jpeg" work_scan_found
  StrCmp $R3 "gif" work_scan_found
  StrCmp $R3 "bmp" work_scan_found
  StrCmp $R3 "webp" work_scan_found
  StrCmp $R3 "svg" work_scan_found
  Goto work_scan_next
work_scan_found:
  StrCpy $WorkScanFound "1"
  Goto work_scan_done
work_scan_next:
  FindNext $R1 $R2
  Goto work_scan_loop
work_scan_done:
  FindClose $R1
  Pop $R3
  Pop $R2
  Pop $R1
  Exch $R0
FunctionEnd
!macroend

!insertmacro DefineWorkFileScanner ""
!insertmacro DefineWorkFileScanner "un."

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

  nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -Command "if (Get-Process -Name ${LEGACY_HOST_PROCESS_NAME} -ErrorAction SilentlyContinue) { exit 10 } else { exit 0 }"'
  Pop $0
  Pop $1
  StrCmp $0 "0" legacy_host_ready
  StrCpy $LegacyHostPreflightStatus "running"
  Return

legacy_host_no_install_dir:
  StrCmp $LegacyHostUninstallString "" 0 legacy_host_invalid
  IfFileExists "${LEGACY_DEFAULT_INSTALL_DIR}\L2DUpdateHost.exe" 0 legacy_host_none
  IfFileExists "${LEGACY_DEFAULT_INSTALL_DIR}\Uninstall.exe" 0 legacy_host_invalid
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
legacy_host_none:
  StrCpy $LegacyHostPreflightStatus "none"
  Return
legacy_host_ready:
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
  StrCpy $LegacyHostPreflightStatus "invalid"
FunctionEnd

Function MigrateLegacyHost
  StrCpy $LegacyHostMigrationStatus "ok"
  StrCmp $LegacyHostDetected "1" 0 legacy_host_cleanup_integration

  ; The legacy uninstaller owns its program directory. Do not recursively
  ; delete a path sourced from per-user registry metadata in this installer.
  ClearErrors
  ExecWait '"$LegacyHostInstallDir\Uninstall.exe" /S _?=$LegacyHostInstallDir' $0
  IfErrors legacy_host_migration_exec_error
  StrCmp $0 "0" 0 legacy_host_migration_exit_error
  IfFileExists "$LegacyHostInstallDir\L2DUpdateHost.exe" legacy_host_migration_marker_error
  Delete "$LegacyHostInstallDir\Uninstall.exe"
  RMDir "$LegacyHostInstallDir"
  IfFileExists "$LegacyHostInstallDir\*.*" legacy_host_migration_marker_error

legacy_host_cleanup_integration:
  ; Remove exact legacy integration points even when only dead shortcuts or
  ; stale uninstall metadata remain. The release cache is untouched.
!ifdef LEGACY_MIGRATION_TEST
  Delete "${LEGACY_START_MENU_SHORTCUT}"
  Delete "${LEGACY_DESKTOP_SHORTCUT}"
  Delete "${LEGACY_STARTUP_SHORTCUT}"
  DeleteINISec "${LEGACY_TEST_METADATA}" "legacy_product"
  DeleteINISec "${LEGACY_TEST_METADATA}" "legacy_uninstall"
!else
!ifndef INSTALLER_TEST_MODE
  Delete "${LEGACY_START_MENU_SHORTCUT}"
  Delete "${LEGACY_DESKTOP_SHORTCUT}"
  Delete "${LEGACY_STARTUP_SHORTCUT}"
  DeleteRegKey HKCU "${LEGACY_UNINSTALL_REG_KEY}"
  DeleteRegKey HKCU "${LEGACY_PRODUCT_REG_KEY}"
  RMDir "$SMPROGRAMS\4S4H1"
!endif
!endif
  Return

legacy_host_migration_exec_error:
  Goto legacy_host_migration_failed
legacy_host_migration_exit_error:
  Goto legacy_host_migration_failed
legacy_host_migration_marker_error:
  Goto legacy_host_migration_failed
legacy_host_migration_failed:
  StrCpy $LegacyHostMigrationStatus "failed"
FunctionEnd

Function .onInit
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
  StrCpy $LegacyHostDetected "0"
  StrCpy $LegacyHostPreflightStatus "none"
!ifndef INSTALLER_TEST_MODE
  Call PreflightLegacyHost
!else
!ifdef LEGACY_MIGRATION_TEST
  Call PreflightLegacyHost
!endif
!endif
  StrCmp $LegacyHostPreflightStatus "invalid" legacy_host_init_invalid
  StrCmp $LegacyHostPreflightStatus "running" legacy_host_init_running
  Goto legacy_host_init_done

legacy_host_init_invalid:
  SetErrorLevel 62
  IfSilent legacy_host_init_abort
  MessageBox MB_ICONSTOP|MB_OK "检测到旧版 L2D 更新主机，但无法安全验证其卸载信息。安装已中止；不会删除注册表所指向的目录。"
  Goto legacy_host_init_abort
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
  IfFileExists "$INSTDIR.__old\*.*" editor_stale_recovery_found editor_scan_current_install
editor_stale_recovery_found:
  IfFileExists "$INSTDIR\*.*" editor_stale_recovery_conflict editor_restore_stale_recovery
editor_restore_stale_recovery:
  ClearErrors
  Rename "$INSTDIR.__old" "$INSTDIR"
  IfErrors editor_stale_restore_failed
  Goto editor_scan_current_install
editor_stale_recovery_conflict:
  StrCpy $WorkScanFound "0"
  StrCpy $WorkScanRoot "$INSTDIR.__old"
  Push "$INSTDIR.__old"
  Call ScanForUserWorkFiles
  Pop $0
  StrCmp $WorkScanFound "1" editor_stale_conflict_blocked

editor_scan_current_install:
  IfFileExists "$INSTDIR\*.*" 0 editor_scan_staging_install
  StrCpy $WorkScanFound "0"
  StrCpy $WorkScanRoot "$INSTDIR"
  Push "$INSTDIR"
  Call ScanForUserWorkFiles
  Pop $0
  StrCmp $WorkScanFound "1" editor_work_files_blocked

editor_scan_staging_install:
  ; A stale staging directory is deleted at the start of the Core section, so
  ; it receives the same work-file protection as the live/recovery directories.
  IfFileExists "$INSTDIR.__new\*.*" 0 editor_preflight_done
  StrCpy $WorkScanFound "0"
  StrCpy $WorkScanRoot "$INSTDIR.__new"
  Push "$INSTDIR.__new"
  Call ScanForUserWorkFiles
  Pop $0
  StrCmp $WorkScanFound "1" editor_work_files_blocked editor_preflight_done

editor_stale_restore_failed:
  SetErrorLevel 67
  IfSilent editor_preflight_abort
  MessageBox MB_ICONSTOP|MB_OK "检测到上次安装留下的恢复目录，但无法恢复：$INSTDIR.__old。安装已中止，文件保持不变。"
  Goto editor_preflight_abort
editor_stale_conflict_blocked:
  SetErrorLevel 68
  IfSilent editor_preflight_abort
  MessageBox MB_ICONSTOP|MB_OK "当前安装与恢复目录同时存在，且恢复目录包含 JSON、CSV 或图片工作文件。安装已中止，请先人工整理：$INSTDIR.__old"
  Goto editor_preflight_abort
editor_work_files_blocked:
  SetErrorLevel 65
  IfSilent editor_preflight_abort
  MessageBox MB_ICONSTOP|MB_OK "安装目录内检测到 JSON、CSV 或图片工作文件。为避免覆盖用户数据，安装已中止；请先把工作文件移出程序目录。"
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
  RMDir /r "$INSTDIR.__new"
  SetOutPath "$INSTDIR.__new"
  ClearErrors
  File /r "${SOURCE_DIR}\*.*"
  IfErrors editor_stage_failed
  WriteUninstaller "$INSTDIR.__new\Uninstall.exe"
  IfErrors editor_stage_failed

  ; SetOutPath also changes the process working directory. Windows refuses
  ; to rename a directory while this installer has it as its current folder.
  SetOutPath "$TEMP"
  RMDir /r "$INSTDIR.__old"
  IfFileExists "$INSTDIR\*.*" editor_has_old editor_no_old
editor_has_old:
  StrCpy $EditorHadOld "1"
  ClearErrors
  Rename "$INSTDIR" "$INSTDIR.__old"
  IfErrors editor_replace_failed
  Goto editor_activate
editor_no_old:
  StrCpy $EditorHadOld "0"
  RMDir "$INSTDIR"
editor_activate:
  ClearErrors
!ifdef FORCE_EDITOR_ACTIVATE_FAILURE
  SetErrors
!else
  Rename "$INSTDIR.__new" "$INSTDIR"
!endif
  IfErrors editor_activate_failed
  Call MigrateLegacyHost
  StrCmp $LegacyHostMigrationStatus "failed" editor_legacy_migration_failed
  RMDir /r "$INSTDIR.__old"
  SetOutPath "$INSTDIR"
  Goto editor_files_ready

editor_activate_failed:
  StrCmp $EditorHadOld "1" 0 editor_activate_failed_without_old
  ClearErrors
!ifdef FORCE_EDITOR_ROLLBACK_FAILURE
  SetErrors
!else
  Rename "$INSTDIR.__old" "$INSTDIR"
!endif
  IfErrors editor_rollback_failed
  Goto editor_replace_failed

editor_rollback_failed:
  RMDir /r "$INSTDIR.__new"
  SetErrorLevel 53
!ifndef INSTALLER_TEST_MODE
  MessageBox MB_ICONSTOP|MB_OK "无法激活新程序，也无法自动恢复原安装。原文件保留在：$INSTDIR.__old"
!endif
  Abort

editor_activate_failed_without_old:
  RMDir /r "$INSTDIR.__new"
!ifdef INSTALLER_TEST_MODE
  SetErrorLevel 54
!else
  MessageBox MB_ICONSTOP|MB_OK "无法激活新程序文件；没有改动其他用户数据。"
!endif
  Abort

editor_replace_failed:
  RMDir /r "$INSTDIR.__new"
!ifdef INSTALLER_TEST_MODE
  SetErrorLevel 51
!else
  MessageBox MB_ICONSTOP|MB_OK "无法安全替换旧程序文件。原安装已保留；请确认程序完全退出后重试。"
!endif
  Abort
editor_stage_failed:
  RMDir /r "$INSTDIR.__new"
!ifdef INSTALLER_TEST_MODE
  SetErrorLevel 52
!else
  MessageBox MB_ICONSTOP|MB_OK "无法完整暂存新程序文件，安装已中止。"
!endif
  Abort

editor_legacy_migration_failed:
  RMDir /r "$INSTDIR.__old"
  SetOutPath "$INSTDIR"
  SetErrorLevel 64
!ifndef INSTALLER_TEST_MODE
  MessageBox MB_ICONSTOP|MB_OK "新编辑器已安装，但旧版独立更新主机未能安全移除。请确认旧 Host 已退出后重新运行安装程序。"
!endif
  Abort

editor_files_ready:
!ifndef INSTALLER_TEST_MODE
  WriteRegStr HKCU "Software\4S4H1\L2DConfigEditor" "InstallDir" "$INSTDIR"
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
    CreateShortcut "$SMPROGRAMS\$StartMenuFolder\卸载.lnk" "$INSTDIR\Uninstall.exe"
  !insertmacro MUI_STARTMENU_WRITE_END
!endif
SectionEnd

!ifndef INSTALLER_TEST_MODE
Section /o "桌面快捷方式" DesktopShortcut
  CreateShortcut "$DESKTOP\L2D 交互图表编辑器.lnk" "$INSTDIR\L2DConfigEditor.exe"
SectionEnd
!endif

Function un.onInit
  StrCpy $WorkScanFound "0"
  StrCpy $WorkScanRoot "$INSTDIR"
  Push "$INSTDIR"
  Call un.ScanForUserWorkFiles
  Pop $0
  StrCmp $WorkScanFound "1" editor_uninstall_work_files_found
  IfFileExists "$INSTDIR.__old\*.*" 0 editor_uninstall_scan_staging
  StrCpy $WorkScanRoot "$INSTDIR.__old"
  Push "$INSTDIR.__old"
  Call un.ScanForUserWorkFiles
  Pop $0
  StrCmp $WorkScanFound "1" editor_uninstall_work_files_found
editor_uninstall_scan_staging:
  IfFileExists "$INSTDIR.__new\*.*" 0 editor_uninstall_preflight_done
  StrCpy $WorkScanRoot "$INSTDIR.__new"
  Push "$INSTDIR.__new"
  Call un.ScanForUserWorkFiles
  Pop $0
  StrCmp $WorkScanFound "1" 0 editor_uninstall_preflight_done
editor_uninstall_work_files_found:
  SetErrorLevel 66
  IfSilent editor_uninstall_preflight_abort
  MessageBox MB_ICONSTOP|MB_OK "程序目录内包含 JSON、CSV 或图片工作文件。为避免删除用户数据，卸载已中止；请先把工作文件移出程序目录。"
editor_uninstall_preflight_abort:
  Abort
editor_uninstall_preflight_done:
FunctionEnd

Section "Uninstall"
!ifndef INSTALLER_TEST_MODE
  Delete "$DESKTOP\L2D 交互图表编辑器.lnk"
  !insertmacro MUI_STARTMENU_GETFOLDER Application $StartMenuFolder
  RMDir /r "$SMPROGRAMS\$StartMenuFolder"
!endif
  RMDir /r "$INSTDIR"
  RMDir /r "$INSTDIR.__new"
  RMDir /r "$INSTDIR.__old"
!ifndef INSTALLER_TEST_MODE
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\L2DConfigEditor"
  DeleteRegKey HKCU "Software\Classes\Applications\L2DConfigEditor.exe"
  DeleteRegValue HKCU "Software\4S4H1\L2DConfigEditor" "InstallDir"
!endif
SectionEnd
