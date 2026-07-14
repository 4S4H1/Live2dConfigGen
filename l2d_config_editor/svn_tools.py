"""Asynchronous SVN CLI integration used by the editor toolbar."""

from __future__ import annotations

import os
from pathlib import Path
from xml.etree import ElementTree

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, QStandardPaths, pyqtSignal


def discover_svn_executable(saved_path: str | None = None) -> Path | None:
    """Find an SVN CLI executable without starting a blocking subprocess."""

    candidates: list[Path] = []
    if saved_path:
        candidates.append(Path(saved_path).expanduser())
    found = QStandardPaths.findExecutable("svn.exe" if os.name == "nt" else "svn")
    if found:
        candidates.append(Path(found))
    if os.name == "nt":
        for environment_name in ("ProgramFiles", "ProgramFiles(x86)"):
            root = os.environ.get(environment_name)
            if root:
                candidates.extend(
                    [
                        Path(root) / "TortoiseSVN" / "bin" / "svn.exe",
                        Path(root) / "SlikSvn" / "bin" / "svn.exe",
                        Path(root) / "VisualSVN Server" / "bin" / "svn.exe",
                    ]
                )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def parse_status_xml(payload: str) -> list[tuple[Path, str]]:
    """Return ``(path, wc-status item)`` pairs from ``svn status --xml``."""

    start = payload.find("<status")
    end = payload.rfind("</status>")
    if start < 0 or end < 0:
        raise ElementTree.ParseError("SVN output does not contain status XML")
    root = ElementTree.fromstring(payload[start : end + len("</status>")])
    entries: list[tuple[Path, str]] = []
    for entry in root.findall(".//entry"):
        status = entry.find("wc-status")
        if status is None:
            continue
        entries.append((Path(entry.attrib.get("path", "")), status.attrib.get("item", "none")))
    return entries


class SvnCommitRunner(QObject):
    """Save-independent SVN add/commit pipeline driven by one QProcess."""

    phaseChanged = pyqtSignal(str)
    outputReceived = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, executable: str | Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.executable = str(executable)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("LC_ALL", "C")
        self.process.setProcessEnvironment(environment)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.readyReadStandardError.connect(self._read_error)
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(self._process_error)
        self._phase = "idle"
        self._buffer = bytearray()
        self._file_path = Path()
        self._workspace_root = Path()
        self._message = ""
        self._candidate_targets: list[Path] = []
        self._cancelled = False
        self._done = False

    def start(self, file_path: str | Path, message: str, workspace_root: str | Path) -> None:
        if self.process.state() != QProcess.ProcessState.NotRunning:
            raise RuntimeError("SVN process is already running")
        self._file_path = Path(file_path).resolve()
        self._workspace_root = Path(workspace_root).resolve()
        self._message = str(message)
        self._candidate_targets = self._build_candidate_targets()
        self._cancelled = False
        self._done = False
        self._run("status", ["status", "--xml", "--depth", "empty", str(self._file_path)])

    def cancel(self) -> None:
        if self._done:
            return
        self._cancelled = True
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.terminate()
            if not self.process.waitForFinished(1000):
                self.process.kill()
        else:
            self._finish(False, "提交已取消。")

    def _build_candidate_targets(self) -> list[Path]:
        try:
            relative_parent = self._file_path.parent.relative_to(self._workspace_root)
        except ValueError:
            return [self._file_path]
        targets: list[Path] = [self._workspace_root]
        cursor = self._workspace_root
        for part in relative_parent.parts:
            cursor = cursor / part
            targets.append(cursor)
        targets.append(self._file_path)
        return targets

    def _run(self, phase: str, arguments: list[str]) -> None:
        self._phase = phase
        self._buffer.clear()
        labels = {
            "status": "检查 SVN 状态…",
            "add": "文件尚未纳入版本控制，正在自动添加…",
            "post_add_status": "确认自动添加的提交范围…",
            "commit": "正在提交到 SVN…",
        }
        self.phaseChanged.emit(labels[phase])
        self.outputReceived.emit(f"> svn {' '.join(arguments)}\n")
        self.process.start(self.executable, arguments)

    def _read_output(self) -> None:
        chunk = bytes(self.process.readAllStandardOutput())
        if not chunk:
            return
        self._buffer.extend(chunk)
        self.outputReceived.emit(self._decode(chunk))

    def _read_error(self) -> None:
        chunk = bytes(self.process.readAllStandardError())
        if chunk:
            self.outputReceived.emit(self._decode(chunk))

    @staticmethod
    def _decode(payload: bytes) -> str:
        for encoding in ("utf-8", "gb18030"):
            try:
                return payload.decode(encoding)
            except UnicodeDecodeError:
                continue
        return payload.decode(errors="replace")

    def _process_finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        self._read_output()
        self._read_error()
        if self._done:
            return
        if self._cancelled:
            self._finish(False, "提交已取消。")
            return
        output = self._decode(bytes(self._buffer))
        if exit_code != 0:
            self._finish(False, f"SVN {self._phase} 失败（退出码 {exit_code}）。")
            return
        try:
            if self._phase == "status":
                self._handle_initial_status(output)
            elif self._phase == "add":
                arguments = ["status", "--xml", "--depth", "empty"]
                arguments.extend(str(path) for path in self._candidate_targets)
                self._run("post_add_status", arguments)
            elif self._phase == "post_add_status":
                self._handle_post_add_status(output)
            elif self._phase == "commit":
                self._finish(True, f"已成功提交 {self._file_path.name}。")
        except (ElementTree.ParseError, ValueError) as exc:
            self._finish(False, f"无法解析 SVN 返回结果：{exc}")

    def _handle_initial_status(self, output: str) -> None:
        entries = parse_status_xml(output)
        states = {state for _path, state in entries}
        unsafe = states & {"conflicted", "missing", "obstructed", "incomplete"}
        if unsafe:
            self._finish(False, f"文件处于不可提交状态：{', '.join(sorted(unsafe))}。")
            return
        if "unversioned" in states:
            self._run("add", ["add", "--parents", str(self._file_path)])
            return
        if not (states & {"added", "modified", "replaced", "deleted"}):
            self._finish(True, "文件内容与 SVN 版本一致，无需提交。")
            return
        self._commit([self._file_path])

    def _handle_post_add_status(self, output: str) -> None:
        entries = parse_status_xml(output)
        added_paths = [path.resolve() for path, state in entries if state == "added"]
        targets = [path for path in self._candidate_targets if path.resolve() in added_paths]
        if self._file_path not in targets:
            targets.append(self._file_path)
        self._commit(targets)

    def _commit(self, targets: list[Path]) -> None:
        arguments = ["commit", "--non-interactive", "--depth", "empty", "--message", self._message]
        arguments.extend(str(path) for path in targets)
        self._run("commit", arguments)

    def _process_error(self, error: QProcess.ProcessError) -> None:
        if self._done or self._cancelled:
            return
        if error == QProcess.ProcessError.FailedToStart:
            self._finish(False, "无法启动 SVN CLI，请重新选择 svn.exe。")

    def _finish(self, success: bool, message: str) -> None:
        if self._done:
            return
        self._done = True
        self.finished.emit(success, message)
