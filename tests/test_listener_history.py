"""Nested listener history stays compact, navigable and detached from editing."""
import copy
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from l2d_config_editor.controller import EditorController
from l2d_config_editor.document_history import history_snapshot, revision_snapshot, snapshot_payload
from l2d_config_editor.graph_diff import diff_documents
from l2d_config_editor.history_view import GraphComparisonWidget
from l2d_config_editor.listener_graph import ListenerPart, ListenerWire
from l2d_config_editor.logic import export_document_dict, load_document_payload, save_document


class ListenerHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from unittest.mock import patch
        feature = patch("l2d_config_editor.features.LISTENER_EDITOR_ENABLED", True)
        feature.start()
        self.addCleanup(feature.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "listener-history.json"
        self.controller = EditorController()
        self.schema, self.document = self.controller.schema, self.controller.document
        meta = self.document.meta
        meta.author, meta.CharName, meta.ship_skin_id, meta.memo = "测试", "测试", 1, "测试"
        self.controller.refresh_derived()
        self.owner_uuid = self.controller.create_node("Listener", (420, 0))
        self.graph = self.controller.get_node(self.owner_uuid).listener_graph
        self.change_uuid = next(part.uuid for part in self.graph.nodes if part.kind == "AddValue")
        self.state_uuid = next(part.uuid for part in self.graph.nodes if part.kind == "ValueState")

    def change_amount(self, amount):
        graph = self.controller.get_node(self.owner_uuid).listener_graph.clone()
        next(part for part in graph.nodes if part.uuid == self.change_uuid).fields["value"] = amount
        self.controller.set_listener_graph(self.owner_uuid, graph)

    def save(self):
        save_document(self.schema, self.document, self.path)

    def snapshot(self):
        return history_snapshot(export_document_dict(self.schema, self.document))

    def test_nested_scalar_delta_restores_graph_without_viewport_noise(self):
        self.save()
        baseline = self.snapshot()
        graph = self.controller.get_node(self.owner_uuid).listener_graph.clone()
        graph.view.update(scale=2, offset_x=100, offset_y=-100)
        self.controller.set_listener_graph(self.owner_uuid, graph)
        self.save()
        self.assertEqual(1, len(self.document.history["revisions"]))
        self.assertEqual(baseline, self.snapshot())
        self.change_amount(3)
        self.save()
        delta = self.document.history["revisions"][-1]["reverse"]
        path = ["nodes", "items", self.owner_uuid, "listener_graph", "nodes", self.change_uuid, "fields", "value"]
        self.assertTrue(any(operation["path"] == path and operation["value"] == 1 for operation in delta))
        self.assertFalse(any(operation["path"][-1] in {"listener_graph", "view"} for operation in delta))
        restored = revision_snapshot(self.document.history, self.document.history_snapshot, 0)
        self.assertEqual(restored, baseline)
        loaded = load_document_payload(self.schema, snapshot_payload(restored))
        self.assertEqual(history_snapshot(export_document_dict(self.schema, loaded)), baseline)

    def test_internal_add_delete_move_and_field_changes_locate_owner(self):
        before = copy.deepcopy(self.document)
        graph = self.graph.clone()
        graph.nodes.append(ListenerPart("range", "IdleRange", {"minimum": 0, "maximum": 2, "idle": 3}, {"x": 1000, "y": 80}))
        graph.connections.append(ListenerWire(self.state_uuid, "value", "range", "value"))
        self.controller.set_listener_graph(self.owner_uuid, graph)
        diff = diff_documents(before, self.document)
        self.assertTrue(any(entry.identity == self.owner_uuid and entry.field_path == "listener_graph.nodes.range" for entry in diff.entries))
        self.assertTrue(any(entry.identity == self.owner_uuid and entry.field_path.startswith("listener_graph.connections.") for entry in diff.entries))
        reversed_diff = diff_documents(self.document, before)
        self.assertTrue(any(entry.field_path == "listener_graph.nodes.range" and entry.after is None for entry in reversed_diff.entries))
        before = copy.deepcopy(self.document)
        graph.nodes[-1].ui_position["x"] = 1200
        self.controller.set_listener_graph(self.owner_uuid, graph)
        self.assertTrue(any(entry.field_path == "listener_graph.nodes.range.ui_position.x" for entry in diff_documents(before, self.document).entries))

    def test_history_buttons_open_detached_before_and_after_graphs(self):
        before = copy.deepcopy(self.document)
        self.change_amount(3)
        after = copy.deepcopy(self.document)
        widget = GraphComparisonWidget(self.schema)
        self.addCleanup(widget.close)
        widget.set_documents(before, after)
        self.assertEqual(widget._status(("nodes", self.owner_uuid)), "modified")
        entry = next(entry for entry in widget.diff.entries if entry.field_path.endswith(f"{self.change_uuid}.fields.value"))
        self.assertIn("累加量", widget._field_label(entry))
        widget._select_listener_owner(self.owner_uuid)
        self.assertTrue(widget.before_listener_button.isEnabled())
        self.assertTrue(widget.after_listener_button.isEnabled())
        dialogs = [widget.open_listener_graph(side) for side in ("before", "after")]
        for dialog, expected in zip(dialogs, (1, 3)):
            self.assertTrue(dialog.read_only)
            self.assertIsNot(dialog.controller, self.controller)
            self.assertEqual(next(part for part in dialog.graph().nodes if part.uuid == self.change_uuid).fields["value"], expected)
            baseline = dialog.graph().to_payload()
            self.assertIsNone(dialog.add_part("ActionEvent"))
            dialog.select_part(self.change_uuid)
            self.assertFalse(dialog.delete_selected())
            self.assertEqual(dialog.graph().to_payload(), baseline)
        self.change_amount(8)
        self.assertEqual(next(part for part in dialogs[1].graph().nodes if part.uuid == self.change_uuid).fields["value"], 3)
        self.assertEqual(before, widget.before)
        self.assertEqual(after, widget.after)
        for dialog in dialogs:
            dialog.close()

    def test_double_click_deleted_listener_opens_its_historical_subgraph(self):
        before = copy.deepcopy(self.document)
        self.controller.remove_nodes([self.owner_uuid])
        after = copy.deepcopy(self.document)
        widget = GraphComparisonWidget(self.schema)
        self.addCleanup(widget.close)
        widget.resize(1100, 700)
        widget.set_documents(before, after)
        widget.show()
        self.app.processEvents()
        card = widget.items_by_key[("nodes", self.owner_uuid)]
        point = widget.canvas.mapFromScene(card.sceneBoundingRect().center())
        QTest.mouseDClick(widget.canvas.viewport(), Qt.MouseButton.LeftButton, pos=point)
        self.app.processEvents()
        self.assertEqual(len(widget._listener_history_dialogs), 1)
        dialog = widget._listener_history_dialogs[0][0]
        self.assertTrue(dialog.read_only)
        self.assertIn("修改前", dialog.windowTitle())
        self.assertEqual(dialog.graph().to_payload(), self.graph.to_payload())
        self.assertFalse(widget.after_listener_button.isEnabled())
        dialog.close()


if __name__ == "__main__":
    unittest.main()
