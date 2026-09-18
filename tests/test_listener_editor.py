"""Listener editing exercises the actual Qt scene and shared command history."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from l2d_config_editor.controller import EditorController
from l2d_config_editor.listener_editor import ListenerGraphDialog


class ListenerEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from unittest.mock import patch
        feature = patch("l2d_config_editor.features.LISTENER_EDITOR_ENABLED", True)
        feature.start()
        self.addCleanup(feature.stop)
        self.controller = EditorController()
        meta = self.controller.document.meta
        meta.author, meta.CharName, meta.ship_skin_id, meta.memo = "测试", "测试", 1, "测试"
        self.controller.refresh_derived()
        self.owner_uuid = self.controller.create_node("Listener", (0, 0))
        self.dialog = ListenerGraphDialog(self.controller, self.owner_uuid)
        self.dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.dialog.show()
        self.app.processEvents()
        self.controller.undo_stack.setClean()

    def tearDown(self):
        # A test may intentionally leave an invalid draft in an editor.
        self.dialog.field_editors = {}
        self.dialog.close()
        self.app.processEvents()

    def graph(self):
        return self.controller.get_node(self.owner_uuid).listener_graph

    def part(self, kind):
        return next(part for part in self.graph().nodes if part.kind == kind)

    def refresh(self):
        self.app.processEvents()
        self.dialog._refresh()

    def test_add_edit_delete_round_trip_with_shared_undo_stack(self):
        original = self.graph().to_payload()
        added = self.dialog.add_part("IdleRange", QPointF(1050, 70))
        self.assertIsNotNone(added)
        editor = self.dialog.field_editors["idle"][1]
        editor.setText("3")
        self.assertTrue(self.dialog.commit_pending_edits())
        self.assertEqual(3, self.part("IdleRange").fields["idle"])
        self.assertTrue(self.dialog.delete_selected())
        self.refresh()
        self.assertEqual(original, self.graph().to_payload())
        self.dialog.undo()
        self.assertEqual(3, self.part("IdleRange").fields["idle"])
        self.dialog.undo()
        self.assertEqual(0, self.part("IdleRange").fields["idle"])
        self.dialog.undo()
        self.assertEqual(original, self.graph().to_payload())
        self.assertTrue(self.controller.undo_stack.isClean())
        self.dialog.redo()
        self.assertEqual(4, len(self.graph().nodes))

    def test_drag_ports_creates_real_connection_and_undo_removes_it(self):
        source, target = self.part("ActionEvent"), self.part("AddValue")
        graph = self.graph().clone()
        graph.connections = [wire for wire in graph.connections if wire.from_uuid != source.uuid]
        self.controller.set_listener_graph(self.owner_uuid, graph, "测试准备")
        self.refresh()
        view = self.dialog.canvas
        start = view.mapFromScene(self.dialog.node_items[source.uuid].ports[(True, "event")].scenePos())
        end = view.mapFromScene(self.dialog.node_items[target.uuid].ports[(False, "event")].scenePos())
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(view.viewport(), end, delay=20)
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
        self.refresh()
        self.assertEqual(2, len(self.graph().connections))
        self.assertIn("listener_data", self.dialog.preview.toPlainText())
        self.dialog.undo()
        self.assertEqual(1, len(self.graph().connections))

    def test_drag_node_persists_and_undo_restores_position(self):
        part = self.part("ActionEvent")
        before = dict(part.ui_position)
        view = self.dialog.canvas
        item = self.dialog.node_items[part.uuid]
        start = view.mapFromScene(item.mapToScene(QPointF(50, 20)))
        end = start + view.mapFromScene(QPointF(100, 60)) - view.mapFromScene(QPointF())
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(view.viewport(), end, delay=20)
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
        self.refresh()
        after = self.part("ActionEvent").ui_position
        self.assertNotEqual(before, after)
        self.assertAlmostEqual(before["x"] + 100, after["x"], delta=2)
        self.dialog.undo()
        self.assertEqual(before, self.part("ActionEvent").ui_position)

    def test_invalid_number_blocks_commit_and_preserves_draft(self):
        part = self.part("AddValue")
        self.dialog.select_part(part.uuid)
        editor = self.dialog.field_editors["value"][1]
        editor.setText("-")
        self.assertFalse(self.dialog.commit_pending_edits())
        self.assertEqual(1, self.part("AddValue").fields["value"])
        self.assertEqual("-", editor.text())
        self.dialog.canvas.setFocus()
        self.assertTrue(self.dialog.is_busy())
        self.dialog.close()
        self.assertTrue(self.dialog.isVisible())
        editor.setText("-2.5")
        self.assertTrue(self.dialog.commit_pending_edits())
        self.assertEqual(-2.5, self.part("AddValue").fields["value"])

    def test_chinese_input_and_unrelated_refresh_preserve_editor_and_cursor(self):
        part = self.part("ValueState")
        self.dialog.select_part(part.uuid)
        editor = self.dialog.field_editors["parameter"][1]
        editor.setFocus()
        editor.setText("中文参数")
        editor.setCursorPosition(2)
        preedit = QInputMethodEvent("ceshi", [])
        QApplication.sendEvent(editor, preedit)
        self.assertTrue(self.dialog.is_busy())
        self.controller.nodeUpdated.emit(self.owner_uuid)
        self.refresh()
        self.assertIs(editor, self.dialog.field_editors["parameter"][1])
        self.assertEqual("中文参数", editor.text())
        self.assertEqual(2, editor.cursorPosition())
        commit = QInputMethodEvent()
        commit.setCommitString("测试")
        QApplication.sendEvent(editor, commit)
        self.assertTrue(self.dialog.commit_pending_edits())
        self.assertEqual("中文测试参数", self.part("ValueState").fields["parameter"])

    def test_undo_updates_clean_inspector_without_overwriting_unrelated_draft(self):
        part = self.part("ValueState")
        self.dialog.select_part(part.uuid)
        editor = self.dialog.field_editors["parameter"][1]
        editor.setText("changed")
        self.assertTrue(self.dialog.commit_pending_edits())
        self.refresh()
        self.dialog.undo()
        self.assertEqual("listener_value1", editor.text())
        editor.setText("尚未应用")
        graph = self.graph().clone()
        graph.nodes[0].ui_position["x"] += 20
        self.controller.set_listener_graph(self.owner_uuid, graph, "移动其他组件")
        self.refresh()
        self.assertEqual("尚未应用", editor.text())

    def test_port_types_duplicate_and_single_input_are_enforced(self):
        event, effect, state = self.part("ActionEvent"), self.part("AddValue"), self.part("ValueState")
        before = len(self.graph().connections)
        self.assertFalse(self.dialog.connect_parts(event.uuid, "event", state.uuid, "changes"))
        self.assertFalse(self.dialog.connect_parts(event.uuid, "event", effect.uuid, "event"))
        other = self.dialog.add_part("ActionEvent")
        self.assertFalse(self.dialog.connect_parts(other, "event", effect.uuid, "event"))
        self.assertEqual(before, len(self.graph().connections))

    def test_selected_wire_delete_and_undo_restore_configuration(self):
        self.dialog.wire_items[0].setSelected(True)
        self.assertTrue(self.dialog.delete_selected())
        self.refresh()
        self.assertEqual(1, len(self.graph().connections))
        self.assertNotIn("listener_data", self.dialog.preview.toPlainText())
        self.assertGreater(self.dialog.issues.count(), 0)
        self.dialog.undo()
        self.assertEqual(2, len(self.graph().connections))
        self.assertIn("listener_data", self.dialog.preview.toPlainText())

    def test_read_only_dialog_cannot_mutate_document(self):
        preview = ListenerGraphDialog(self.controller, self.owner_uuid, read_only=True)
        preview.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        before = self.graph().to_payload()
        self.assertIsNone(preview.add_part("ActionEvent"))
        preview.select_part(self.part("AddValue").uuid)
        self.assertFalse(preview.delete_selected())
        preview.undo()
        self.assertEqual(before, self.graph().to_payload())
        self.assertFalse(preview.field_editors["value"][1].isEnabled())
        preview.close()

    def test_document_switch_closes_without_committing_old_draft(self):
        self.dialog.select_part(self.part("ValueState").uuid)
        self.dialog.field_editors["parameter"][1].setText("旧文档草稿")
        from l2d_config_editor.logic import create_document
        self.controller.document = create_document(self.controller.schema)
        self.controller.documentLoaded.emit()
        self.assertFalse(self.dialog.isVisible())

    def test_host_form_button_and_generated_fields_are_protected(self):
        from l2d_config_editor.widgets import NodeFormWidget
        attached_uuid = self.controller.create_node("TouchIdle", (0, 0))
        self.controller.set_listener_graph(attached_uuid, self.graph().clone(), "测试附属蓝图")
        form = NodeFormWidget(self.controller.schema)
        form.set_node(self.controller.get_node(attached_uuid), "advanced")
        requested = []
        form.listenerEditRequested.connect(requested.append)
        form._listener_button.click()
        self.assertEqual([attached_uuid], requested)
        for key in ("listener_data", "parameter", "range", "start_value"):
            self.assertTrue(form._bindings[key].widget.isReadOnly(), key)
        changed = []
        form.fieldsCommitted.connect(changed.append)
        form._bindings["parameter"].widget.setText("不能覆盖生成值")
        form.commit_pending_edits()
        self.assertFalse(any("parameter" in values for values in changed))
        form.refresh()
        self.assertTrue(form._bindings["parameter"].widget.isReadOnly())
        form.close()

    def test_form_and_canvas_route_to_subgraph_and_listener_has_no_pins(self):
        from l2d_config_editor.canvas import NodeCanvasView
        canvas = NodeCanvasView(self.controller.schema, self.controller)
        canvas.resize(900, 700)
        canvas.show()
        self.app.processEvents()
        item = canvas.node_items[self.owner_uuid]
        self.assertIsNone(item.input_pin_scene_pos())
        self.assertIsNone(item.output_pin_scene_pos())
        requested = []
        canvas.listenerEditRequested.connect(requested.append)
        item.form._listener_button.click()
        self.assertEqual([self.owner_uuid], requested)
        canvas.centerOn(item)
        point = canvas.mapFromScene(item.mapToScene(QPointF(50, 15)))
        QTest.mouseDClick(canvas.viewport(), Qt.MouseButton.LeftButton, pos=point)
        self.app.processEvents()
        self.assertEqual([self.owner_uuid, self.owner_uuid], requested)
        canvas.close()

    def test_plan_double_click_opens_listener_subgraph(self):
        from l2d_config_editor.plan_canvas import PlanCanvasView
        canvas = PlanCanvasView(self.controller.schema, self.controller)
        canvas.resize(900, 700)
        canvas.show()
        self.app.processEvents()
        item = canvas.topic_items[self.owner_uuid]
        requested = []
        canvas.listenerEditRequested.connect(requested.append)
        canvas.centerOn(item)
        point = canvas.mapFromScene(item.sceneBoundingRect().center())
        QTest.mouseDClick(canvas.viewport(), Qt.MouseButton.LeftButton, pos=point)
        self.app.processEvents()
        self.assertEqual([self.owner_uuid], requested)
        canvas.close()

    def test_save_shortcut_commits_draft_before_emitting_save_request(self):
        self.dialog.select_part(self.part("ValueState").uuid)
        editor = self.dialog.field_editors["parameter"][1]
        editor.setFocus()
        editor.setText("保存中文参数")
        saved_values = []
        self.dialog.saveRequested.connect(lambda: saved_values.append(self.part("ValueState").fields["parameter"]))
        QTest.keyClick(editor, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(["保存中文参数"], saved_values)

    def test_owner_lock_change_disables_open_subgraph_editors(self):
        self.dialog.select_part(self.part("AddValue").uuid)
        self.controller.set_node_locked(self.owner_uuid, True)
        self.refresh()
        self.assertTrue(self.dialog.read_only)
        self.assertFalse(self.dialog.field_editors["value"][1].isEnabled())
        self.assertIsNone(self.dialog.add_part("ActionEvent"))
        self.controller.set_node_locked(self.owner_uuid, False)
        self.refresh()
        self.assertFalse(self.dialog.read_only)
        self.assertTrue(self.dialog.field_editors["value"][1].isEnabled())

    def test_undo_attachment_closes_editor_when_graph_is_removed(self):
        attached_uuid = self.controller.create_node("TouchIdle", (0, 0))
        self.controller.set_listener_graph(attached_uuid, self.graph().clone(), "测试附属蓝图")
        attached = ListenerGraphDialog(self.controller, attached_uuid)
        attached.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        attached.show()
        self.app.processEvents()
        attached.undo()
        self.assertIsNone(self.controller.get_node(attached_uuid).listener_graph)
        self.assertFalse(attached.isVisible())
        attached.close()


if __name__ == "__main__":
    unittest.main()
