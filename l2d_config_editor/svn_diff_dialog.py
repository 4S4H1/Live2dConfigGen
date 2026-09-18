"""Read-only SVN graph history and structural diff dialog."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from .graph_diff import GraphDiff, diff_documents
from .history_view import GraphComparisonWidget
from .logic import load_document_payload
from .models import DocumentModel
from .schema import EditorSchema
from .svn_tools import SvnFileInfo, SvnHistoryRunner, SvnRevision


class SvnGraphDiffDialog(QDialog):
    """Compare two committed SVN file revisions, independently of local edits."""

    CACHE_LIMIT = 12

    def __init__(
        self,
        runner: SvnHistoryRunner,
        schema: EditorSchema,
        file_path: str | Path,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.runner = runner
        self.schema = schema
        self.file_path = Path(file_path).resolve()
        self._revisions: list[SvnRevision] = []
        self._documents: OrderedDict[int, DocumentModel] = OrderedDict()
        self._pending_revisions: list[int] = []
        self._pending_endpoints: tuple[int, int] | None = None
        self._has_more = False
        self._closing = False

        self.setWindowTitle("SVN 历史版本 · 图表对比")
        self.resize(1180, 760)
        layout = QVBoxLayout(self)
        self.status_label = QLabel(f"文件：{self.file_path}\n正在确认 SVN 状态…")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        selector_row = QHBoxLayout()
        selector_row.addWidget(QLabel("修改前"))
        self.left_combo = QComboBox()
        self.left_combo.setMinimumWidth(300)
        selector_row.addWidget(self.left_combo, 1)
        selector_row.addWidget(QLabel("修改后"))
        self.right_combo = QComboBox()
        self.right_combo.setMinimumWidth(300)
        selector_row.addWidget(self.right_combo, 1)
        self.compare_button = QPushButton("比较")
        self.compare_button.clicked.connect(self._start_compare)
        self.compare_button.setEnabled(False)
        selector_row.addWidget(self.compare_button)
        self.more_button = QPushButton("继续加载 100 条")
        self.more_button.clicked.connect(lambda: self.runner.query_revisions(reset=False))
        self.more_button.setEnabled(False)
        selector_row.addWidget(self.more_button)
        layout.addLayout(selector_row)

        self.summary_label = QLabel("从 SVN 读取已提交的文件版本；默认比较最近两次提交。")
        layout.addWidget(self.summary_label)
        self.comparison = GraphComparisonWidget(schema)
        layout.addWidget(self.comparison, 1)

        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumHeight(115)
        self.log_edit.setPlaceholderText("SVN 查询日志")
        layout.addWidget(self.log_edit)

        button_row = QHBoxLayout()
        self.cancel_button = QPushButton("取消查询")
        self.cancel_button.clicked.connect(self.runner.cancel)
        self.cancel_button.setEnabled(False)
        button_row.addWidget(self.cancel_button)
        button_row.addStretch(1)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.reject)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

        runner.infoReady.connect(self._on_info_ready)
        runner.revisionsReady.connect(self._on_revisions_ready)
        runner.contentReady.connect(self._on_content_ready)
        runner.failed.connect(self._on_failed)
        runner.cancelled.connect(self._on_cancelled)
        runner.busyChanged.connect(self._on_busy_changed)
        runner.outputReceived.connect(self._append_log)
        runner.query_info(self.file_path)

    def _append_log(self, value: str) -> None:
        self.log_edit.moveCursor(self.log_edit.textCursor().MoveOperation.End)
        self.log_edit.insertPlainText(value)
        self.log_edit.ensureCursorVisible()

    def _on_busy_changed(self, busy: bool) -> None:
        self.cancel_button.setEnabled(busy)
        self.compare_button.setEnabled(not busy and len(self._revisions) >= 2)
        self.left_combo.setEnabled(not busy)
        self.right_combo.setEnabled(not busy)
        self.more_button.setEnabled(not busy and self._has_more)

    def _on_info_ready(self, info: SvnFileInfo) -> None:
        self.status_label.setText(
            f"仓库：{info.repository_root}\n文件 URL：{info.url}\n"
            f"工作副本状态：{info.working_copy_status}"
        )
        if not info.has_history:
            self.status_label.setText(self.status_label.text() + "\n当前文件没有可查询的 SVN 历史。")
            return
        self.runner.query_revisions(reset=True)

    def _revision_label(self, revision: SvnRevision) -> str:
        message = " ".join(revision.message.split())
        if len(message) > 55:
            message = message[:52] + "…"
        author = revision.author or "未知作者"
        return f"r{revision.revision} · {author}" + (f" · {message}" if message else "")

    def _fill_selectors(self, *, initial: bool) -> None:
        current_left = self.left_combo.currentData()
        current_right = self.right_combo.currentData()
        for combo in (self.left_combo, self.right_combo):
            combo.blockSignals(True)
            combo.clear()
            for revision in self._revisions:
                combo.addItem(self._revision_label(revision), revision.revision)
            combo.blockSignals(False)
        if initial and len(self._revisions) >= 2:
            self.left_combo.setCurrentIndex(1)
            self.right_combo.setCurrentIndex(0)
        else:
            self._restore_combo_value(self.left_combo, current_left)
            self._restore_combo_value(self.right_combo, current_right)

    @staticmethod
    def _restore_combo_value(combo: QComboBox, value: Any) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _on_revisions_ready(self, revisions: list[SvnRevision], has_more: bool) -> None:
        initial = len(self._revisions) < 2
        existing = {item.revision for item in self._revisions}
        self._revisions.extend(item for item in revisions if item.revision not in existing)
        self._revisions.sort(key=lambda item: item.revision, reverse=True)
        self._has_more = has_more
        self._fill_selectors(initial=initial)
        self.more_button.setEnabled(has_more)
        self.compare_button.setEnabled(len(self._revisions) >= 2)
        if not self._revisions:
            self.status_label.setText(self.status_label.text() + "\n该文件没有可读取的提交版本。")
            return
        if len(self._revisions) < 2:
            self.summary_label.setText("该文件只有一个已提交版本，需要至少两次 SVN 提交才能比较。")
            return
        if initial:
            self._start_compare()

    def _start_compare(self) -> None:
        left = self.left_combo.currentData()
        right = self.right_combo.currentData()
        if type(left) is not int or type(right) is not int:
            return
        if left == right:
            self.summary_label.setText("请选择两个不同的版本。")
            return
        self._pending_endpoints = (left, right)
        needed: list[int] = []
        for value in (left, right):
            if value in self._documents:
                self._documents.move_to_end(value)
            elif value not in needed:
                needed.append(value)
        self._pending_revisions = needed
        if self._pending_revisions:
            self.runner.query_content(self._pending_revisions.pop(0))
        else:
            self._render_pending_diff()

    def _on_content_ready(self, revision: int, payload: bytes) -> None:
        if self._closing or self._pending_endpoints is None or revision not in self._pending_endpoints:
            return
        try:
            document = load_document_payload(self.schema, payload)
        except Exception as exc:
            self._pending_revisions.clear()
            self._pending_endpoints = None
            self.summary_label.setText(f"r{revision} 不可比较：{exc}")
            return
        self._documents[revision] = document
        self._documents.move_to_end(revision)
        while len(self._documents) > self.CACHE_LIMIT:
            self._documents.popitem(last=False)
        if self._pending_revisions:
            self.runner.query_content(self._pending_revisions.pop(0))
        else:
            self._render_pending_diff()

    def _endpoint_document(self, endpoint: int) -> DocumentModel:
        return self._documents[endpoint]

    def _render_pending_diff(self) -> None:
        if self._pending_endpoints is None:
            return
        left, right = self._pending_endpoints
        graph_diff = diff_documents(
            self._endpoint_document(left),
            self._endpoint_document(right),
        )
        self.comparison.set_documents(self._endpoint_document(left), self._endpoint_document(right))
        self._show_diff(graph_diff)

    def _show_diff(self, graph_diff: GraphDiff) -> None:
        counts = graph_diff.counts()
        self.summary_label.setText(
            f"新增 {counts['added']} · 删除 {counts['deleted']} · 修改 {counts['modified']}"
            if not graph_diff.is_empty
            else "两个版本的规范化图表内容完全一致。"
        )

    def _on_cancelled(self) -> None:
        self._pending_revisions.clear()
        self._pending_endpoints = None
        self.status_label.setText("SVN 查询已取消。")

    def _on_failed(self, message: str) -> None:
        self._pending_revisions.clear()
        self._pending_endpoints = None
        self.status_label.setText(message)
        self._append_log(f"\n{message}\n")

    def reject(self) -> None:
        if self._closing:
            return
        self._closing = True
        self.runner.cancel()
        self._documents.clear()
        self._pending_revisions.clear()
        super().reject()
