Unicode True
RequestExecutionLevel user
SetCompressor /SOLID lzma

!include "MUI2.nsh"

!ifndef VERSION
  !error "VERSION define is required"
!endif
!ifndef SOURCE_DIR
  !error "SOURCE_DIR define is required"
!endif
!ifndef OUTPUT_DIR
  !error "OUTPUT_DIR define is required"
!endif
!ifndef PUBLIC_KEY
  !error "PUBLIC_KEY define is required"
!endif

Name "L2D 局域网更新主机"
!define PRODUCT_GUID "{B72C06D9-3E0B-4363-BE31-691845B77710}"
OutFile "${OUTPUT_DIR}\L2DUpdateHost-Setup-${VERSION}-x64.exe"
InstallDir "$LOCALAPPDATA\Programs\L2DUpdateHost"
InstallDirRegKey HKCU "Software\4S4H1\L2DUpdateHost" "InstallDir"
VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=2052 "ProductName" "L2D 局域网更新主机"
VIAddVersionKey /LANG=2052 "CompanyName" "4S4H1"
VIAddVersionKey /LANG=2052 "FileDescription" "L2D 局域网更新主机安装程序"
VIAddVersionKey /LANG=2052 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=2052 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=2052 "LegalCopyright" "Copyright 4S4H1"
VIAddVersionKey /LANG=2052 "OriginalFilename" "L2DUpdateHost-Setup-${VERSION}-x64.exe"

!define MUI_ICON "..\build\icons\L2DUpdateHost.ico"
!define MUI_UNICON "..\build\icons\L2DUpdateHost.ico"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

Function .onInit
  nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -Command "if (Get-Process -Name L2DUpdateHost -ErrorAction SilentlyContinue) { exit 10 } else { exit 0 }"'
  Pop $0
  Pop $1
  StrCmp $0 "0" host_init_done
  IfSilent host_init_abort
  MessageBox MB_ICONEXCLAMATION|MB_OK "L2D 局域网更新主机仍在运行。请先从托盘退出，再重新安装。"
host_init_abort:
  Abort
host_init_done:
FunctionEnd

Section "L2D 更新主机（必选）" Core
  SectionIn RO
  RMDir /r "$INSTDIR.__new"
  SetOutPath "$INSTDIR.__new"
  ClearErrors
  File /r "${SOURCE_DIR}\*.*"
  IfErrors host_stage_failed
  WriteUninstaller "$INSTDIR.__new\Uninstall.exe"
  IfErrors host_stage_failed

  RMDir /r "$INSTDIR.__old"
  IfFileExists "$INSTDIR\*.*" host_has_old host_no_old
host_has_old:
  ClearErrors
  Rename "$INSTDIR" "$INSTDIR.__old"
  IfErrors host_replace_failed
  Goto host_activate
host_no_old:
  RMDir "$INSTDIR"
host_activate:
  ClearErrors
  Rename "$INSTDIR.__new" "$INSTDIR"
  IfErrors host_activate_failed
  RMDir /r "$INSTDIR.__old"
  SetOutPath "$INSTDIR"
  Goto host_files_ready

host_activate_failed:
  Rename "$INSTDIR.__old" "$INSTDIR"
host_replace_failed:
  RMDir /r "$INSTDIR.__new"
  MessageBox MB_ICONSTOP|MB_OK "无法安全替换旧 Host 文件。原安装已保留；请确认托盘程序完全退出后重试。"
  Abort
host_stage_failed:
  RMDir /r "$INSTDIR.__new"
  MessageBox MB_ICONSTOP|MB_OK "无法完整暂存新 Host 文件，安装已中止。"
  Abort

host_files_ready:
  CreateDirectory "$LOCALAPPDATA\4S4H1\L2DUpdateHost"
  SetOutPath "$LOCALAPPDATA\4S4H1\L2DUpdateHost"
  File /oname=release_public_key.pem "${PUBLIC_KEY}"
  SetOutPath "$INSTDIR"
  WriteRegStr HKCU "Software\4S4H1\L2DUpdateHost" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\4S4H1\L2DUpdateHost" "UpgradeCode" "${PRODUCT_GUID}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\L2DUpdateHost" "DisplayName" "L2D 局域网更新主机"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\L2DUpdateHost" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\L2DUpdateHost" "Publisher" "4S4H1"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\L2DUpdateHost" "DisplayIcon" "$INSTDIR\L2DUpdateHost.exe,0"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\L2DUpdateHost" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  CreateDirectory "$SMPROGRAMS\4S4H1"
  CreateShortcut "$SMPROGRAMS\4S4H1\L2D 局域网更新主机.lnk" "$INSTDIR\L2DUpdateHost.exe"
SectionEnd

Section /o "桌面快捷方式" DesktopShortcut
  CreateShortcut "$DESKTOP\L2D 局域网更新主机.lnk" "$INSTDIR\L2DUpdateHost.exe"
SectionEnd

Section /o "登录后自动启动" AutoStart
  CreateShortcut "$SMSTARTUP\L2DUpdateHost.lnk" "$INSTDIR\L2DUpdateHost.exe"
SectionEnd

Section "Uninstall"
  Delete "$DESKTOP\L2D 局域网更新主机.lnk"
  Delete "$SMSTARTUP\L2DUpdateHost.lnk"
  Delete "$SMPROGRAMS\4S4H1\L2D 局域网更新主机.lnk"
  RMDir "$SMPROGRAMS\4S4H1"
  RMDir /r "$INSTDIR"
  RMDir /r "$INSTDIR.__new"
  RMDir /r "$INSTDIR.__old"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\L2DUpdateHost"
  DeleteRegValue HKCU "Software\4S4H1\L2DUpdateHost" "InstallDir"
  ; Deliberately keep release_public_key.pem, releases, logs and settings.
SectionEnd
