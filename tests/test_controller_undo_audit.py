"""Undo and document-opening regression tests found during the full audit."""

import copy
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from l2d_config_editor.controller import EditorController
from l2d_config_editor.models import CanvasImageRecord


class ControllerUndoAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.controller = EditorController()
        meta = self.controller.document.meta
        meta.author = "tester"
        meta.ship_skin_id = 1
        meta.memo = "audit"
        meta.CharName = "测试角色"
        self.controller.refresh_derived()

    def test_undo_field_edits_restores_manual_ownership(self):
        for operation in ("single", "batch", "many"):
            with self.subTest(operation=operation):
                uuid = self.controller.create_node("TouchIdle", (100.0, 100.0))
                node = self.controller.get_node(uuid)
                before = node.clone()
                if operation == "single":
                    self.controller.update_field(uuid, "draw_able_name", "Custom")
                elif operation == "batch":
                    self.controller.update_fields(uuid, {"draw_able_name": "Custom", "tips": "备注"})
                else:
                    self.controller.update_fields_for_nodes([uuid], {"draw_able_name": "Custom"})
                after = node.clone()
                self.controller.undo_stack.undo()
                self.assertEqual(before.manual_fields, node.manual_fields)
                self.assertEqual(before.fields, node.fields)
                self.controller.undo_stack.redo()
                self.assertEqual(after.manual_fields, node.manual_fields)
                self.assertEqual(after.fields, node.fields)

    def test_undo_linked_field_edit_restores_all_generated_values(self):
        uuid = self.controller.create_node("TouchIdle", (100.0, 100.0))
        self.controller.set_numeric_linkage_enabled(True)
        node = self.controller.get_node(uuid)
        before = node.clone()
        self.controller.update_field(uuid, "parameter", "touch_idle9", "simple")
        after = node.clone()
        self.assertNotEqual(before.fields, after.fields)
        self.controller.undo_stack.undo()
        self.assertEqual(before.fields, node.fields)
        self.assertEqual(before.manual_fields, node.manual_fields)
        self.controller.undo_stack.redo()
        self.assertEqual(after.fields, node.fields)

    def test_failed_open_keeps_current_undo_history(self):
        uuid = self.controller.create_node("Comment", (100.0, 100.0))
        self.controller.update_field(uuid, "content", "未保存")
        document = self.controller.document
        count = self.controller.undo_stack.count()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "invalid.json"
            path.write_text("{", encoding="utf-8")
            with self.assertRaises(ValueError):
                self.controller.open_document(path)
        self.assertIs(document, self.controller.document)
        self.assertEqual(count, self.controller.undo_stack.count())
        self.controller.undo_stack.undo()
        self.assertEqual("", self.controller.get_node(uuid).fields["content"])

    def test_undo_delete_preserves_node_and_connection_order(self):
        nodes = [self.controller.create_node("TouchIdle", (index * 100.0, 100.0)) for index in range(3)]
        root = self.controller.document.nodes[0].uuid
        for uuid in nodes:
            self.controller.add_connection(root, uuid)
        before_nodes = [node.uuid for node in self.controller.document.nodes]
        before_connections = copy.deepcopy(self.controller.document.connections)
        self.controller.remove_nodes([nodes[0]])
        self.controller.undo_stack.undo()
        self.assertEqual(before_nodes, [node.uuid for node in self.controller.document.nodes])
        self.assertEqual(before_connections, self.controller.document.connections)

    def test_undo_connection_delete_preserves_edge_order(self):
        nodes = [self.controller.create_node("TouchIdle", (index * 100.0, 100.0)) for index in range(3)]
        root = self.controller.document.nodes[0].uuid
        for uuid in nodes:
            self.controller.add_connection(root, uuid)
        before = copy.deepcopy(self.controller.document.connections)
        self.controller.remove_connection(root, nodes[0])
        self.controller.undo_stack.undo()
        self.assertEqual(before, self.controller.document.connections)

    def test_undo_image_delete_preserves_painting_order(self):
        self.controller.document.canvas_images = [
            CanvasImageRecord(uuid=str(index), data_base64="") for index in range(4)
        ]
        before = copy.deepcopy(self.controller.document.canvas_images)
        self.controller.remove_canvas_images(["0", "2"])
        self.controller.undo_stack.undo()
        self.assertEqual(before, self.controller.document.canvas_images)


if __name__ == "__main__":
    unittest.main()
