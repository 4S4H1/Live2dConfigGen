"""Built-in OpenAI-compatible chat panel for editor tool calling."""

from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import os
from pathlib import Path
from typing import Any, Callable
import uuid

from PySide6.QtCore import QByteArray, QSettings, QStandardPaths, QTimer, QUrl, Signal
from PySide6.QtNetwork import (
    QNetworkAccessManager,
    QNetworkReply,
    QNetworkRequest,
)
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .app_settings import create_app_settings
from .tool_service import EditorToolService


SETTINGS_BASE_URL = "llm/base_url"
SETTINGS_MODEL = "llm/model"
SETTINGS_API_KEY_ENV = "llm/api_key_environment_variable"
DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_API_KEY_ENV = "OPENAI_API_KEY"
MAX_TOOL_ROUNDS = 6
MAX_TOOL_CALLS_PER_ROUND = 32
REQUEST_TIMEOUT_MS = 90_000
MAX_HISTORY_MESSAGES = 200
MAX_HISTORY_BYTES = 2 * 1024 * 1024
MAX_HISTORY_TOTAL_BYTES = 32 * 1024 * 1024
MAX_HISTORY_FILES = 256
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_STREAM_CONTENT_CHARS = 1 * 1024 * 1024
MAX_TOOL_ARGUMENT_CHARS = 512 * 1024

_SYSTEM_PROMPT = """You are the L2D Config Editor assistant.
Use only the supplied editor tools for graph facts and graph changes.
Read the current graph before editing and pass its current revision to every
mutation. Put all related graph changes into one apply_graph_edits call, and
emit at most one confirmation-required tool call per assistant turn. Never
invent node UUIDs. Explain the result to the user concisely."""


def chat_completions_url(base_url: str) -> QUrl:
    """Resolve a configured service root to its chat-completions endpoint."""

    text = str(base_url or "").strip().rstrip("/")
    if text.endswith("/chat/completions"):
        return QUrl(text)
    if text.endswith("/v1"):
        return QUrl(f"{text}/chat/completions")
    return QUrl(f"{text}/v1/chat/completions")


def validated_chat_completions_url(
    base_url: str,
    *,
    has_api_key: bool = False,
) -> QUrl:
    """Return a safe HTTP(S) endpoint or raise a user-facing error."""

    url = chat_completions_url(base_url)
    if (
        not url.isValid()
        or url.scheme().lower() not in {"http", "https"}
        or not url.host()
        or url.userName()
        or url.password()
    ):
        raise ValueError("Base URL 必须是无内嵌凭据的 HTTP(S) 地址")
    if has_api_key and url.scheme().lower() == "http":
        host = url.host().strip("[]").casefold()
        is_loopback = host == "localhost"
        if not is_loopback:
            try:
                is_loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                is_loopback = False
        if not is_loopback:
            raise ValueError("带 API Key 的远程服务必须使用 HTTPS")
    return url


