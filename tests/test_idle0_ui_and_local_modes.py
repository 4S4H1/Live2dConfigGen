import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("L2D_CONFIG_EDITOR_TEST_CLOSE_EVENT_POLICY", "discard")

from PyQt6.QtWidgets import QApplication

from l2d_config_editor.main_window import MainWindow
from l2d_config_editor.widgets import APPEARANCE_BUTTON_HIDDEN_NODE_TYPES, APPEARANCE_DISABLED_NODE_TYPES


class Idle0UiAndLocalModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _ready(window: MainWindow) -> None:
        meta = window.controller.document.meta
        meta.author = "tester"
        meta.ship_skin_id = 1
        meta.memo = "mode-test"
        meta.CharName = "测试"
        window.controller.refresh_derived()

    def test_idle0_is_output_only_and_has_no_appearance_editor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(temp_dir, prefer_saved_workspace=False)
            root = next(node for node in window.controller.document.nodes if node.type == "Idle0")
            item = window.canvas.node_items[root.uuid]

            self.assertEqual({}, root.fields)
            self.assertIsNone(item.input_pin_scene_pos())
            self.assertIsNotNone(item.output_pin_scene_pos())
            self.assertTrue(item.input_pin_rect().isEmpty())
            self.assertIn("Idle0", APPEARANCE_DISABLED_NODE_TYPES)
            self.assertIn("Idle0", APPEARANCE_BUTTON_HIDDEN_NODE_TYPES)
            self.assertIsNone(item.form._appearance_button)

            window._mark_saved_checkpoint(saved=True)
            window.close()

    def test_node_commit_mode_follows_its_own_card_or_detail_view(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(temp_dir, prefer_saved_workspace=False)
            self._ready(window)
            node_uuid = window.controller.create_node("TouchIdle", (200.0, 120.0))
            item = window.canvas.node_items[node_uuid]

            with patch.object(window.controller, "update_field") as update_field:
                item._commit_field("parameter", "card-value")
                self.assertEqual("simple", update_field.call_args.args[3])

                window.canvas.toggle_node_display_mode(node_uuid)
                item = window.canvas.node_items[node_uuid]
                item._commit_field("drag_direct", 2)
                self.assertEqual("advanced", update_field.call_args.args[3])

            window._mark_saved_checkpoint(saved=True)
            window.close()

    def test_removed_global_setting_no_longer_controls_editor_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(temp_dir, prefer_saved_workspace=False)
            window.settings.setValue("ui/global_mode", "advanced")
            window.controller.preferences.global_mode = "advanced"

            window._apply_saved_preferences()

            self.assertEqual("simple", window.controller.preferences.global_mode)
            window.settings.remove("ui/global_mode")
            window._mark_saved_checkpoint(saved=True)
            window.close()


if __name__ == "__main__":
    unittest.main()
