"""The visible graph owns the toolbar, including plan sequence locking."""
import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("L2D_CONFIG_EDITOR_TEST_CLOSE_EVENT_POLICY", "discard")
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from l2d_config_editor.main_window import MainWindow


class GraphViewToolbarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.window = MainWindow(self.temp.name, prefer_saved_workspace=False)
        self.addCleanup(self.window.close)
        meta = self.window.controller.document.meta
        meta.ship_skin_id, meta.CharName, meta.author, meta.memo = 1, "test", "test", "test"
        self.window.controller.refresh_derived()
        self.window.show()
        self.app.processEvents()

    def assert_mode(self, mode):
        window = self.window
        self.assertEqual(mode == "formal", window.formal_view_button.isChecked())
        self.assertEqual(mode == "plan", window.plan_view_button.isChecked())
        self.assertTrue(window.formal_view_button.property("graphViewSwitch"))
        self.assertTrue(window.plan_view_button.property("graphViewSwitch"))
        for action in window._formal_toolbar_actions:
            self.assertEqual(mode == "formal", action.isVisible())
            if mode != "formal":
                self.assertFalse(action.defaultWidget().isVisible())
        for action in window._plan_toolbar_actions:
            self.assertEqual(mode == "plan", action.isVisible())
            if mode != "plan":
                self.assertFalse(action.defaultWidget().isVisible())
        history_action = next(action for action in window.top_toolbar.actions()
                              if getattr(action, "defaultWidget", lambda: None)() is window.history_button)
        self.assertTrue(history_action.isVisible())
        self.assertFalse(window.listener_button.isVisible())

    def test_toolbar_and_selected_view_match_initial_and_repeated_switches(self):
        self.assert_mode("formal")
        for width in (1800, 1500, 1700):
            QTest.mouseClick(self.window.plan_view_button, Qt.MouseButton.LeftButton)
            self.window.resize(width, 960)
            self.app.processEvents()
            self.assert_mode("plan")
            QTest.mouseClick(self.window.formal_view_button, Qt.MouseButton.LeftButton)
            self.app.processEvents()
            self.assert_mode("formal")

    def test_plan_fixed_button_materializes_and_locks_in_one_undo_step(self):
        controller = self.window.controller
        uuid = controller.create_plan_topic(controller.document.nodes[0].uuid, "摸头")
        self.window._switch_graph_view("plan")
        self.window.plan_canvas.select_node_uuids([uuid])
        self.app.processEvents()
        before = controller.undo_stack.index()
        self.assertTrue(self.window.sequence_lock_button.isEnabled())
        QTest.mouseClick(self.window.sequence_lock_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertEqual("TouchIdle", controller.get_node(uuid).type)
        self.assertTrue(controller.get_node(uuid).sequence_locked)
        self.assertGreater(controller.get_node(uuid).type_slot, 0)
        self.assertEqual(before + 1, controller.undo_stack.index())
        controller.undo_stack.undo()
        self.assertEqual("PlanPlaceholder", controller.get_node(uuid).type)
        self.assertFalse(controller.get_node(uuid).sequence_locked)

    def test_fixed_button_can_unlock_plan_nodes_and_cannot_mutate_in_formal_view(self):
        controller = self.window.controller
        uuid = controller.create_node("TouchDrag", (400, 100))
        self.window.canvas.select_node_uuids([uuid])
        before = controller.undo_stack.index()
        self.window._toggle_selected_sequence_lock()
        self.assertEqual(before, controller.undo_stack.index())
        self.window._switch_graph_view("plan")
        self.window.plan_canvas.select_node_uuids([uuid])
        self.app.processEvents()
        self.window._toggle_selected_sequence_lock()
        self.assertTrue(controller.get_node(uuid).sequence_locked)
        self.window._toggle_selected_sequence_lock()
        self.assertFalse(controller.get_node(uuid).sequence_locked)

    def test_locking_reordered_existing_topic_keeps_its_current_number(self):
        controller = self.window.controller
        root = controller.document.nodes[0].uuid
        first = controller.create_plan_topic(root, "摸头")
        second = controller.create_plan_topic(root, "拉袖子")
        controller.materialize_plan_topics()
        first_slot = controller.get_node(first).type_slot
        self.window._switch_graph_view("plan")
        controller.reorder_plan_topic(second, 0)
        self.window.plan_canvas.select_node_uuids([first])
        self.window._toggle_selected_sequence_lock()
        self.assertEqual(first_slot, controller.get_node(first).type_slot)
        self.assertTrue(controller.get_node(first).sequence_locked)
        self.window._switch_graph_view("formal")
        self.assertEqual(first_slot, controller.get_node(first).type_slot)

    def test_mixed_existing_and_draft_selection_reserves_existing_number(self):
        controller = self.window.controller
        root = controller.document.nodes[0].uuid
        first = controller.create_plan_topic(root, "摸头")
        controller.materialize_plan_topics()
        first_slot = controller.get_node(first).type_slot
        second = controller.create_plan_topic(root, "拉袖子")
        self.window._switch_graph_view("plan")
        controller.reorder_plan_topic(second, 0)
        self.window.plan_canvas.select_node_uuids([first, second])
        before = controller.undo_stack.index()
        self.window._toggle_selected_sequence_lock()
        self.assertEqual(first_slot, controller.get_node(first).type_slot)
        self.assertNotEqual(first_slot, controller.get_node(second).type_slot)
        self.assertTrue(controller.get_node(first).sequence_locked)
        self.assertTrue(controller.get_node(second).sequence_locked)
        self.assertEqual(before + 1, controller.undo_stack.index())
        controller.undo_stack.undo()
        self.assertEqual("PlanPlaceholder", controller.get_node(second).type)
        self.assertFalse(controller.get_node(first).sequence_locked)

    def test_unlocking_existing_topic_does_not_materialize_other_drafts(self):
        controller = self.window.controller
        root = controller.document.nodes[0].uuid
        first = controller.create_plan_topic(root, "摸头")
        controller.materialize_plan_topics()
        controller.set_nodes_sequence_locked([first], True)
        second = controller.create_plan_topic(root, "草稿")
        self.window._switch_graph_view("plan")
        self.window.plan_canvas.select_node_uuids([first])
        self.window._toggle_selected_sequence_lock()
        self.assertFalse(controller.get_node(first).sequence_locked)
        self.assertEqual("PlanPlaceholder", controller.get_node(second).type)


if __name__ == "__main__":
    unittest.main()
