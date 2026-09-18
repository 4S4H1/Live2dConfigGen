"""History dialogs release detached graphs and safely cancel live processes."""
import copy
import gc
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import weakref
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("L2D_CONFIG_EDITOR_TEST_CLOSE_EVENT_POLICY", "discard")
from PySide6.QtCore import QCoreApplication, QEvent, QProcess
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid
from l2d_config_editor.controller import EditorController
from l2d_config_editor.logic import save_document
from l2d_config_editor.main_window import MainWindow
from l2d_config_editor.svn_diff_dialog import SvnGraphDiffDialog
from l2d_config_editor.svn_tools import SvnHistoryRunner


class HistoryDialogLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.window = MainWindow(self.temp.name, prefer_saved_workspace=False)
        self.addCleanup(self.window.close)
        self.path = Path(self.temp.name) / "graph.json"
        self.path.write_text("{}", encoding="utf-8")
        self.window.controller.document.path = str(self.path)
        select = patch.object(self.window, "_select_svn_executable", return_value=Path(sys.executable))
        select.start()
        self.addCleanup(select.stop)

    def flush_deletes(self):
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def wait_until(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(20)
            self.flush_deletes()
        self.assertTrue(predicate())

    def test_repeated_open_close_releases_dialog_runner_and_large_graph_copies(self):
        snapshots = []
        with patch.object(SvnHistoryRunner, "query_info", return_value=None):
            for index in range(8):
                self.window._show_svn_graph_diff()
                dialog = self.window.svn_diff_dialog
                runner = dialog.runner
                self.assertIs(dialog, runner.parent())
                document = copy.deepcopy(self.window.controller.document)
                document.nodes[-1].fields["test_payload"] = "x" * 100000
                snapshots.append(weakref.ref(document))
                dialog.comparison.set_documents(document, copy.deepcopy(document))
                del document
                dialog.reject()
                self.flush_deletes()
                self.assertIsNone(self.window.svn_diff_dialog)
                self.assertFalse(isValid(dialog))
                self.assertFalse(isValid(runner))
                self.assertEqual([], self.window.findChildren(SvnGraphDiffDialog))
                self.assertEqual([], self.window.findChildren(SvnHistoryRunner))
                del dialog, runner
        gc.collect()
        self.assertTrue(all(reference() is None for reference in snapshots))

    def test_replacing_open_dialog_does_not_clear_or_delete_new_dialog(self):
        with patch.object(SvnHistoryRunner, "query_info", return_value=None):
            self.window._show_svn_graph_diff()
            first = self.window.svn_diff_dialog
            self.window._show_svn_graph_diff()
            second = self.window.svn_diff_dialog
            self.flush_deletes()
            self.assertFalse(isValid(first))
            self.assertTrue(isValid(second))
            self.assertIs(second, self.window.svn_diff_dialog)
            self.assertEqual([second], self.window.findChildren(SvnGraphDiffDialog))
            second.reject()
            self.flush_deletes()

    def test_close_during_running_query_waits_for_process_exit_then_releases_objects(self):
        def start_query(runner, path):
            runner._info_path = Path(path)
            runner._run("info", ["-u", "-c", "import time; print('ready', flush=True); time.sleep(30)"], "test")
        with patch.object(SvnHistoryRunner, "query_info", start_query):
            self.window._show_svn_graph_diff()
        dialog = self.window.svn_diff_dialog
        runner = dialog.runner
        process = runner.process
        self.addCleanup(lambda: process.kill() if isValid(process) else None)
        self.wait_until(lambda: process.state() == QProcess.ProcessState.Running)
        stopped = []
        process.finished.connect(lambda *_args: stopped.append(True))
        dialog.reject()
        self.assertIsNone(self.window.svn_diff_dialog)
        if process.state() != QProcess.ProcessState.NotRunning:
            self.assertTrue(isValid(dialog))
            self.assertTrue(isValid(runner))
        self.wait_until(lambda: not isValid(dialog))
        self.assertTrue(stopped)
        self.assertFalse(isValid(runner))
        self.assertFalse(isValid(process))
        self.assertEqual([], self.window.findChildren(SvnHistoryRunner))
        QTest.qWait(1100)  # A queued cancellation timer must not access a deleted process.
        self.flush_deletes()

    @unittest.skipUnless(shutil.which("svn") and shutil.which("svnadmin"), "svn and svnadmin are not installed")
    def test_real_async_dialog_compares_commits_and_ignores_local_edits(self):
        root = Path(self.temp.name)
        repository, working_copy = root / "repository", root / "working-copy"
        svn, svnadmin = shutil.which("svn"), shutil.which("svnadmin")
        subprocess.run([svnadmin, "create", str(repository)], check=True, capture_output=True)
        subprocess.run([svn, "checkout", repository.as_uri(), str(working_copy)], check=True, capture_output=True)
        controller = EditorController()
        meta = controller.document.meta
        meta.ship_skin_id, meta.CharName, meta.author, meta.memo = 1, "test", "test", "test"
        controller.refresh_derived()
        path = working_copy / "graph.json"
        save_document(controller.schema, controller.document, path)
        subprocess.run([svn, "add", str(path)], check=True, capture_output=True)
        subprocess.run([svn, "commit", str(working_copy), "-m", "first"], check=True, capture_output=True)
        committed_uuid = controller.create_node("Comment", (300, 200))
        self.assertIsNotNone(committed_uuid)
        save_document(controller.schema, controller.document, path)
        subprocess.run([svn, "commit", str(working_copy), "-m", "second"], check=True, capture_output=True)
        local_uuid = controller.create_node("Comment", (500, 400))
        self.assertIsNotNone(local_uuid)
        path.write_text("deliberately invalid local JSON", encoding="utf-8")
        runner = SvnHistoryRunner(svn, self.window)
        dialog = SvnGraphDiffDialog(runner, controller.schema, path, self.window)
        runner.setParent(dialog)
        self.window.svn_diff_dialog = dialog
        dialog.finished.connect(lambda _result: self.window._clear_svn_diff_dialog(dialog))
        failures = []
        runner.failed.connect(failures.append)
        dialog.show()
        self.wait_until(lambda: bool(failures) or dialog.comparison.after is not None, timeout=10)
        self.assertFalse(failures)
        self.assertEqual((1, 2), dialog._pending_endpoints)
        before = {node.uuid for node in dialog.comparison.before.nodes}
        after = {node.uuid for node in dialog.comparison.after.nodes}
        self.assertNotIn(committed_uuid, before)
        self.assertIn(committed_uuid, after)
        self.assertNotIn(local_uuid, before | after)
        self.assertEqual("deliberately invalid local JSON", path.read_text(encoding="utf-8"))
        self.assertTrue(any(node.uuid == local_uuid for node in controller.document.nodes))
        dialog.reject()
        self.flush_deletes()
        self.assertFalse(isValid(dialog))
        self.assertFalse(isValid(runner))


if __name__ == "__main__":
    unittest.main()
