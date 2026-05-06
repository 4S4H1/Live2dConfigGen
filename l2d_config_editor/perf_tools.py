"""Performance tracing and benchmark tooling for the editor."""

from __future__ import annotations

import json
import math
import time
import uuid
from collections import deque
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .logic import document_to_csv_rows, validate_document


@dataclass(slots=True)
class PerformanceEvent:
    name: str
    category: str
    duration_ms: float
    started_at_ms: float
    meta: dict[str, Any] = field(default_factory=dict)
    session_id: str | None = None
    session_label: str | None = None


@dataclass(slots=True)
class PerformanceSummaryRow:
    category: str
    name: str
    count: int
    total_ms: float
    average_ms: float
    min_ms: float
    p95_ms: float
    max_ms: float
    last_ms: float


class PerformanceRecorder:
    def __init__(self, *, max_events: int = 4000) -> None:
        self.enabled = False
        self._events: deque[PerformanceEvent] = deque(maxlen=max_events)
        self._session_stack: list[tuple[str, str, dict[str, Any]]] = []

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)

    def clear(self) -> None:
        self._events.clear()

    def snapshot(self, limit: int | None = None) -> list[PerformanceEvent]:
        events = list(self._events)
        if limit is None or limit >= len(events):
            return events
        return events[-limit:]

    def events_for_session(self, session_id: str) -> list[PerformanceEvent]:
        return [event for event in self._events if event.session_id == session_id]

    def export_payload(self, *, limit: int = 1000, session_id: str | None = None) -> dict[str, Any]:
        events = self.events_for_session(session_id) if session_id else self.snapshot(limit)
        if session_id is None and limit and len(events) > limit:
            events = events[-limit:]
        return {
            "enabled": self.enabled,
            "session_id": session_id,
            "summary": [asdict(row) for row in self.summarize(events)],
            "events": [asdict(event) for event in events],
        }

    @contextmanager
    def session(self, label: str, meta: dict[str, Any] | None = None):
        session_id = uuid.uuid4().hex[:12]
        payload = dict(meta or {})
        self._session_stack.append((session_id, label, payload))
        try:
            yield session_id
        finally:
            self._session_stack.pop()

    @contextmanager
    def measure(self, name: str, category: str = "general", meta: dict[str, Any] | None = None):
        if not self.enabled:
            yield
            return
        started_ns = time.perf_counter_ns()
        started_at_ms = time.time() * 1000.0
        try:
            yield
        finally:
            duration_ms = (time.perf_counter_ns() - started_ns) / 1_000_000.0
            session_id = None
            session_label = None
            merged_meta = dict(meta or {})
            if self._session_stack:
                session_id, session_label, session_meta = self._session_stack[-1]
                for key, value in session_meta.items():
                    merged_meta.setdefault(key, value)
            self._events.append(
                PerformanceEvent(
                    name=name,
                    category=category,
                    duration_ms=duration_ms,
                    started_at_ms=started_at_ms,
                    meta=merged_meta,
                    session_id=session_id,
                    session_label=session_label,
                )
            )

    def summarize(self, events: Iterable[PerformanceEvent] | None = None) -> list[PerformanceSummaryRow]:
        grouped: dict[tuple[str, str], list[PerformanceEvent]] = {}
        for event in list(events) if events is not None else self._events:
            grouped.setdefault((event.category, event.name), []).append(event)

        summary_rows: list[PerformanceSummaryRow] = []
        for (category, name), current_events in grouped.items():
            durations = sorted(event.duration_ms for event in current_events)
            count = len(durations)
            total_ms = sum(durations)
            p95_index = max(0, min(count - 1, math.ceil(count * 0.95) - 1))
            summary_rows.append(
                PerformanceSummaryRow(
                    category=category,
                    name=name,
                    count=count,
                    total_ms=total_ms,
                    average_ms=total_ms / count,
                    min_ms=durations[0],
                    p95_ms=durations[p95_index],
                    max_ms=durations[-1],
                    last_ms=current_events[-1].duration_ms,
                )
            )
        summary_rows.sort(key=lambda row: (-row.total_ms, row.category, row.name))
        return summary_rows


