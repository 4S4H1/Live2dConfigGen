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
    restart_host_executable: str | Path | None = None,
    host_pid: int | None = None,
    restore_host_process: bool = True,
    restore_host_service: bool = False,
    restore_host_login: bool = False,
    restore_host_run_entry: bool = False,
) -> str:
    """Build the encoded-script body without invoking a command shell."""

    installer_path = Path(installer).resolve()
    if not installer_path.is_file():
        raise FileNotFoundError(installer_path)
    if current_pid <= 0:
        raise ValueError("current_pid must be positive")
    working_directory = installer_path.parent
    command = (
        "$ErrorActionPreference='Stop';"
        f"$installer={_powershell_quote(str(installer_path))};"
        f"$workingDirectory={_powershell_quote(str(working_directory))};"
        "Set-Location -LiteralPath $workingDirectory;"
        f"Wait-Process -Id {int(current_pid)} -ErrorAction SilentlyContinue;"
    )
    if host_pid is not None:
        if int(host_pid) <= 0:
            raise ValueError("host_pid must be positive")
        command += (
            f"Wait-Process -Id {int(host_pid)} -ErrorAction SilentlyContinue;"
        )
    command += (
        "$result=Start-Process -FilePath $installer -ArgumentList @('/S') "
        "-WorkingDirectory $workingDirectory -Wait -PassThru;"
        "if($result.ExitCode -ne 0){exit $result.ExitCode};"
    )
    if restart_executable is not None:
        restart = Path(restart_executable).resolve()
        command += (
            f"$restart={_powershell_quote(str(restart))};"
            "if(Test-Path -LiteralPath $restart){"
            "Start-Process -FilePath $restart "
            "-WorkingDirectory $workingDirectory"
            "}"
        )
    if restart_host_executable is not None:
        restart_host = Path(restart_host_executable).resolve()
        host_arguments = (
            "'--restore-after-update',"
            f"'--restore-login','{int(bool(restore_host_login))}',"
            f"'--restore-run','{int(bool(restore_host_run_entry))}',"
            f"'--restore-service','{int(bool(restore_host_service))}',"
            f"'--restore-process','{int(bool(restore_host_process))}'"
        )
        command += (
            f"$restartHost={_powershell_quote(str(restart_host))};"
            "if(Test-Path -LiteralPath $restartHost){"
            "Start-Process -FilePath $restartHost "
            f"-ArgumentList @({host_arguments}) "
            "-WorkingDirectory $workingDirectory"
            "}"
        )
    return command


def launch_installer_after_exit(
    installer: str | Path,
    *,
    current_pid: int | None = None,
    restart_executable: str | Path | None = None,
    restart_host_executable: str | Path | None = None,
    host_pid: int | None = None,
    restore_host_process: bool = True,
    restore_host_service: bool = False,
    restore_host_login: bool = False,
    restore_host_run_entry: bool = False,
) -> bool:
    """Start a detached PowerShell waiter that installs after this process exits."""

    if sys.platform != "win32":
        return False
    command = installer_handoff_command(
        installer,
        current_pid=current_pid or os.getpid(),
        restart_executable=restart_executable,
        restart_host_executable=restart_host_executable,
        host_pid=host_pid,
        restore_host_process=restore_host_process,
        restore_host_service=restore_host_service,
        restore_host_login=restore_host_login,
        restore_host_run_entry=restore_host_run_entry,
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
        str(Path(installer).resolve().parent),
    )
    if isinstance(result, tuple):
        return bool(result[0])
    return bool(result)