class ChatHistoryStore:
    """Bounded per-document history stored outside business JSON/CSV files."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        max_messages: int = MAX_HISTORY_MESSAGES,
        max_bytes: int = MAX_HISTORY_BYTES,
        max_total_bytes: int = MAX_HISTORY_TOTAL_BYTES,
        max_files: int = MAX_HISTORY_FILES,
    ) -> None:
        if root is None:
            app_data = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppLocalDataLocation
            )
            root = Path(app_data) / "ai_chat_history"
        self.root = Path(root)
        self.max_messages = max(1, int(max_messages))
        self.max_bytes = max(4096, int(max_bytes))
        self.max_total_bytes = max(self.max_bytes, int(max_total_bytes))
        self.max_files = max(1, int(max_files))

    @staticmethod
    def _history_key(identity: str) -> str:
        normalized = str(identity or "__unsaved__")
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def path_for(self, identity: str) -> Path:
        return self.root / f"{self._history_key(identity)}.json"

    def load(self, identity: str) -> list[dict[str, Any]]:
        path = self.path_for(identity)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return []
        try:
            os.utime(path, None)
        except OSError:
            pass
        messages = payload.get("messages") if isinstance(payload, dict) else None
        if not isinstance(messages, list):
            return []
        return self.bounded_messages([
            message
            for message in messages
            if isinstance(message, dict)
            and str(message.get("role") or "") in {"user", "assistant", "tool"}
        ])

    def bounded_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Bound both in-memory requests and persisted history."""

        bounded = [
            dict(message)
            for message in messages[-self.max_messages :]
            if isinstance(message, dict)
            and str(message.get("role") or "") in {"user", "assistant", "tool"}
        ]
        while bounded:
            encoded = (
                json.dumps(
                    {"messages": bounded},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
            if len(encoded) <= self.max_bytes:
                break
            bounded.pop(0)
        # A retained tool response without its assistant tool_call is invalid.
        while bounded and bounded[0].get("role") == "tool":
            bounded.pop(0)
        normalized: list[dict[str, Any]] = []
        index = 0
        while index < len(bounded):
            message = bounded[index]
            calls = message.get("tool_calls") if message.get("role") == "assistant" else None
            if not isinstance(calls, list) or not calls:
                if message.get("role") != "tool":
                    normalized.append(message)
                index += 1
                continue
            expected = [
                str(call.get("id") or "")
                for call in calls
                if isinstance(call, dict)
            ]
            following: list[dict[str, Any]] = []
            cursor = index + 1
            while cursor < len(bounded) and bounded[cursor].get("role") == "tool":
                following.append(bounded[cursor])
                cursor += 1
            actual = [str(item.get("tool_call_id") or "") for item in following]
            if expected and all(call_id in actual for call_id in expected):
                normalized.append(message)
                normalized.extend(following)
            elif str(message.get("content") or ""):
                without_calls = dict(message)
                without_calls.pop("tool_calls", None)
                normalized.append(without_calls)
            index = cursor
        return normalized

    def save(self, identity: str, messages: list[dict[str, Any]]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        bounded = self.bounded_messages(messages)
        payload = (
            json.dumps(
                {"messages": bounded},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        )
        encoded = payload.encode("utf-8")
        if len(encoded) > self.max_bytes:
            # The empty envelope is always below the constructor's 4 KiB floor.
            encoded = b'{"messages":[]}\n'
        target = self.path_for(identity)
        temp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temp.open("wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, target)
        finally:
            if temp.exists():
                try:
                    temp.unlink()
                except OSError:
                    pass
        self._prune(protected=target)

    def _prune(self, *, protected: Path | None = None) -> None:
        try:
            files = [path for path in self.root.glob("*.json") if path.is_file()]
            entries = sorted(
                (
                    (path.stat().st_mtime_ns, path.stat().st_size, path)
                    for path in files
                ),
                key=lambda item: (item[0], item[2].name),
            )
        except OSError:
            return
        total = sum(size for _mtime, size, _path in entries)
        count = len(entries)
        for _mtime, size, path in entries:
            if total <= self.max_total_bytes and count <= self.max_files:
                break
            if protected is not None and path == protected:
                continue
            try:
                path.unlink()
            except OSError:
                continue
            total -= size
            count -= 1

    def clear(self, identity: str) -> None:
        path = self.path_for(identity)
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def migrate(self, old_identity: str, new_identity: str) -> None:
        if old_identity == new_identity:
            return
        old_messages = self.load(old_identity)
        if not old_messages:
            return
        combined = [*self.load(new_identity), *old_messages]
        self.save(new_identity, combined)
        self.clear(old_identity)


class ChatStreamAccumulator:
    """Incremental parser for OpenAI-compatible SSE response bodies."""

    def __init__(
        self,
        *,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        max_content_chars: int = MAX_STREAM_CONTENT_CHARS,
        max_tool_argument_chars: int = MAX_TOOL_ARGUMENT_CHARS,
        max_tool_calls: int = MAX_TOOL_CALLS_PER_ROUND,
    ) -> None:
        self._buffer = bytearray()
        self.raw = bytearray()
        self.content_parts: list[str] = []
        self._content_chars = 0
        self.tool_calls: dict[int, dict[str, Any]] = {}
        self.done = False
        self.error: dict[str, Any] | None = None
        self.saw_sse_data = False
        self.max_response_bytes = max(1024, int(max_response_bytes))
        self.max_content_chars = max(1024, int(max_content_chars))
        self.max_tool_argument_chars = max(
            1024,
            int(max_tool_argument_chars),
        )
        self.max_tool_calls = max(1, int(max_tool_calls))

    def feed(self, data: bytes | QByteArray) -> list[str]:
        chunk = bytes(data)
        if self.error is not None:
            return []
        if len(self.raw) + len(chunk) > self.max_response_bytes:
            self.error = {
                "code": "RESPONSE_TOO_LARGE",
                "message": "LLM 响应超过安全大小限制",
            }
            self._buffer.clear()
            return []
        self.raw.extend(chunk)
        self._buffer.extend(chunk)
        visible: list[str] = []
        while b"\n" in self._buffer:
            line, _, remainder = self._buffer.partition(b"\n")
            self._buffer = bytearray(remainder)
            line = line.rstrip(b"\r")
            if not line.startswith(b"data:"):
                continue
            self.saw_sse_data = True
            raw_data = line[5:].strip()
            if raw_data == b"[DONE]":
                self.done = True
                continue
            if not raw_data:
                continue
            try:
                payload = json.loads(raw_data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            visible.extend(self._consume_payload(payload))
        return visible

    def finish(self) -> list[str]:
        if self._buffer:
            return self.feed(b"\n")
        return []

    def _consume_payload(self, payload: Any) -> list[str]:
        if not isinstance(payload, dict):
            return []
        if isinstance(payload.get("error"), dict):
            self.error = dict(payload["error"])
            return []
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return []
        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            delta = choice.get("message")
        if not isinstance(delta, dict):
            return []
        content = delta.get("content")
        visible: list[str] = []
        if isinstance(content, str) and content:
            if self._content_chars + len(content) > self.max_content_chars:
                self.error = {
                    "code": "RESPONSE_TOO_LARGE",
                    "message": "LLM 文本响应超过安全大小限制",
                }
                return []
            self.content_parts.append(content)
            self._content_chars += len(content)
            visible.append(content)
        raw_calls = delta.get("tool_calls")
        if isinstance(raw_calls, list):
            for fallback_index, raw_call in enumerate(raw_calls):
                if not isinstance(raw_call, dict):
                    continue
                raw_index = raw_call.get("index", fallback_index)
                try:
                    index = int(raw_index)
                except (TypeError, ValueError):
                    index = fallback_index
                if (
                    index not in self.tool_calls
                    and len(self.tool_calls) >= self.max_tool_calls
                ):
                    self.error = {
                        "code": "TOO_MANY_TOOL_CALLS",
                        "message": "LLM 单轮工具调用数量超过安全上限",
                    }
                    return []
                call = self.tool_calls.setdefault(
                    index,
                    {
                        "id": "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    },
                )
                if raw_call.get("id"):
                    call["id"] = str(raw_call["id"])
                function = raw_call.get("function")
                if isinstance(function, dict):
                    if function.get("name"):
                        call["function"]["name"] += str(function["name"])
                    if function.get("arguments"):
                        fragment = str(function["arguments"])
                        if (
                            len(call["function"]["arguments"]) + len(fragment)
                            > self.max_tool_argument_chars
                        ):
                            self.error = {
                                "code": "RESPONSE_TOO_LARGE",
                                "message": "LLM 工具参数超过安全大小限制",
                            }
                            return []
                        call["function"]["arguments"] += fragment
        return visible

    def assistant_message(self) -> dict[str, Any]:
        message: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(self.content_parts),
        }
        if self.tool_calls:
            calls: list[dict[str, Any]] = []
            for index, call in sorted(self.tool_calls.items()):
                resolved = dict(call)
                if not resolved.get("id"):
                    resolved["id"] = f"tool_call_{index}"
                calls.append(resolved)
            message["tool_calls"] = calls
        return message


def parse_chat_completion(payload: bytes | str) -> dict[str, Any]:
    """Parse a non-streaming compatible response into one assistant message."""

    raw = payload.encode("utf-8") if isinstance(payload, str) else payload
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("The LLM service returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise ValueError("The LLM service returned an invalid response")
    if isinstance(decoded.get("error"), dict):
        message = str(decoded["error"].get("message") or "LLM service error")
        raise ValueError(message)
    choices = decoded.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("The LLM service returned no choices")
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message")
    if not isinstance(message, dict):
        raise ValueError("The LLM service returned no assistant message")
    result: dict[str, Any] = {
        "role": "assistant",
        "content": str(message.get("content") or ""),
    }
    if isinstance(message.get("tool_calls"), list):
        result["tool_calls"] = [
            dict(call) for call in message["tool_calls"] if isinstance(call, dict)
        ]
    return result


class _ChangePreviewDialog(QDialog):
    def __init__(self, previews: list[dict[str, Any]], parent: QWidget | None) -> None:
        super().__init__(parent)
        self.setWindowTitle("确认 AI 变更")
        self.resize(680, 480)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                f"AI 本轮请求执行 {len(previews)} 个写操作。确认后将按编辑器事务提交："
            )
        )
        content = QPlainTextEdit(self)
        content.setReadOnly(True)
        content.setPlainText(json.dumps(previews, ensure_ascii=False, indent=2))
        layout.addWidget(content, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确认执行")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("拒绝")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class LLMChatPanel(QWidget):
    """Right-side collapsible chat panel with bounded tool-call loops."""

    statusChanged = Signal(str)
    busyChanged = Signal(bool)
    assistantMessageAdded = Signal(str)
    historyCleared = Signal()

    def __init__(
        self,
        tool_service: EditorToolService,
        settings: QSettings | None = None,
        parent: QWidget | None = None,
        *,
        network_manager: QNetworkAccessManager | None = None,
        confirmation_handler: Callable[[list[dict[str, Any]]], bool] | None = None,
        history_store: ChatHistoryStore | None = None,
    ) -> None:
        super().__init__(parent)
        self.tool_service = tool_service
        self.settings = settings or create_app_settings()
        self.network = network_manager or QNetworkAccessManager(self)
        self.confirmation_handler = confirmation_handler
        self.history_store = history_store or ChatHistoryStore()
        self._identity = tool_service.document_identity
        self._messages = self.history_store.load(self._identity)
        self._reply: QNetworkReply | None = None
        self._timeout_timer = QTimer(self)
        self._timeout_timer.setSingleShot(True)
        self._timeout_timer.timeout.connect(self._on_timeout)
        self._stream: ChatStreamAccumulator | None = None
        self._tool_round = 0
        self._cancelled = False
        self._timed_out = False
        self._busy = False
        self._stream_preview = ""
        self._active_identity: str | None = None
        self._build_ui()
        self._load_settings()
        self._render_history()
        self.tool_service.documentIdentityChanged.connect(
            self._on_document_identity_changed
        )
        self.tool_service.documentIdentityMigrated.connect(
            self.migrate_history
        )

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)

        settings_box = QGroupBox("模型设置", self)
        settings_box.setCheckable(True)
        settings_box.setChecked(False)
        settings_layout = QFormLayout(settings_box)
        self.base_url_edit = QLineEdit(settings_box)
        self.base_url_edit.setPlaceholderText(DEFAULT_BASE_URL)
        self.model_edit = QLineEdit(settings_box)
        self.model_edit.setPlaceholderText("模型名称")
        self.api_key_env_edit = QLineEdit(settings_box)
        self.api_key_env_edit.setPlaceholderText(DEFAULT_API_KEY_ENV)
        self.api_key_env_edit.setToolTip("只保存环境变量名；密钥值不会落盘")
        settings_layout.addRow("Base URL", self.base_url_edit)
        settings_layout.addRow("模型", self.model_edit)
        settings_layout.addRow("API Key 环境变量", self.api_key_env_edit)
        save_settings = QPushButton("保存设置", settings_box)
        save_settings.clicked.connect(self.save_settings)
        settings_layout.addRow("", save_settings)
        outer.addWidget(settings_box)

        self.transcript = QTextBrowser(self)
        self.transcript.setOpenExternalLinks(False)
        outer.addWidget(self.transcript, 1)

        self.input_edit = QPlainTextEdit(self)
        self.input_edit.setPlaceholderText("用自然语言查询或修改当前图表……")
        self.input_edit.setMaximumHeight(110)
        outer.addWidget(self.input_edit)

        buttons = QHBoxLayout()
        self.send_button = QPushButton("发送", self)
        self.cancel_button = QPushButton("取消", self)
        self.cancel_button.setEnabled(False)
        self.clear_button = QPushButton("清除历史", self)
        self.send_button.clicked.connect(self.send_message)
        self.cancel_button.clicked.connect(self.cancel)
        self.clear_button.clicked.connect(self.clear_history)
        buttons.addWidget(self.send_button)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch(1)
        buttons.addWidget(self.clear_button)
        outer.addLayout(buttons)

        self.status_label = QLabel("", self)
        self.status_label.setWordWrap(True)
        outer.addWidget(self.status_label)

    def _load_settings(self) -> None:
        self.base_url_edit.setText(
            str(self.settings.value(SETTINGS_BASE_URL, DEFAULT_BASE_URL) or "")
        )
        self.model_edit.setText(
            str(self.settings.value(SETTINGS_MODEL, "") or "")
        )
        self.api_key_env_edit.setText(
            str(
                self.settings.value(
                    SETTINGS_API_KEY_ENV,
                    DEFAULT_API_KEY_ENV,
                )
                or DEFAULT_API_KEY_ENV
            )
        )

    def save_settings(self) -> None:
        self.settings.setValue(
            SETTINGS_BASE_URL,
            self.base_url_edit.text().strip() or DEFAULT_BASE_URL,
        )
        self.settings.setValue(SETTINGS_MODEL, self.model_edit.text().strip())
        self.settings.setValue(
            SETTINGS_API_KEY_ENV,
            self.api_key_env_edit.text().strip() or DEFAULT_API_KEY_ENV,
        )
        self.settings.sync()
        self._set_status("模型设置已保存（密钥值未保存）")

    def _set_status(self, message: str) -> None:
        self.status_label.setText(message)
        self.statusChanged.emit(message)

    def _set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        self.send_button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        self.input_edit.setEnabled(not busy)
        self.busyChanged.emit(busy)

    def send_message(self) -> None:
        if self._busy:
            return
        text = self.input_edit.toPlainText().strip()
        if not text:
            return
        if not self.model_edit.text().strip():
            self._set_status("请先填写模型名称")
            return
        env_name = self.api_key_env_edit.text().strip() or DEFAULT_API_KEY_ENV
        try:
            validated_chat_completions_url(
                self.base_url_edit.text(),
                has_api_key=bool(os.environ.get(env_name, "")),
            )
        except ValueError as exc:
            self._set_status(str(exc))
            return
        self.input_edit.clear()
        self._messages.append({"role": "user", "content": text})
        self._save_history()
        self._render_history()
        self._tool_round = 0
        self._cancelled = False
        self._timed_out = False
        self._active_identity = self._identity
        self._set_busy(True)
        self._start_request()

    def _request_messages(self) -> list[dict[str, Any]]:
        self._messages = self.history_store.bounded_messages(self._messages)
        return [
            {"role": "system", "content": _SYSTEM_PROMPT},
            *[dict(message) for message in self._messages],
        ]

    def _start_request(self) -> None:
        if self._cancelled:
            self._finish_busy("已取消")
            return
        if self._timed_out:
            self._append_local_notice("请求超时")
            self._finish_busy("请求超时")
            return
        payload = {
            "model": self.model_edit.text().strip(),
            "messages": self._request_messages(),
            "tools": self.tool_service.tool_definitions(),
            "tool_choice": "auto",
            "stream": True,
        }
        env_name = self.api_key_env_edit.text().strip() or DEFAULT_API_KEY_ENV
        api_key = os.environ.get(env_name, "")
        try:
            endpoint = validated_chat_completions_url(
                self.base_url_edit.text(),
                has_api_key=bool(api_key),
            )
        except ValueError as exc:
            self._append_local_notice(str(exc))
            self._finish_busy(str(exc))
            return
        request = QNetworkRequest(endpoint)
        request.setHeader(
            QNetworkRequest.KnownHeaders.ContentTypeHeader,
            "application/json",
        )
        if api_key:
            request.setRawHeader(
                QByteArray(b"Authorization"),
                QByteArray(f"Bearer {api_key}".encode("utf-8")),
            )
        self._stream = ChatStreamAccumulator()
        self._stream_preview = ""
        self._reply = self.network.post(
            request,
            QByteArray(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
        )
        self._reply.readyRead.connect(self._on_ready_read)
        self._reply.finished.connect(self._on_reply_finished)
        self._timeout_timer.start(REQUEST_TIMEOUT_MS)
        self._set_status(
            f"正在请求模型（工具轮次 {self._tool_round + 1}/{MAX_TOOL_ROUNDS}）……"
        )

    def _on_ready_read(self) -> None:
        if self._reply is None or self._stream is None:
            return
        reply = self._reply
        stream = self._stream
        chunks = stream.feed(reply.readAll())
        if chunks:
            self._stream_preview += "".join(chunks)
            self._render_history(stream_preview=self._stream_preview)
        if stream.error is not None:
            reply.abort()

    def _on_reply_finished(self) -> None:
        self._timeout_timer.stop()
        reply = self._reply
        stream = self._stream
        self._reply = None
        self._stream = None
        if reply is None or stream is None:
            return
        if reply.bytesAvailable():
            chunks = stream.feed(reply.readAll())
            self._stream_preview += "".join(chunks)
        chunks = stream.finish()
        self._stream_preview += "".join(chunks)
        network_error = reply.error()
        error_text = reply.errorString()
        reply.deleteLater()
        if self._cancelled:
            self._finish_busy("已取消")
            return
        if self._timed_out:
            self._append_local_notice("请求超时")
            self._finish_busy("请求超时")
            return
        if self._active_identity != self._identity:
            self._finish_busy("图表已切换，本轮 AI 请求已取消")
            return
        if stream.error is not None:
            message = str(stream.error.get("message") or "LLM service error")
            self._append_local_notice(message)
            self._finish_busy(message)
            return
        if network_error != QNetworkReply.NetworkError.NoError:
            self._append_local_notice(f"请求失败：{error_text}")
            self._finish_busy(f"请求失败：{error_text}")
            return
        try:
            if stream.saw_sse_data:
                if stream.error:
                    raise ValueError(
                        str(stream.error.get("message") or "LLM service error")
                    )
                assistant = stream.assistant_message()
            else:
                assistant = parse_chat_completion(bytes(stream.raw))
        except ValueError as exc:
            self._append_local_notice(str(exc))
            self._finish_busy(str(exc))
            return
        content = str(assistant.get("content") or "")
        tool_calls = assistant.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            if len(tool_calls) > MAX_TOOL_CALLS_PER_ROUND:
                self._append_local_notice("单轮工具调用数量超过安全上限")
                self._finish_busy("单轮工具调用数量超过安全上限")
                return
            if self._tool_round >= MAX_TOOL_ROUNDS:
                self._append_local_notice("已达到工具调用轮次上限")
                self._finish_busy("已达到工具调用轮次上限")
                return
            # Persist only after matching tool responses have been appended;
            # otherwise a crash could leave invalid assistant/tool history.
            self._messages.append(assistant)
            self._render_history()
            if content:
                self.assistantMessageAdded.emit(content)
            self._execute_tool_round(tool_calls)
            return
        self._messages.append(assistant)
        self._save_history()
        self._render_history()
        if content:
            self.assistantMessageAdded.emit(content)
        self._finish_busy("完成")

    @staticmethod
    def _decode_tool_arguments(call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        function = call.get("function")
        if not isinstance(function, dict):
            raise ValueError("工具调用缺少 function")
        name = str(function.get("name") or "").strip()
        raw_arguments = function.get("arguments", "{}")
        if isinstance(raw_arguments, dict):
            arguments = dict(raw_arguments)
        else:
            if len(str(raw_arguments or "{}")) > MAX_TOOL_ARGUMENT_CHARS:
                raise ValueError("工具参数超过安全大小限制")
            try:
                arguments = json.loads(str(raw_arguments or "{}"))
            except json.JSONDecodeError as exc:
                raise ValueError("工具参数不是有效 JSON") from exc
        if not isinstance(arguments, dict):
            raise ValueError("工具参数必须是 JSON 对象")
        return name, arguments

    def _execute_tool_round(self, tool_calls: list[dict[str, Any]]) -> None:
        if self._tool_round >= MAX_TOOL_ROUNDS:
            self._append_local_notice("已达到工具调用轮次上限")
            self._finish_busy("已达到工具调用轮次上限")
            return
        if len(tool_calls) > MAX_TOOL_CALLS_PER_ROUND:
            self._append_local_notice("单轮工具调用数量超过安全上限")
            self._finish_busy("单轮工具调用数量超过安全上限")
            return
        decoded: list[dict[str, Any]] = []
        previews: list[dict[str, Any]] = []
        for index, call in enumerate(tool_calls):
            call_id = str(call.get("id") or f"tool_call_{index}")
            try:
                name, arguments = self._decode_tool_arguments(call)
                item = {
                    "id": call_id,
                    "name": name,
                    "arguments": arguments,
                    "decode_error": None,
                    "preview_error": None,
                    "preview_token": None,
                }
                if self.tool_service.requires_confirmation(name):
                    preview = self.tool_service.preview_tool(name, arguments)
                    if preview.get("ok"):
                        preview_result = dict(preview.get("result") or {})
                        item["preview_token"] = preview_result.pop(
                            "preview_token",
                            None,
                        )
                        previews.append(
                            {
                                "tool": name,
                                "arguments": arguments,
                                "preview": preview_result,
                            }
                        )
                    else:
                        item["preview_error"] = preview
                decoded.append(item)
            except ValueError as exc:
                decoded.append(
                    {
                        "id": call_id,
                        "name": "",
                        "arguments": {},
                        "decode_error": str(exc),
                        "preview_error": None,
                        "preview_token": None,
                    }
                )

        mutation_items = [
            item
            for item in decoded
            if not item["decode_error"]
            and self.tool_service.requires_confirmation(item["name"])
        ]
        requires_single_transaction = len(mutation_items) > 1
        accepted = True
        if previews and not requires_single_transaction:
            accepted = self._confirm_changes(previews)

        round_start_revision = self.tool_service.revision
        for item in decoded:
            if item["decode_error"]:
                result = {
                    "ok": False,
                    "revision": self.tool_service.revision,
                    "error": {
                        "code": "INVALID_ARGUMENT",
                        "message": item["decode_error"],
                    },
                }
            elif requires_single_transaction and self.tool_service.requires_confirmation(
                item["name"]
            ):
                self.tool_service.discard_prepared_preview(item["preview_token"])
                result = {
                    "ok": False,
                    "revision": self.tool_service.revision,
                    "error": {
                        "code": "MUTATION_BATCH_REQUIRED",
                        "message": (
                            "Use one confirmation-required tool call in this "
                            "assistant turn; combine graph edits into one "
                            "apply_graph_edits transaction"
                        ),
                    },
                }
            elif item["preview_error"] is not None:
                result = item["preview_error"]
            elif (
                self.tool_service.requires_confirmation(item["name"])
                and not accepted
            ):
                self.tool_service.discard_prepared_preview(item["preview_token"])
                result = self.tool_service.user_rejected_result()
            else:
                arguments = dict(item["arguments"])
                # Multiple confirmed mutations in one assistant round commonly
                # share the same starting revision. Keep the later calls guarded
                # while allowing them to follow the just-confirmed transaction.
                if (
                    self.tool_service.requires_confirmation(item["name"])
                    and arguments.get("expected_revision") == round_start_revision
                ):
                    arguments["expected_revision"] = self.tool_service.revision
                if item["preview_token"]:
                    result = self.tool_service.invoke_prepared_preview(
                        item["preview_token"]
                    )
                else:
                    result = self.tool_service.invoke_tool(item["name"], arguments)
            self._messages.append(
                {
                    "role": "tool",
                    "tool_call_id": item["id"],
                    "name": item["name"],
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
        self._tool_round += 1
        self._save_history()
        self._render_history()
        if self._cancelled:
            self._finish_busy("已取消")
            return
        self._start_request()

    def _confirm_changes(self, previews: list[dict[str, Any]]) -> bool:
        if self.confirmation_handler is not None:
            return bool(self.confirmation_handler(previews))
        dialog = _ChangePreviewDialog(previews, self)
        return dialog.exec() == QDialog.DialogCode.Accepted

    def cancel(self) -> None:
        if not self._busy:
            return
        self._cancelled = True
        self._timeout_timer.stop()
        if self._reply is not None:
            self._reply.abort()
        else:
            self._finish_busy("已取消")

    def _on_timeout(self) -> None:
        if self._reply is not None:
            self._timed_out = True
            self._set_status("请求超时，正在取消……")
            self._reply.abort()

    def _finish_busy(self, status: str) -> None:
        self._set_busy(False)
        self._set_status(status)
        self._stream_preview = ""
        self._active_identity = None
        self._render_history()

    def _append_local_notice(self, text: str) -> None:
        self._messages.append({"role": "assistant", "content": f"⚠ {text}"})
        self._save_history()
        self._render_history()

    def _save_history(self) -> None:
        self._messages = self.history_store.bounded_messages(self._messages)
        self.history_store.save(self._identity, self._messages)

    def clear_history(self) -> None:
        if self._busy:
            return
        answer = QMessageBox.question(
            self,
            "清除 AI 对话",
            "确定清除当前图表的本地 AI 对话历史吗？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._messages.clear()
        self.history_store.clear(self._identity)
        self._render_history()
        self.historyCleared.emit()
        self._set_status("当前图表的 AI 对话历史已清除")

    def clear_history_without_prompt(self) -> None:
        """Test/integration helper for an already-confirmed clear action."""

        self._messages.clear()
        self.history_store.clear(self._identity)
        self._render_history()
        self.historyCleared.emit()

    def migrate_history(self, old_identity: str, new_identity: str) -> None:
        self.history_store.migrate(old_identity, new_identity)

    def _on_document_identity_changed(
        self,
        old_identity: str,
        new_identity: str,
    ) -> None:
        del old_identity
        self._identity = new_identity
        self._messages = self.history_store.load(new_identity)
        self._render_history()
        if self._busy:
            self._cancelled = True
            self._timeout_timer.stop()
            if self._reply is not None:
                self._reply.abort()
            else:
                self._finish_busy("图表已切换，本轮 AI 请求已取消")
            self._set_status("图表已切换，本轮 AI 请求已取消")

    def _render_history(self, *, stream_preview: str = "") -> None:
        blocks: list[str] = []
        role_labels = {
            "user": "你",
            "assistant": "AI",
            "tool": "工具",
        }
        for message in self._messages:
            role = str(message.get("role") or "")
            label = role_labels.get(role, role)
            content = str(message.get("content") or "")
            if role == "tool":
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, dict):
                        status = "成功" if parsed.get("ok") else str(
                            (parsed.get("error") or {}).get("code") or "失败"
                        )
                        content = f"{message.get('name')}: {status}"
                except json.JSONDecodeError:
                    pass
            if not content and message.get("tool_calls"):
                names = [
                    str((call.get("function") or {}).get("name") or "")
                    for call in message["tool_calls"]
                    if isinstance(call, dict)
                ]
                content = "调用工具：" + "、".join(filter(None, names))
            blocks.append(
                "<div style='margin:6px 0'>"
                f"<b>{html.escape(label)}</b><br>"
                f"<span style='white-space:pre-wrap'>{html.escape(content)}</span>"
                "</div>"
            )
        if stream_preview:
            blocks.append(
                "<div style='margin:6px 0'>"
                "<b>AI</b><br>"
                f"<span style='white-space:pre-wrap'>{html.escape(stream_preview)}</span>"
                "</div>"
            )
        if not blocks:
            blocks.append(
                "<span style='color:#7f8c8d'>对话只保存在本机，不写入图表 JSON/CSV。</span>"
            )
        self.transcript.setHtml("".join(blocks))
        scrollbar = self.transcript.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