_performance_recorder = PerformanceRecorder()


def get_performance_recorder() -> PerformanceRecorder:
    return _performance_recorder


@dataclass(slots=True)
class BenchmarkScenarioResult:
    name: str
    description: str
    iterations: int
    average_ms: float
    p95_ms: float
    max_ms: float
    total_ms: float


class PerformanceScenarioRunner:
    def __init__(self, window, recorder: PerformanceRecorder | None = None) -> None:
        self.window = window
        self.controller = window.controller
        self.canvas = window.canvas
        self.recorder = recorder or get_performance_recorder()

    def run(self, *, iterations: int) -> tuple[str, list[BenchmarkScenarioResult]]:
        document = self.controller.document
        session_meta = {
            "document_path": str(document.path or ""),
            "node_count": len(document.nodes),
            "connection_count": len(document.connections),
        }
        results: list[BenchmarkScenarioResult] = []
        with self.recorder.session("performance-benchmark", session_meta) as session_id:
            for name, description, callback in self._scenario_specs():
                durations: list[float] = []
                for iteration in range(iterations):
                    self._process_events()
                    started_ns = time.perf_counter_ns()
                    with self.recorder.measure(
                        name,
                        "benchmark",
                        {"iteration": iteration + 1, "description": description},
                    ):
                        callback()
                    self._process_events()
                    durations.append((time.perf_counter_ns() - started_ns) / 1_000_000.0)
                results.append(self._build_result(name, description, durations))
        return session_id, results

    def _scenario_specs(self):
        return [
            ("benchmark.refresh_derived", "重算派生状态", self._scenario_refresh_derived),
            ("benchmark.rebuild_scene", "重建画布场景", self._scenario_rebuild_scene),
            ("benchmark.validation_only", "仅验证规则", self._scenario_validation_only),
            ("benchmark.csv_preview_only", "仅生成 CSV 预览", self._scenario_csv_preview_only),
            ("benchmark.search_document", "搜索当前文档", self._scenario_search_document),
            ("benchmark.zoom_roundtrip", "缩放往返刷新", self._scenario_zoom_roundtrip),
        ]

    @staticmethod
    def _build_result(name: str, description: str, durations: list[float]) -> BenchmarkScenarioResult:
        ordered = sorted(durations)
        count = len(ordered)
        p95_index = max(0, min(count - 1, math.ceil(count * 0.95) - 1))
        total_ms = sum(ordered)
        return BenchmarkScenarioResult(
            name=name,
            description=description,
            iterations=count,
            average_ms=total_ms / count,
            p95_ms=ordered[p95_index],
            max_ms=ordered[-1],
            total_ms=total_ms,
        )

    def _scenario_refresh_derived(self) -> None:
        self.controller.refresh_derived()

    def _scenario_rebuild_scene(self) -> None:
        self.canvas.rebuild_scene()

    def _scenario_validation_only(self) -> None:
        validate_document(self.controller.schema, self.controller.document)

    def _scenario_csv_preview_only(self) -> None:
        document_to_csv_rows(self.controller.schema, self.controller.document)

    def _scenario_search_document(self) -> None:
        query = self._benchmark_query()
        self.controller.search(query)

    def _scenario_zoom_roundtrip(self) -> None:
        scale = max(0.03, float(self.canvas.transform().m11()))
        target_center = self.canvas.mapToScene(self.canvas.viewport().rect().center())
        zoomed_out_scale = max(0.03, min(8.0, scale * 0.6))
        self.canvas._apply_view_state(zoomed_out_scale, target_center)
        self._process_events()
        self.canvas._apply_view_state(scale, target_center)

    def _benchmark_query(self) -> str:
        if self.controller.selected_node_uuid:
            node = self.controller.get_node(self.controller.selected_node_uuid)
            if node:
                return str(node.fields.get("draw_able_name") or node.type or "Touch")
        for node in self.controller.document.nodes:
            for key in ("draw_able_name", "action_trigger", "parameter", "tips"):
                value = str(node.fields.get(key) or "").strip()
                if value:
                    return value[:12]
        return "Touch"

    @staticmethod
    def _process_events() -> None:
        app = QApplication.instance()
        if app is not None:
            app.processEvents()


