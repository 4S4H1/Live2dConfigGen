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
!define MUI_ICON "..\build\icons\L2DConfigEditor.ico"
!define MUI_UNICON "..\build\icons\L2DConfigEditor.ico"
!define MUI_STARTMENUPAGE_REGISTRY_ROOT "HKCU"
!define MUI_STARTMENUPAGE_REGISTRY_KEY "Software\4S4H1\L2DConfigEditor"
!define MUI_STARTMENUPAGE_REGISTRY_VALUENAME "StartMenuFolder"

Var StartMenuFolder

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_STARTMENU Application $StartMenuFolder
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

Function .onInit
  nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -Command "if (Get-Process -Name L2DConfigEditor -ErrorAction SilentlyContinue) { exit 10 } else { exit 0 }"'
  Pop $0
  Pop $1
  StrCmp $0 "0" editor_init_done
  IfSilent editor_init_abort
  MessageBox MB_ICONEXCLAMATION|MB_OK "L2D 交互图表编辑器仍在运行。请先退出程序，再重新安装。"
editor_init_abort:
  Abort
editor_init_done:
FunctionEnd

Section "L2D 交互图表编辑器（必选）" Core
  SectionIn RO
  RMDir /r "$INSTDIR.__new"
  SetOutPath "$INSTDIR.__new"
  ClearErrors
  File /r "${SOURCE_DIR}\*.*"
  IfErrors editor_stage_failed
  WriteUninstaller "$INSTDIR.__new\Uninstall.exe"
  IfErrors editor_stage_failed

  RMDir /r "$INSTDIR.__old"
  IfFileExists "$INSTDIR\*.*" editor_has_old editor_no_old
editor_has_old:
  ClearErrors
  Rename "$INSTDIR" "$INSTDIR.__old"
  IfErrors editor_replace_failed
  Goto editor_activate
editor_no_old:
  RMDir "$INSTDIR"
editor_activate:
  ClearErrors
  Rename "$INSTDIR.__new" "$INSTDIR"
  IfErrors editor_activate_failed
  RMDir /r "$INSTDIR.__old"
  SetOutPath "$INSTDIR"
  Goto editor_files_ready

editor_activate_failed:
  Rename "$INSTDIR.__old" "$INSTDIR"
editor_replace_failed:
  RMDir /r "$INSTDIR.__new"
  MessageBox MB_ICONSTOP|MB_OK "无法安全替换旧程序文件。原安装已保留；请确认程序完全退出后重试。"
  Abort
editor_stage_failed:
  RMDir /r "$INSTDIR.__new"
  MessageBox MB_ICONSTOP|MB_OK "无法完整暂存新程序文件，安装已中止。"
  Abort

editor_files_ready:
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
SectionEnd

Section /o "桌面快捷方式" DesktopShortcut
  CreateShortcut "$DESKTOP\L2D 交互图表编辑器.lnk" "$INSTDIR\L2DConfigEditor.exe"
SectionEnd

Section "Uninstall"
  Delete "$DESKTOP\L2D 交互图表编辑器.lnk"
  !insertmacro MUI_STARTMENU_GETFOLDER Application $StartMenuFolder
  RMDir /r "$SMPROGRAMS\$StartMenuFolder"
  RMDir /r "$INSTDIR"
  RMDir /r "$INSTDIR.__new"
  RMDir /r "$INSTDIR.__old"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\L2DConfigEditor"
  DeleteRegKey HKCU "Software\Classes\Applications\L2DConfigEditor.exe"
  DeleteRegValue HKCU "Software\4S4H1\L2DConfigEditor" "InstallDir"
SectionEnd
