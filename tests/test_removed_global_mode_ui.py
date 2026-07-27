import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("L2D_CONFIG_EDITOR_TEST_CLOSE_EVENT_POLICY", "discard")

from PySide6.QtWidgets import QApplication, QRadioButton

from l2d_config_editor.main_window import MainWindow


class RemovedGlobalModeUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_global_simple_and_advanced_entries_are_not_exposed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(temp_dir, prefer_saved_workspace=False)
            radio_labels = {button.text() for button in window.findChildren(QRadioButton)}
            action_labels = {action.text() for action in window.findChildren(type(window.save_action))}

            self.assertNotIn("简易", radio_labels)
            self.assertNotIn("高级", radio_labels)
            self.assertNotIn("简易模式", action_labels)
            self.assertNotIn("高级模式", action_labels)
            self.assertNotIn("编辑模式", {action.text() for action in window.menuBar().actions()})
            self.assertFalse(hasattr(window, "simple_mode_action"))
            self.assertFalse(hasattr(window, "advanced_mode_action"))

            window._mark_saved_checkpoint(saved=True)
            window.close()


if __name__ == "__main__":
    unittest.main()
