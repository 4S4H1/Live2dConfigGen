"""Safe handoff from the running editor to a verified NSIS installer."""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

from PySide6.QtCore import QProcess


def _powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def installer_handoff_command(
    installer: str | Path,
    *,
    current_pid: int,
    restart_executable: str | Path | None,
) -> str:
    """Build the encoded-script body without invoking a command shell."""

    installer_path = Path(installer).resolve()
    if not installer_path.is_file():
        raise FileNotFoundError(installer_path)
    if current_pid <= 0:
        raise ValueError("current_pid must be positive")
    command = (
        "$ErrorActionPreference='Stop';"
        f"$installer={_powershell_quote(str(installer_path))};"
        f"Wait-Process -Id {int(current_pid)} -ErrorAction SilentlyContinue;"
        "$result=Start-Process -FilePath $installer -ArgumentList @('/S') "
        "-Wait -PassThru;"
        "if($result.ExitCode -ne 0){exit $result.ExitCode};"
    )
    if restart_executable is not None:
        restart = Path(restart_executable).resolve()
        command += (
            f"$restart={_powershell_quote(str(restart))};"
            "if(Test-Path -LiteralPath $restart){"
            "Start-Process -FilePath $restart"
            "}"
        )
    return command


def launch_installer_after_exit(
    installer: str | Path,
    *,
    current_pid: int | None = None,
    restart_executable: str | Path | None = None,
) -> bool:
    """Start a detached PowerShell waiter that installs after this process exits."""

    if sys.platform != "win32":
        return False
    command = installer_handoff_command(
        installer,
        current_pid=current_pid or os.getpid(),
        restart_executable=restart_executable,
    )
    encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
    result = QProcess.startDetached(
        "powershell.exe",
        [
            "-NoProfile",
            "-NonInteractive",
            "-WindowStyle",
            "Hidden",
            "-EncodedCommand",
            encoded,
        ],
    )
    if isinstance(result, tuple):
        return bool(result[0])
    return bool(result)
