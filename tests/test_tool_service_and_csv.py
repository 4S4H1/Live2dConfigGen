from __future__ import annotations

import csv
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QByteArray, QObject, QSettings, QTimer, Signal
from PySide6.QtGui import QUndoStack
from PySide6.QtNetwork import QNetworkReply
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from l2d_config_editor.controller import EditorController
from l2d_config_editor.csv_export import (
    build_current_csv_export_filename,
    export_current_document_csv,
    sanitize_csv_identifier,
)
from l2d_config_editor.llm_chat import (
    MAX_TOOL_CALLS_PER_ROUND,
    ChatHistoryStore,
    ChatStreamAccumulator,
    LLMChatPanel,
    parse_chat_completion,
    validated_chat_completions_url,
)
from l2d_config_editor.logic import create_node
from l2d_config_editor.models import CanvasImageRecord, CanvasStrokeRecord
from l2d_config_editor.tool_service import EditorToolService


_APP = QApplication.instance() or QApplication([])


class _FakeReply(QObject):
    readyRead = Signal()
    finished = Signal()

    def __init__(self, chunks: list[bytes], *, delay_ms: int = 0) -> None:
        super().__init__()
        self._chunks = list(chunks)
        self._buffer = bytearray()
        self._error = QNetworkReply.NetworkError.NoError
        self._error_text = ""
        self._finished = False
        QTimer.singleShot(delay_ms, self._emit_next)

    def _emit_next(self) -> None:
        if self._finished:
            return
        if self._chunks:
            self._buffer.extend(self._chunks.pop(0))
            self.readyRead.emit()
            QTimer.singleShot(0, self._emit_next)
            return
        self._finished = True
        self.finished.emit()

    def readAll(self) -> QByteArray:
        value = bytes(self._buffer)
        self._buffer.clear()
        return QByteArray(value)

    def bytesAvailable(self) -> int:
        return len(self._buffer)

    def error(self):
        return self._error

    def errorString(self) -> str:
        return self._error_text

    def abort(self) -> None:
        if self._finished:
            return
        self._error = QNetworkReply.NetworkError.OperationCanceledError
        self._error_text = "aborted"
        self._finished = True
        self.finished.emit()


class _FakeNetworkManager(QObject):
    def __init__(self, responses: list[tuple[list[bytes], int]]) -> None:
        super().__init__()
        self.responses = list(responses)
        self.request_payloads: list[dict] = []
        self.replies: list[_FakeReply] = []

    def post(self, _request, body: QByteArray):
        self.request_payloads.append(json.loads(bytes(body).decode("utf-8")))
        chunks, delay_ms = self.responses.pop(0)
        reply = _FakeReply(chunks, delay_ms=delay_ms)
        self.replies.append(reply)
        return reply


def _sse_message(message: dict) -> list[bytes]:
    payload = {"choices": [{"delta": message}]}
    return [
        b"data: " + json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n\n",
        b"data: [DONE]\n\n",
    ]


def _wait_until(predicate, timeout_ms: int = 1000) -> bool:
    elapsed = 0
    while elapsed < timeout_ms and not predicate():
        QTest.qWait(10)
        elapsed += 10
    return bool(predicate())


class CurrentCsvExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = EditorController()

    def test_filename_uses_current_json_stem_and_sanitizes_windows_names(self) -> None:
        self.controller.document.path = r"C:\charts\CON.json"
        filename = build_current_csv_export_filename(
            self.controller.document,
            timestamp=datetime(2026, 7, 29, 12, 34, 56),
        )
        self.assertEqual("ship_l2d_export__CON_20260729_123456.csv", filename)
        self.assertEqual("a_b_c", sanitize_csv_identifier('a<b>c.'))

    def test_unsaved_filename_falls_back_to_character_then_localized_name(self) -> None:
        self.controller.document.meta.CharName = "Z23 / 改"
        self.assertIn(
            "Z23 _ 改",
            build_current_csv_export_filename(
                self.controller.document,
                timestamp=datetime(2026, 7, 29, 12, 34, 56),
            ),
        )
        self.controller.document.meta.CharName = ""
        self.assertIn(
            "未命名图表",
            build_current_csv_export_filename(
                self.controller.document,
                timestamp=datetime(2026, 7, 29, 12, 34, 56),
            ),
        )

    def test_export_only_uses_current_memory_document_and_never_overwrites(self) -> None:
        node = create_node(
            self.controller.schema,
            self.controller.document,
            "TouchIdle",
            (100.0, 100.0),
        )
        node.fields["tips"] = "only-current-document"
        self.controller.document.nodes.append(node)
        self.controller.document.path = r"C:\outside\current_graph.json"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            # Another editor JSON in the workspace must not be read or merged.
            (root / "other.json").write_text(
                '{"editor_signature":"l2d_config_editor/v1","format_version":4}',
                encoding="utf-8",
            )
            now = lambda: datetime(2026, 7, 29, 12, 34, 56)
            first = export_current_document_csv(
                self.controller.schema,
                self.controller.document,
                root,
                _now=now,
            )
            second = export_current_document_csv(
                self.controller.schema,
                self.controller.document,
                root,
                _now=now,
            )
            self.assertTrue(first.name.endswith("_20260729_123456.csv"))
            self.assertTrue(second.name.endswith("_20260729_123456_2.csv"))
            self.assertNotEqual(first, second)
            with first.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(list(self.controller.schema.csv_columns), rows[0])
            self.assertEqual(2, len(rows))
            self.assertEqual(
                2,
                len(self.controller.document.nodes),
                "export must not mutate the live document",
            )

    def test_failed_atomic_replace_removes_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch(
                "l2d_config_editor.csv_export.os.replace",
                side_effect=PermissionError("locked"),
            ):
                with self.assertRaises(PermissionError):
                    export_current_document_csv(
                        self.controller.schema,
                        self.controller.document,
                        root,
                    )
            self.assertEqual([], list(root.glob("*.csv")))
            self.assertEqual([], list(root.glob("*.tmp")))
            self.assertEqual([], list(root.glob("*.lock")))

    def test_destination_is_not_visible_until_atomic_activation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            observed: dict[str, bool] = {}

            def inspect_before_failure(source, destination):
                del source
                target = Path(destination)
                observed["target_exists"] = target.exists()
                observed["lock_exists"] = (
                    target.parent / f".{target.name}.lock"
                ).exists()
                raise PermissionError("activation blocked")

            with patch(
                "l2d_config_editor.csv_export.os.replace",
                side_effect=inspect_before_failure,
            ):
                with self.assertRaises(PermissionError):
                    export_current_document_csv(
                        self.controller.schema,
                        self.controller.document,
                        root,
                    )
            self.assertEqual(
                {"target_exists": False, "lock_exists": True},
                observed,
            )
            self.assertEqual([], list(root.iterdir()))

    def test_failed_row_preparation_never_reserves_an_output_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch(
                "l2d_config_editor.csv_export.document_to_csv_rows",
                side_effect=ValueError("invalid graph"),
            ):
                with self.assertRaises(ValueError):
                    export_current_document_csv(
                        self.controller.schema,
                        self.controller.document,
                        root,
                    )
            self.assertEqual([], list(root.iterdir()))


class EditorToolServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.controller = EditorController()
        self.controller.set_workspace_root(self.temp.name)
        self.service = EditorToolService(self.controller, self.temp.name)
        self.root_uuid = next(
            node.uuid for node in self.controller.document.nodes if node.type == "Idle0"
        )

    def invoke(self, name: str, **arguments):
        return self.service.invoke_tool(name, arguments)

    def test_schema_is_json_safe_and_defines_operation_shapes(self) -> None:
        definitions = self.service.tool_definitions()
        self.assertEqual(11, len(definitions))
        json.dumps(definitions)
        result = self.invoke("get_editor_schema")
        self.assertTrue(result["ok"])
        operations = result["result"]["graph_edit_operations"]
        self.assertIn("add_node", operations)
        self.assertIn("reparent_plan_topic", operations)
        fields = {
            field["key"]
            for node_type in result["result"]["node_types"]
            for field in node_type["fields"]
        }
        self.assertNotIn("target_idle", fields)

    def test_get_graph_returns_deep_detached_dto(self) -> None:
        result = self.invoke("get_current_graph")
        graph = result["result"]["graph"]
        graph["nodes"][0]["type"] = "Comment"
        self.assertEqual("Idle0", self.controller.document.nodes[0].type)

    def test_tool_results_above_the_old_two_mib_limit_are_returned(self) -> None:
        large_value = "x" * (2 * 1024 * 1024 + 1)
        with patch.object(
            self.service,
            "_get_current_graph",
            return_value={"payload": large_value},
        ):
            result = self.invoke("get_current_graph")
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(large_value), len(result["result"]["payload"]))

    def test_plan_stroke_mutation_advances_graph_revision(self) -> None:
        revision = self.service.revision
        self.controller.add_canvas_stroke(
            [(0.0, 0.0), (10.0, 10.0)],
            "#123456",
            4.0,
            "plan",
        )
        self.assertGreater(self.service.revision, revision)

    def test_get_graph_summarizes_binary_images_and_stroke_points(self) -> None:
        self.controller.document.canvas_images.append(
            CanvasImageRecord(
                uuid="image-1",
                data_base64="SECRET_BASE64_PAYLOAD",
                name="参考图",
            )
        )
        self.controller.document.canvas_strokes.append(
            CanvasStrokeRecord(
                uuid="stroke-1",
                points=[(1.0, 2.0), (5.0, 9.0)],
                color="#123456",
                width=3.0,
            )
        )
        self.controller.document.plan_canvas_strokes.append(
            CanvasStrokeRecord(
                uuid="plan-stroke-1",
                points=[(-3.0, 4.0), (8.0, 12.0)],
                color="#654321",
                width=5.0,
            )
        )
        result = self.invoke("get_current_graph")
        self.assertTrue(result["ok"], result)
        graph = result["result"]["graph"]
        encoded = json.dumps(graph, ensure_ascii=False)
        self.assertNotIn("SECRET_BASE64_PAYLOAD", encoded)
        self.assertEqual(2, graph["canvas_strokes"][0]["point_count"])
        self.assertNotIn("points", graph["canvas_strokes"][0])
        self.assertEqual(
            {"left": 1.0, "top": 2.0, "right": 5.0, "bottom": 9.0},
            graph["canvas_strokes"][0]["bounds"],
        )
        self.assertEqual(2, graph["plan_canvas_strokes"][0]["point_count"])
        self.assertNotIn("points", graph["plan_canvas_strokes"][0])
        self.assertEqual(
            {"left": -3.0, "top": 4.0, "right": 8.0, "bottom": 12.0},
            graph["plan_canvas_strokes"][0]["bounds"],
        )

    def test_unsaved_graph_identities_are_isolated_by_root_uuid(self) -> None:
        other = EditorController()
        other_service = EditorToolService(other, self.temp.name)
        self.assertTrue(self.service.document_identity.startswith("__unsaved__:"))
        self.assertTrue(other_service.document_identity.startswith("__unsaved__:"))
        self.assertNotEqual(
            self.service.document_identity,
            other_service.document_identity,
        )

    def test_tool_dtos_do_not_expose_absolute_local_paths(self) -> None:
        self.controller.document.path = str(
            Path(self.temp.name) / "private" / "graph.json"
        )
        graph = self.invoke("get_current_graph")["result"]
        self.assertEqual("graph.json", graph["document_name"])
        self.assertNotIn("path", graph["graph"])
        preview = self.service.preview_tool(
            "save_current_graph",
            {"expected_revision": self.service.revision},
        )
        encoded = json.dumps(preview, ensure_ascii=False)
        self.assertNotIn(str(Path(self.temp.name).resolve()), encoded)

    def test_cancelled_cross_thread_envelope_never_invokes_late_mutation(self) -> None:
        envelope = {
            "event": threading.Event(),
            "result": None,
            "lock": threading.Lock(),
            "state": "cancelled",
        }
        called: list[str] = []
        original = self.service._invoke_guarded
        self.service._invoke_guarded = lambda *_args: called.append("called")
        try:
            self.service._complete_cross_thread_invoke(
                "apply_graph_edits",
                {},
                envelope,
            )
        finally:
            self.service._invoke_guarded = original
        self.assertEqual([], called)
        self.assertTrue(envelope["event"].is_set())

    def test_apply_batch_supports_client_references_and_one_undo_step(self) -> None:
        revision = self.service.revision
        result = self.invoke(
            "apply_graph_edits",
            expected_revision=revision,
            operations=[
                {
                    "op": "set_metadata",
                    "values": {"CharName": "测试角色", "version": "2026-07-29"},
                },
                {
                    "op": "add_node",
                    "node_type": "Comment",
                    "client_id": "note",
                    "fields": {"content": "工具创建"},
                    "position": {"x": 500, "y": 200},
                },
                {
                    "op": "add_connection",
                    "from_uuid": self.root_uuid,
                    "to_uuid": {"client_id": "note"},
                },
            ],
        )
        self.assertTrue(result["ok"], result)
        note_uuid = result["result"]["client_ids"]["note"]
        self.assertIsNotNone(self.controller.get_node(note_uuid))
        self.assertEqual(1, self.controller.undo_stack.count())
        self.controller.undo_stack.undo()
        self.assertIsNone(self.controller.get_node(note_uuid))
        self.assertEqual("", self.controller.document.meta.CharName)
        self.controller.undo_stack.redo()
        self.assertIsNotNone(self.controller.get_node(note_uuid))

    def test_bulk_node_and_connection_operations_are_confirmed_atomic_batches(self) -> None:
        def confirm(operations: list[dict]) -> dict:
            before_index = self.controller.undo_stack.index()
            preview = self.service.preview_tool(
                "apply_graph_edits",
                {
                    "expected_revision": self.service.revision,
                    "operations": operations,
                },
            )
            self.assertTrue(preview["ok"], preview)
            committed = self.service.invoke_prepared_preview(
                preview["result"]["preview_token"]
            )
            self.assertTrue(committed["ok"], committed)
            self.assertEqual(before_index + 1, self.controller.undo_stack.index())
            return committed

        created = confirm(
            [
                {
                    "op": "add_node",
                    "node_type": "Comment",
                    "client_id": f"note-{index}",
                    "fields": {"content": f"created-{index}"},
                }
                for index in range(3)
            ]
        )
        node_uuids = [
            created["result"]["client_ids"][f"note-{index}"]
            for index in range(3)
        ]
        self.assertEqual(3, sum(self.controller.get_node(value) is not None for value in node_uuids))
        self.controller.undo_stack.undo()
        self.assertTrue(all(self.controller.get_node(value) is None for value in node_uuids))
        self.controller.undo_stack.redo()

        confirm(
            [
                {
                    "op": "update_node",
                    "node_uuid": node_uuid,
                    "fields": {"content": f"updated-{index}"},
                }
                for index, node_uuid in enumerate(node_uuids)
            ]
        )
        self.assertEqual(
            [f"updated-{index}" for index in range(3)],
            [self.controller.get_node(value).fields["content"] for value in node_uuids],
        )
        self.controller.undo_stack.undo()
        self.assertEqual(
            [f"created-{index}" for index in range(3)],
            [self.controller.get_node(value).fields["content"] for value in node_uuids],
        )
        self.controller.undo_stack.redo()

        connection_operations = [
            {
                "op": "add_connection",
                "from_uuid": self.root_uuid,
                "to_uuid": node_uuid,
            }
            for node_uuid in node_uuids
        ]
        confirm(connection_operations)
        self.assertEqual(
            set(node_uuids),
            {
                edge.to_uuid
                for edge in self.controller.document.connections
                if edge.from_uuid == self.root_uuid and edge.to_uuid in node_uuids
            },
        )
        self.controller.undo_stack.undo()
        self.assertFalse(
            any(edge.to_uuid in node_uuids for edge in self.controller.document.connections)
        )
        self.controller.undo_stack.redo()

        confirm(
            [
                {**operation, "op": "delete_connection"}
                for operation in connection_operations
            ]
        )
        self.assertFalse(
            any(edge.to_uuid in node_uuids for edge in self.controller.document.connections)
        )
        self.controller.undo_stack.undo()
        self.assertEqual(
            set(node_uuids),
            {
                edge.to_uuid
                for edge in self.controller.document.connections
                if edge.from_uuid == self.root_uuid and edge.to_uuid in node_uuids
            },
        )
        self.controller.undo_stack.redo()

        confirm(
            [{"op": "delete_node", "node_uuid": node_uuid} for node_uuid in node_uuids]
        )
        self.assertTrue(all(self.controller.get_node(value) is None for value in node_uuids))
        self.controller.undo_stack.undo()
        self.assertTrue(all(self.controller.get_node(value) is not None for value in node_uuids))

    def test_invalid_late_operation_rolls_back_entire_clone(self) -> None:
        before = self.controller.export_current_document()
        result = self.invoke(
            "apply_graph_edits",
            expected_revision=self.service.revision,
            operations=[
                {"op": "set_metadata", "values": {"memo": "must-not-stick"}},
                {
                    "op": "add_connection",
                    "from_uuid": self.root_uuid,
                    "to_uuid": self.root_uuid,
                },
            ],
        )
        self.assertFalse(result["ok"])
        self.assertEqual("INVALID_CONNECTION", result["error"]["code"])
        self.assertEqual(before, self.controller.export_current_document())
        self.assertEqual(0, self.controller.undo_stack.count())

    def test_unknown_operation_field_and_noop_never_create_undo(self) -> None:
        typo = self.invoke(
            "apply_graph_edits",
            expected_revision=self.service.revision,
            operations=[
                {
                    "op": "update_node",
                    "node_uuid": self.root_uuid,
                    "postion": {"x": 9, "y": 9},
                }
            ],
        )
        self.assertFalse(typo["ok"])
        self.assertEqual("INVALID_ARGUMENT", typo["error"]["code"])
        noop = self.invoke(
            "apply_graph_edits",
            expected_revision=self.service.revision,
            operations=[
                {"op": "set_metadata", "values": {"memo": ""}},
            ],
        )
        self.assertFalse(noop["ok"])
        self.assertEqual("NO_CHANGES", noop["error"]["code"])
        self.assertEqual(0, self.controller.undo_stack.count())

    def test_confirmed_preview_commits_the_exact_previewed_uuid_once(self) -> None:
        preview = self.service.preview_tool(
            "apply_graph_edits",
            {
                "expected_revision": self.service.revision,
                "operations": [
                    {
                        "op": "add_plan_topic",
                        "parent_uuid": self.root_uuid,
                        "client_id": "topic",
                        "title": "稳定 UUID",
                    }
                ],
            },
        )
        self.assertTrue(preview["ok"], preview)
        preview_uuid = preview["result"]["client_ids"]["topic"]
        token = preview["result"]["preview_token"]
        committed = self.service.invoke_prepared_preview(token)
        self.assertTrue(committed["ok"], committed)
        self.assertEqual(
            preview_uuid,
            committed["result"]["client_ids"]["topic"],
        )
        self.assertIsNotNone(self.controller.get_node(preview_uuid))
        reused = self.service.invoke_prepared_preview(token)
        self.assertEqual("PREVIEW_EXPIRED", reused["error"]["code"])

    def test_revision_conflict_prevents_overwrite(self) -> None:
        stale = self.service.revision
        self.controller.set_global_mode("advanced")
        result = self.invoke(
            "apply_graph_edits",
            expected_revision=stale,
            operations=[{"op": "set_metadata", "values": {"memo": "stale"}}],
        )
        self.assertFalse(result["ok"])
        self.assertEqual("REVISION_CONFLICT", result["error"]["code"])
        self.assertEqual("", self.controller.document.meta.memo)

    def test_locked_node_and_unknown_field_return_stable_errors(self) -> None:
        created = self.invoke(
            "apply_graph_edits",
            expected_revision=self.service.revision,
            operations=[
                {
                    "op": "add_node",
                    "node_type": "Comment",
                    "client_id": "locked",
                    "locked": True,
                }
            ],
        )
        node_uuid = created["result"]["client_ids"]["locked"]
        locked = self.invoke(
            "apply_graph_edits",
            expected_revision=self.service.revision,
            operations=[
                {
                    "op": "update_node",
                    "node_uuid": node_uuid,
                    "fields": {"content": "no"},
                }
            ],
        )
        self.assertEqual("NODE_LOCKED", locked["error"]["code"])
        unknown = self.invoke(
            "apply_graph_edits",
            expected_revision=self.service.revision,
            operations=[
                {
                    "op": "add_node",
                    "node_type": "Comment",
                    "fields": {"__private": "no"},
                }
            ],
        )
        self.assertEqual("FIELD_NOT_WRITABLE", unknown["error"]["code"])

    def test_main_window_style_undo_stack_replacement_is_honoured(self) -> None:
        replacement = QUndoStack()
        self.controller.undo_stack = replacement
        result = self.invoke(
            "apply_graph_edits",
            expected_revision=self.service.revision,
            operations=[{"op": "set_metadata", "values": {"memo": "session"}}],
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(1, replacement.count())
        replacement.undo()
        self.assertEqual("", self.controller.document.meta.memo)

    def test_plan_reparent_to_unconnected_removes_only_primary_edge(self) -> None:
        created = self.invoke(
            "apply_graph_edits",
            expected_revision=self.service.revision,
            operations=[
                {
                    "op": "add_plan_topic",
                    "parent_uuid": self.root_uuid,
                    "client_id": "topic",
                    "title": "主题",
                }
            ],
        )
        self.assertTrue(created["ok"], created)
        topic_uuid = created["result"]["client_ids"]["topic"]
        self.assertTrue(
            any(
                edge.from_uuid == self.root_uuid and edge.to_uuid == topic_uuid
                for edge in self.controller.document.connections
            )
        )
        detached = self.invoke(
            "apply_graph_edits",
            expected_revision=self.service.revision,
            operations=[
                {
                    "op": "reparent_plan_topic",
                    "node_uuid": topic_uuid,
                    "new_parent_uuid": None,
                }
            ],
        )
        self.assertTrue(detached["ok"], detached)
        self.assertFalse(
            any(
                edge.from_uuid == self.root_uuid and edge.to_uuid == topic_uuid
                for edge in self.controller.document.connections
            )
        )
        topic = self.controller.plan_topic(topic_uuid)
        self.assertIsNotNone(topic)
        self.assertIsNone(topic.parent_uuid)
        reconnected = self.invoke(
            "apply_graph_edits",
            expected_revision=self.service.revision,
            operations=[
                {
                    "op": "add_connection",
                    "from_uuid": self.root_uuid,
                    "to_uuid": topic_uuid,
                }
            ],
        )
        self.assertTrue(reconnected["ok"], reconnected)
        self.assertEqual(
            self.root_uuid,
            self.controller.plan_topic(topic_uuid).parent_uuid,
        )

    def test_view_switch_uses_signal_and_reports_actual_view(self) -> None:
        seen: list[str] = []
        self.service.viewSwitchRequested.connect(seen.append)
        result = self.invoke("switch_graph_view", view="plan")
        self.assertTrue(result["ok"])
        self.assertEqual(["plan"], seen)
        self.assertEqual("plan", self.service.current_view)


class ChatComponentTests(unittest.TestCase):
    def test_stream_accumulates_text_and_fragmented_tool_arguments(self) -> None:
        stream = ChatStreamAccumulator()
        chunks = [
            b'data: {"choices":[{"delta":{"content":"hel"}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":"lo","tool_calls":[{"index":0,"id":"call-1","function":{"name":"get_","arguments":"{\\"q"}}]}}]}\n\n',
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"current_graph","arguments":"uery\\":\\"x\\"}"}}]}}]}\n\n',
            b"data: [DONE]\n\n",
        ]
        visible: list[str] = []
        for chunk in chunks:
            visible.extend(stream.feed(chunk))
        message = stream.assistant_message()
        self.assertEqual(["hel", "lo"], visible)
        self.assertEqual("hello", message["content"])
        function = message["tool_calls"][0]["function"]
        self.assertEqual("get_current_graph", function["name"])
        self.assertEqual({"query": "x"}, json.loads(function["arguments"]))

    def test_stream_rejects_excessive_tool_call_fanout(self) -> None:
        stream = ChatStreamAccumulator(max_tool_calls=2)
        calls = [
            {
                "index": index,
                "id": f"call-{index}",
                "function": {"name": "get_current_graph", "arguments": "{}"},
            }
            for index in range(3)
        ]
        stream.feed(
            b"data: "
            + json.dumps(
                {"choices": [{"delta": {"tool_calls": calls}}]}
            ).encode("utf-8")
            + b"\n\n"
        )
        self.assertEqual("TOO_MANY_TOOL_CALLS", stream.error["code"])

    def test_stream_accepts_content_and_tool_arguments_above_old_byte_limits(self) -> None:
        content = "响" * (4 * 1024 * 1024 + 1)
        stream = ChatStreamAccumulator()
        stream.feed(
            b"data: "
            + json.dumps(
                {"choices": [{"delta": {"content": content}}]},
                ensure_ascii=False,
            ).encode("utf-8")
            + b"\n\ndata: [DONE]\n\n"
        )
        self.assertIsNone(stream.error)
        self.assertEqual(len(content), len(stream.assistant_message()["content"]))

        arguments = json.dumps({"payload": "x" * (2 * 1024 * 1024 + 1)})
        tool_stream = ChatStreamAccumulator()
        tool_stream.feed(
            b"data: "
            + json.dumps(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "large-call",
                                        "function": {
                                            "name": "get_current_graph",
                                            "arguments": arguments,
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                }
            ).encode("utf-8")
            + b"\n\n"
        )
        self.assertIsNone(tool_stream.error)
        self.assertEqual(
            len(arguments),
            len(tool_stream.assistant_message()["tool_calls"][0]["function"]["arguments"]),
        )

    def test_history_quota_evicts_old_turns_but_keeps_current_large_response(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ChatHistoryStore(directory, max_bytes=4096)
            messages = [
                {"role": "user", "content": "old"},
                {"role": "assistant", "content": "y" * 3500},
                {"role": "user", "content": "current"},
                {"role": "assistant", "content": "z" * 12000},
            ]
            bounded = store.bounded_messages(messages)
            self.assertEqual(["current", "z" * 12000], [item["content"] for item in bounded])
            store.save("document", bounded)
            self.assertLessEqual(store.path_for("document").stat().st_size, 4096)

    def test_endpoint_validation_rejects_credentials_and_insecure_remote_key(self) -> None:
        with self.assertRaises(ValueError):
            validated_chat_completions_url("file:///tmp/model")
        with self.assertRaises(ValueError):
            validated_chat_completions_url("https://user:secret@example.com/v1")
        with self.assertRaises(ValueError):
            validated_chat_completions_url(
                "http://example.com/v1",
                has_api_key=True,
            )
        loopback = validated_chat_completions_url(
            "http://127.0.0.1:11434/v1",
            has_api_key=True,
        )
        self.assertEqual("http", loopback.scheme())

    def test_non_streaming_response_parser(self) -> None:
        message = parse_chat_completion(
            json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "完成",
                                "tool_calls": [],
                            }
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )
        self.assertEqual("完成", message["content"])

    def test_history_is_per_graph_bounded_and_migratable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ChatHistoryStore(temp_dir, max_messages=3, max_bytes=4096)
            store.save(
                "a",
                [
                    {"role": "user", "content": str(index)}
                    for index in range(6)
                ],
            )
            self.assertEqual(["3", "4", "5"], [m["content"] for m in store.load("a")])
            store.migrate("a", "b")
            self.assertEqual([], store.load("a"))
            self.assertEqual(["3", "4", "5"], [m["content"] for m in store.load("b")])

    def test_history_enforces_directory_cap_and_valid_protocol_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ChatHistoryStore(
                temp_dir,
                max_messages=20,
                max_bytes=4096,
                max_total_bytes=8192,
                max_files=2,
            )
            for identity in ("one", "two", "three"):
                store.save(
                    identity,
                    [{"role": "user", "content": identity * 500}],
                )
            self.assertLessEqual(len(list(Path(temp_dir).glob("*.json"))), 2)

            bounded = store.bounded_messages(
                [
                    {
                        "role": "assistant",
                        "content": "说明",
                        "tool_calls": [
                            {
                                "id": "missing",
                                "type": "function",
                                "function": {
                                    "name": "get_current_graph",
                                    "arguments": "{}",
                                },
                            }
                        ],
                    },
                    {"role": "tool", "tool_call_id": "different", "content": "{}"},
                ]
            )
            self.assertEqual([{"role": "assistant", "content": "说明"}], bounded)

    def test_panel_persists_only_api_key_environment_variable_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = QSettings(
                str(Path(temp_dir) / "settings.ini"),
                QSettings.Format.IniFormat,
            )
            controller = EditorController()
            service = EditorToolService(controller, temp_dir)
            secret = "sk-must-never-be-persisted"
            with patch.dict(os.environ, {"MY_LLM_KEY": secret}, clear=False):
                panel = LLMChatPanel(
                    service,
                    settings,
                    history_store=ChatHistoryStore(Path(temp_dir) / "history"),
                )
                panel.api_key_env_edit.setText("MY_LLM_KEY")
                panel.save_settings()
                panel.deleteLater()
            raw = Path(settings.fileName()).read_text(encoding="utf-8")
            self.assertIn("MY_LLM_KEY", raw)
            self.assertNotIn(secret, raw)

    def _panel_with_responses(
        self,
        temp_dir: str,
        responses: list[tuple[list[bytes], int]],
        *,
        confirmation_handler=None,
    ):
        controller = EditorController()
        service = EditorToolService(controller, temp_dir)
        manager = _FakeNetworkManager(responses)
        settings = QSettings(
            str(Path(temp_dir) / "settings.ini"),
            QSettings.Format.IniFormat,
        )
        panel = LLMChatPanel(
            service,
            settings,
            network_manager=manager,
            confirmation_handler=confirmation_handler,
            history_store=ChatHistoryStore(Path(temp_dir) / "history"),
        )
        panel.model_edit.setText("fake-model")
        panel.base_url_edit.setText("http://fake.invalid/v1")
        return controller, service, manager, panel

    def test_fake_service_streams_text_and_runs_multiple_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            controller = EditorController()
            revision_holder = {"value": 0}
            # Build after service creation so the mutation carries its revision.
            service = EditorToolService(controller, temp_dir)
            revision_holder["value"] = service.revision
            calls = [
                {
                    "index": 0,
                    "id": "read",
                    "type": "function",
                    "function": {
                        "name": "get_current_graph",
                        "arguments": "{}",
                    },
                },
                {
                    "index": 1,
                    "id": "write",
                    "type": "function",
                    "function": {
                        "name": "apply_graph_edits",
                        "arguments": json.dumps(
                            {
                                "expected_revision": revision_holder["value"],
                                "operations": [
                                    {
                                        "op": "set_metadata",
                                        "values": {"memo": "from fake model"},
                                    }
                                ],
                            }
                        ),
                    },
                },
            ]
            responses = [
                (_sse_message({"content": "准备修改", "tool_calls": calls}), 0),
                (_sse_message({"content": "修改完成"}), 0),
            ]
            manager = _FakeNetworkManager(responses)
            settings = QSettings(
                str(Path(temp_dir) / "settings.ini"),
                QSettings.Format.IniFormat,
            )
            confirmations: list[list[dict]] = []
            panel = LLMChatPanel(
                service,
                settings,
                network_manager=manager,
                confirmation_handler=lambda previews: (
                    confirmations.append(previews) or True
                ),
                history_store=ChatHistoryStore(Path(temp_dir) / "history"),
            )
            panel.model_edit.setText("fake-model")
            panel.input_edit.setPlainText("修改备注")
            panel.send_message()
            self.assertTrue(_wait_until(lambda: not panel._busy))
            self.assertEqual("from fake model", controller.document.meta.memo)
            self.assertEqual(2, len(manager.request_payloads))
            self.assertEqual(1, len(confirmations))
            self.assertEqual(1, len(confirmations[0]))
            self.assertEqual("apply_graph_edits", confirmations[0][0]["tool"])
            self.assertEqual(
                ["user", "assistant", "tool", "tool", "assistant"],
                [message["role"] for message in panel._messages],
            )
            panel.deleteLater()

    def test_fake_service_rejected_mutation_returns_user_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            controller = EditorController()
            service = EditorToolService(controller, temp_dir)
            call = {
                "index": 0,
                "id": "write",
                "type": "function",
                "function": {
                    "name": "apply_graph_edits",
                    "arguments": json.dumps(
                        {
                            "expected_revision": service.revision,
                            "operations": [
                                {
                                    "op": "set_metadata",
                                    "values": {"memo": "rejected"},
                                }
                            ],
                        }
                    ),
                },
            }
            manager = _FakeNetworkManager(
                [
                    (_sse_message({"tool_calls": [call]}), 0),
                    (_sse_message({"content": "已取消修改"}), 0),
                ]
            )
            panel = LLMChatPanel(
                service,
                QSettings(
                    str(Path(temp_dir) / "settings.ini"),
                    QSettings.Format.IniFormat,
                ),
                network_manager=manager,
                confirmation_handler=lambda _previews: False,
                history_store=ChatHistoryStore(Path(temp_dir) / "history"),
            )
            panel.model_edit.setText("fake-model")
            panel.input_edit.setPlainText("不要真的修改")
            panel.send_message()
            self.assertTrue(_wait_until(lambda: not panel._busy))
            self.assertEqual("", controller.document.meta.memo)
            tool_message = next(
                message for message in panel._messages if message["role"] == "tool"
            )
            result = json.loads(tool_message["content"])
            self.assertEqual("USER_REJECTED", result["error"]["code"])
            panel.deleteLater()

    def test_fake_service_rejects_multiple_mutations_as_one_atomic_round(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            controller = EditorController()
            service = EditorToolService(controller, temp_dir)
            calls = [
                {
                    "index": index,
                    "id": f"write-{index}",
                    "type": "function",
                    "function": {
                        "name": "apply_graph_edits",
                        "arguments": json.dumps(
                            {
                                "expected_revision": service.revision,
                                "operations": [
                                    {
                                        "op": "set_metadata",
                                        "values": {"memo": f"write-{index}"},
                                    }
                                ],
                            }
                        ),
                    },
                }
                for index in range(2)
            ]
            manager = _FakeNetworkManager(
                [
                    (_sse_message({"content": "", "tool_calls": calls}), 0),
                    (_sse_message({"content": "请合并事务后重试"}), 0),
                ]
            )
            panel = LLMChatPanel(
                service,
                QSettings(
                    str(Path(temp_dir) / "settings.ini"),
                    QSettings.Format.IniFormat,
                ),
                network_manager=manager,
                confirmation_handler=lambda _previews: self.fail(
                    "invalid multi-mutation round must not be confirmed"
                ),
                history_store=ChatHistoryStore(Path(temp_dir) / "history"),
            )
            panel.model_edit.setText("fake-model")
            panel.input_edit.setPlainText("执行两个写操作")
            panel.send_message()
            self.assertTrue(_wait_until(lambda: not panel._busy))
            self.assertEqual("", controller.document.meta.memo)
            self.assertEqual(0, controller.undo_stack.count())
            tool_results = [
                json.loads(message["content"])
                for message in panel._messages
                if message["role"] == "tool"
            ]
            self.assertEqual(2, len(tool_results))
            self.assertTrue(
                all(
                    result["error"]["code"] == "MUTATION_BATCH_REQUIRED"
                    for result in tool_results
                )
            )
            panel.deleteLater()

    def test_fake_service_rejects_excessive_single_round_tool_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            calls = [
                {
                    "index": index,
                    "id": f"read-{index}",
                    "type": "function",
                    "function": {
                        "name": "get_current_graph",
                        "arguments": "{}",
                    },
                }
                for index in range(MAX_TOOL_CALLS_PER_ROUND + 1)
            ]
            _controller, _service, manager, panel = self._panel_with_responses(
                temp_dir,
                [(_sse_message({"tool_calls": calls}), 0)],
            )
            panel.input_edit.setPlainText("调用过多工具")
            panel.send_message()
            self.assertTrue(_wait_until(lambda: not panel._busy))
            self.assertEqual(1, len(manager.request_payloads))
            self.assertFalse(
                any(message.get("tool_calls") for message in panel._messages)
            )
            self.assertIn("安全上限", panel.status_label.text())
            panel.deleteLater()

    def test_fake_service_reports_bad_tool_arguments_and_continues(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            bad_call = {
                "index": 0,
                "id": "bad",
                "type": "function",
                "function": {
                    "name": "apply_graph_edits",
                    "arguments": "{not-json",
                },
            }
            controller, _service, _manager, panel = self._panel_with_responses(
                temp_dir,
                [
                    (_sse_message({"tool_calls": [bad_call]}), 0),
                    (_sse_message({"content": "参数已纠正"}), 0),
                ],
            )
            panel.input_edit.setPlainText("错误参数")
            panel.send_message()
            self.assertTrue(_wait_until(lambda: not panel._busy))
            self.assertEqual("", controller.document.meta.memo)
            tool_message = next(
                message for message in panel._messages if message["role"] == "tool"
            )
            self.assertEqual(
                "INVALID_ARGUMENT",
                json.loads(tool_message["content"])["error"]["code"],
            )
            panel.deleteLater()

    def test_fake_service_cancel_and_timeout_abort_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _controller, _service, _manager, panel = self._panel_with_responses(
                temp_dir,
                [(_sse_message({"content": "too late"}), 500)],
            )
            panel.input_edit.setPlainText("取消")
            panel.send_message()
            panel.cancel()
            self.assertTrue(_wait_until(lambda: not panel._busy))
            self.assertEqual("已取消", panel.status_label.text())
            panel.deleteLater()

        with tempfile.TemporaryDirectory() as temp_dir:
            _controller, _service, _manager, panel = self._panel_with_responses(
                temp_dir,
                [(_sse_message({"content": "too late"}), 500)],
            )
            panel.input_edit.setPlainText("超时")
            panel.send_message()
            panel._on_timeout()
            self.assertTrue(_wait_until(lambda: not panel._busy))
            self.assertEqual("请求超时", panel.status_label.text())
            panel.deleteLater()

    def test_panel_restores_history_for_same_graph_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            controller = EditorController()
            controller.document.path = str(Path(temp_dir) / "graph.json")
            service = EditorToolService(controller, temp_dir)
            store = ChatHistoryStore(Path(temp_dir) / "history")
            store.save(
                service.document_identity,
                [{"role": "user", "content": "恢复我"}],
            )
            first = LLMChatPanel(
                service,
                QSettings(
                    str(Path(temp_dir) / "settings.ini"),
                    QSettings.Format.IniFormat,
                ),
                history_store=store,
            )
            self.assertEqual("恢复我", first._messages[0]["content"])
            first.deleteLater()

    def test_delayed_response_is_discarded_when_active_graph_switches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            controller, service, _manager, panel = self._panel_with_responses(
                temp_dir,
                [(_sse_message({"content": "旧图迟到响应"}), 500)],
            )
            old_identity = service.document_identity
            panel.input_edit.setPlainText("旧图问题")
            panel.send_message()
            controller.documentLoaded.emit()
            new_path = Path(temp_dir) / "new.json"
            controller.document.path = str(new_path)
            controller.pathChanged.emit(str(new_path))
            self.assertTrue(_wait_until(lambda: not panel._busy))
            self.assertEqual([], panel._messages)
            old_messages = panel.history_store.load(old_identity)
            self.assertEqual(["旧图问题"], [item["content"] for item in old_messages])
            self.assertNotIn(
                "旧图迟到响应",
                json.dumps(panel._messages, ensure_ascii=False),
            )
            panel.deleteLater()

    def test_active_graph_rename_migrates_history_but_document_switch_does_not(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            controller = EditorController()
            old_path = Path(temp_dir) / "old.json"
            new_path = Path(temp_dir) / "new.json"
            controller.document.path = str(old_path)
            service = EditorToolService(controller, temp_dir)
            store = ChatHistoryStore(Path(temp_dir) / "history")
            panel = LLMChatPanel(
                service,
                QSettings(
                    str(Path(temp_dir) / "settings.ini"),
                    QSettings.Format.IniFormat,
                ),
                history_store=store,
            )
            panel._messages = [{"role": "user", "content": "保留在重命名后"}]
            panel._save_history()
            controller.document.path = str(new_path)
            controller.pathChanged.emit(str(new_path))
            self.assertEqual("保留在重命名后", panel._messages[0]["content"])
            self.assertEqual([], store.load(service._normalize_identity(old_path)))

            other_path = Path(temp_dir) / "other.json"
            store.save(
                service._normalize_identity(other_path),
                [{"role": "user", "content": "另一张图"}],
            )
            controller.documentLoaded.emit()
            controller.document.path = str(other_path)
            controller.pathChanged.emit(str(other_path))
            self.assertEqual("另一张图", panel._messages[0]["content"])
            retained = store.load(service._normalize_identity(new_path))
            self.assertEqual("保留在重命名后", retained[0]["content"])
            panel.deleteLater()


if __name__ == "__main__":
    unittest.main()
