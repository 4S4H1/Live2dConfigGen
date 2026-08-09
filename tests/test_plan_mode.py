from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from l2d_config_editor.canvas import NodeCanvasView
from l2d_config_editor.controller import EditorController
from l2d_config_editor.logic import (
    document_to_csv_rows,
    export_document_dict,
    get_default_schema,
    load_document,
    parameter_table_id,
    save_document,
)
from l2d_config_editor.main_window import MainWindow
from l2d_config_editor.models import (
    ConnectionRecord,
    NodeRecord,
    PlanLayout,
)
from l2d_config_editor.plan import (
    PLAN_UNCONNECTED_UUID,
    normalize_plan_layout,
    plan_primary_edges,
    plan_reference_edges,
)
from l2d_config_editor.plan_canvas import PlanCanvasView


def make_ready_controller() -> EditorController:
    controller = EditorController()
    controller.document.meta.ship_skin_id = 101
    controller.document.meta.memo = "asset/path"
    controller.document.meta.CharName = "测试角色"
    controller.refresh_derived()
    return controller


class PlanModelTests(unittest.TestCase):
    def test_v4_roundtrip_keeps_plan_metadata_and_formal_coordinates_separate(self) -> None:
        controller = make_ready_controller()
        root_uuid = controller.document.nodes[0].uuid
        root_position = dict(controller.document.nodes[0].ui_position)
        branch_uuid = controller.create_plan_topic(root_uuid, "分支")
        child_uuid = controller.create_plan_topic(branch_uuid, "子主题")
        controller.set_plan_title(child_uuid, "独立计划标题")
        controller.set_plan_collapsed(branch_uuid, True)
        controller.set_plan_branch_color(branch_uuid, "#123ABC")

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "plan-v4.json"
            save_document(controller.schema, controller.document, path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            loaded = load_document(controller.schema, path)

        self.assertEqual(5, payload["format_version"])
        self.assertIn("plan_layout", payload)
        loaded_topics = {
            topic.node_uuid: topic for topic in loaded.plan_layout.topics
        }
        self.assertEqual("独立计划标题", loaded_topics[child_uuid].plan_title)
        self.assertTrue(loaded_topics[branch_uuid].collapsed)
        self.assertEqual("#123ABC", loaded_topics[branch_uuid].branch_color)
        self.assertEqual("#123ABC", loaded_topics[child_uuid].branch_color)
        self.assertEqual(
            root_position,
            next(node for node in loaded.nodes if node.uuid == root_uuid).ui_position,
        )
        child = next(node for node in loaded.nodes if node.uuid == child_uuid)
        self.assertEqual("PlanPlaceholder", child.type)
        self.assertEqual("独立计划标题", child.fields["plan_source_title"])
        self.assertEqual([], document_to_csv_rows(controller.schema, loaded))

    def test_v1_through_v3_deterministically_migrate_forest_cycle_and_multi_parent(self) -> None:
        controller = make_ready_controller()
        root_uuid = controller.document.nodes[0].uuid
        first_uuid = controller.create_plan_topic(root_uuid, "一")
        second_uuid = controller.create_plan_topic(root_uuid, "二")
        child_uuid = controller.create_plan_topic(first_uuid, "多父")
        orphan_uuid = controller.create_plan_topic(None, "孤立")
        controller.add_connection(second_uuid, child_uuid)
        controller.add_connection(child_uuid, first_uuid)
        payload = export_document_dict(controller.schema, controller.document)
        payload.pop("plan_layout")

        expected = None
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "legacy.json"
            for version in (1, 2, 3):
                payload["format_version"] = version
                path.write_text(json.dumps(payload), encoding="utf-8")
                loaded = load_document(controller.schema, path)
                snapshot = [
                    (
                        topic.node_uuid,
                        topic.parent_uuid,
                        topic.order,
                        topic.branch_color,
                    )
                    for topic in loaded.plan_layout.topics
                ]
                if expected is None:
                    expected = snapshot
                self.assertEqual(expected, snapshot)
                primary = plan_primary_edges(loaded)
                self.assertEqual(len(loaded.nodes) - 2, len(primary))
                self.assertIsNone(
                    next(
                        topic.parent_uuid
                        for topic in loaded.plan_layout.topics
                        if topic.node_uuid == orphan_uuid
                    )
                )
                references = {
                    (edge.from_uuid, edge.to_uuid)
                    for edge in plan_reference_edges(loaded)
                }
                self.assertIn((second_uuid, child_uuid), references)
                self.assertIn((child_uuid, first_uuid), references)

    def test_v4_without_plan_layout_is_rejected(self) -> None:
        schema = get_default_schema()
        controller = make_ready_controller()
        payload = export_document_dict(schema, controller.document)
        payload.pop("plan_layout")
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "invalid-v4.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "plan_layout"):
                load_document(schema, path)

    def test_reparent_replaces_only_primary_edge_and_is_one_undo_step(self) -> None:
        controller = make_ready_controller()
        root_uuid = controller.document.nodes[0].uuid
        first_uuid = controller.create_plan_topic(root_uuid, "一")
        second_uuid = controller.create_plan_topic(root_uuid, "二")
        child_uuid = controller.create_plan_topic(first_uuid, "子")
        controller.add_connection(second_uuid, child_uuid)
        before_index = controller.undo_stack.index()

        self.assertTrue(
            controller.reparent_plan_topic(child_uuid, second_uuid)
        )

        self.assertEqual(before_index + 1, controller.undo_stack.index())
        pairs = {
            (connection.from_uuid, connection.to_uuid)
            for connection in controller.document.connections
        }
        self.assertNotIn((first_uuid, child_uuid), pairs)
        self.assertIn((second_uuid, child_uuid), pairs)
        self.assertEqual(
            second_uuid,
            controller.plan_topic(child_uuid).parent_uuid,
        )

        controller.undo_stack.undo()
        pairs = {
            (connection.from_uuid, connection.to_uuid)
            for connection in controller.document.connections
        }
        self.assertIn((first_uuid, child_uuid), pairs)
        self.assertIn((second_uuid, child_uuid), pairs)
        self.assertEqual(
            first_uuid,
            controller.plan_topic(child_uuid).parent_uuid,
        )

    def test_subtree_and_regular_delete_restore_all_plan_metadata(self) -> None:
        controller = make_ready_controller()
        root_uuid = controller.document.nodes[0].uuid
        parent_uuid = controller.create_plan_topic(root_uuid, "父")
        child_uuid = controller.create_plan_topic(parent_uuid, "子")
        controller.set_plan_title(child_uuid, "保留的标题")
        controller.set_plan_collapsed(parent_uuid, True)
        before_index = controller.undo_stack.index()

        self.assertTrue(controller.delete_plan_subtree(parent_uuid))
        self.assertEqual(before_index + 1, controller.undo_stack.index())
        self.assertIsNone(controller.get_node(parent_uuid))
        self.assertIsNone(controller.get_node(child_uuid))

        controller.undo_stack.undo()
        self.assertEqual("保留的标题", controller.plan_topic(child_uuid).plan_title)
        self.assertTrue(controller.plan_topic(parent_uuid).collapsed)
        self.assertEqual(
            parent_uuid,
            controller.plan_topic(child_uuid).parent_uuid,
        )

        controller.remove_nodes([child_uuid])
        self.assertIsNone(controller.get_node(child_uuid))
        controller.undo_stack.undo()
        self.assertEqual("保留的标题", controller.plan_topic(child_uuid).plan_title)
        self.assertEqual(
            parent_uuid,
            controller.plan_topic(child_uuid).parent_uuid,
        )

    def test_formal_primary_edge_remove_and_undo_restore_primary_choice(self) -> None:
        controller = make_ready_controller()
        root_uuid = controller.document.nodes[0].uuid
        first_uuid = controller.create_plan_topic(root_uuid, "一")
        second_uuid = controller.create_plan_topic(root_uuid, "二")
        child_uuid = controller.create_plan_topic(first_uuid, "子")
        controller.add_connection(second_uuid, child_uuid)

        controller.remove_connection(first_uuid, child_uuid)
        self.assertEqual(
            second_uuid,
            controller.plan_topic(child_uuid).parent_uuid,
        )
        controller.undo_stack.undo()
        self.assertEqual(
            first_uuid,
            controller.plan_topic(child_uuid).parent_uuid,
        )

    def test_formal_edge_to_unconnected_topic_becomes_primary_and_undo_restores_forest(self) -> None:
        controller = make_ready_controller()
        root_uuid = controller.document.nodes[0].uuid
        orphan_uuid = controller.create_plan_topic(None, "孤立")
        self.assertIsNone(controller.plan_topic(orphan_uuid).parent_uuid)

        controller.add_connection(root_uuid, orphan_uuid)
        self.assertEqual(
            root_uuid,
            controller.plan_topic(orphan_uuid).parent_uuid,
        )
        controller.undo_stack.undo()
        self.assertIsNone(controller.plan_topic(orphan_uuid).parent_uuid)
        controller.undo_stack.redo()
        self.assertEqual(
            root_uuid,
            controller.plan_topic(orphan_uuid).parent_uuid,
        )

    def test_reorder_promote_and_idle0_protection(self) -> None:
        controller = make_ready_controller()
        root_uuid = controller.document.nodes[0].uuid
        first_uuid = controller.create_plan_topic(root_uuid, "一")
        second_uuid = controller.create_plan_topic(root_uuid, "二")
        child_uuid = controller.create_plan_topic(first_uuid, "子")

        before_index = controller.undo_stack.index()
        self.assertTrue(controller.reorder_plan_topic(second_uuid, 0))
        self.assertEqual(before_index + 1, controller.undo_stack.index())
        self.assertEqual(
            [second_uuid, first_uuid],
            controller.plan_children(root_uuid),
        )
        controller.undo_stack.undo()
        self.assertEqual(
            [first_uuid, second_uuid],
            controller.plan_children(root_uuid),
        )

        self.assertTrue(controller.promote_plan_topic(child_uuid))
        self.assertEqual(root_uuid, controller.plan_topic(child_uuid).parent_uuid)
        controller.undo_stack.undo()
        self.assertEqual(first_uuid, controller.plan_topic(child_uuid).parent_uuid)

        self.assertFalse(controller.reparent_plan_topic(root_uuid, first_uuid))
        self.assertFalse(controller.delete_plan_subtree(root_uuid))
        self.assertIsNotNone(controller.get_node(root_uuid))

    def test_disconnected_dag_prefers_zero_indegree_root(self) -> None:
        controller = make_ready_controller()
        child_uuid = controller.create_node("Comment", (100.0, 100.0))
        parent_uuid = controller.create_node("Comment", (200.0, 100.0))
        self.assertIsNotNone(child_uuid)
        self.assertIsNotNone(parent_uuid)
        controller.document.connections.append(
            ConnectionRecord(
                from_uuid=parent_uuid,
                to_uuid=child_uuid,
            )
        )
        controller.document.plan_layout = PlanLayout()

        controller.ensure_plan_layout()

        self.assertIsNone(controller.plan_topic(parent_uuid).parent_uuid)
        self.assertEqual(
            parent_uuid,
            controller.plan_topic(child_uuid).parent_uuid,
        )

    def test_formal_card_title_is_shared_with_the_plan_topic(self) -> None:
        controller = make_ready_controller()
        node_uuid = controller.create_node("TouchIdle", (100.0, 100.0))
        node = controller.get_node(node_uuid)
        self.assertIsNotNone(node)
        node.fields["draw_able_name"] = "CustomDraw"
        node.fields["tips"] = "说明"
        controller.ensure_plan_layout()

        self.assertEqual(
            "说明",
            controller.plan_title(node_uuid),
        )

    def test_copy_paste_keeps_plan_metadata_and_is_one_undo_step(self) -> None:
        controller = make_ready_controller()
        root_uuid = controller.document.nodes[0].uuid
        source_uuid = controller.create_plan_topic(root_uuid, "正式备注")
        controller.set_plan_title(source_uuid, "计划标题")
        controller.set_plan_collapsed(source_uuid, True)
        controller.set_plan_branch_color(source_uuid, "#123ABC")
        payload = controller.serialize_selection([source_uuid])
        self.assertIsNotNone(payload)
        before_index = controller.undo_stack.index()

        pasted = controller.paste_payload(payload, (600.0, 240.0))

        self.assertEqual(before_index + 1, controller.undo_stack.index())
        self.assertEqual(1, len(pasted))
        pasted_uuid = pasted[0]
        source = controller.get_node(source_uuid)
        clone = controller.get_node(pasted_uuid)
        self.assertEqual(source.type, clone.type)
        self.assertEqual(source.type, "PlanPlaceholder")
        self.assertEqual(source.fields, clone.fields)
        topic = controller.plan_topic(pasted_uuid)
        self.assertEqual(root_uuid, topic.parent_uuid)
        self.assertEqual("计划标题", topic.plan_title)
        self.assertTrue(topic.collapsed)
        self.assertEqual("#123ABC", topic.branch_color)
        self.assertIn(
            (root_uuid, pasted_uuid),
            {
                (connection.from_uuid, connection.to_uuid)
                for connection in controller.document.connections
            },
        )
        controller.undo_stack.undo()
        self.assertIsNone(controller.get_node(pasted_uuid))

    def test_plan_view_is_persisted_dirty_and_topic_undo_preserves_it(self) -> None:
        controller = make_ready_controller()
        root_uuid = controller.document.nodes[0].uuid
        before_index = controller.undo_stack.index()
        self.assertTrue(controller.set_plan_view_state(1.6, 120.0, -45.0))
        self.assertEqual(before_index + 1, controller.undo_stack.index())
        child_uuid = controller.create_plan_topic(root_uuid, "主题")
        self.assertIsNotNone(child_uuid)

        # Simulate a viewport move after the topic command.  Undoing topic
        # content must not rewind the independently stored current viewport.
        controller._set_plan_view_state(2.0, 300.0, 80.0)
        controller.undo_stack.undo()
        state = controller.document.plan_layout.view
        self.assertEqual((2.0, 300.0, 80.0), (
            state.scale,
            state.offset_x,
            state.offset_y,
        ))
        self.assertIsNone(controller.get_node(child_uuid))

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "plan-view.json"
            save_document(controller.schema, controller.document, path)
            loaded = load_document(controller.schema, path)
        loaded_state = loaded.plan_layout.view
        self.assertEqual((2.0, 300.0, 80.0), (
            loaded_state.scale,
            loaded_state.offset_x,
            loaded_state.offset_y,
        ))


class PlanCanvasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_view_renders_virtual_forest_and_dashed_reference_without_moving_nodes(self) -> None:
        controller = make_ready_controller()
        root_uuid = controller.document.nodes[0].uuid
        branch_uuid = controller.create_plan_topic(root_uuid, "分支")
        child_uuid = controller.create_plan_topic(branch_uuid, "子")
        orphan_uuid = controller.create_plan_topic(None, "孤立")
        controller.add_connection(root_uuid, child_uuid)
        positions_before = {
            node.uuid: dict(node.ui_position)
            for node in controller.document.nodes
        }

        view = PlanCanvasView(controller.schema, controller)
        view.resize(900, 600)
        view.show()
        self.app.processEvents()

        self.assertIn(PLAN_UNCONNECTED_UUID, view.topic_items)
        self.assertIn(orphan_uuid, view.topic_items)
        self.assertEqual(1, len(view.reference_items))
        view.reset_view_layout()
        view.focus_on_node(child_uuid, target_scale=0.8, emphasize=True)
        self.app.processEvents()
        self.assertEqual([child_uuid], view.selected_node_uuids())
        self.assertEqual(
            positions_before,
            {
                node.uuid: dict(node.ui_position)
                for node in controller.document.nodes
            },
        )
        controller.set_plan_collapsed(branch_uuid, True)
        self.app.processEvents()
        self.assertNotIn(child_uuid, view.topic_items)
        view.focus_on_node(child_uuid)
        self.assertIn(child_uuid, view.topic_items)
        self.assertTrue(controller.plan_topic(branch_uuid).collapsed)
        view.close()

    def test_standard_keyboard_create_collapse_and_promote(self) -> None:
        controller = make_ready_controller()
        root_uuid = controller.document.nodes[0].uuid
        branch_uuid = controller.create_plan_topic(root_uuid, "分支")
        view = PlanCanvasView(controller.schema, controller)
        view.resize(800, 500)
        view.show()
        view.focus_on_node(branch_uuid)
        self.app.processEvents()

        QTest.keyClick(view, Qt.Key.Key_Tab)
        self.app.processEvents()
        created_child = controller.plan_children(branch_uuid)[0]
        view.focus_on_node(created_child)
        QTest.keyClick(view, Qt.Key.Key_Space)
        self.assertTrue(controller.plan_topic(created_child).collapsed)

        QTest.keyClick(
            view,
            Qt.Key.Key_Backtab,
            Qt.KeyboardModifier.ShiftModifier,
        )
        self.app.processEvents()
        self.assertEqual(
            root_uuid,
            controller.plan_topic(created_child).parent_uuid,
        )
        view.focus_on_node(created_child)
        before_nodes = len(controller.document.nodes)
        QTest.keyClick(view, Qt.Key.Key_Return)
        self.app.processEvents()
        self.assertEqual(before_nodes + 1, len(controller.document.nodes))
        view.close()

    def test_deep_chain_uses_one_normalization_and_iterative_layout(self) -> None:
        controller = make_ready_controller()
        root_uuid = controller.document.nodes[0].uuid
        previous_uuid = root_uuid
        for index in range(1100):
            node_uuid = f"deep-topic-{index}"
            controller.document.nodes.append(
                NodeRecord(
                    uuid=node_uuid,
                    type="Comment",
                    fields={"content": f"主题 {index}"},
                    ui_position={"x": 0.0, "y": 0.0},
                )
            )
            controller.document.connections.append(
                ConnectionRecord(
                    from_uuid=previous_uuid,
                    to_uuid=node_uuid,
                )
            )
            previous_uuid = node_uuid
        controller.document.connections.append(
            ConnectionRecord(
                from_uuid=root_uuid,
                to_uuid=previous_uuid,
            )
        )
        controller.document.plan_layout = PlanLayout()

        with patch(
            "l2d_config_editor.controller.normalize_plan_layout",
            wraps=normalize_plan_layout,
        ) as normalize_spy:
            view = PlanCanvasView(controller.schema, controller)

        self.assertEqual(1, normalize_spy.call_count)
        self.assertEqual(1101, len(view.topic_items))
        self.assertIn(previous_uuid, view.topic_items)
        view.close()

    def test_tab_expands_collapsed_parent_in_same_undo_transaction(self) -> None:
        controller = make_ready_controller()
        root_uuid = controller.document.nodes[0].uuid
        parent_uuid = controller.create_plan_topic(root_uuid, "父主题")
        controller.set_plan_collapsed(parent_uuid, True)
        view = PlanCanvasView(controller.schema, controller)
        view.resize(800, 500)
        view.show()
        view.focus_on_node(parent_uuid)
        self.app.processEvents()
        before_index = controller.undo_stack.index()

        QTest.keyClick(view, Qt.Key.Key_Tab)
        self.app.processEvents()

        self.assertEqual(before_index + 1, controller.undo_stack.index())
        self.assertFalse(controller.plan_topic(parent_uuid).collapsed)
        children = controller.plan_children(parent_uuid)
        self.assertEqual(1, len(children))
        child_uuid = children[0]
        controller.undo_stack.undo()
        self.assertTrue(controller.plan_topic(parent_uuid).collapsed)
        self.assertIsNone(controller.get_node(child_uuid))
        view.close()

    def test_root_enter_and_unconnected_shift_tab_have_valid_parents(self) -> None:
        controller = make_ready_controller()
        root_uuid = controller.document.nodes[0].uuid
        orphan_uuid = controller.create_plan_topic(None, "未连接")
        view = PlanCanvasView(controller.schema, controller)
        view.resize(800, 500)
        view.show()
        view.focus_on_node(root_uuid)
        self.app.processEvents()

        before_nodes = {node.uuid for node in controller.document.nodes}
        QTest.keyClick(view, Qt.Key.Key_Return)
        self.app.processEvents()
        created = next(
            node.uuid
            for node in controller.document.nodes
            if node.uuid not in before_nodes
        )
        self.assertEqual(
            root_uuid,
            controller.plan_topic(created).parent_uuid,
        )

        view.focus_on_node(orphan_uuid)
        QTest.keyClick(
            view,
            Qt.Key.Key_Backtab,
            Qt.KeyboardModifier.ShiftModifier,
        )
        self.app.processEvents()
        self.assertEqual(
            root_uuid,
            controller.plan_topic(orphan_uuid).parent_uuid,
        )
        view.close()

    def test_duplicate_keeps_node_and_plan_metadata(self) -> None:
        controller = make_ready_controller()
        root_uuid = controller.document.nodes[0].uuid
        source_uuid = controller.create_plan_topic(root_uuid, "内容")
        controller.set_plan_title(source_uuid, "独立标题")
        controller.set_plan_collapsed(source_uuid, True)
        controller.set_plan_branch_color(source_uuid, "#654321")
        view = PlanCanvasView(controller.schema, controller)
        view.select_node_uuids([source_uuid])

        duplicate_uuid = view.duplicate_selected_as_sibling()

        self.assertIsNotNone(duplicate_uuid)
        self.assertEqual(
            controller.get_node(source_uuid).fields,
            controller.get_node(duplicate_uuid).fields,
        )
        duplicate_topic = controller.plan_topic(duplicate_uuid)
        self.assertEqual(root_uuid, duplicate_topic.parent_uuid)
        self.assertEqual("独立标题", duplicate_topic.plan_title)
        self.assertTrue(duplicate_topic.collapsed)
        self.assertEqual("#654321", duplicate_topic.branch_color)
        view.close()


class MainWindowPlanIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_switching_views_keeps_multi_selection_and_wheel_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(temp_dir, prefer_saved_workspace=False)
            window.controller.document.meta.ship_skin_id = 101
            window.controller.document.meta.memo = "asset/path"
            window.controller.document.meta.CharName = "测试角色"
            window.controller.refresh_derived()
            root_uuid = window.controller.document.nodes[0].uuid
            first_uuid = window.controller.create_plan_topic(root_uuid, "一")
            second_uuid = window.controller.create_plan_topic(root_uuid, "二")
            self.app.processEvents()
            window.canvas.select_node_uuids([first_uuid, second_uuid])

            window._switch_graph_view("plan")
            self.app.processEvents()
            self.assertEqual(
                {first_uuid, second_uuid},
                set(window.plan_canvas.selected_node_uuids()),
            )

            window._switch_graph_view("formal")
            self.app.processEvents()
            self.assertEqual(
                {first_uuid, second_uuid},
                set(window.canvas.selected_node_uuids()),
            )

            appearance = {
                "theme_body_color": "#102030",
                "theme_border_color": "#405060",
                "theme_text_color": "#F0E0D0",
            }
            window.controller.update_fields(first_uuid, appearance, "advanced")
            expected_appearance = {
                key: window.controller.get_node(first_uuid).fields[key]
                for key in appearance
            }
            window._switch_graph_view("plan")
            self.app.processEvents()
            window._switch_graph_view("formal")
            self.app.processEvents()
            self.assertEqual(
                expected_appearance,
                {
                    key: window.controller.get_node(first_uuid).fields[key]
                    for key in appearance
                },
            )

            window._apply_wheel_settings(
                {
                    "zoom_modifier": "alt",
                    "horizontal_modifier": "shift",
                }
            )
            self.assertEqual("alt", window.canvas.zoom_wheel_modifier)
            self.assertEqual("alt", window.plan_canvas.zoom_wheel_modifier)
            self.assertEqual(
                "shift",
                window.canvas.horizontal_wheel_modifier,
            )
            self.assertEqual(
                "shift",
                window.plan_canvas.horizontal_wheel_modifier,
            )
            window.deleteLater()
            self.app.processEvents()


class DirectPenGestureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_default_ctrl_node_click_selects_but_threshold_drag_draws(self) -> None:
        controller = EditorController()
        canvas = NodeCanvasView(controller.schema, controller)
        canvas.resize(1000, 700)
        canvas.show()
        self.app.processEvents()
        root_uuid = controller.document.nodes[0].uuid
        root_item = canvas.node_items[root_uuid]
        canvas.centerOn(root_item)
        self.app.processEvents()
        start = canvas.mapFromScene(root_item.sceneBoundingRect().center())

        QTest.mousePress(
            canvas.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ControlModifier,
            start,
        )
        QTest.mouseMove(canvas.viewport(), start + QPoint(2, 2), delay=10)
        QTest.mouseRelease(
            canvas.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ControlModifier,
            start + QPoint(2, 2),
        )
        self.app.processEvents()
        self.assertEqual([root_uuid], canvas.selected_node_uuids())
        self.assertEqual([], controller.document.canvas_strokes)

        QTest.mousePress(
            canvas.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ControlModifier,
            start,
        )
        QTest.mouseMove(canvas.viewport(), start + QPoint(10, 8), delay=10)
        QTest.mouseMove(canvas.viewport(), start + QPoint(40, 24), delay=10)
        QTest.mouseRelease(
            canvas.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ControlModifier,
            start + QPoint(55, 32),
        )
        self.app.processEvents()
        self.assertEqual(1, len(controller.document.canvas_strokes))
        self.assertGreaterEqual(len(controller.document.canvas_strokes[0].points), 3)
        canvas.close()

    def test_plan_canvas_draws_and_sweep_erases_only_plan_strokes(self) -> None:
        controller = make_ready_controller()
        canvas = PlanCanvasView(controller.schema, controller)
        canvas.resize(1000, 700)
        canvas.show()
        self.app.processEvents()
        blank = next(
            point
            for point in (QPoint(20, 20), QPoint(950, 30), QPoint(30, 650))
            if canvas._topic_item_at_view_point(point) is None
        )
        end = blank + QPoint(110, 50)
        before_index = controller.undo_stack.index()

        QTest.mousePress(
            canvas.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ControlModifier,
            blank,
        )
        QTest.mouseMove(canvas.viewport(), blank + QPoint(35, 16), delay=10)
        QTest.mouseMove(canvas.viewport(), end, delay=10)
        QTest.mouseRelease(
            canvas.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ControlModifier,
            end,
        )
        self.app.processEvents()
        self.assertEqual(before_index + 1, controller.undo_stack.index())
        self.assertEqual(1, len(controller.document.plan_canvas_strokes))
        self.assertEqual([], controller.document.canvas_strokes)

        erase_index = controller.undo_stack.index()
        QTest.mousePress(
            canvas.viewport(),
            Qt.MouseButton.RightButton,
            Qt.KeyboardModifier.ControlModifier,
            blank - QPoint(5, 0),
        )
        QTest.mouseMove(canvas.viewport(), blank + QPoint(55, 25), delay=10)
        QTest.mouseRelease(
            canvas.viewport(),
            Qt.MouseButton.RightButton,
            Qt.KeyboardModifier.ControlModifier,
            end + QPoint(5, 0),
        )
        self.app.processEvents()
        self.assertEqual(erase_index + 1, controller.undo_stack.index())
        self.assertEqual([], controller.document.plan_canvas_strokes)
        controller.undo_stack.undo()
        self.assertEqual(1, len(controller.document.plan_canvas_strokes))

        QTest.mousePress(
            canvas.viewport(),
            Qt.MouseButton.RightButton,
            Qt.KeyboardModifier.ControlModifier,
            blank,
        )
        QTest.mouseMove(canvas.viewport(), end, delay=10)
        QTest.keyClick(canvas, Qt.Key.Key_Escape)
        self.app.processEvents()
        self.assertEqual(1, len(controller.document.plan_canvas_strokes))
        self.assertTrue(next(iter(canvas.stroke_items.values())).isVisible())
        canvas.close()

    def test_blank_draw_delete_escape_undo_redo_and_roundtrip_need_no_switch(self) -> None:
        controller = EditorController()
        canvas = NodeCanvasView(controller.schema, controller)
        canvas.resize(1000, 700)
        canvas.show()
        self.app.processEvents()
        blank = next(
            point
            for point in (
                QPoint(20, 20),
                QPoint(980, 20),
                QPoint(20, 680),
                QPoint(980, 680),
            )
            if canvas._node_item_at_view_point(point) is None
        )
        end = blank + QPoint(80, 45)

        QTest.mousePress(
            canvas.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ControlModifier,
            blank,
        )
        QTest.mouseMove(canvas.viewport(), blank + QPoint(25, 12), delay=10)
        QTest.mouseMove(canvas.viewport(), end, delay=10)
        QTest.mouseRelease(
            canvas.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ControlModifier,
            end,
        )
        self.app.processEvents()
        self.assertEqual(1, len(controller.document.canvas_strokes))
        stroke_uuid = controller.document.canvas_strokes[0].uuid

        midpoint = blank + QPoint(25, 12)
        QTest.mouseClick(
            canvas.viewport(),
            Qt.MouseButton.RightButton,
            Qt.KeyboardModifier.ControlModifier,
            midpoint,
        )
        self.app.processEvents()
        self.assertEqual([], controller.document.canvas_strokes)
        controller.undo_stack.undo()
        self.assertEqual(stroke_uuid, controller.document.canvas_strokes[0].uuid)
        controller.undo_stack.redo()
        self.assertEqual([], controller.document.canvas_strokes)
        controller.undo_stack.undo()

        before_count = len(controller.document.canvas_strokes)
        QTest.mousePress(
            canvas.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ControlModifier,
            blank,
        )
        QTest.mouseMove(canvas.viewport(), blank + QPoint(35, 20), delay=10)
        QTest.keyClick(canvas, Qt.Key.Key_Escape)
        QTest.mouseRelease(
            canvas.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ControlModifier,
            blank + QPoint(35, 20),
        )
        self.app.processEvents()
        self.assertEqual(before_count, len(controller.document.canvas_strokes))

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "direct-pen.json"
            save_document(controller.schema, controller.document, path)
            loaded = load_document(controller.schema, path)
        self.assertEqual(
            controller.document.canvas_strokes,
            loaded.canvas_strokes,
        )
        canvas.close()

    def test_ctrl_parameter_row_click_multiselects_but_drag_draws(self) -> None:
        controller = make_ready_controller()
        first_uuid = controller.create_node(
            "ParameterTrigger",
            (120.0, 120.0),
        )
        first = controller.get_node(first_uuid)
        second_uuid = controller.add_parameter_table_row(
            parameter_table_id(first),
            first_uuid,
        )
        canvas = NodeCanvasView(controller.schema, controller)
        canvas.resize(1200, 760)
        canvas.show()
        self.app.processEvents()
        table = canvas.table_row_to_item[first_uuid]
        canvas.centerOn(table)
        self.app.processEvents()
        first_point = canvas.mapFromScene(
            table.row_scene_rect(first_uuid).center()
        )
        second_point = canvas.mapFromScene(
            table.row_scene_rect(second_uuid).center()
        )

        QTest.mousePress(
            canvas.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ControlModifier,
            second_point,
        )
        QTest.mouseMove(
            canvas.viewport(),
            second_point + QPoint(2, 1),
            delay=10,
        )
        QTest.mouseRelease(
            canvas.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ControlModifier,
            second_point + QPoint(2, 1),
        )
        self.app.processEvents()
        self.assertEqual(
            {first_uuid, second_uuid},
            set(canvas.selected_node_uuids()),
        )
        self.assertEqual([], controller.document.canvas_strokes)

        QTest.mousePress(
            canvas.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ControlModifier,
            first_point,
        )
        QTest.mouseMove(
            canvas.viewport(),
            first_point + QPoint(12, 8),
            delay=10,
        )
        QTest.mouseMove(
            canvas.viewport(),
            first_point + QPoint(55, 28),
            delay=10,
        )
        QTest.mouseRelease(
            canvas.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ControlModifier,
            first_point + QPoint(70, 36),
        )
        self.app.processEvents()
        self.assertEqual(1, len(controller.document.canvas_strokes))
        self.assertEqual(
            {first_uuid, second_uuid},
            set(canvas.selected_node_uuids()),
        )
        canvas.close()


if __name__ == "__main__":
    unittest.main()
