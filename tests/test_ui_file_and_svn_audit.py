"""File actions and asynchronous process lifecycle audit regressions."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("L2D_CONFIG_EDITOR_TEST_CLOSE_EVENT_POLICY", "discard")

from PySide6.QtCore import QProcess
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from l2d_config_editor.main_window import MainWindow
from l2d_config_editor.svn_tools import SvnProcessExecutor


class FileActionAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.window = MainWindow(self.root, prefer_saved_workspace=False)
        self.addCleanup(self.window.close)
        meta = self.window.controller.document.meta
        meta.author, meta.ship_skin_id, meta.memo, meta.CharName = "tester", 1, "test", "角色"
        self.window.controller.refresh_derived()
        self.group = self.root / "v1.2"
        self.group.mkdir()
        self.path = self.group / "source.json"
        self.window.controller.save_document(str(self.path))
        self.window._refresh_file_list()
        self.window._select_file_in_list("v1.2/source.json")

    def rename(self, filename):
        with patch("l2d_config_editor.main_window.QInputDialog.getText", return_value=(filename, True)), \
             patch("l2d_config_editor.main_window.QMessageBox.warning") as warning:
            self.window._rename_selected_file()
        return warning

    def test_rename_rejects_path_components_before_moving_file(self):
        warning = self.rename("../escaped.json")
        self.assertTrue(warning.called)
        self.assertTrue(self.path.exists())
        self.assertFalse((self.root / "escaped.json").exists())
        self.assertEqual(str(self.path), self.window.controller.document.path)

    def test_rename_collision_reports_error_without_losing_either_file(self):
        target = self.group / "existing.json"
        target.write_text("keep existing", encoding="utf-8")
        warning = self.rename(target.name)
        self.assertTrue(warning.called)
        self.assertTrue(self.path.exists())
        self.assertEqual("keep existing", target.read_text(encoding="utf-8"))

    def test_rename_keeps_uppercase_json_extension(self):
        self.rename("renamed.JSON")
        self.assertTrue((self.group / "renamed.JSON").exists())
        self.assertFalse((self.group / "renamed.JSON.json").exists())

    def test_file_group_keeps_dots_in_directory_name(self):
        self.assertEqual("v1.2", self.window._current_group_dir())

    def test_rename_keeps_cached_document_and_undo_stack_under_new_path(self):
        self.window._stash_current_document_session()
        document = self.window.controller.document
        stack = self.window.controller.undo_stack
        self.rename("renamed.json")
        target = self.group / "renamed.json"
        old_key = self.window._session_key_for_path(self.path)
        new_key = self.window._session_key_for_path(target)
        self.assertNotIn(old_key, self.window._document_sessions)
        self.assertIs(document, self.window._document_sessions[new_key]["document"])
        self.assertIs(stack, self.window._document_sessions[new_key]["undo_stack"])
        self.assertEqual(str(target), document.path)
        self.assertEqual(new_key, self.window._current_session_key)


class _ProcessDouble:
    def __init__(self):
        self.running = True
        self.kills = 0

    def state(self):
        return QProcess.ProcessState.Running if self.running else QProcess.ProcessState.NotRunning

    def start(self, _executable, _arguments):
        self.running = True

    def terminate(self):
        pass

    def kill(self):
        self.kills += 1

    def readAllStandardOutput(self):
        return b""

    def readAllStandardError(self):
        return b""


class SvnCancellationAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_old_cancel_timeout_does_not_kill_the_next_query(self):
        executor = SvnProcessExecutor()
        executor.process = process = _ProcessDouble()
        executor.cancel()
        process.running = False
        executor._finished(1, QProcess.ExitStatus.NormalExit)
        executor.start("svn", ["info"])
        QTest.qWait(1150)
        self.assertEqual(0, process.kills)

    def test_cancellation_still_kills_a_process_that_will_not_exit(self):
        executor = SvnProcessExecutor()
        executor.process = process = _ProcessDouble()
        executor.cancel()
        QTest.qWait(1150)
        self.assertEqual(1, process.kills)


if __name__ == "__main__":
    unittest.main()