class PerformanceToolDialog(QDialog):
    def __init__(self, window, parent=None) -> None:
        super().__init__(parent)
        self.window = window
        self.recorder = get_performance_recorder()
        self._last_session_id: str | None = None
        self._last_event_count = 0
        self.setWindowTitle("性能测试工具")
        self.resize(1180, 760)

        layout = QVBoxLayout(self)
        self.description_label = QLabel(
            "在当前真实文档上采集性能事件，并执行基准场景。开启采集后，可直接回到主界面操作，再点“刷新视图”查看各阶段耗时。"
        )
        self.description_label.setWordWrap(True)
        layout.addWidget(self.description_label)

        controls = QHBoxLayout()
        self.capture_button = QPushButton("开启实时采集")
        self.capture_button.clicked.connect(self._toggle_capture)
        controls.addWidget(self.capture_button)

        controls.addWidget(QLabel("场景迭代次数"))
        self.iterations_spin = QSpinBox()
        self.iterations_spin.setRange(1, 20)
        self.iterations_spin.setValue(3)
        controls.addWidget(self.iterations_spin)

        self.run_button = QPushButton("运行基准")
        self.run_button.clicked.connect(self._run_benchmarks)
        controls.addWidget(self.run_button)

        self.refresh_button = QPushButton("刷新视图")
        self.refresh_button.clicked.connect(self.refresh_display)
        controls.addWidget(self.refresh_button)

        self.clear_button = QPushButton("清空记录")
        self.clear_button.clicked.connect(self._clear_history)
        controls.addWidget(self.clear_button)

        self.export_button = QPushButton("导出 JSON")
        self.export_button.clicked.connect(self._export_report)
        controls.addWidget(self.export_button)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.document_label = QLabel("")
        layout.addWidget(self.document_label)

        self.last_run_output = QPlainTextEdit()
        self.last_run_output.setReadOnly(True)
        self.last_run_output.setPlaceholderText("最近一次基准运行结果会显示在这里。")
        self.last_run_output.setMaximumHeight(180)
        layout.addWidget(self.last_run_output)

        self.summary_table = QTableWidget(0, 8, self)
        self.summary_table.setHorizontalHeaderLabels(
            ["分类", "阶段", "次数", "平均(ms)", "P95(ms)", "最大(ms)", "总计(ms)", "最近(ms)"]
        )
        self.summary_table.verticalHeader().setVisible(False)
        self.summary_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.summary_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.summary_table.setAlternatingRowColors(True)
        layout.addWidget(self.summary_table, 1)

        self.events_output = QPlainTextEdit()
        self.events_output.setReadOnly(True)
        self.events_output.setPlaceholderText("最近采集到的事件明细。")
        layout.addWidget(self.events_output, 1)

        self._live_refresh_timer = QTimer(self)
        self._live_refresh_timer.setInterval(400)
        self._live_refresh_timer.timeout.connect(self._refresh_live_capture)
        self._live_refresh_timer.start()

        self._update_capture_button()
        self.refresh_display()

    def refresh_display(self) -> None:
        document = self.window.controller.document
        events = self.recorder.snapshot(limit=120)
        self._last_event_count = len(self.recorder.snapshot())
        self.document_label.setText(
            f"当前文档: {Path(document.path).name if document.path else '未保存草稿'} | "
            f"节点 {len(document.nodes)} | 连线 {len(document.connections)} | "
            f"实时采集 {'开启' if self.recorder.enabled else '关闭'}"
        )
        self._populate_summary_table(self.recorder.summarize())
        self._populate_recent_events(events)
        self._update_capture_button()

    def _refresh_live_capture(self) -> None:
        if not self.isVisible() or not self.recorder.enabled:
            return
        event_count = len(self.recorder.snapshot())
        if event_count == self._last_event_count:
            return
        self.refresh_display()

    def _populate_summary_table(self, rows: list[PerformanceSummaryRow]) -> None:
        self.summary_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = [
                row.category,
                row.name,
                str(row.count),
                f"{row.average_ms:.3f}",
                f"{row.p95_ms:.3f}",
                f"{row.max_ms:.3f}",
                f"{row.total_ms:.3f}",
                f"{row.last_ms:.3f}",
            ]
            for column_index, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column_index >= 2:
                    item.setTextAlignment(int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))
                self.summary_table.setItem(row_index, column_index, item)
        self.summary_table.resizeColumnsToContents()

    def _populate_recent_events(self, events: list[PerformanceEvent]) -> None:
        lines: list[str] = []
        for event in reversed(events):
            session_prefix = f"[{event.session_label}] " if event.session_label else ""
            meta_text = ""
            if event.meta:
                pairs = ", ".join(f"{key}={value}" for key, value in sorted(event.meta.items()))
                meta_text = f" | {pairs}"
            lines.append(f"{session_prefix}{event.category} | {event.name} | {event.duration_ms:.3f} ms{meta_text}")
        self.events_output.setPlainText("\n".join(lines))

    def _toggle_capture(self) -> None:
        self.recorder.set_enabled(not self.recorder.enabled)
        self.refresh_display()

    def _run_benchmarks(self) -> None:
        previous_enabled = self.recorder.enabled
        self.recorder.set_enabled(True)
        runner = PerformanceScenarioRunner(self.window, self.recorder)
        session_id, results = runner.run(iterations=int(self.iterations_spin.value()))
        self._last_session_id = session_id
        session_events = self.recorder.events_for_session(session_id)
        session_summary = self.recorder.summarize(session_events)
        self.last_run_output.setPlainText(self._format_benchmark_report(results, session_summary))
        self.recorder.set_enabled(previous_enabled)
        self.refresh_display()

    def _clear_history(self) -> None:
        self.recorder.clear()
        self._last_session_id = None
        self.last_run_output.clear()
        self.refresh_display()

    def _export_report(self) -> None:
        suggested = Path(self.window.workdir) / "performance-report.json"
        target, _ = QFileDialog.getSaveFileName(self, "导出性能报告", str(suggested), "JSON (*.json)")
        if not target:
            return
        payload = self.recorder.export_payload(limit=1500, session_id=self._last_session_id)
        Path(target).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.window.statusBar().showMessage(f"已导出性能报告: {Path(target).name}", 3000)

    def _update_capture_button(self) -> None:
        self.capture_button.setText("关闭实时采集" if self.recorder.enabled else "开启实时采集")

    @staticmethod
    def _format_benchmark_report(
        results: list[BenchmarkScenarioResult],
        summary_rows: list[PerformanceSummaryRow],
    ) -> str:
        lines = ["最近一次基准结果", ""]
        for result in results:
            lines.append(
                f"{result.description}: avg {result.average_ms:.3f} ms | "
                f"p95 {result.p95_ms:.3f} ms | max {result.max_ms:.3f} ms | total {result.total_ms:.3f} ms"
            )
        if summary_rows:
            lines.extend(["", "基准期间阶段汇总"])
            for row in summary_rows[:20]:
                lines.append(
                    f"{row.category} / {row.name}: count {row.count}, avg {row.average_ms:.3f} ms, "
                    f"p95 {row.p95_ms:.3f} ms, max {row.max_ms:.3f} ms"
                )
        return "\n".join(lines)
