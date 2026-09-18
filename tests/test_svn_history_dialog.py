"""The history UI compares repository revisions only, never the local editor."""
import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication
from l2d_config_editor.controller import EditorController
from l2d_config_editor.logic import export_document_dict
from l2d_config_editor.svn_diff_dialog import SvnGraphDiffDialog
from l2d_config_editor.svn_tools import SvnRevision


class HistoryRunner(QObject):
    infoReady = Signal(object)
    revisionsReady = Signal(object, bool)
    contentReady = Signal(int, bytes)
    failed = Signal(str)
    cancelled = Signal()
    busyChanged = Signal(bool)
    outputReceived = Signal(str)

    def __init__(self):
        super().__init__()
        self.requests = []
        self.info_paths = []
        self.cancel_count = 0

    def query_info(self, path):
        self.info_paths.append(path)

    def query_content(self, revision):
        self.requests.append(revision)

    def query_revisions(self, *, reset=True):
        pass

    def cancel(self):
        self.cancel_count += 1


class SvnHistoryDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "graph.json"
        self.path.write_text("local content deliberately is not JSON", encoding="utf-8")
        self.controller = EditorController()
        self.schema = self.controller.schema
        self.runner = HistoryRunner()
        self.dialog = SvnGraphDiffDialog(self.runner, self.schema, self.path)
        self.addCleanup(self.dialog.reject)

    def revisions(self, values, more=False):
        self.runner.revisionsReady.emit([SvnRevision(value, "author", "2026-09-19", f"revision {value}")
                                        for value in values], more)

    def content(self, revision, memo=None):
        payload = export_document_dict(self.schema, self.controller.document)
        payload["meta"]["memo"] = memo if memo is not None else f"repository r{revision}"
        self.runner.contentReady.emit(revision, json.dumps(payload).encode("utf-8"))

    def initial_comparison(self):
        self.revisions([25, 9, 2])
        self.content(9)
        self.content(25)

    def choose(self, left, right):
        self.dialog.left_combo.setCurrentIndex(self.dialog.left_combo.findData(left))
        self.dialog.right_combo.setCurrentIndex(self.dialog.right_combo.findData(right))
        self.dialog._start_compare()

    def test_default_compares_latest_file_commit_to_its_previous_file_commit(self):
        self.revisions([9, 25, 2])
        self.assertEqual(9, self.dialog.left_combo.currentData())
        self.assertEqual(25, self.dialog.right_combo.currentData())
        self.assertEqual([9], self.runner.requests)
        self.content(9)
        self.assertEqual([9, 25], self.runner.requests)
        self.content(25)
        self.assertEqual("repository r9", self.dialog.comparison.before.meta.memo)
        self.assertEqual("repository r25", self.dialog.comparison.after.meta.memo)
        for combo in (self.dialog.left_combo, self.dialog.right_combo):
            self.assertEqual([25, 9, 2], [combo.itemData(i) for i in range(combo.count())])
            self.assertFalse(any("当前" in combo.itemText(i) for i in range(combo.count())))
        self.assertEqual("local content deliberately is not JSON", self.path.read_text(encoding="utf-8"))

    def test_any_two_committed_versions_can_be_compared_in_either_order(self):
        self.initial_comparison()
        self.choose(25, 2)
        self.assertEqual([9, 25, 2], self.runner.requests)
        self.content(2)
        self.assertEqual("repository r25", self.dialog.comparison.before.meta.memo)
        self.assertEqual("repository r2", self.dialog.comparison.after.meta.memo)
        self.choose(2, 9)
        self.assertEqual([9, 25, 2], self.runner.requests)
        self.assertEqual("repository r2", self.dialog.comparison.before.meta.memo)
        self.assertEqual("repository r9", self.dialog.comparison.after.meta.memo)

    def test_identical_committed_versions_do_not_switch_endpoints(self):
        self.revisions([25, 9, 2])
        self.content(9, "same")
        self.content(25, "same")
        self.assertTrue(self.dialog.comparison.diff.is_empty)
        self.assertEqual((9, 25), self.dialog._pending_endpoints)
        self.assertEqual([9, 25], self.runner.requests)

    def test_zero_or_one_revision_never_falls_back_to_local_content(self):
        self.revisions([])
        self.assertFalse(self.dialog.compare_button.isEnabled())
        self.revisions([25])
        self.assertFalse(self.dialog.compare_button.isEnabled())
        self.assertIn("至少两次", self.dialog.summary_label.text())
        self.assertEqual([], self.runner.requests)
        self.assertIsNone(self.dialog.comparison.before)
        self.dialog._start_compare()
        self.assertEqual([], self.runner.requests)

    def test_loading_more_revisions_preserves_selected_endpoints(self):
        self.initial_comparison()
        self.choose(25, 9)
        self.revisions([2, 1], more=True)
        self.assertEqual((25, 9), (self.dialog.left_combo.currentData(), self.dialog.right_combo.currentData()))
        self.assertEqual([25, 9, 2, 1], [self.dialog.left_combo.itemData(i)
                                      for i in range(self.dialog.left_combo.count())])
        self.assertEqual([9, 25], self.runner.requests)

    def test_second_page_can_supply_the_missing_previous_revision(self):
        self.revisions([25], more=True)
        self.assertEqual([], self.runner.requests)
        self.revisions([9])
        self.assertEqual([9], self.runner.requests)
        self.content(9)
        self.content(25)
        self.assertEqual((9, 25), self.dialog._pending_endpoints)

    def test_cache_keeps_requested_cached_endpoint_when_loading_another(self):
        self.initial_comparison()
        cached = self.dialog._documents[9]
        self.dialog._documents.clear()
        for revision in range(1, self.dialog.CACHE_LIMIT + 1):
            self.dialog._documents[revision] = cached
        self.revisions([100, *range(1, self.dialog.CACHE_LIMIT + 1)])
        self.choose(1, 100)
        self.content(100)
        self.assertIn(1, self.dialog._documents)
        self.assertIn(100, self.dialog._documents)
        self.assertEqual(self.dialog.CACHE_LIMIT, len(self.dialog._documents))

    def test_malformed_revision_does_not_substitute_local_or_other_revision(self):
        self.revisions([25, 9])
        self.runner.contentReady.emit(9, b"not-json")
        self.assertIn("不可比较", self.dialog.summary_label.text())
        self.assertIsNone(self.dialog.comparison.before)
        self.assertIsNone(self.dialog._pending_endpoints)
        self.assertEqual([9], self.runner.requests)

    def test_cancel_discards_pending_revision_and_late_content(self):
        self.revisions([25, 9])
        self.runner.cancelled.emit()
        self.content(9)
        self.assertIsNone(self.dialog._pending_endpoints)
        self.assertEqual([], self.dialog._pending_revisions)
        self.assertIsNone(self.dialog.comparison.before)
        self.assertEqual([9], self.runner.requests)

    def test_non_numeric_endpoint_cannot_start_a_comparison(self):
        self.initial_comparison()
        self.dialog.left_combo.addItem("injected local", "current")
        self.dialog.left_combo.setCurrentIndex(self.dialog.left_combo.count() - 1)
        self.dialog._start_compare()
        self.assertEqual([9, 25], self.runner.requests)


if __name__ == "__main__":
    unittest.main()
