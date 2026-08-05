import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("L2D_CONFIG_EDITOR_TEST_CLOSE_EVENT_POLICY", "discard")

from PySide6.QtCore import QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QColor, QFontMetricsF
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from l2d_config_editor.app_settings import create_app_settings
from l2d_config_editor.canvas import CanvasStrokeItem
from l2d_config_editor.logic import load_document
from l2d_config_editor.main_window import MainWindow
from l2d_config_editor.models import CanvasStrokeRecord
from l2d_config_editor.plan import parse_touchidle_plan_title
from l2d_config_editor.plan_canvas import PlanTopicItem
from l2d_config_editor.styles import ThemeMode


class CanvasModernizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _window(self, root: str) -> MainWindow:
        window = MainWindow(root, prefer_saved_workspace=False)
        window.controller.document.meta.author = "test"
        window.controller.document.meta.ship_skin_id = 1
        window.controller.document.meta.memo = "test"
        window.controller.document.meta.CharName = "test"
        window.controller.refresh_derived()
        window.show()
        self.app.processEvents()
        return window

    def _close(self, window: MainWindow) -> None:
        window._mark_saved_checkpoint(saved=True)
        window.close()
        self.app.processEvents()

    def test_virtual_placeholder_uses_the_touchidle_card_title_layout(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            window = self._window(root)
            root_uuid = window.controller.document.nodes[0].uuid
            placeholder_uuid = window.controller.create_plan_topic(root_uuid, "纯备注")
            topic = next(
                topic
                for topic in window.controller.ensure_plan_layout().topics
                if topic.node_uuid == placeholder_uuid
            )
            topic.branch_color = "#2F80ED"
            window.controller.materialize_plan_topics()
            touch_uuid = window.controller.create_node("TouchIdle", (760.0, 160.0))
            self.app.processEvents()

            placeholder_item = window.canvas.node_items[placeholder_uuid]
            touch_item = window.canvas.node_items[touch_uuid]
            self.assertEqual("card", placeholder_item._display_mode)
            self.assertTrue(placeholder_item._uses_compact_card())
            self.assertEqual("card", touch_item._display_mode)
            self.assertEqual(
                touch_item._compact_title_font().pointSizeF(),
                placeholder_item._compact_title_font().pointSizeF(),
            )
            self.assertTrue(placeholder_item._card_layout["draw"].isValid())
            self.assertTrue(placeholder_item._card_layout["action"].isValid())
            self.assertTrue(
                placeholder_item._begin_card_field_edit("planned_draw_name")
            )
            editor = placeholder_item._card_editor_proxy.widget()
            editor.setText("TouchIdle24")
            QTest.keyClick(editor, Qt.Key.Key_Return)
            self.app.processEvents()
            self.assertEqual(
                "TouchIdle",
                window.controller.get_node(placeholder_uuid).type,
            )
            self.assertEqual(
                "TouchIdle24",
                window.controller.get_node(placeholder_uuid).fields[
                    "draw_able_name"
                ],
            )
            self._close(window)

    def test_plan_title_keeps_frame_and_animation_suffixes_when_note_is_present(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            window = self._window(root)
            parsed = parse_touchidle_plan_title(
                "TouchIdle123456789-touch_idle987654321-备注"
            )
            self.assertIsNotNone(parsed)
            metrics = QFontMetricsF(window.font())

            draw = PlanTopicItem._elide_semantic_segment(
                metrics,
                parsed.draw_text,
                92.0,
            )
            action = PlanTopicItem._elide_semantic_segment(
                metrics,
                parsed.action_text,
                92.0,
            )

            self.assertTrue(draw.endswith("456789"))
            self.assertTrue(action.endswith("654321"))
            self._close(window)

    def test_plan_view_uses_an_independent_hierarchy_layout(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            window = self._window(root)
            controller = window.controller
            root_node = controller.document.nodes[0]
            root_node.ui_position = {"x": 120.0, "y": 180.0}
            child_uuid = controller.create_plan_topic(root_node.uuid, "纯备注")
            child = controller.get_node(child_uuid)
            child.ui_position = {"x": 610.0, "y": 370.0}

            window._switch_graph_view("plan")
            self.app.processEvents()

            root_item = window.plan_canvas.topic_items[root_node.uuid]
            child_item = window.plan_canvas.topic_items[child_uuid]
            self.assertNotEqual((120.0, 180.0), (root_item.pos().x(), root_item.pos().y()))
            self.assertNotEqual((610.0, 370.0), (child_item.pos().x(), child_item.pos().y()))
            self.assertGreater(child_item.pos().x(), root_item.pos().x())
            self.assertEqual(
                root_item.sceneBoundingRect().center().y(),
                child_item.sceneBoundingRect().center().y(),
            )
            self._close(window)

    def test_plan_topic_uses_semantic_zoom_instead_of_inverse_font_scaling(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            window = self._window(root)
            controller = window.controller
            child_uuid = controller.create_plan_topic(
                controller.document.nodes[0].uuid,
                "纯备注",
            )
            window._switch_graph_view("plan")
            self.app.processEvents()
            topic = window.plan_canvas.topic_items[child_uuid]

            font_size = topic._topic_font().pointSizeF()
            window.plan_canvas.resetTransform()
            window.plan_canvas.scale(0.35, 0.35)
            self.assertTrue(window.plan_canvas.is_overview_mode())
            self.assertFalse(window.plan_canvas.shows_topic_text())
            self.assertEqual(font_size, topic._topic_font().pointSizeF())

            window.plan_canvas.resetTransform()
            window.plan_canvas.scale(0.55, 0.55)
            self.assertFalse(window.plan_canvas.is_overview_mode())
            self.assertTrue(window.plan_canvas.shows_topic_text())

            window.plan_canvas.resetTransform()
            window.plan_canvas.scale(0.8, 0.8)
            self.assertFalse(window.plan_canvas.is_overview_mode())
            self.assertTrue(window.plan_canvas.shows_topic_text())
            self.assertEqual(font_size, topic._topic_font().pointSizeF())
            self.assertGreaterEqual(topic.boundingRect().width(), PlanTopicItem.MIN_CARD_WIDTH)
            self.assertGreaterEqual(topic.boundingRect().height(), PlanTopicItem.MIN_CARD_HEIGHT)

            window.plan_canvas.resetTransform()
            window.plan_canvas.scale(0.35, 0.35)
            window.plan_canvas.focus_on_node(child_uuid)
            self.assertGreaterEqual(
                window.plan_canvas.transform().m11(),
                PlanTopicItem.READABLE_SCALE,
            )
            self._close(window)

    def test_plan_topic_drag_only_changes_plan_structure_not_formal_position(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            window = self._window(root)
            controller = window.controller
            root_uuid = controller.document.nodes[0].uuid
            child_uuid = controller.create_plan_topic(root_uuid, "纯备注")
            child = controller.get_node(child_uuid)
            child.ui_position = {"x": 420.0, "y": 80.0}

            window._switch_graph_view("plan")
            self.app.processEvents()
            original = QPointF(window.plan_canvas.topic_items[child_uuid].pos())
            dropped = QPointF(420.0, 310.0)
            window.plan_canvas._handle_topic_drop(child_uuid, dropped, original)
            self.app.processEvents()

            self.assertEqual({"x": 420.0, "y": 80.0}, child.ui_position)
            self._close(window)

    def test_plan_edges_stay_attached_while_a_topic_moves(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            window = self._window(root)
            controller = window.controller
            root_uuid = controller.document.nodes[0].uuid
            parent_uuid = controller.create_plan_topic(root_uuid, "parent")
            child_uuid = controller.create_plan_topic(parent_uuid, "child")
            window._switch_graph_view("plan")
            self.app.processEvents()

            child_item = window.plan_canvas.topic_items[child_uuid]
            edge_item = next(
                path_item
                for path_item, _source, target in window.plan_canvas._curve_bindings
                if target is child_item
            )
            old_end = edge_item.path().pointAtPercent(1.0)
            child_item.setPos(child_item.pos() + QPointF(140.0, 90.0))
            new_end = edge_item.path().pointAtPercent(1.0)
            anchor = child_item.connection_point("left")

            self.assertNotEqual((old_end.x(), old_end.y()), (new_end.x(), new_end.y()))
            self.assertAlmostEqual(anchor.x(), new_end.x())
            self.assertAlmostEqual(anchor.y(), new_end.y())
            self._close(window)

    def test_plan_drag_shows_a_dashed_preview_to_the_candidate_parent(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            window = self._window(root)
            controller = window.controller
            root_uuid = controller.document.nodes[0].uuid
            candidate_uuid = controller.create_plan_topic(root_uuid, "candidate")
            dragged_uuid = controller.create_plan_topic(root_uuid, "dragged")
            window._switch_graph_view("plan")
            self.app.processEvents()

            canvas = window.plan_canvas
            candidate = canvas.topic_items[candidate_uuid]
            dragged = canvas.topic_items[dragged_uuid]
            original = QPointF(dragged.pos())
            canvas._begin_topic_drag(dragged_uuid, original)
            dragged.setPos(
                QPointF(
                    candidate.pos().x() + candidate.boundingRect().width() + 80.0,
                    candidate.pos().y(),
                )
            )

            intent = canvas._topic_drop_intent(
                dragged_uuid,
                QPointF(dragged.pos()),
                original,
            )
            preview = canvas._drop_preview_item
            self.assertIsNotNone(intent)
            self.assertEqual("reparent", intent.action)
            self.assertEqual(candidate_uuid, intent.new_parent_uuid)
            self.assertTrue(preview.isVisible())
            self.assertEqual(Qt.PenStyle.DashLine, preview.pen().style())
            self.assertAlmostEqual(
                candidate.connection_point("right").x(),
                preview.path().pointAtPercent(0.0).x(),
            )
            self.assertAlmostEqual(
                dragged.connection_point("left").x(),
                preview.path().pointAtPercent(1.0).x(),
            )
            canvas._end_topic_drag()
            self.assertFalse(preview.isVisible())
            self._close(window)

    def test_plan_cards_size_to_content_and_keep_connection_anchors_on_edges(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            window = self._window(root)
            controller = window.controller
            root_uuid = controller.document.nodes[0].uuid
            short_uuid = controller.create_plan_topic(root_uuid, "短标题")
            long_uuid = controller.create_plan_topic(
                root_uuid,
                "这是一个用于验证计划图自适应卡片换行和完整可读性的很长中文标题",
            )
            semantic_uuid = controller.create_plan_topic(
                root_uuid,
                "TouchIdle123456-touch_idle987654-这是独立备注内容",
            )
            window._switch_graph_view("plan")
            self.app.processEvents()

            short_item = window.plan_canvas.topic_items[short_uuid]
            long_item = window.plan_canvas.topic_items[long_uuid]
            semantic_item = window.plan_canvas.topic_items[semantic_uuid]
            self.assertGreater(long_item.boundingRect().height(), short_item.boundingRect().height())
            self.assertGreater(semantic_item.boundingRect().height(), short_item.boundingRect().height())
            self.assertTrue(semantic_item._card_spec.note_lines)
            self.assertEqual(
                semantic_item.sceneBoundingRect().left(),
                semantic_item.connection_point("left").x(),
            )
            self.assertEqual(
                semantic_item.sceneBoundingRect().right(),
                semantic_item.connection_point("right").x(),
            )
            self.assertFalse(
                short_item.sceneBoundingRect().intersects(
                    long_item.sceneBoundingRect()
                )
            )
            self.assertFalse(
                long_item.sceneBoundingRect().intersects(
                    semantic_item.sceneBoundingRect()
                )
            )
            self.assertEqual(
                "TouchIdle123456-touch_idle987654-这是独立备注内容\n绿色：转换为 TouchIdle",
                semantic_item.toolTip(),
            )
            self._close(window)

    def test_pen_gesture_creates_and_deletes_complete_stroke(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ, {"L2D_CONFIG_EDITOR_SETTINGS_DIR": str(Path(root) / "settings")}
        ):
            window = self._window(root)
            canvas = window.canvas
            canvas.set_pen_mode(True)
            QTest.mousePress(
                canvas.viewport(),
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.ControlModifier,
                QPoint(50, 50),
            )
            QTest.mouseMove(canvas.viewport(), QPoint(100, 80), 10)
            QTest.mouseRelease(
                canvas.viewport(),
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.ControlModifier,
                QPoint(100, 80),
            )
            self.app.processEvents()
            self.assertEqual(1, len(window.controller.document.canvas_strokes))
            self.assertEqual(1, len(canvas.stroke_items))

            QTest.mouseClick(
                canvas.viewport(),
                Qt.MouseButton.RightButton,
                Qt.KeyboardModifier.ControlModifier,
                QPoint(75, 65),
            )
            self.app.processEvents()
            self.assertEqual([], window.controller.document.canvas_strokes)
            self.assertEqual({}, canvas.stroke_items)
            window.controller.undo_stack.undo()
            self.assertEqual(1, len(window.controller.document.canvas_strokes))
            self._close(window)

    def test_fast_right_drag_sweep_deletes_all_hit_strokes_as_one_command(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            window = self._window(root)
            canvas = window.canvas
            first = [canvas.mapToScene(QPoint(x, 120)) for x in (80, 240)]
            second = [canvas.mapToScene(QPoint(x, 155)) for x in (80, 240)]
            survivor = [canvas.mapToScene(QPoint(x, 300)) for x in (80, 240)]
            first_id = window.controller.add_canvas_stroke(
                [(point.x(), point.y()) for point in first], "#112233", 4
            )
            second_id = window.controller.add_canvas_stroke(
                [(point.x(), point.y()) for point in second], "#445566", 4
            )
            survivor_id = window.controller.add_canvas_stroke(
                [(point.x(), point.y()) for point in survivor], "#778899", 4
            )
            survivor_item = canvas.stroke_items[survivor_id]
            before_index = window.controller.undo_stack.index()

            QTest.mousePress(
                canvas.viewport(),
                Qt.MouseButton.RightButton,
                Qt.KeyboardModifier.ControlModifier,
                QPoint(160, 100),
            )
            QTest.mouseMove(canvas.viewport(), QPoint(160, 175), 10)
            QTest.mouseRelease(
                canvas.viewport(),
                Qt.MouseButton.RightButton,
                Qt.KeyboardModifier.ControlModifier,
                QPoint(160, 180),
            )
            self.app.processEvents()

            self.assertEqual(before_index + 1, window.controller.undo_stack.index())
            self.assertEqual(
                [survivor_id],
                [stroke.uuid for stroke in window.controller.document.canvas_strokes],
            )
            self.assertIs(survivor_item, canvas.stroke_items[survivor_id])
            window.controller.undo_stack.undo()
            self.assertEqual(
                {first_id, second_id, survivor_id},
                {stroke.uuid for stroke in window.controller.document.canvas_strokes},
            )
            self._close(window)

    def test_eraser_scene_index_keeps_move_event_p95_under_sixteen_ms(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            window = self._window(root)
            canvas = window.canvas
            window.controller.document.canvas_strokes = [
                CanvasStrokeRecord(
                    uuid=f"perf-{row}-{column}",
                    points=[
                        (column * 80.0, row * 40.0),
                        (column * 80.0 + 50.0, row * 40.0),
                    ],
                    color="#336699",
                    width=4.0,
                )
                for row in range(40)
                for column in range(40)
            ]
            canvas._rebuild_canvas_strokes()
            self.app.processEvents()

            original_shape = CanvasStrokeItem.shape
            inspected: set[str] = set()

            def tracked_shape(item):
                inspected.add(item.record.uuid)
                return original_shape(item)

            start = QPointF(801.0, 801.0)
            end = QPointF(847.0, 801.0)
            durations: list[float] = []
            with patch.object(CanvasStrokeItem, "shape", tracked_shape):
                for _ in range(10):
                    canvas._stroke_uuids_in_eraser_segment(start, end)
                inspected.clear()
                for _ in range(100):
                    started = time.perf_counter_ns()
                    canvas._stroke_uuids_in_eraser_segment(start, end)
                    durations.append((time.perf_counter_ns() - started) / 1_000_000.0)

            p95_ms = sorted(durations)[94]
            self.assertLess(len(inspected), len(canvas.stroke_items) // 10)
            self.assertLess(p95_ms, 16.0)
            self._close(window)

    def test_comment_and_plan_placeholder_keep_visible_connection_pins(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            window = self._window(root)
            root_uuid = window.controller.document.nodes[0].uuid
            comment_uuid = window.controller.create_node("Comment", (420.0, 120.0))
            window.controller.add_connection(root_uuid, comment_uuid)
            placeholder_uuid = window.controller.create_plan_topic(
                root_uuid,
                "not-a-touchidle-rule",
            )
            topic = next(
                topic
                for topic in window.controller.ensure_plan_layout().topics
                if topic.node_uuid == placeholder_uuid
            )
            topic.branch_color = "#2F80ED"
            window.controller.materialize_plan_topics()
            self.app.processEvents()

            for node_uuid in (comment_uuid, placeholder_uuid):
                item = window.canvas.node_items[node_uuid]
                self.assertTrue(item.input_pin_rect().isValid())
                self.assertTrue(item.output_pin_rect().isValid())
                self.assertIn(
                    (root_uuid, node_uuid),
                    window.canvas.connection_items,
                )
            self._close(window)

    def test_pen_stroke_drawn_over_node_stays_above_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ, {"L2D_CONFIG_EDITOR_SETTINGS_DIR": str(Path(root) / "settings")}
        ):
            window = self._window(root)
            canvas = window.canvas
            node_uuid = window.controller.create_node("TouchIdle", (0.0, 0.0))
            node_item = canvas.node_items[node_uuid]
            node_bounds = node_item.sceneBoundingRect()
            canvas.centerOn(node_bounds.center())
            self.app.processEvents()

            start = canvas.mapFromScene(
                QPointF(node_bounds.left() + node_bounds.width() * 0.25, node_bounds.center().y())
            )
            middle = canvas.mapFromScene(node_bounds.center())
            end = canvas.mapFromScene(
                QPointF(node_bounds.left() + node_bounds.width() * 0.75, node_bounds.center().y())
            )
            canvas.set_pen_mode(True)
            QTest.mousePress(
                canvas.viewport(),
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.ControlModifier,
                start,
            )
            QTest.mouseMove(canvas.viewport(), middle, 10)
            QTest.mouseRelease(
                canvas.viewport(),
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.ControlModifier,
                end,
            )
            self.app.processEvents()

            self.assertEqual(1, len(window.controller.document.canvas_strokes))
            stroke = window.controller.document.canvas_strokes[0]
            stroke_item = canvas.stroke_items[stroke.uuid]
            self.assertTrue(stroke_item.isVisible())
            self.assertGreater(
                stroke_item.zValue(),
                node_item.zValue(),
                "The persisted stroke must retain the preview's above-node stacking order",
            )

            saved_path = Path(root) / "stroke-round-trip.json"
            window.controller.save_document(str(saved_path))
            loaded = load_document(window.controller.schema, saved_path)
            self.assertEqual([stroke], loaded.canvas_strokes)
            self._close(window)

    def test_pen_mode_off_preserves_ctrl_selection(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            window = self._window(root)
            node_uuid = window.controller.create_node("TouchIdle", (100, 100))
            item = window.canvas.node_items[node_uuid]
            item.setSelected(False)
            window.canvas.set_pen_mode(False)
            view_point = window.canvas.mapFromScene(item.mapToScene(item.boundingRect().center()))
            QTest.mouseClick(
                window.canvas.viewport(),
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.ControlModifier,
                view_point,
            )
            self.assertTrue(item.isSelected())
            self.assertEqual([], window.controller.document.canvas_strokes)
            self._close(window)

    def test_concise_mode_filters_fields_and_canvas_elements_without_dirtying_document(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            window = self._window(root)
            node_uuid = window.controller.create_node("TouchIdle", (100, 100))
            window._mark_saved_checkpoint(saved=True)
            undo_index = window.controller.undo_stack.index()
            window.canvas.set_concise_display(
                True,
                {"tips", "action_trigger", "action_trigger_active"},
                {"groups": False, "tables": False, "images": False, "strokes": False},
            )
            item = window.canvas.node_items[node_uuid]
            self.assertTrue(item._card_layout["identity"].isValid())
            self.assertTrue(item._card_layout["draw"].isNull())
            self.assertTrue(item._card_layout["parameter"].isNull())
            self.assertTrue(item._card_layout["action"].isValid())
            self.assertEqual(undo_index, window.controller.undo_stack.index())
            self.assertFalse(window._is_dirty())
            self._close(window)

    def test_concise_hidden_parameter_table_renders_rows_as_nodes_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            window = self._window(root)
            parameter_uuid = window.controller.create_node("ParameterTrigger", (180, 360))
            target_uuid = window.controller.create_node("TouchIdle", (620, 360))
            window.controller.add_connection(parameter_uuid, target_uuid)
            parameter = window.controller.get_node(parameter_uuid)
            original_position = dict(parameter.ui_position)
            original_connections = [
                (connection.from_uuid, connection.to_uuid)
                for connection in window.controller.document.connections
            ]
            window._mark_saved_checkpoint(saved=True)
            undo_index = window.controller.undo_stack.index()

            window.canvas.set_concise_display(
                True,
                {"tips", "action_trigger", "action_trigger_active"},
                {"groups": False, "tables": False, "images": False, "strokes": False},
            )

            item = window.canvas.node_items[parameter_uuid]
            self.assertTrue(item._uses_compact_card())
            self.assertIn(str(parameter.type_slot or parameter.sequence_no or 1), item._compact_identity_text())
            self.assertTrue(item.input_pin_rect().isValid())
            self.assertTrue(item.output_pin_rect().isValid())
            self.assertIn((parameter_uuid, target_uuid), window.canvas.connection_items)
            self.assertNotIn(parameter_uuid, window.canvas.table_row_to_item)
            self.assertEqual(original_position, parameter.ui_position)
            self.assertEqual(undo_index, window.controller.undo_stack.index())
            self.assertFalse(window._is_dirty())

            window.canvas.set_concise_display(
                True,
                {"tips", "action_trigger", "action_trigger_active"},
                {"groups": False, "tables": True, "images": False, "strokes": False},
            )

            self.assertNotIn(parameter_uuid, window.canvas.node_items)
            self.assertIn(parameter_uuid, window.canvas.table_row_to_item)
            self.assertTrue(window.canvas.table_row_to_item[parameter_uuid].isVisible())
            self.assertEqual(original_position, parameter.ui_position)
            self.assertEqual(
                original_connections,
                [
                    (connection.from_uuid, connection.to_uuid)
                    for connection in window.controller.document.connections
                ],
            )
            self.assertEqual(undo_index, window.controller.undo_stack.index())
            self.assertFalse(window._is_dirty())
            self._close(window)

    def test_light_cards_choose_wcag_contrast_text(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            window = self._window(root)
            node_uuid = window.controller.create_node("TouchIdle", (100, 100))
            window.canvas.set_ui_theme(ThemeMode.LIGHT)
            item = window.canvas.node_items[node_uuid]
            palette = item._compact_card_palette()

            def luminance(color: QColor) -> float:
                values = []
                for channel in (color.redF(), color.greenF(), color.blueF()):
                    values.append(channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4)
                return 0.2126 * values[0] + 0.7152 * values[1] + 0.0722 * values[2]

            first = luminance(palette["note_fill"])
            second = luminance(palette["note_text"])
            ratio = (max(first, second) + 0.05) / (min(first, second) + 0.05)
            self.assertGreaterEqual(ratio, 4.5)
            self._close(window)

    def test_comment_and_compact_fonts_follow_zoom_without_geometry_changes(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            window = self._window(root)
            comment_uuid = window.controller.create_node("Comment", (100, 100))
            node_uuid = window.controller.create_node("TouchIdle", (500, 100))
            comment = window.canvas.node_items[comment_uuid]
            card = window.canvas.node_items[node_uuid]
            comment_rect = comment.boundingRect()
            card_rect = card.boundingRect()
            baseline_comment = comment._comment_content_font().pointSizeF()
            baseline_card = card._compact_action_font().pointSizeF()
            window.canvas._apply_view_state(0.4, QPointF())
            self.app.processEvents()
            self.assertGreater(comment._comment_content_font().pointSizeF(), baseline_comment)
            self.assertGreater(card._compact_action_font().pointSizeF(), baseline_card)
            self.assertEqual(comment_rect, comment.boundingRect())
            self.assertEqual(card_rect, card.boundingRect())
            self._close(window)

    def test_removed_trash_controls_are_absent(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            window = self._window(root)
            self.assertFalse(hasattr(window, "trash_button"))
            self.assertFalse(hasattr(window, "trash_enabled_checkbox"))
            self.assertFalse(hasattr(window, "trash_action"))
            self.assertNotIn("回收站", " ".join(action.text() for action in window.findChildren(type(window.save_action))))
            self._close(window)

    def test_settings_migration_uses_new_namespace_and_skips_trash(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ, {"L2D_CONFIG_EDITOR_SETTINGS_DIR": root}
        ):
            legacy_path = Path(root) / "OpenAI" / "L2DConfigEditor.ini"
            current_path = Path(root) / "4S4H1" / "L2DConfigEditor.ini"
            legacy_path.parent.mkdir(parents=True, exist_ok=True)
            current_path.parent.mkdir(parents=True, exist_ok=True)
            legacy = QSettings(str(legacy_path), QSettings.Format.IniFormat)
            legacy.clear()
            legacy.setValue("ui/theme_mode", "light")
            legacy.setValue("trash_enabled_default", True)
            legacy.sync()
            current = QSettings(str(current_path), QSettings.Format.IniFormat)
            current.clear()
            current.sync()

            migrated = create_app_settings()

            self.assertEqual("light", migrated.value("ui/theme_mode"))
            self.assertFalse(migrated.contains("trash_enabled_default"))
