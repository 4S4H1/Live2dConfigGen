"""Search IME, external writes and workspace startup regression coverage."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("L2D_CONFIG_EDITOR_TEST_CLOSE_EVENT_POLICY", "discard")

from PySide6.QtGui import QInputMethodEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from l2d_config_editor.main_window import MainWindow


class WorkspaceRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.window = MainWindow(self.temp.name, prefer_saved_workspace=False)
        self.addCleanup(self.window.close)
        self.controller = self.window.controller
        self.path = Path(self.temp.name) / "graph.json"
        self.controller.document.meta.author = "tester"
        self.controller.document.meta.CharName = "测试"
        self.controller.document.meta.ship_skin_id = 1
        self.controller.document.meta.memo = "测试"
        self.controller.save_document(str(self.path))

    def external_write(self, name="外部更新"):
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        payload["meta"]["CharName"] = name
        replacement = self.path.with_suffix(".tmp")
        replacement.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        replacement.replace(self.path)

    def test_find_focuses_sidebar_and_accepts_chinese_preedit(self):
        self.window.show()
        self.window.activateWindow()
        self.app.processEvents()
        self.window._focus_search()
        self.app.processEvents()
        self.assertFalse(hasattr(self.window, "search_panel"))
        self.assertTrue(self.window.node_search_edit.hasFocus())
        self.app.sendEvent(self.window.node_search_edit, QInputMethodEvent("zhongwen", []))
        event = QInputMethodEvent()
        event.setCommitString("中文")
        self.app.sendEvent(self.window.node_search_edit, event)
        self.assertEqual("中文", self.window.node_search_edit.text())

    def test_clean_external_atomic_replace_reloads_without_schema_reload(self):
        self.external_write()
        self.window._check_external_document()
        self.assertEqual("外部更新", self.controller.document.meta.CharName)
        self.assertTrue(self.controller.undo_stack.isClean())

    def test_watcher_handles_atomic_replacement_and_rearms_for_next_update(self):
        self.window._disk_poll.stop()
        for name in ("第一次更新", "第二次更新"):
            self.external_write(name)
            for _ in range(20):
                QTest.qWait(50)
                if self.controller.document.meta.CharName == name:
                    break
            self.assertEqual(name, self.controller.document.meta.CharName)

    def test_external_change_keeps_pending_inline_chinese_input(self):
        uuid = self.controller.create_node("Comment", (100, 100))
        self.window._save_current_file(silent=True)
        self.window.show()
        self.app.processEvents()
        item = self.window.canvas.node_items[uuid]
        item.begin_comment_edit()
        editor = item._comment_editor_proxy.widget()
        editor.setPlainText("未提交的中文")
        self.app.sendEvent(editor, QInputMethodEvent("pinyin", []))
        self.external_write()
        self.window._check_external_document()
        self.assertTrue(item.has_comment_editor())
        self.assertEqual("未提交的中文", editor.toPlainText())
        self.assertTrue(self.window._external_change_pending)

    def test_unchanged_poll_is_stat_only_and_pending_change_is_hashed_once(self):
        with patch.object(Path, "read_bytes", side_effect=AssertionError("unchanged read")):
            self.window._check_external_document()
        self.controller.create_node("Comment", (100, 100))
        self.external_write()
        self.window._check_external_document()
        with patch.object(Path, "read_bytes", side_effect=AssertionError("pending change read again")):
            self.window._check_external_document()

    def test_dirty_external_change_does_not_overwrite_either_version(self):
        uuid = self.controller.create_node("Comment", (100, 100))
        self.external_write()
        self.window._check_external_document()
        self.window._run_auto_save()
        self.assertIsNotNone(self.controller.get_node(uuid))
        self.assertEqual("测试", self.controller.document.meta.CharName)
        self.assertEqual("外部更新", json.loads(self.path.read_text(encoding="utf-8"))["meta"]["CharName"])
        self.assertFalse(self.window.external_change_bar.isHidden())

    def test_partial_external_json_preserves_live_document_then_retries(self):
        original = self.path.read_bytes()
        self.path.write_bytes(b'{"meta":')
        self.window._check_external_document()
        self.assertEqual("测试", self.controller.document.meta.CharName)
        self.path.write_bytes(original)
        self.external_write("完整更新")
        self.window._check_external_document()
        self.assertEqual("完整更新", self.controller.document.meta.CharName)

    def test_inactive_clean_session_is_invalidated_after_external_change(self):
        self.window._stash_current_document_session()
        other = Path(self.temp.name) / "other.json"
        other.write_bytes(self.path.read_bytes())
        self.window._open_existing_session_or_file(other)
        self.external_write()
        self.window._open_existing_session_or_file(self.path)
        self.assertEqual("外部更新", self.controller.document.meta.CharName)

    def test_save_conflict_copy_does_not_leave_original_session_pointing_to_copy(self):
        from PySide6.QtWidgets import QFileDialog

        self.window._stash_current_document_session()
        self.controller.create_node("Comment", (100, 100))
        self.external_write()
        copy_path = Path(self.temp.name) / "copy.json"
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(copy_path), "")):
            self.window._save_external_conflict_copy()
        self.assertTrue(copy_path.is_file())
        self.window._open_existing_session_or_file(self.path)
        self.assertEqual(str(self.path), self.controller.document.path)
        self.assertEqual("外部更新", self.controller.document.meta.CharName)

    def test_unchanged_file_list_reuses_json_metadata_and_prunes_svn(self):
        svn = Path(self.temp.name) / ".svn"
        svn.mkdir()
        (svn / "hidden.json").write_bytes(self.path.read_bytes())
        self.assertEqual(["graph.json"], self.controller.file_list())
        with patch.object(Path, "read_text", side_effect=AssertionError("unchanged JSON re-read")):
            self.assertEqual(["graph.json"], self.controller.file_list())


if __name__ == "__main__":
    unittest.main()
