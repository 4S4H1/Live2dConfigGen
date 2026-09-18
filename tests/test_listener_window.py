"""Listener subgraphs at real main-window save and document boundaries."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("L2D_CONFIG_EDITOR_TEST_CLOSE_EVENT_POLICY", "discard")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from l2d_config_editor.main_window import MainWindow
from l2d_config_editor.listener_compiler import parse_listener_literal


class ListenerWindowTests(unittest.TestCase):
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
        self.window = MainWindow(self.temp.name, prefer_saved_workspace=False)
        self.addCleanup(self.cleanup_window)
        self.controller = self.window.controller
        meta = self.controller.document.meta
        meta.ship_skin_id = 101
        meta.CharName = "监听测试"
        meta.author = "tester"
        meta.memo = "asset/test"
        self.controller.refresh_derived()
        self.path = Path(self.temp.name) / "graph.json"
        self.controller.save_document(self.path)

    def cleanup_window(self):
        if self.window.listener_dialog is not None:
            dialog = self.window.listener_dialog
            # Release intentionally malformed drafts after asserting protection.
            dialog.field_editors.clear()
            dialog.close()
        self.window.close()
        self.app.processEvents()

    def create_listener(self):
        self.window._create_listener()
        self.app.processEvents()
        self.assertIsNotNone(self.window.listener_dialog)
        return self.window.listener_dialog

    def amount_editor(self, dialog):
        part = next(part for part in dialog.graph().nodes if part.kind == "AddValue")
        dialog.select_part(part.uuid)
        self.app.processEvents()
        return dialog.field_editors["value"][1]

    def test_new_listener_opens_from_toolbar_and_saves_pending_edit(self):
        dialog = self.create_listener()
        self.amount_editor(dialog).setText("0.35")
        self.assertEqual(str(self.path), self.window._save_current_file(silent=True))
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        node = next(node for node in payload["nodes"] if node["type"] == "Listener")
        self.assertEqual(6, payload["format_version"])
        parts = node["listener_graph"]["nodes"].values()
        self.assertEqual(0.35, next(part["fields"]["value"] for part in parts if part["kind"] == "AddValue"))
        self.assertTrue(self.controller.undo_stack.isClean())

    def test_malformed_pending_edit_blocks_save_export_switch_and_close(self):
        dialog = self.create_listener()
        editor = self.amount_editor(dialog)
        editor.setText("unfinished-number")
        before = self.path.read_bytes()
        self.assertIsNone(self.window._save_current_file(silent=True))
        self.assertIsNone(self.window._export_current_graph_csv())
        self.assertFalse(self.window._ensure_safe_to_leave_document(Path(self.temp.name) / "other.json"))
        self.assertFalse(self.window._confirm_reload_current_document_from_disk())
        self.assertFalse(self.window._confirm_safe_to_close())
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual("unfinished-number", editor.text())
        self.assertTrue(dialog.isVisible())

    def test_ctrl_s_inside_subgraph_saves_main_document(self):
        dialog = self.create_listener()
        editor = self.amount_editor(dialog)
        editor.setText("0.75")
        editor.setFocus()
        QTest.keyClick(editor, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
        self.app.processEvents()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        node = next(node for node in payload["nodes"] if node["type"] == "Listener")
        literal = parse_listener_literal(node["listener_data"])
        self.assertEqual(0.75, literal["change"][0][2])
        self.assertTrue(self.controller.undo_stack.isClean())

    def test_malformed_unfocused_draft_blocks_external_auto_reload(self):
        dialog = self.create_listener()
        self.window._save_current_file(silent=True)
        editor = self.amount_editor(dialog)
        editor.setText("-")
        editor.clearFocus()
        self.assertTrue(self.window._has_active_text_editor())
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        payload["meta"]["CharName"] = "磁盘新版"
        self.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        self.window._check_external_document()
        self.assertNotEqual("磁盘新版", self.controller.document.meta.CharName)
        self.assertTrue(self.window._external_change_pending)
        self.assertEqual("-", editor.text())

    def test_legacy_listener_import_keeps_action_and_is_undoable(self):
        uuid = self.controller.create_node("TouchDrag", (100, 100))
        original = "{type=2,change={{2,{'TouchDrag1'},0.5,2}}}"
        self.controller.update_field(uuid, "listener_data", original, "advanced")
        owner = self.controller.get_node(uuid)
        original_action = owner.fields["action_trigger"]
        self.window._show_listener_editor(uuid)
        self.app.processEvents()
        self.assertIsNotNone(owner.listener_graph)
        self.assertEqual(original_action, owner.fields["action_trigger"])
        self.assertEqual(original_action, json.loads(self.window.listener_dialog.preview.toPlainText())["action_trigger"])
        self.assertEqual(parse_listener_literal(original), parse_listener_literal(owner.fields["listener_data"]))
        self.controller.undo_stack.undo()
        self.app.processEvents()
        self.assertIsNone(self.controller.get_node(uuid).listener_graph)
        self.assertEqual(original, self.controller.get_node(uuid).fields["listener_data"])

    def test_unsupported_legacy_listener_is_left_untouched(self):
        uuid = self.controller.create_node("TouchDrag", (100, 100))
        self.controller.update_field(uuid, "listener_data", "{type=9,change={}}", "advanced")
        before = self.controller.undo_stack.index()
        with patch("l2d_config_editor.main_window.QMessageBox.warning") as warning:
            self.window._show_listener_editor(uuid)
            warning.assert_called_once()
        self.assertEqual(before, self.controller.undo_stack.index())
        self.assertIsNone(self.controller.get_node(uuid).listener_graph)
        self.assertIsNone(self.window.listener_dialog)

    def test_locked_host_opens_readonly_and_repeated_open_reuses_dialog(self):
        dialog = self.create_listener()
        uuid = dialog.owner_uuid
        dialog.close()
        self.app.processEvents()
        self.controller.get_node(uuid).locked = True
        self.window._show_listener_editor(uuid)
        dialog = self.window.listener_dialog
        self.assertTrue(dialog.read_only)
        self.window._show_listener_editor(uuid)
        self.assertIs(dialog, self.window.listener_dialog)
        self.controller._set_node_locked(uuid, False)
        self.app.processEvents()
        self.assertFalse(dialog.read_only)

    def test_graph_tools_refuse_uncommitted_invalid_subgraph_input(self):
        dialog = self.create_listener()
        self.amount_editor(dialog).setText("?")
        before = self.path.read_bytes()
        result = self.window.tool_service.invoke_tool("save_current_graph", {})
        self.assertFalse(result["ok"])
        self.assertEqual("PENDING_EDITOR_INPUT", result["error"]["code"])
        self.assertEqual(before, self.path.read_bytes())


if __name__ == "__main__":
    unittest.main()
