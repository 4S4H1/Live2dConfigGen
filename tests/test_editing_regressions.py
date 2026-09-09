"""Regression coverage for editing, save checkpoints and Windows CSV exports."""

from __future__ import annotations

import codecs
import copy
import csv
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("L2D_CONFIG_EDITOR_TEST_CLOSE_EVENT_POLICY", "discard")

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QFontMetricsF, QInputMethodEvent
from PySide6.QtWidgets import QApplication

from l2d_config_editor.csv_export import export_current_document_csv
from l2d_config_editor.logic import export_documents_to_csv, load_document
from l2d_config_editor.main_window import MainWindow
from l2d_config_editor.styles import stylesheet_for_theme
from l2d_config_editor.widgets import NumericLineEdit


class EditingRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.window = MainWindow(self.temp.name, prefer_saved_workspace=False)
        self.addCleanup(self._close)
        self.controller = self.window.controller
        meta = self.controller.document.meta
        meta.author = "tester"
        meta.ship_skin_id = 1
        meta.memo = "editing"
        meta.CharName = "测试角色"
        self.controller.refresh_derived()
        self.path = Path(self.temp.name) / "editing.json"
        self.controller.document.path = str(self.path)

    def _close(self) -> None:
        self.window.close()
        self.app.processEvents()
        self.app.setStyleSheet("")

    def _node(self, kind="Comment"):
        uuid = self.controller.create_node(kind, (100.0, 100.0))
        return self.window.canvas.node_items[uuid]

    def _show(self, item) -> None:
        self.window.show()
        self.window.canvas.centerOn(item)
        self.app.processEvents()

    def _saved_fields(self, uuid):
        document = load_document(self.controller.schema, self.path)
        return next(node.fields for node in document.nodes if node.uuid == uuid)

    def test_autosave_does_not_end_comment_edit_or_chinese_preedit(self) -> None:
        item = self._node()
        self.window._save_current_file(silent=True)
        self.controller.move_node(item.node.uuid, (100.0, 100.0), (110.0, 110.0))
        self._show(item)
        self.assertTrue(item.begin_comment_edit())
        editor = item._comment_editor_proxy.widget()
        editor.setPlainText("正在输入的备注")
        cursor = editor.textCursor()
        cursor.setPosition(3)
        editor.setTextCursor(cursor)
        self.app.sendEvent(editor, QInputMethodEvent("beizhu", []))
        for _ in range(3):
            self.window._run_auto_save()
            self.assertTrue(item.has_comment_editor(), "autosave closed the active editor")
            self.assertEqual("正在输入的备注", editor.toPlainText())
            self.assertEqual(3, editor.textCursor().position())
        event = QInputMethodEvent()
        event.setCommitString("备注")
        self.app.sendEvent(editor, event)
        item.commit_pending_inline_edit()
        self.window._run_auto_save()
        self.assertEqual("正在输备注入的备注", self._saved_fields(item.node.uuid)["content"])

    def test_comment_edit_font_matches_display_under_app_styles_and_zoom(self) -> None:
        for theme in ("dark", "light"):
            self.app.setStyleSheet(stylesheet_for_theme(theme))
            item = self._node()
            self._show(item)
            for scale in (1.0, 0.5, 0.18, 1.5):
                self.window.canvas._apply_view_state(scale, QPointF())
                item.begin_comment_edit()
                self.app.processEvents()
                editor = item._comment_editor_proxy.widget()
                expected = QFontMetricsF(item._comment_content_font()).height()
                actual = QFontMetricsF(editor.font()).height()
                self.assertAlmostEqual(expected, actual, delta=2.0, msg=f"{theme}, zoom={scale}")
            item.commit_pending_inline_edit()

    def test_save_commits_active_card_editor(self) -> None:
        item = self._node("TouchIdle")
        self._show(item)
        self.assertTrue(item._begin_card_field_edit("tips"))
        item._card_editor_proxy.widget().setText("刚输入但尚未失焦的备注")
        self.window._save_current_file(silent=True)
        self.assertEqual("刚输入但尚未失焦的备注", self._saved_fields(item.node.uuid)["tips"])

    def test_undo_then_different_edit_at_saved_index_stays_dirty(self) -> None:
        item = self._node()
        self.controller.update_field(item.node.uuid, "content", "保存版本", "simple")
        self.window._save_current_file(silent=True)
        saved_index = self.controller.undo_stack.index()
        self.controller.undo_stack.undo()
        self.controller.update_field(item.node.uuid, "content", "新的分支", "simple")
        self.assertEqual(saved_index, self.controller.undo_stack.index())
        self.assertTrue(self.window._is_dirty(), "new undo branch was mistaken for the saved version")
        self.window._run_auto_save()
        self.assertEqual("新的分支", self._saved_fields(item.node.uuid)["content"])

    def test_form_refresh_preserves_pending_text_and_cursor(self) -> None:
        item = self._node()
        self.controller.set_selected_node(item.node.uuid)
        self._show(item)
        form = self.window.inspector_form
        editor = form._bindings["content"].widget
        editor.setFocus()
        editor.setPlainText("未提交内容")
        cursor = editor.textCursor()
        cursor.setPosition(2)
        editor.setTextCursor(cursor)
        self.controller.update_field(item.node.uuid, "note_box_color", "#338855", "advanced")
        self.assertEqual("未提交内容", editor.toPlainText())
        self.assertEqual(2, editor.textCursor().position())
        self.window._save_current_file(silent=True)
        self.assertEqual("未提交内容", self._saved_fields(item.node.uuid)["content"])

    def test_save_preserves_multiple_pending_form_values(self) -> None:
        item = self._node("TouchIdle")
        self.controller.set_selected_node(item.node.uuid)
        form = self.window.inspector_form
        form._bindings["tips"].widget.setText("待提交备注")
        form._bindings["draw_able_name"].widget.setText("CustomFrame")
        self.window._save_current_file(silent=True)
        fields = self._saved_fields(item.node.uuid)
        self.assertEqual("待提交备注", fields["tips"])
        self.assertEqual("CustomFrame", fields["draw_able_name"])

    def test_deselection_clears_inspector_binding(self) -> None:
        item = self._node()
        self.controller.set_selected_node(item.node.uuid)
        self.controller.set_selected_node(None)
        self.assertIsNone(self.window.inspector_form.node)
        self.assertFalse(self.window.inspector_form._bindings)

    def test_csv_exports_have_bom_and_roundtrip_chinese_multiline_notes(self) -> None:
        item = self._node("TouchIdle")
        note = '中文备注，繁體與emoji🎵\n第二行,"引号"'
        self.controller.update_field(item.node.uuid, "desc", note, "advanced")
        self.controller.document.meta.memo = "中文资源说明"
        current = export_current_document_csv(self.controller.schema, self.controller.document, self.temp.name)
        batch = export_documents_to_csv(self.controller.schema, [self.controller.document], Path(self.temp.name) / "batch.csv")
        for path in (current, batch):
            self.assertTrue(path.read_bytes().startswith(codecs.BOM_UTF8), path.name)
            with path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(note, rows[0]["desc"])
            self.assertEqual("中文资源说明", rows[0]["memo"])

    def test_switching_selection_commits_to_previous_node_only(self) -> None:
        first = self._node()
        second = self._node()
        self.controller.set_selected_node(first.node.uuid)
        self.window.inspector_form._bindings["content"].widget.setPlainText("第一节点的草稿")
        self.controller.set_selected_node(second.node.uuid)
        self.assertEqual("第一节点的草稿", first.node.fields["content"])
        self.assertEqual("", second.node.fields["content"])

    def test_saved_session_keeps_dirty_branch_checkpoint_when_reopened(self) -> None:
        item = self._node()
        self.window._save_current_file(silent=True)
        self.controller.update_field(item.node.uuid, "content", "仍未保存", "simple")
        self.window._stash_current_document_session()
        self.window._open_existing_session_or_file(self.path)
        self.assertTrue(self.window._is_dirty())
        self.controller.undo_stack.undo()
        self.assertFalse(self.window._is_dirty())
        self.controller.undo_stack.redo()
        self.assertTrue(self.window._is_dirty())

    def test_intermediate_numeric_keystrokes_do_not_raise_or_clear_value(self) -> None:
        for mode, text in (("int", "-"), ("float", "-."), ("nullable_int", "-")):
            editor = NumericLineEdit(mode)
            committed = []
            editor.committed.connect(committed.append)
            editor.setText(text)
            editor._emit_commit()
            self.assertEqual([], committed)
            editor.setText("12")
            editor._emit_commit()
            self.assertEqual([12], committed)

    def test_invalid_legacy_csv_template_does_not_block_export(self) -> None:
        self._node("TouchIdle")
        from l2d_config_editor.logic import CSV_TEMPLATE_FILES
        template = Path(self.temp.name) / CSV_TEMPLATE_FILES[0]
        template.write_bytes("旧编码模板".encode("gbk"))
        output = export_current_document_csv(
            self.controller.schema, self.controller.document, self.temp.name,
            template_search_roots=(self.temp.name,),
        )
        with output.open(encoding="utf-8-sig", newline="") as handle:
            self.assertEqual(list(self.controller.schema.csv_columns), next(csv.reader(handle)))

    def test_batch_export_failure_preserves_existing_csv_and_live_model(self) -> None:
        item = self._node("TouchIdle")
        before = copy.deepcopy(self.controller.document)
        target = Path(self.temp.name) / "batch.csv"
        target.write_bytes(b"previous export")
        with patch("l2d_config_editor.logic.document_to_csv_rows", side_effect=ValueError("invalid row")):
            with self.assertRaises(ValueError):
                export_documents_to_csv(self.controller.schema, [self.controller.document], target)
        self.assertEqual(b"previous export", target.read_bytes())
        self.assertEqual(before, self.controller.document)


if __name__ == "__main__":
    unittest.main()
