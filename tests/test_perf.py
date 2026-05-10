import os
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("L2D_CONFIG_EDITOR_TEST_CLOSE_EVENT_POLICY", "discard")

from PyQt6.QtWidgets import QApplication

from l2d_config_editor.main_window import MainWindow
from l2d_config_editor.perf_tools import PerformanceRecorder, PerformanceScenarioRunner


class PerformanceRecorderTests(unittest.TestCase):
    def test_summary_statistics_and_session_filtering(self) -> None:
        recorder = PerformanceRecorder(max_events=32)
        recorder.set_enabled(True)
        with recorder.session("unit-test", {"kind": "summary"}) as session_id:
            with recorder.measure("stage.alpha", "unit"):
                pass
            with recorder.measure("stage.alpha", "unit"):
                pass
            with recorder.measure("stage.beta", "unit"):
                pass
        summary = recorder.summarize(recorder.events_for_session(session_id))
        self.assertEqual({row.name for row in summary}, {"stage.alpha", "stage.beta"})
        alpha_row = next(row for row in summary if row.name == "stage.alpha")
        self.assertEqual(alpha_row.count, 2)
        self.assertGreaterEqual(alpha_row.max_ms, alpha_row.min_ms)
        self.assertGreaterEqual(alpha_row.p95_ms, alpha_row.min_ms)


class PerformanceScenarioRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_runner_produces_benchmark_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(temp_dir, prefer_saved_workspace=False)
            recorder = PerformanceRecorder(max_events=256)
            recorder.set_enabled(True)
            runner = PerformanceScenarioRunner(window, recorder)
            session_id, results = runner.run(iterations=1)
            self.assertTrue(session_id)
            self.assertGreaterEqual(len(results), 4)
            self.assertTrue(any(result.name == "benchmark.refresh_derived" for result in results))
            session_events = recorder.events_for_session(session_id)
            self.assertTrue(session_events)
            window.close()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
