"""Legacy history preservation and read-only SVN comparison interaction."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication
from l2d_config_editor.controller import EditorController
from l2d_config_editor.file_tracking import ExternalDocumentChangeError
from l2d_config_editor.graph_diff import diff_documents
from l2d_config_editor.history_view import GraphComparisonWidget
from l2d_config_editor.logic import export_document_dict, load_document, load_document_payload, save_document


class DocumentHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "history.json"
        self.controller = EditorController()
        self.schema = self.controller.schema
        self.document = self.controller.document
        self.document.meta.author = "测试作者"
        self.document.meta.CharName = "版本测试"
        self.document.meta.ship_skin_id = 1
        self.document.meta.memo = "测试"
        self.controller.refresh_derived()

    def save(self):
        save_document(self.schema, self.document, self.path)

    def test_new_and_repeated_saves_never_embed_history(self):
        for memo in ("first", "second", "third"):
            self.document.meta.memo = memo
            self.save()
            self.assertNotIn("history", json.loads(self.path.read_text(encoding="utf-8")))
            self.assertEqual({}, self.document.history)

    def test_old_document_without_history_stays_without_history(self):
        payload = export_document_dict(self.schema, self.document)
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        self.document = load_document(self.schema, self.path)
        self.document.meta.memo = "首次修改"
        self.save()
        loaded = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(5, loaded["format_version"])
        self.assertNotIn("history", loaded)

    def test_legacy_and_future_history_are_preserved_without_interpretation(self):
        for history in (
            {"version": 1, "revisions": [{"reverse": "legacy invalid delta", "digest": "old"}]},
            {"version": 999, "future": {"preserve": ["中文", None, True]}},
            {"version": 1, "large_legacy_value": "x" * (513 * 1024)},
        ):
            with self.subTest(version=history["version"]):
                payload = export_document_dict(self.schema, self.document)
                payload["history"] = history
                self.document = load_document_payload(self.schema, payload)
                self.document.meta.memo = "正文已修改"
                self.save()
                self.assertEqual(history, self.document.history)
                self.assertEqual(history, json.loads(self.path.read_text(encoding="utf-8"))["history"])
                self.assertEqual(history, load_document(self.schema, self.path).history)
                self.assertIsNot(history, self.document.history)

    def test_non_object_history_does_not_block_old_graph(self):
        for history in (None, [], "old", 123):
            payload = export_document_dict(self.schema, self.document)
            payload["history"] = history
            self.assertEqual({}, load_document_payload(self.schema, payload).history)

    def test_history_and_view_state_are_not_compared(self):
        before = copy.deepcopy(self.document)
        self.document.history = {"version": 99, "different": True}
        self.document.canvas_view.scale = .5
        self.document.plan_layout.view.offset_x = 500
        self.assertTrue(diff_documents(before, self.document).is_empty)

    def test_atomic_save_failure_preserves_file_and_opaque_history(self):
        self.document.history = {"version": 1, "legacy": "keep"}
        self.save()
        history = copy.deepcopy(self.document.history)
        original = self.path.read_bytes()
        self.document.meta.memo = "not saved"
        with patch("l2d_config_editor.logic.os.replace", side_effect=OSError("locked")):
            with self.assertRaises(OSError):
                self.save()
        self.assertEqual(history, self.document.history)
        self.assertEqual(original, self.path.read_bytes())

    def test_external_write_is_guarded_for_direct_controller_saves_too(self):
        self.save()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        payload["meta"]["memo"] = "external"
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(ExternalDocumentChangeError):
            self.controller.save_document(str(self.path))
        self.assertEqual("external", json.loads(self.path.read_text(encoding="utf-8"))["meta"]["memo"])

    def test_snapshot_command_undo_redo_preserves_saved_baseline_and_legacy_history(self):
        from l2d_config_editor.tool_service import _DocumentSnapshotCommand
        self.document.history = {"version": 1, "legacy": "keep"}
        self.save()
        after = copy.deepcopy(self.document)
        after.meta.memo = "工具修改"
        self.controller.undo_stack.push(_DocumentSnapshotCommand(self.controller, self.document, after, "test"))
        self.controller.save_document(str(self.path))
        self.controller.undo_stack.undo()
        self.controller.save_document(str(self.path))
        self.assertEqual("测试", load_document(self.schema, self.path).meta.memo)
        self.controller.undo_stack.redo()
        self.controller.save_document(str(self.path))
        loaded = load_document(self.schema, self.path)
        self.assertEqual("工具修改", loaded.meta.memo)
        self.assertEqual({"version": 1, "legacy": "keep"}, loaded.history)

    def comparison(self, before, after):
        widget = GraphComparisonWidget(self.schema)
        self.addCleanup(widget.close)
        widget.resize(1200, 800)
        widget.set_documents(before, after)
        widget.show()
        self.app.processEvents()
        return widget

    @staticmethod
    def wheel(canvas, delta):
        position = QPointF(canvas.viewport().rect().center())
        event = QWheelEvent(position, QPointF(canvas.viewport().mapToGlobal(position.toPoint())),
                            QPoint(), QPoint(0, delta), Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(canvas.viewport(), event)

    def drag_blank(self, canvas):
        candidates = [QPoint(x, y) for y in range(10, canvas.viewport().height() - 100, 30)
                      for x in range(10, canvas.viewport().width() - 150, 30)]
        point = QPointF(next(point for point in candidates if canvas.itemAt(point) is None))
        end = point + QPointF(120, 75)
        before = canvas.mapToScene(canvas.viewport().rect().center())
        for event_type, position, button, buttons in (
            (QEvent.Type.MouseButtonPress, point, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton),
            (QEvent.Type.MouseMove, end, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton),
            (QEvent.Type.MouseButtonRelease, end, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton),
        ):
            event = QMouseEvent(event_type, position,
                                QPointF(canvas.viewport().mapToGlobal(position.toPoint())),
                                button, buttons, Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(canvas.viewport(), event)
        after = canvas.mapToScene(canvas.viewport().rect().center())
        self.assertGreater((after - before).manhattanLength(), 1)

    def test_initial_large_graph_zooms_and_pans_without_selecting_a_change(self):
        self.controller.create_node("Comment", (0, 35000))
        before = copy.deepcopy(self.document)
        widget = self.comparison(before, self.document)
        self.assertIsNone(widget.changes.currentItem())
        scale = widget.canvas.transform().m11()
        self.assertLess(scale, .04)
        self.wheel(widget.canvas, 120)
        self.assertGreater(widget.canvas.transform().m11(), scale)
        self.drag_blank(widget.canvas)

    def test_initial_small_graph_can_pan_even_when_whole_graph_is_visible(self):
        widget = self.comparison(copy.deepcopy(self.document), self.document)
        self.drag_blank(widget.canvas)

    def test_fit_after_show_uses_final_viewport_and_remains_interactive(self):
        self.controller.create_node("Comment", (2500, 6000))
        widget = self.comparison(copy.deepcopy(self.document), self.document)
        initial = widget.canvas.transform().m11()
        widget.fit_graph()
        self.assertAlmostEqual(initial, widget.canvas.transform().m11(), places=6)
        self.wheel(widget.canvas, -120)
        self.assertLess(widget.canvas.transform().m11(), initial)
        self.drag_blank(widget.canvas)

    def test_diff_loaded_into_visible_widget_is_immediately_navigable(self):
        widget = self.comparison(copy.deepcopy(self.document), self.document)
        self.controller.create_node("Comment", (0, 35000))
        widget.set_documents(copy.deepcopy(self.document), self.document)
        scale = widget.canvas.transform().m11()
        self.wheel(widget.canvas, 120)
        self.assertGreater(widget.canvas.transform().m11(), scale)
        self.drag_blank(widget.canvas)

    def test_comparison_has_added_deleted_modified_nodes_and_is_read_only(self):
        removed = self.controller.create_node("Comment", (400, 0))
        edited = self.controller.create_node("Comment", (400, 300))
        before = copy.deepcopy(self.document)
        self.controller.remove_nodes([removed])
        added = self.controller.create_node("Comment", (800, 0))
        self.controller.update_field(edited, "content", "修改后")
        original = copy.deepcopy(export_document_dict(self.schema, self.document))
        widget = self.comparison(before, self.document)
        self.assertEqual("deleted", widget._status(("nodes", removed)))
        self.assertEqual("added", widget._status(("nodes", added)))
        self.assertEqual("modified", widget._status(("nodes", edited)))
        self.assertIn(("nodes", removed), widget.items_by_key)
        self.assertTrue(any("修改后" in item.toPlainText() for item in widget._decorations
                            if hasattr(item, "toPlainText")))
        widget.mode_combo.setCurrentIndex(1)
        self.assertEqual(original, export_document_dict(self.schema, self.document))


if __name__ == "__main__":
    unittest.main()
