"""Asynchronous SVN CLI integration used by the editor toolbar."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QStandardPaths, QTimer, Signal


SVN_QUERY_TIMEOUT_MS = 60_000
SVN_LOG_PAGE_SIZE = 100


@dataclass(frozen=True)
class SvnFileInfo:
    local_path: Path
    url: str
    repository_root: str
    repository_uuid: str
    revision: int
    working_copy_status: str
    copy_from_url: str = ""
    copy_from_revision: int | None = None

    @property
    def has_history(self) -> bool:
        return self.working_copy_status not in {"added", "unversioned"} or bool(self.copy_from_url)

    @property
    def history_target(self) -> str:
        if self.working_copy_status == "added" and self.copy_from_url and self.copy_from_revision:
            return f"{self.copy_from_url}@{self.copy_from_revision}"
        peg = self.revision if self.revision > 0 else "HEAD"
        return f"{self.url}@{peg}"


@dataclass(frozen=True)
class SvnRevision:
    revision: int
    author: str = ""
    date: str = ""
    message: str = ""


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


def decode_svn_output(payload: bytes) -> str:
    for encoding in ("utf-8", "gb18030"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode(errors="replace")


class SvnProcessExecutor(QObject):
    """Reusable argument-array QProcess transport for every SVN operation."""

    stdoutReceived = Signal(object)
    stderrReceived = Signal(object)
    finished = Signal(int, object)
    failedToStart = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("LC_ALL", "C")
        self.process.setProcessEnvironment(environment)
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)

    def start(self, executable: str | Path, arguments: list[str]) -> None:
        if self.process.state() != QProcess.ProcessState.NotRunning:
            raise RuntimeError("SVN process is already running")
        self.process.start(str(executable), list(arguments))

    def cancel(self) -> None:
        if self.process.state() == QProcess.ProcessState.NotRunning:
            return
        self.process.terminate()
        QTimer.singleShot(1000, self._kill_if_running)

    def kill(self) -> None:
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()

    def _kill_if_running(self) -> None:
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()

    def _read_stdout(self) -> None:
        chunk = bytes(self.process.readAllStandardOutput())
        if chunk:
            self.stdoutReceived.emit(chunk)

    def _read_stderr(self) -> None:
        chunk = bytes(self.process.readAllStandardError())
        if chunk:
            self.stderrReceived.emit(chunk)

    def _finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        self._read_stdout()
        self._read_stderr()
        self.finished.emit(exit_code, exit_status)

    def _error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self.failedToStart.emit()


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


def _xml_fragment(payload: str, root_name: str) -> str:
    start = payload.find(f"<{root_name}")
    end_marker = f"</{root_name}>"
    end = payload.rfind(end_marker)
    if start < 0 or end < 0:
        raise ElementTree.ParseError(f"SVN output does not contain {root_name} XML")
    return payload[start : end + len(end_marker)]


def _positive_revision(value: str | None) -> int | None:
    if value is None or not re.fullmatch(r"[1-9][0-9]*", value.strip()):
        return None
    return int(value)


def parse_info_xml(payload: str, local_path: str | Path) -> SvnFileInfo:
    root = ElementTree.fromstring(_xml_fragment(payload, "info"))
    entry = root.find("entry")
    if entry is None or entry.attrib.get("kind") != "file":
        raise ValueError("当前目标不是受 SVN 管理的文件")
    url = (entry.findtext("url") or "").strip()
    repository_root = (entry.findtext("repository/root") or "").strip()
    repository_uuid = (entry.findtext("repository/uuid") or "").strip()
    revision = _positive_revision(entry.attrib.get("revision")) or 0
    wc_info = entry.find("wc-info")
    schedule = ((wc_info.findtext("schedule") if wc_info is not None else None) or "normal").strip()
    # Subversion emits ``add`` here; normalize it to the user-facing/status
    # vocabulary used by the rest of the history pipeline.
    if schedule == "add":
        schedule = "added"
    copy_from_url = ((wc_info.findtext("copy-from-url") if wc_info is not None else None) or "").strip()
    copy_from_revision = _positive_revision(
        wc_info.findtext("copy-from-rev") if wc_info is not None else None
    )
    if not url or not repository_root or not repository_uuid:
        raise ValueError("SVN info 缺少仓库 URL 或 UUID")
    return SvnFileInfo(
        local_path=Path(local_path).resolve(),
        url=url,
        repository_root=repository_root,
        repository_uuid=repository_uuid,
        revision=revision,
        working_copy_status=schedule,
        copy_from_url=copy_from_url,
        copy_from_revision=copy_from_revision,
    )


def parse_log_xml(payload: str) -> list[SvnRevision]:
    root = ElementTree.fromstring(_xml_fragment(payload, "log"))
    revisions: list[SvnRevision] = []
    seen: set[int] = set()
    for entry in root.findall("logentry"):
        revision = _positive_revision(entry.attrib.get("revision"))
        if revision is None or revision in seen:
            continue
        seen.add(revision)
        revisions.append(
            SvnRevision(
                revision=revision,
                author=(entry.findtext("author") or "").strip(),
                date=(entry.findtext("date") or "").strip(),
                message=(entry.findtext("msg") or "").strip(),
            )
        )
    revisions.sort(key=lambda item: item.revision, reverse=True)
    return revisions


def svn_error_message(stderr: str, phase: str) -> str:
    lowered = stderr.lower()
    if "authentication" in lowered or "authorization failed" in lowered or "e170001" in lowered:
        return "SVN 认证失败。请先在 SVN 客户端中登录并缓存凭据后重试。"
    if "certificate" in lowered or "server certificate verification failed" in lowered:
        return "SVN 证书未受信任。请在 SVN 客户端中人工核验证书后重试。"
    if "could not resolve hostname" in lowered or "connection timed out" in lowered or "e170013" in lowered:
        return "无法连接 SVN 仓库。请检查网络、代理或离线状态后重试。"
    if "is not a working copy" in lowered or "not a versioned resource" in lowered or "w155010" in lowered:
        return "当前 JSON 未纳入 SVN 版本控制，因此没有 SVN 历史。"
    if "path not found" in lowered or "non-existent in that revision" in lowered or "e160013" in lowered:
        return "所选 SVN 路径或版本不存在，可能已被删除或历史不可访问。"
    detail = stderr.strip().splitlines()[-1] if stderr.strip() else "未返回错误详情"
    return f"SVN {phase} 查询失败：{detail}"


class SvnHistoryRunner(QObject):
    """Asynchronous, in-memory info/log/cat query pipeline."""

    phaseChanged = Signal(str)
    outputReceived = Signal(str)
    busyChanged = Signal(bool)
    infoReady = Signal(object)
    revisionsReady = Signal(object, bool)
    contentReady = Signal(int, bytes)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        executable: str | Path,
        parent: QObject | None = None,
        *,
        executor: SvnProcessExecutor | None = None,
    ) -> None:
        super().__init__(parent)
        self.executable = str(executable)
        self.executor = executor or SvnProcessExecutor(self)
        self.process = self.executor.process
        self.executor.stdoutReceived.connect(self._receive_stdout)
        self.executor.stderrReceived.connect(self._receive_stderr)
        self.executor.finished.connect(self._process_finished)
        self.executor.failedToStart.connect(self._process_failed_to_start)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._timeout)
        self._phase = "idle"
        self._stdout = bytearray()
        self._stderr = bytearray()
        self._cancel_requested = False
        self._timed_out = False
        self._failed_to_start = False
        self._info: SvnFileInfo | None = None
        self._info_path: Path | None = None
        self._next_log_revision: int | None = None
        self._cat_revision: int | None = None

    @property
    def info(self) -> SvnFileInfo | None:
        return self._info

    @property
    def is_busy(self) -> bool:
        return self._phase != "idle"

    def query_info(self, file_path: str | Path) -> None:
        target = Path(file_path).resolve()
        if target.suffix.lower() != ".json":
            raise ValueError("SVN 图表 Diff 仅支持当前 JSON 文件")
        self._info = None
        self._next_log_revision = None
        self._info_path = target
        self._run(
            "info",
            ["info", "--xml", "--depth", "empty", "--", str(target)],
            "正在读取当前文件的 SVN 信息…",
        )

    def query_revisions(self, *, reset: bool = True) -> None:
        info = self._require_info()
        if not info.has_history:
            self.revisionsReady.emit([], False)
            return
        if reset:
            revision_range = "HEAD:0"
            self._next_log_revision = None
        else:
            if self._next_log_revision is None or self._next_log_revision < 1:
                self.revisionsReady.emit([], False)
                return
            revision_range = f"{self._next_log_revision}:0"
        self._run(
            "log",
            [
                "log",
                "--xml",
                "-r",
                revision_range,
                "--limit",
                str(SVN_LOG_PAGE_SIZE),
                "--",
                info.history_target,
            ],
            "正在读取 SVN 文件历史…",
        )

    def query_content(self, revision: int) -> None:
        info = self._require_info()
        revision_text = str(revision)
        if not re.fullmatch(r"[1-9][0-9]*", revision_text):
            raise ValueError("SVN 版本号必须是正整数")
        self._cat_revision = int(revision_text)
        self._run(
            "cat",
            [
                "cat",
                "--non-interactive",
                "-r",
                revision_text,
                "--",
                info.history_target,
            ],
            f"正在读取 SVN r{revision_text}…",
        )

    def cancel(self) -> None:
        if not self.is_busy:
            return
        self._cancel_requested = True
        self.executor.cancel()

    def _require_info(self) -> SvnFileInfo:
        if self._info is None:
            raise RuntimeError("必须先查询并验证当前文件的 SVN info")
        return self._info

    def _run(self, phase: str, arguments: list[str], label: str) -> None:
        if self.is_busy or self.process.state() != QProcess.ProcessState.NotRunning:
            raise RuntimeError("SVN 查询仍在进行中")
        self._phase = phase
        self._stdout.clear()
        self._stderr.clear()
        self._cancel_requested = False
        self._timed_out = False
        self._failed_to_start = False
        self.phaseChanged.emit(label)
        self.outputReceived.emit(f"> svn {' '.join(arguments)}\n")
        self.busyChanged.emit(True)
        self._timer.start(SVN_QUERY_TIMEOUT_MS)
        self.executor.start(self.executable, arguments)

    def _receive_stdout(self, chunk: bytes) -> None:
        self._stdout.extend(chunk)
        if chunk and self._phase != "cat":
            self.outputReceived.emit(self._decode(chunk))

    def _receive_stderr(self, chunk: bytes) -> None:
        self._stderr.extend(chunk)
        if chunk:
            self.outputReceived.emit(self._decode(chunk))

    def _process_finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        if self._phase == "idle":
            return
        phase = self._phase
        stdout = bytes(self._stdout)
        stderr = self._decode(bytes(self._stderr))
        self._finish_query()
        if self._cancel_requested:
            self.cancelled.emit()
            return
        if self._timed_out:
            self.failed.emit("SVN 查询超过 60 秒，已取消。请检查网络或仓库状态。")
            return
        if self._failed_to_start:
            return
        if exit_code != 0:
            self.failed.emit(svn_error_message(stderr, phase))
            return
        try:
            decoded = self._decode(stdout)
            if phase == "info":
                if self._info_path is None:
                    raise ValueError("缺少当前 JSON 路径")
                self._info = parse_info_xml(decoded, self._info_path)
                self.infoReady.emit(self._info)
            elif phase == "log":
                revisions = parse_log_xml(decoded)
                has_more = len(revisions) >= SVN_LOG_PAGE_SIZE and revisions[-1].revision > 1
                self._next_log_revision = revisions[-1].revision - 1 if has_more else None
                self.revisionsReady.emit(revisions, has_more)
            elif phase == "cat":
                if self._cat_revision is None:
                    raise ValueError("缺少 SVN 版本号")
                self.contentReady.emit(self._cat_revision, stdout)
        except (ElementTree.ParseError, UnicodeDecodeError, ValueError) as exc:
            self.failed.emit(f"无法解析 SVN {phase} 返回结果：{exc}")

    def _process_failed_to_start(self) -> None:
        if self._phase == "idle":
            return
        self._failed_to_start = True
        self._finish_query()
        self.failed.emit("无法启动 SVN CLI，请重新选择 svn.exe。")

    def _timeout(self) -> None:
        if not self.is_busy:
            return
        self._timed_out = True
        self.executor.kill()

    def _finish_query(self) -> None:
        self._timer.stop()
        self._phase = "idle"
        self.busyChanged.emit(False)

    @staticmethod
    def _decode(payload: bytes) -> str:
        return decode_svn_output(payload)


class SvnCommitRunner(QObject):
    """Save-independent SVN add/commit pipeline driven by one QProcess."""

    phaseChanged = Signal(str)
    outputReceived = Signal(str)
    finished = Signal(bool, str)

    def __init__(self, executable: str | Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.executable = str(executable)
        self.executor = SvnProcessExecutor(self)
        self.process = self.executor.process
        self.executor.stdoutReceived.connect(self._receive_output)
        self.executor.stderrReceived.connect(self._receive_error)
        self.executor.finished.connect(self._process_finished)
        self.executor.failedToStart.connect(self._process_failed_to_start)
        self._phase = "idle"
        self._buffer = bytearray()
        self._error_buffer = bytearray()
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
            self.executor.cancel()
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
        self._error_buffer.clear()
        labels = {
            "status": "检查 SVN 状态…",
            "add": "文件尚未纳入版本控制，正在自动添加…",
            "post_add_status": "确认自动添加的提交范围…",
            "commit": "正在提交到 SVN…",
        }
        self.phaseChanged.emit(labels[phase])
        self.outputReceived.emit(f"> svn {' '.join(arguments)}\n")
        self.executor.start(self.executable, arguments)

    def _receive_output(self, chunk: bytes) -> None:
        if not chunk:
            return
        self._buffer.extend(chunk)
        self.outputReceived.emit(self._decode(chunk))

    def _receive_error(self, chunk: bytes) -> None:
        if chunk:
            self._error_buffer.extend(chunk)
            self.outputReceived.emit(self._decode(chunk))

    @staticmethod
    def _decode(payload: bytes) -> str:
        return decode_svn_output(payload)

    def _process_finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        if self._done:
            return
        if self._cancelled:
            self._finish(False, "提交已取消。")
            return
        output = self._decode(bytes(self._buffer))
        if exit_code != 0 and self._error_buffer:
            self._finish(
                False,
                svn_error_message(
                    self._decode(bytes(self._error_buffer)),
                    self._phase,
                ),
            )
            return
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

    def _process_failed_to_start(self) -> None:
        if self._done or self._cancelled:
            return
        self._finish(False, "无法启动 SVN CLI，请重新选择 svn.exe。")

    def _finish(self, success: bool, message: str) -> None:
        if self._done:
            return
        self._done = True
        self.finished.emit(success, message)
