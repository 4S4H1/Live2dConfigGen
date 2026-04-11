@echo off
setlocal EnableExtensions
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
  echo Python not found. Install Python 3 and add it to PATH.
  exit /b 1
)

python -c "import PyInstaller" 2>nul
if errorlevel 1 (
  echo Installing PyInstaller...
  python -m pip install -q "pyinstaller>=6"
  if errorlevel 1 exit /b 1
)

python -m pip install -q -r "%~dp0requirements.txt"
if errorlevel 1 exit /b 1

echo Building single-file GUI executable (no console)...
REM After cd, use CD for PyInstaller paths (quoted %%~dp0 ends with \ and breaks cmd quoting).
python -m PyInstaller --noconfirm --clean --onefile --windowed --name "L2DConfigEditor" --add-data "l2d_config_editor/editor_schema.json:l2d_config_editor" --paths "%CD%" "l2d_config_editor\main.py"

if errorlevel 1 (
  echo Build failed.
  exit /b 1
)

echo.
echo Output: "%~dp0dist\L2DConfigEditor.exe"
echo Place the exe next to your project files; it uses the exe folder as the workspace root.
pause
