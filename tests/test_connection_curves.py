import os
import math
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("L2D_CONFIG_EDITOR_TEST_CLOSE_EVENT_POLICY", "discard")

from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QImage, QPainter, QPainterPath
from PyQt6.QtWidgets import QApplication, QGraphicsItem, QGraphicsView

from l2d_config_editor.canvas import ConnectionCurveLookup, build_connection_curve
from l2d_config_editor.logic import create_node
from l2d_config_editor.main_window import MainWindow
from l2d_config_editor.models import ConnectionRecord


class ConnectionCurveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _ready_window(self, temp_dir: str) -> MainWindow:
        window = MainWindow(temp_dir, prefer_saved_workspace=False)
        initial = next((node for node in window.controller.document.nodes if node.type == "Initial"), None)
        if initial is not None:
            for key, value in (
                ("author", "curve-test"),
                ("ship_skin_id", 1),
                ("memo", "curve-test"),
                ("CharName", "curve-test"),
            ):
                window.controller.update_field(initial.uuid, key, value, "simple")
        else:
            window.controller.document.meta.author = "curve-test"
            window.controller.document.meta.ship_skin_id = 1
            window.controller.document.meta.memo = "curve-test"
            window.controller.document.meta.CharName = "curve-test"
            window.controller.document.state.is_meta_ready = True
            window.controller.document.state.meta_missing_fields = []
        return window

    def test_curve_markers_follow_the_curve_arc_and_tangent(self) -> None:
        start = QPointF(0.0, 0.0)
        end = QPointF(600.0, 400.0)
        lookup = ConnectionCurveLookup(build_connection_curve(start, end))

        point, tangent = lookup.sample(0.25)
        chord_dx = end.x() - start.x()
        chord_dy = end.y() - start.y()
        distance_from_chord = abs(chord_dy * point.x() - chord_dx * point.y()) / math.hypot(chord_dx, chord_dy)

        self.assertGreater(distance_from_chord, 10.0)
        self.assertAlmostEqual(1.0, math.hypot(tangent.x(), tangent.y()), places=5)
        self.assertEqual(start, lookup.sample(0.0)[0])
        self.assertEqual(end, lookup.sample(1.0)[0])

    def test_curve_lookup_handles_a_zero_length_path(self) -> None:
        origin = QPointF(12.0, 34.0)
        lookup = ConnectionCurveLookup(QPainterPath(origin))

        point, tangent = lookup.sample(0.5)

        self.assertEqual(origin, point)
        self.assertEqual(QPointF(1.0, 0.0), tangent)

    def test_direction_marker_is_painted_on_the_curve_not_the_chord(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._ready_window(temp_dir)
            source_uuid = window.controller.create_node("TouchIdle", (160.0, 120.0))
            target_uuid = window.controller.create_node("TouchDrag", (1300.0, 920.0))
            window.controller.add_connection(source_uuid, target_uuid)
            window.controller.set_selected_node(None)
            connection = window.canvas.connection_items[(source_uuid, target_uuid)]
            path = connection.path()
            bounds = connection.boundingRect().toAlignedRect()
            image = QImage(bounds.width() + 4, bounds.height() + 4, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(0)
            painter = QPainter(image)
            painter.translate(2 - bounds.left(), 2 - bounds.top())
            connection.paint(painter, None)
            painter.end()

            start = path.elementAt(0)
            end = path.elementAt(path.elementCount() - 1)
            chord_marker = QPointF(start.x + (end.x - start.x) * 0.58, start.y + (end.y - start.y) * 0.58)
            curve_marker = ConnectionCurveLookup(path).sample(0.58)[0]
            self.assertGreater((curve_marker - chord_marker).manhattanLength(), 20.0)

            def has_ink(point: QPointF, radius: int = 3) -> bool:
                pixel_x = round(point.x() - bounds.left() + 2)
                pixel_y = round(point.y() - bounds.top() + 2)
                return any(
                    image.pixelColor(pixel_x + dx, pixel_y + dy).alpha() > 0
                    for dx in range(-radius, radius + 1)
                    for dy in range(-radius, radius + 1)
                )

            self.assertTrue(has_ink(curve_marker))
            self.assertFalse(has_ink(chord_marker))

            window._mark_saved_checkpoint(saved=True)
            window.close()

    def test_flow_pulse_and_tail_are_painted_on_the_curve_not_the_chord(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._ready_window(temp_dir)
            source_uuid = window.controller.create_node("TouchIdle", (160.0, 120.0))
            target_uuid = window.controller.create_node("TouchDrag", (1300.0, 920.0))
            window.controller.add_connection(source_uuid, target_uuid)
            window.controller.set_selected_node(source_uuid)
            connection = window.canvas.connection_items[(source_uuid, target_uuid)]
            path = connection.path()
            bounds = connection.boundingRect().toAlignedRect()
            image = QImage(bounds.width() + 4, bounds.height() + 4, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(0)
            painter = QPainter(image)
            painter.translate(2 - bounds.left(), 2 - bounds.top())
            connection.paint(painter, None)
            painter.end()

            start = path.elementAt(0)
            end = path.elementAt(path.elementCount() - 1)
            chord_pulse = QPointF(start.x + (end.x - start.x) * 0.56, start.y + (end.y - start.y) * 0.56)
            curve_pulse = ConnectionCurveLookup(path).sample(0.56)[0]
            self.assertGreater((curve_pulse - chord_pulse).manhattanLength(), 20.0)

            def has_ink(point: QPointF, radius: int = 3) -> bool:
                pixel_x = round(point.x() - bounds.left() + 2)
                pixel_y = round(point.y() - bounds.top() + 2)
                return any(
                    image.pixelColor(pixel_x + dx, pixel_y + dy).alpha() > 0
                    for dx in range(-radius, radius + 1)
                    for dy in range(-radius, radius + 1)
                )

            self.assertTrue(has_ink(curve_pulse))
            self.assertFalse(has_ink(chord_pulse))

            window._mark_saved_checkpoint(saved=True)
            window.close()

    def test_many_active_connections_degrade_to_one_curved_arrow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._ready_window(temp_dir)
            source_uuid = window.controller.create_node("TouchIdle", (100.0, 100.0))
            target_uuids = []
            for index in range(26):
                target_uuid = window.controller.create_node(
                    "TouchIdle",
                    (800.0 + (index % 4) * 260.0, 120.0 + index * 80.0),
                )
                self.assertIsNotNone(target_uuid)
                window.controller.add_connection(source_uuid, target_uuid)
                target_uuids.append(target_uuid)
            window.controller.set_selected_node(None)
            connections = list(window.canvas.connection_items.values())
            chosen = window.canvas.connection_items[(source_uuid, target_uuids[0])]

            def rendered_ink_count() -> int:
                bounds = chosen.boundingRect().toAlignedRect()
                image = QImage(bounds.width() + 4, bounds.height() + 4, QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(0)
                painter = QPainter(image)
                painter.translate(2 - bounds.left(), 2 - bounds.top())
                chosen.paint(painter, None)
                painter.end()
                return sum(
                    1
                    for y in range(image.height())
                    for x in range(image.width())
                    if image.pixelColor(x, y).alpha() > 0
                )

            chosen.setSelected(True)
            detailed_ink = rendered_ink_count()
            for connection in connections:
                connection.setSelected(True)
            minimal_ink = rendered_ink_count()

            self.assertEqual(4, chosen.path().elementCount())
            self.assertLess(minimal_ink, detailed_ink * 0.7)

            window._mark_saved_checkpoint(saved=True)
            window.close()

    def test_connection_adjacency_tracks_both_ends_and_rebuilds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._ready_window(temp_dir)
            source_uuid = window.controller.create_node("TouchIdle", (100.0, 100.0))
            middle_uuid = window.controller.create_node("TouchIdle", (700.0, 200.0))
            target_uuid = window.controller.create_node("TouchDrag", (1300.0, 300.0))
            window.controller.add_connection(source_uuid, middle_uuid)
            window.controller.add_connection(middle_uuid, target_uuid)

            self.assertEqual(
                frozenset({(source_uuid, middle_uuid)}),
                window.canvas.connection_pairs_for_node(source_uuid),
            )
            self.assertEqual(
                frozenset({(source_uuid, middle_uuid), (middle_uuid, target_uuid)}),
                window.canvas.connection_pairs_for_node(middle_uuid),
            )

            window.controller.remove_connection(source_uuid, middle_uuid)
            self.assertEqual(frozenset(), window.canvas.connection_pairs_for_node(source_uuid))
            self.assertEqual(
                frozenset({(middle_uuid, target_uuid)}),
                window.canvas.connection_pairs_for_node(middle_uuid),
            )

            window._mark_saved_checkpoint(saved=True)
            window.close()

    def test_preview_pulse_uses_arc_length_position_on_the_curve(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._ready_window(temp_dir)
            source_uuid = window.controller.create_node("TouchIdle", (160.0, 120.0))
            target_uuid = window.controller.create_node("TouchDrag", (1300.0, 920.0))
            window.canvas._start_connection_from_uuid(source_uuid)
            target_anchor = window.canvas.connection_anchor_scene_pos(target_uuid, "input")
            window.canvas._update_temp_connection(target_anchor)
            preview = window.canvas._temp_path
            path = preview.path()
            arc_pulse = ConnectionCurveLookup(path).sample(0.1)[0]
            parameter_pulse = path.pointAtPercent(0.1)
            self.assertGreater((arc_pulse - parameter_pulse).manhattanLength(), 20.0)

            bounds = preview.boundingRect().toAlignedRect()
            image = QImage(bounds.width() + 4, bounds.height() + 4, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(0)
            painter = QPainter(image)
            painter.translate(2 - bounds.left(), 2 - bounds.top())
            preview.paint(painter, None)
            painter.end()

            def brightest_red(point: QPointF) -> int:
                pixel_x = round(point.x() - bounds.left() + 2)
                pixel_y = round(point.y() - bounds.top() + 2)
                return max(
                    image.pixelColor(pixel_x + dx, pixel_y + dy).red()
                    for dx in range(-2, 3)
                    for dy in range(-2, 3)
                )

            self.assertGreater(brightest_red(arc_pulse), brightest_red(parameter_pulse) + 30)

            window.canvas.cancel_connection_preview()
            window._mark_saved_checkpoint(saved=True)
            window.close()

    def test_one_hundred_node_curve_refresh_and_paint_stay_within_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._ready_window(temp_dir)
            document = window.controller.document
            nodes = []
            for index in range(100):
                node = create_node(
                    window.controller.schema,
                    document,
                    "TouchIdle",
                    (40.0 + (index % 10) * 100.0, 40.0 + (index // 10) * 80.0),
                )
                document.nodes.append(node)
                nodes.append(node)
            document.connections.extend(
                ConnectionRecord(nodes[index].uuid, nodes[index + 1].uuid)
                for index in range(len(nodes) - 1)
            )
            window.canvas.rebuild_scene()
            window.controller.set_selected_node(None)
            self.assertEqual(99, len(window.canvas.connection_items))

            middle_uuid = nodes[50].uuid
            for _ in range(10):
                window.canvas._update_connections_for_node(middle_uuid)
            refresh_durations = []
            for _ in range(100):
                started = time.perf_counter_ns()
                window.canvas._update_connections_for_node(middle_uuid)
                refresh_durations.append((time.perf_counter_ns() - started) / 1_000_000.0)
            refresh_p95 = sorted(refresh_durations)[94]

            image = QImage(1600, 1200, QImage.Format.Format_ARGB32_Premultiplied)
            paint_durations = []
            for iteration in range(8):
                image.fill(0)
                painter = QPainter(image)
                started = time.perf_counter_ns()
                for connection in window.canvas.connection_items.values():
                    connection.paint(painter, None)
                painter.end()
                if iteration >= 3:
                    paint_durations.append((time.perf_counter_ns() - started) / 1_000_000.0)
            paint_p95 = max(paint_durations)

            self.assertLess(refresh_p95, 10.0)
            self.assertLess(paint_p95, 50.0)

            window._mark_saved_checkpoint(saved=True)
            window.close()

    def test_persistent_and_preview_connections_are_cubic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._ready_window(temp_dir)
            source_uuid = window.controller.create_node("TouchIdle", (100.0, 100.0))
            target_uuid = window.controller.create_node("TouchDrag", (620.0, 220.0))
            self.assertIsNotNone(source_uuid)
            self.assertIsNotNone(target_uuid)
            window.controller.add_connection(source_uuid, target_uuid)

            connection = window.canvas.connection_items[(source_uuid, target_uuid)]
            self.assertEqual(4, connection.path().elementCount())

            window.canvas._start_connection_from_uuid(source_uuid)
            target_anchor = window.canvas.connection_anchor_scene_pos(target_uuid, "input")
            window.canvas._update_temp_connection(target_anchor)
            self.assertEqual(4, window.canvas._temp_path.path().elementCount())

            window.canvas.cancel_connection_preview()
            window._mark_saved_checkpoint(saved=True)
            window.close()

    def test_zoom_then_drag_keeps_curve_attached_to_the_moving_node(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._ready_window(temp_dir)
            source_uuid = window.controller.create_node("TouchIdle", (160.0, 160.0))
            target_uuid = window.controller.create_node("TouchDrag", (520.0, 180.0))
            window.controller.add_connection(source_uuid, target_uuid)
            for index in range(55):
                self.assertIsNotNone(window.controller.create_node("TouchIdle", (900.0 + index * 260.0, 120.0)))
            window.canvas._apply_view_state(0.18, QPointF(0.0, 0.0))

            connection = window.canvas.connection_items[(source_uuid, target_uuid)]
            target_item = window.canvas.node_items[target_uuid]
            window.canvas._set_interaction_busy("wheel", True)
            window.canvas._set_interaction_busy("drag", True)
            target_item.setPos(target_item.pos() + QPointF(180.0, 40.0))

            end_element = connection.path().elementAt(connection.path().elementCount() - 1)
            path_end = QPointF(end_element.x, end_element.y)
            self.assertEqual(window.canvas.connection_anchor_scene_pos(target_uuid, "input"), path_end)
            self.assertEqual(4, connection.path().elementCount())

            window.canvas._set_interaction_busy("drag", False)
            window.canvas._set_interaction_busy("wheel", False)
            window._mark_saved_checkpoint(saved=True)
            window.close()

    def test_motion_preview_still_paints_the_cubic_curve(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._ready_window(temp_dir)
            source_uuid = window.controller.create_node("TouchIdle", (160.0, 120.0))
            target_uuid = window.controller.create_node("TouchDrag", (720.0, 720.0))
            window.controller.add_connection(source_uuid, target_uuid)
            for index in range(55):
                self.assertIsNotNone(window.controller.create_node("TouchIdle", (1200.0 + index * 260.0, 120.0)))
            window.canvas._apply_view_state(0.18, QPointF(0.0, 0.0))
            window.canvas._set_interaction_busy("wheel", True)
            self.assertTrue(window.canvas.should_use_motion_preview())

            connection = window.canvas.connection_items[(source_uuid, target_uuid)]
            path = connection.path()
            curve_point = path.pointAtPercent(0.25)
            start = path.elementAt(0)
            end = path.elementAt(path.elementCount() - 1)
            chord_point = QPointF(start.x + (end.x - start.x) * 0.25, start.y + (end.y - start.y) * 0.25)
            self.assertGreater((curve_point - chord_point).manhattanLength(), 20.0)

            bounds = connection.boundingRect().toAlignedRect()
            image = QImage(bounds.width() + 4, bounds.height() + 4, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(0)
            painter = QPainter(image)
            painter.translate(2 - bounds.left(), 2 - bounds.top())
            connection.paint(painter, None)
            painter.end()

            pixel_x = round(curve_point.x() - bounds.left() + 2)
            pixel_y = round(curve_point.y() - bounds.top() + 2)
            curve_is_painted = any(
                image.pixelColor(pixel_x + dx, pixel_y + dy).alpha() > 0
                for dx in range(-2, 3)
                for dy in range(-2, 3)
            )
            self.assertTrue(curve_is_painted)

            window.canvas._set_interaction_busy("wheel", False)
            window._mark_saved_checkpoint(saved=True)
            window.close()

    def test_persistent_connections_do_not_cache_dynamic_paint_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._ready_window(temp_dir)
            source_uuid = window.controller.create_node("TouchIdle", (100.0, 100.0))
            target_uuid = window.controller.create_node("TouchDrag", (620.0, 220.0))
            window.controller.add_connection(source_uuid, target_uuid)

            connection = window.canvas.connection_items[(source_uuid, target_uuid)]
            self.assertEqual(QGraphicsItem.CacheMode.NoCache, connection.cacheMode())

            window._mark_saved_checkpoint(saved=True)
            window.close()

    def test_canvas_keeps_antialiasing_dirty_region_adjustment_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self._ready_window(temp_dir)

            self.assertFalse(
                window.canvas.optimizationFlags()
                & QGraphicsView.OptimizationFlag.DontAdjustForAntialiasing
            )

            window._mark_saved_checkpoint(saved=True)
            window.close()


if __name__ == "__main__":
    unittest.main()
