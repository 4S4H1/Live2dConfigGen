from __future__ import annotations

import json
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QSettings

from l2d_config_editor.controller import EditorController
from l2d_config_editor.llm_chat import ChatHistoryStore, LLMChatPanel
from l2d_config_editor.tool_service import EditorToolService
from l2d_config_editor.logic import MAX_CANVAS_COORDINATE, create_node
from l2d_config_editor.models import ConnectionRecord
from tests.test_tool_service_and_csv import _APP, _FakeNetworkManager, _sse_message, _wait_until


class ChatAuditRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.controller = EditorController()
        self.service = EditorToolService(self.controller, self.root)
        self.call = {
            "index": 0,
            "id": "write",
            "type": "function",
            "function": {
                "name": "apply_graph_edits",
                "arguments": json.dumps({
                    "expected_revision": self.service.revision,
                    "operations": [{"op": "set_metadata", "values": {"memo": "AI edit"}}],
                }),
            },
        }
        self.manager = _FakeNetworkManager([
            (_sse_message({"tool_calls": [self.call]}), 0),
            (_sse_message({"content": "done"}), 0),
        ])
        self.panel = LLMChatPanel(
            self.service,
            QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat),
            network_manager=self.manager,
            history_store=ChatHistoryStore(self.root / "history"),
        )
        self.addCleanup(self.panel.deleteLater)
        self.panel.model_edit.setText("fake-model")
        self.panel.input_edit.setPlainText("修改备注")

    def test_cancel_during_confirmation_prevents_mutation(self) -> None:
        def confirm(_previews):
            self.panel.cancel()
            return True

        self.panel.confirmation_handler = confirm
        self.panel.send_message()
        self.assertTrue(_wait_until(lambda: not self.panel._busy))
        self.assertEqual("", self.controller.document.meta.memo)
        self.assertEqual(0, self.controller.undo_stack.count())
        self.assertEqual(1, len(self.manager.request_payloads))
        self.assertEqual({}, self.service._prepared_previews)

    def test_switch_during_confirmation_does_not_pollute_new_history(self) -> None:
        new_path = self.root / "other.json"
        new_identity = self.service._normalize_identity(new_path)
        expected = [{"role": "user", "content": "另一张图的历史"}]
        self.panel.history_store.save(new_identity, expected)

        def confirm(_previews):
            self.controller.documentLoaded.emit()
            self.controller.document.path = str(new_path)
            self.controller.pathChanged.emit(str(new_path))
            return True

        self.panel.confirmation_handler = confirm
        self.panel.send_message()
        self.assertTrue(_wait_until(lambda: not self.panel._busy))
        self.assertEqual(expected, self.panel._messages)
        self.assertEqual(expected, self.panel.history_store.load(new_identity))
        self.assertEqual(1, len(self.manager.request_payloads))
        self.assertEqual({}, self.service._prepared_previews)

    def test_new_turn_after_cancel_cannot_reactivate_old_confirmation(self) -> None:
        def confirm(_previews):
            self.panel.cancel()
            self.panel.input_edit.setPlainText("新的问题")
            self.panel.send_message()
            return True

        self.panel.confirmation_handler = confirm
        self.panel.send_message()
        self.assertTrue(_wait_until(lambda: not self.panel._busy))
        self.assertEqual("", self.controller.document.meta.memo)
        self.assertEqual(0, self.controller.undo_stack.count())
        self.assertEqual(2, len(self.manager.request_payloads))
        self.assertEqual("done", self.panel._messages[-1]["content"])
        self.assertEqual({}, self.service._prepared_previews)
        self.assertFalse(any(item["role"] == "tool" for item in self.panel._messages))

    def test_history_write_failure_does_not_abort_chat(self) -> None:
        self.manager.responses = [(_sse_message({"content": "已收到"}), 0)]
        with patch.object(self.panel.history_store, "save", side_effect=OSError("disk full")):
            self.panel.send_message()
            self.assertTrue(_wait_until(lambda: not self.panel._busy))
        self.assertEqual(1, len(self.manager.request_payloads))
        self.assertEqual("已收到", self.panel._messages[-1]["content"])
        self.assertIn("历史", self.panel.status_label.text())
        self.assertIn("保存", self.panel.status_label.text())

    def test_malformed_provider_tool_function_does_not_strand_panel(self) -> None:
        payload = {"choices": [{"message": {
            "tool_calls": [{"id": "bad-call", "type": "function", "function": "invalid"}],
        }}]}
        self.manager.responses = [
            ([json.dumps(payload).encode("utf-8")], 0),
            (_sse_message({"content": "已修正"}), 0),
        ]
        self.panel.send_message()
        self.assertTrue(_wait_until(lambda: not self.panel._busy))
        results = [json.loads(message["content"]) for message in self.panel._messages
                   if message["role"] == "tool"]
        self.assertEqual("INVALID_ARGUMENT", results[0]["error"]["code"])
        self.assertEqual("已修正", self.panel._messages[-1]["content"])

    def test_layout_rejects_out_of_bounds_computed_positions_atomically(self) -> None:
        root = self.controller.document.nodes[0]
        parent = root
        for _ in range(3):
            node = create_node(self.controller.schema, self.controller.document, "TouchIdle", (0, 0))
            self.controller.document.nodes.append(node)
            self.controller.document.connections.append(ConnectionRecord(parent.uuid, node.uuid))
            parent = node
        before = copy.deepcopy(self.controller.document)
        for spacing in (MAX_CANVAS_COORDINATE / 2, 1e308):
            with self.subTest(spacing=spacing):
                arguments = {"expected_revision": self.service.revision, "horizontal_spacing": spacing}
                preview = self.service.preview_tool("optimize_layout", arguments)
                result = self.service.invoke_tool("optimize_layout", arguments)
                self.assertFalse(preview["ok"], preview)
                self.assertFalse(result["ok"], result)
                self.assertEqual("INVALID_ARGUMENT", result["error"]["code"])
                self.assertEqual(before, self.controller.document)
                self.assertEqual(0, self.controller.undo_stack.count())

    def test_integer_fields_reject_fractional_values_without_truncation(self) -> None:
        for value in (1.5, True, float("inf")):
            for operation in (
                {"op": "set_metadata", "values": {"ship_skin_id": value}},
                {"op": "add_node", "node_type": "TouchIdle", "fields": {"offset_x": value}},
            ):
                with self.subTest(value=value, operation=operation["op"]):
                    result = self.service.invoke_tool("apply_graph_edits", {
                        "expected_revision": self.service.revision,
                        "operations": [operation],
                    })
                    self.assertFalse(result["ok"], result)
                    self.assertEqual("INVALID_ARGUMENT", result["error"]["code"])
                    self.assertEqual(0, self.controller.undo_stack.count())


if __name__ == "__main__":
    unittest.main()
