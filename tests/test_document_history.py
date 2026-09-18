"""Round-trip, retention, compatibility and read-only graphical history tests."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from l2d_config_editor.controller import EditorController
from l2d_config_editor.document_history import (
    MAX_HISTORY_BYTES, MAX_HISTORY_VERSIONS, append_history, history_is_anchored,
    history_size, history_snapshot, revision_snapshot, snapshot_payload,
)
from l2d_config_editor.file_tracking import ExternalDocumentChangeError
from l2d_config_editor.graph_diff import diff_documents
from l2d_config_editor.history_view import DocumentHistoryDialog
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

    def snapshot(self):
        return history_snapshot(export_document_dict(self.schema, self.document))

    def test_saved_revisions_reconstruct_add_edit_delete_and_metadata(self):
        self.save()
        expected = [self.snapshot()]
        uuid = self.controller.create_node("Comment", (200, 100))
        self.save()
        expected.append(self.snapshot())
        self.controller.update_field(uuid, "content", "中文历史\n第二行")
        self.document.meta.memo = "新元数据"
        self.save()
        expected.append(self.snapshot())
        self.controller.remove_nodes([uuid])
        self.save()
        expected.append(self.snapshot())
        loaded = load_document(self.schema, self.path)
        self.assertTrue(history_is_anchored(loaded.history, loaded.history_snapshot))
        self.assertEqual(4, len(loaded.history["revisions"]))
        for index, snapshot in enumerate(expected):
            restored = revision_snapshot(loaded.history, loaded.history_snapshot, index)
            self.assertEqual(snapshot, restored)
            self.assertEqual(snapshot, history_snapshot(export_document_dict(self.schema, load_document_payload(self.schema, snapshot_payload(restored)))))

    def test_repeated_save_and_viewport_changes_do_not_create_versions(self):
        self.save()
        self.document.canvas_view.scale = .5
        self.document.plan_layout.view.offset_x = 500
        self.save()
        self.assertEqual(1, len(self.document.history["revisions"]))

    def test_legacy_json_without_history_gets_baseline_on_first_save(self):
        payload = export_document_dict(self.schema, self.document)
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        self.document = load_document(self.schema, self.path)
        old = self.document.history_snapshot
        self.document.meta.memo = "首次修改"
        self.save()
        self.assertEqual(5, json.loads(self.path.read_text(encoding="utf-8"))["format_version"])
        self.assertEqual(old, revision_snapshot(self.document.history, self.document.history_snapshot, 0))

    def test_retention_bounds_and_every_retained_revision_is_replayable(self):
        self.save()
        for index in range(65):
            self.document.meta.memo = f"version {index}"
            self.save()
        history = self.document.history
        self.assertEqual(MAX_HISTORY_VERSIONS, len(history["revisions"]))
        self.assertLessEqual(history_size(history), MAX_HISTORY_BYTES)
        for index in range(len(history["revisions"])):
            revision_snapshot(history, self.document.history_snapshot, index)

    def test_large_removed_value_trims_history_without_bloating_json(self):
        before = {"image": "x" * (MAX_HISTORY_BYTES + 1000), "field": "old"}
        after = {"field": "new"}
        history = append_history({}, before, after, "tester")
        self.assertLessEqual(history_size(history), MAX_HISTORY_BYTES)
        self.assertEqual(1, len(history["revisions"]))
        self.assertEqual(after, revision_snapshot(history, after, 0))

    def test_unchanged_large_image_is_not_copied_into_each_revision(self):
        before = {"image": "x" * (MAX_HISTORY_BYTES * 2), "field": "old"}
        after = {**before, "field": "new"}
        history = append_history({}, before, after, "tester")
        self.assertLess(history_size(history), 2000)
        self.assertEqual(before, revision_snapshot(history, after, 0))

    def test_atomic_save_failure_does_not_advance_history(self):
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

    def test_snapshot_command_undo_after_save_preserves_disk_baseline_and_history(self):
        from l2d_config_editor.tool_service import _DocumentSnapshotCommand

        self.save()
        after = copy.deepcopy(self.document)
        after.meta.memo = "工具修改"
        self.controller.undo_stack.push(_DocumentSnapshotCommand(self.controller, self.document, after, "test"))
        self.controller.save_document(str(self.path))
        self.controller.undo_stack.undo()
        self.controller.save_document(str(self.path))
        self.assertEqual(3, len(self.controller.document.history["revisions"]))
        self.assertEqual("测试", load_document(self.schema, self.path).meta.memo)
        self.controller.undo_stack.redo()
        self.controller.save_document(str(self.path))
        self.assertEqual(4, len(self.controller.document.history["revisions"]))
        self.assertEqual("工具修改", load_document(self.schema, self.path).meta.memo)

    def test_unknown_history_extension_is_preserved_and_graph_still_loads(self):
        payload = export_document_dict(self.schema, self.document)
        payload["history"] = {"version": 999, "future": "keep"}
        self.document = load_document_payload(self.schema, payload)
        self.save()
        self.assertEqual(payload["history"], json.loads(self.path.read_text(encoding="utf-8"))["history"])

    def test_corrupt_delta_is_rejected_without_changing_live_graph(self):
        self.save()
        self.document.meta.memo = "changed"
        self.save()
        history = copy.deepcopy(self.document.history)
        history["revisions"][-1]["reverse"][0]["value"] = "tampered"
        with self.assertRaises(ValueError):
            revision_snapshot(history, self.document.history_snapshot, 0)
        self.assertEqual("changed", self.document.meta.memo)

    def test_history_canvas_has_added_deleted_modified_nodes_and_is_read_only(self):
        removed = self.controller.create_node("Comment", (400, 0))
        edited = self.controller.create_node("Comment", (400, 300))
        self.save()
        self.controller.remove_nodes([removed])
        added = self.controller.create_node("Comment", (800, 0))
        self.controller.update_field(edited, "content", "修改后")
        self.save()
        original = copy.deepcopy(export_document_dict(self.schema, self.document))
        dialog = DocumentHistoryDialog(self.schema, self.document)
        self.addCleanup(dialog.close)
        self.assertEqual("deleted", dialog.comparison._status(("nodes", removed)))
        self.assertEqual("added", dialog.comparison._status(("nodes", added)))
        self.assertEqual("modified", dialog.comparison._status(("nodes", edited)))
        self.assertIn(("nodes", removed), dialog.comparison.items_by_key)
        self.assertTrue(dialog.comparison.items_by_key[("nodes", edited)].childItems())
        self.assertTrue(any("修改后" in item.toPlainText()
                            for item in dialog.comparison._decorations if hasattr(item, "toPlainText")))
        dialog.comparison.mode_combo.setCurrentIndex(1)
        dialog.left_combo.setCurrentIndex(0)
        self.assertEqual(original, export_document_dict(self.schema, self.document))

    def test_diff_includes_metadata(self):
        before = copy.deepcopy(self.document)
        self.document.meta.memo = "new"
        self.assertTrue(any(entry.category == "metadata" and entry.field_path == "memo"
                            for entry in diff_documents(before, self.document).entries))


if __name__ == "__main__":
    unittest.main()
