import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("L2D_CONFIG_EDITOR_TEST_CLOSE_EVENT_POLICY", "discard")

from PySide6.QtCore import QMimeData, QPointF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QGraphicsItem, QInputDialog

from l2d_config_editor.controller import EditorController
from l2d_config_editor.logic import create_document, create_node, get_default_schema, load_document, save_document
from l2d_config_editor.models import CanvasImageRecord, GroupRecord
from l2d_config_editor.main_window import MainWindow
from l2d_config_editor.reference_images import encode_reference_image


class PersistentGroupTests(unittest.TestCase):
    def test_group_geometry_roundtrips_with_document(self) -> None:
        schema = get_default_schema()
        document = create_document(schema)
        member = create_node(schema, document, "TouchIdle", (200.0, 160.0))
        document.nodes.append(member)
        document.groups.append(
            GroupRecord(
                uuid="group-1",
                title="参考区",
                node_uuids=[member.uuid],
                ui_position={"x": 120.0, "y": 80.0},
                ui_size={"width": 640.0, "height": 360.0},
            )
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "group.json"
            save_document(schema, document, path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            loaded = load_document(schema, path)

        self.assertEqual({"x": 120.0, "y": 80.0}, payload["groups"][0]["ui_position"])
        self.assertEqual({"width": 640.0, "height": 360.0}, payload["groups"][0]["ui_size"])
        self.assertEqual({"x": 120.0, "y": 80.0}, loaded.groups[0].ui_position)
        self.assertEqual({"width": 640.0, "height": 360.0}, loaded.groups[0].ui_size)

    def test_empty_group_with_geometry_remains_a_drop_target_after_roundtrip(self) -> None:
        schema = get_default_schema()
        document = create_document(schema)
        document.groups.append(
            GroupRecord(
                uuid="empty-group",
                title="待补节点",
                ui_position={"x": 40.0, "y": 60.0},
                ui_size={"width": 520.0, "height": 300.0},
            )
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "empty-group.json"
            save_document(schema, document, path)
            loaded = load_document(schema, path)

        self.assertEqual(1, len(loaded.groups))
        self.assertEqual([], loaded.groups[0].node_uuids)
        self.assertEqual({"x": 40.0, "y": 60.0}, loaded.groups[0].ui_position)

    def test_loading_legacy_overlapping_groups_keeps_each_node_in_first_group_only(self) -> None:
        schema = get_default_schema()
        document = create_document(schema)
        first = create_node(schema, document, "TouchIdle", (200.0, 160.0))
        second = create_node(schema, document, "TouchIdle", (760.0, 160.0))
        document.nodes.extend([first, second])
        document.groups = [
            GroupRecord(uuid="first", node_uuids=[first.uuid, second.uuid]),
            GroupRecord(
                uuid="second",
                node_uuids=[second.uuid],
                ui_position={"x": 700.0, "y": 100.0},
                ui_size={"width": 600.0, "height": 400.0},
            ),
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "legacy-overlap.json"
            save_document(schema, document, path)
            loaded = load_document(schema, path)

        self.assertEqual([first.uuid, second.uuid], loaded.groups[0].node_uuids)
        self.assertEqual([], loaded.groups[1].node_uuids)

    def test_idle0_root_is_never_added_to_a_group(self) -> None:
        controller = EditorController()
        idle0 = next(node for node in controller.document.nodes if node.type == "Idle0")
        first = create_node(controller.schema, controller.document, "TouchIdle", (200.0, 160.0))
        second = create_node(controller.schema, controller.document, "TouchIdle", (760.0, 160.0))
        controller.document.nodes.extend([first, second])

        group_uuid = controller.create_group([idle0.uuid, first.uuid, second.uuid])

        self.assertIsNotNone(group_uuid)
        self.assertEqual([first.uuid, second.uuid], controller.get_group(group_uuid).node_uuids)

    def test_regrouping_moves_nodes_from_old_group_and_undoes_in_one_step(self) -> None:
        controller = EditorController()
        first = create_node(controller.schema, controller.document, "TouchIdle", (200.0, 160.0))
        second = create_node(controller.schema, controller.document, "TouchIdle", (760.0, 160.0))
        third = create_node(controller.schema, controller.document, "TouchIdle", (1320.0, 160.0))
        controller.document.nodes.extend([first, second, third])
        old_group_uuid = controller.create_group(
            [first.uuid, second.uuid],
            title="旧组",
            bounds=(120.0, 80.0, 1240.0, 520.0),
        )

        new_group_uuid = controller.create_group(
            [second.uuid, third.uuid],
            title="新组",
            bounds=(680.0, 80.0, 1240.0, 520.0),
        )

        self.assertEqual([first.uuid], controller.get_group(old_group_uuid).node_uuids)
        self.assertEqual([second.uuid, third.uuid], controller.get_group(new_group_uuid).node_uuids)
        self.assertEqual({"x": 680.0, "y": 80.0}, controller.get_group(new_group_uuid).ui_position)
        controller.undo_stack.undo()
        self.assertEqual([first.uuid, second.uuid], controller.get_group(old_group_uuid).node_uuids)
        self.assertIsNone(controller.get_group(new_group_uuid))

    def test_move_and_group_membership_change_share_one_undo_step(self) -> None:
        controller = EditorController()
        first = create_node(controller.schema, controller.document, "TouchIdle", (200.0, 160.0))
        second = create_node(controller.schema, controller.document, "TouchIdle", (760.0, 160.0))
        controller.document.nodes.extend([first, second])
        group_uuid = controller.create_group(
            [first.uuid, second.uuid],
            bounds=(120.0, 80.0, 1240.0, 520.0),
        )
        before_index = controller.undo_stack.index()

        controller.move_nodes_with_group_memberships(
            {first.uuid: (1500.0, 900.0)},
            {first.uuid: None},
        )

        self.assertEqual(before_index + 1, controller.undo_stack.index())
        self.assertNotIn(first.uuid, controller.get_group(group_uuid).node_uuids)
        controller.undo_stack.undo()
        self.assertEqual({"x": 200.0, "y": 160.0}, controller.get_node(first.uuid).ui_position)
        self.assertIn(first.uuid, controller.get_group(group_uuid).node_uuids)

    def test_undoing_node_deletion_restores_its_group_membership(self) -> None:
        controller = EditorController()
        first = create_node(controller.schema, controller.document, "TouchIdle", (200.0, 160.0))
        second = create_node(controller.schema, controller.document, "TouchIdle", (760.0, 160.0))
        controller.document.nodes.extend([first, second])
        group_uuid = controller.create_group(
            [first.uuid, second.uuid],
            bounds=(120.0, 80.0, 1240.0, 520.0),
        )

        controller.remove_nodes([first.uuid])
        self.assertNotIn(first.uuid, controller.get_group(group_uuid).node_uuids)
        controller.undo_stack.undo()

        self.assertIsNotNone(controller.get_node(first.uuid))
        self.assertIn(first.uuid, controller.get_group(group_uuid).node_uuids)

    def test_moving_group_frame_and_members_undoes_as_one_action(self) -> None:
        controller = EditorController()
        first = create_node(controller.schema, controller.document, "TouchIdle", (200.0, 160.0))
        second = create_node(controller.schema, controller.document, "TouchIdle", (760.0, 160.0))
        controller.document.nodes.extend([first, second])
        group_uuid = controller.create_group(
            [first.uuid, second.uuid],
            bounds=(120.0, 80.0, 1240.0, 520.0),
        )
        before_index = controller.undo_stack.index()

        controller.move_group_with_nodes(
            group_uuid,
            {first.uuid: (300.0, 260.0), second.uuid: (860.0, 260.0)},
            (220.0, 180.0, 1240.0, 520.0),
        )

        self.assertEqual(before_index + 1, controller.undo_stack.index())
        self.assertEqual({"x": 220.0, "y": 180.0}, controller.get_group(group_uuid).ui_position)
        self.assertEqual({"x": 300.0, "y": 260.0}, controller.get_node(first.uuid).ui_position)
        controller.undo_stack.undo()
        self.assertEqual({"x": 120.0, "y": 80.0}, controller.get_group(group_uuid).ui_position)
        self.assertEqual({"x": 200.0, "y": 160.0}, controller.get_node(first.uuid).ui_position)


class CanvasImagePersistenceTests(unittest.TestCase):
    def test_embedded_canvas_image_roundtrips_inside_document_json(self) -> None:
        schema = get_default_schema()
        document = create_document(schema)
        source = QImage(320, 180, QImage.Format.Format_ARGB32)
        source.fill(0xFF336699)
        data_base64, size = encode_reference_image(source)
        document.canvas_images.append(
            CanvasImageRecord(
                uuid="image-1",
                name="流程截图",
                data_base64=data_base64,
                ui_position={"x": 320.0, "y": 240.0},
                ui_size={"width": size[0], "height": size[1]},
                opacity=0.8,
                locked=True,
            )
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "image.json"
            save_document(schema, document, path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            loaded = load_document(schema, path)

        self.assertEqual(data_base64, payload["canvas_images"][0]["data_base64"])
        self.assertEqual(5, payload["format_version"])
        self.assertEqual(document.canvas_images[0], loaded.canvas_images[0])

    def test_canvas_image_add_remove_and_move_are_undoable(self) -> None:
        controller = EditorController()
        source = QImage(320, 180, QImage.Format.Format_ARGB32)
        source.fill(0xFF884422)
        data_base64, size = encode_reference_image(source)

        image_uuid = controller.add_canvas_image(
            data_base64,
            size,
            (100.0, 80.0),
            name="参考截图",
        )
        self.assertEqual({"x": 100.0, "y": 80.0}, controller.get_canvas_image(image_uuid).ui_position)
        controller.move_canvas_image(image_uuid, (420.0, 260.0))
        self.assertEqual({"x": 420.0, "y": 260.0}, controller.get_canvas_image(image_uuid).ui_position)
        controller.undo_stack.undo()
        self.assertEqual({"x": 100.0, "y": 80.0}, controller.get_canvas_image(image_uuid).ui_position)

        controller.remove_canvas_images([image_uuid])
        self.assertIsNone(controller.get_canvas_image(image_uuid))
        controller.undo_stack.undo()
        self.assertIsNotNone(controller.get_canvas_image(image_uuid))

    def test_canvas_image_resize_is_undoable_and_roundtrips(self) -> None:
        schema = get_default_schema()
        controller = EditorController()
        source = QImage(320, 180, QImage.Format.Format_ARGB32)
        source.fill(0xFF557799)
        data_base64, size = encode_reference_image(source)
        image_uuid = controller.add_canvas_image(data_base64, size, (100.0, 80.0))

        controller.resize_canvas_image(image_uuid, (475.0, 265.0))

        self.assertEqual(
            {"width": 475.0, "height": 265.0},
            controller.get_canvas_image(image_uuid).ui_size,
        )
        controller.undo_stack.undo()
        self.assertEqual(
            {"width": 320.0, "height": 180.0},
            controller.get_canvas_image(image_uuid).ui_size,
        )
        controller.undo_stack.redo()

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "resized-image.json"
            save_document(schema, controller.document, path)
            loaded = load_document(schema, path)

        self.assertEqual(
            {"width": 475.0, "height": 265.0},
            loaded.canvas_images[0].ui_size,
        )

    def test_invalid_embedded_image_is_safely_ignored(self) -> None:
        schema = get_default_schema()
        document = create_document(schema)
        document.canvas_images.append(
            CanvasImageRecord(
                uuid="broken-image",
                data_base64="this-is-not-valid-base64!",
                ui_position={"x": 0.0, "y": 0.0},
                ui_size={"width": 1e300, "height": 1e300},
            )
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "broken-image.json"
            save_document(schema, document, path)
            loaded = load_document(schema, path)

        self.assertEqual([], loaded.canvas_images)

    def test_controller_rejects_invalid_or_nonfinite_reference_image_input(self) -> None:
        controller = EditorController()
        self.assertIsNone(controller.add_canvas_image("broken", (1.0, 1.0), (0.0, 0.0)))
        source = QImage(16, 8, QImage.Format.Format_ARGB32)
        source.fill(0xFF112233)
        data_base64, size = encode_reference_image(source)
        self.assertIsNone(controller.add_canvas_image(data_base64, size, (float("inf"), 0.0)))
        self.assertEqual([], controller.document.canvas_images)
        image_uuid = controller.add_canvas_image(data_base64, size, (12.0, 8.0))
        controller.move_canvas_image(image_uuid, (float("nan"), 20.0))
        self.assertEqual({"x": 12.0, "y": 8.0}, controller.get_canvas_image(image_uuid).ui_position)
        controller.resize_canvas_image(image_uuid, (float("inf"), 20.0))
        self.assertEqual({"width": 16.0, "height": 8.0}, controller.get_canvas_image(image_uuid).ui_size)
        controller.get_canvas_image(image_uuid).locked = True
        controller.resize_canvas_image(image_uuid, (120.0, 60.0))
        self.assertEqual({"width": 16.0, "height": 8.0}, controller.get_canvas_image(image_uuid).ui_size)

    def test_loading_invalid_canvas_image_display_size_falls_back_to_source(self) -> None:
        schema = get_default_schema()
        document = create_document(schema)
        source = QImage(64, 32, QImage.Format.Format_ARGB32)
        source.fill(0xFF224466)
        data_base64, _size = encode_reference_image(source)
        document.canvas_images.append(
            CanvasImageRecord(
                uuid="oversized-image",
                data_base64=data_base64,
                ui_size={"width": 1e300, "height": 1e300},
            )
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "oversized-image.json"
            save_document(schema, document, path)
            loaded = load_document(schema, path)

        self.assertEqual({"width": 64.0, "height": 32.0}, loaded.canvas_images[0].ui_size)

    def test_resizing_display_does_not_consume_embedded_pixel_budget(self) -> None:
        controller = EditorController()
        source = QImage(10, 10, QImage.Format.Format_ARGB32)
        source.fill(0xFF335577)
        data_base64, size = encode_reference_image(source)
        first_uuid = controller.add_canvas_image(data_base64, size, (0.0, 0.0))

        controller.resize_canvas_image(first_uuid, (10_000.0, 10_000.0))
        second_uuid = controller.add_canvas_image(data_base64, size, (20.0, 20.0))

        self.assertIsNotNone(second_uuid)
        self.assertEqual(2, len(controller.document.canvas_images))

    def test_loading_caps_embedded_reference_image_count(self) -> None:
        schema = get_default_schema()
        document = create_document(schema)
        source = QImage(2, 2, QImage.Format.Format_ARGB32)
        source.fill(0xFF445566)
        data_base64, size = encode_reference_image(source)
        document.canvas_images = [
            CanvasImageRecord(
                uuid=f"broken-{index}",
                data_base64="not-base64!",
            )
            for index in range(3)
        ] + [
            CanvasImageRecord(
                uuid=f"image-{index}",
                data_base64=data_base64,
                ui_position={"x": float(index), "y": 0.0},
                ui_size={"width": size[0], "height": size[1]},
            )
            for index in range(40)
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "too-many-images.json"
            save_document(schema, document, path)
            loaded = load_document(schema, path)

        self.assertEqual(32, len(loaded.canvas_images))
        self.assertEqual("image-0", loaded.canvas_images[0].uuid)


class CanvasGroupInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def make_ready_window(self, directory: str) -> MainWindow:
        window = MainWindow(directory, prefer_saved_workspace=False)
        window.controller.document.meta.author = "tester"
        window.controller.document.meta.ship_skin_id = 1
        window.controller.document.meta.memo = "group-test"
        window.controller.document.meta.CharName = "测试"
        window.controller.refresh_derived()
        return window

    def close_window(self, window: MainWindow) -> None:
        window._mark_saved_checkpoint(saved=True)
        window.close()
        self.app.processEvents()

    def test_grouping_selected_nodes_persists_the_exact_canvas_frame(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self.make_ready_window(temp_dir)
            first_uuid = window.controller.create_node("TouchIdle", (260.0, 180.0))
            second_uuid = window.controller.create_node("TouchIdle", (920.0, 300.0))
            window.canvas.node_items[first_uuid].setSelected(True)
            window.canvas.node_items[second_uuid].setSelected(True)

            window._group_selected_nodes()
            self.app.processEvents()

            group = window.controller.document.groups[0]
            group_item = window.canvas.group_items[group.uuid]
            frame = group_item.focus_rect()
            self.assertEqual({"x": frame.x(), "y": frame.y()}, group.ui_position)
            self.assertEqual({"width": frame.width(), "height": frame.height()}, group.ui_size)
            self.close_window(window)

    def test_group_frame_does_not_shrink_when_members_leave_or_become_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self.make_ready_window(temp_dir)
            first_uuid = window.controller.create_node("TouchIdle", (260.0, 180.0))
            second_uuid = window.controller.create_node("TouchIdle", (920.0, 300.0))
            window.canvas.node_items[first_uuid].setSelected(True)
            window.canvas.node_items[second_uuid].setSelected(True)
            window._group_selected_nodes()
            self.app.processEvents()
            group_uuid = window.controller.document.groups[0].uuid
            original_frame = window.canvas.group_items[group_uuid].focus_rect()

            window.controller.set_node_group_memberships({first_uuid: None})
            self.app.processEvents()
            after_first_exit = window.canvas.group_items[group_uuid].focus_rect()
            window.controller.set_node_group_memberships({second_uuid: None})
            self.app.processEvents()

            self.assertEqual(original_frame, after_first_exit)
            self.assertIsNotNone(window.controller.get_group(group_uuid))
            self.assertEqual([], window.controller.get_group(group_uuid).node_uuids)
            self.assertEqual(original_frame, window.canvas.group_items[group_uuid].focus_rect())
            self.close_window(window)

    def test_group_frame_expands_when_a_member_moves_beyond_its_edge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self.make_ready_window(temp_dir)
            first_uuid = window.controller.create_node("TouchIdle", (260.0, 180.0))
            second_uuid = window.controller.create_node("TouchIdle", (920.0, 300.0))
            window.canvas.node_items[first_uuid].setSelected(True)
            window.canvas.node_items[second_uuid].setSelected(True)
            window._group_selected_nodes()
            self.app.processEvents()
            group_uuid = window.controller.document.groups[0].uuid
            original_frame = window.canvas.group_items[group_uuid].focus_rect()
            first = window.controller.get_node(first_uuid)
            old_position = (first.ui_position["x"], first.ui_position["y"])

            window.controller.move_node(
                first_uuid,
                old_position,
                (original_frame.right() + 600.0, original_frame.bottom() + 300.0),
            )
            self.app.processEvents()

            expanded_frame = window.canvas.group_items[group_uuid].focus_rect()
            moved_rect = window.canvas.node_visual_rect(first_uuid)
            self.assertGreater(expanded_frame.width(), original_frame.width())
            self.assertGreater(expanded_frame.height(), original_frame.height())
            self.assertTrue(expanded_frame.contains(moved_rect))
            self.assertIn(first_uuid, window.controller.get_group(group_uuid).node_uuids)

            window.controller.undo_stack.undo()
            self.app.processEvents()
            self.assertEqual(
                original_frame,
                window.canvas.group_items[group_uuid].focus_rect(),
            )
            self.close_window(window)

    def test_fit_group_to_contents_is_explicit_and_undoable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self.make_ready_window(temp_dir)
            first_uuid = window.controller.create_node("TouchIdle", (260.0, 180.0))
            second_uuid = window.controller.create_node("TouchIdle", (920.0, 300.0))
            group_uuid = window.controller.create_group(
                [first_uuid, second_uuid],
                bounds=(0.0, 0.0, 2400.0, 1400.0),
            )
            expected = window.canvas.group_bounds_for_nodes([first_uuid, second_uuid])

            window.canvas.fit_group_to_contents(group_uuid)
            self.app.processEvents()

            fitted = window.canvas.group_items[group_uuid].focus_rect()
            self.assertEqual(expected, fitted)
            window.controller.undo_stack.undo()
            self.app.processEvents()
            self.assertEqual((0.0, 0.0, 2400.0, 1400.0), tuple(window.canvas.group_items[group_uuid].focus_rect().getRect()))
            self.close_window(window)

    def test_empty_persisted_group_is_rendered_as_a_drop_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self.make_ready_window(temp_dir)
            window.controller.document.groups = [
                GroupRecord(
                    uuid="empty",
                    title="空组",
                    ui_position={"x": 300.0, "y": 220.0},
                    ui_size={"width": 700.0, "height": 420.0},
                )
            ]
            window.controller.refresh_derived()
            self.app.processEvents()

            self.assertIn("empty", window.canvas.group_items)
            self.assertEqual(
                (300.0, 220.0, 700.0, 420.0),
                (
                    window.canvas.group_items["empty"].focus_rect().x(),
                    window.canvas.group_items["empty"].focus_rect().y(),
                    window.canvas.group_items["empty"].focus_rect().width(),
                    window.canvas.group_items["empty"].focus_rect().height(),
                ),
            )
            self.close_window(window)

    def test_legacy_group_without_geometry_migrates_from_member_bounds_on_first_render(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self.make_ready_window(temp_dir)
            first_uuid = window.controller.create_node("TouchIdle", (260.0, 180.0))
            second_uuid = window.controller.create_node("TouchIdle", (920.0, 300.0))
            window.controller.document.groups = [
                GroupRecord(uuid="legacy", title="旧组", node_uuids=[first_uuid, second_uuid])
            ]

            window.controller.refresh_derived()
            self.app.processEvents()

            group = window.controller.get_group("legacy")
            frame = window.canvas.group_items["legacy"].focus_rect()
            self.assertEqual({"x": frame.x(), "y": frame.y()}, group.ui_position)
            self.assertEqual({"width": frame.width(), "height": frame.height()}, group.ui_size)
            self.close_window(window)

    def test_node_can_join_group_when_at_least_quarter_of_visual_area_overlaps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self.make_ready_window(temp_dir)
            first_uuid = window.controller.create_node("TouchIdle", (260.0, 180.0))
            second_uuid = window.controller.create_node("TouchIdle", (920.0, 300.0))
            dropped_uuid = window.controller.create_node("TouchIdle", (1900.0, 300.0))
            frame = window.canvas.group_bounds_for_nodes([first_uuid, second_uuid])
            group_uuid = window.controller.create_group(
                [first_uuid, second_uuid],
                bounds=(frame.x(), frame.y(), frame.width(), frame.height()),
            )
            self.app.processEvents()

            dropped_item = window.canvas.node_items[dropped_uuid]
            dropped_rect = window.canvas.node_visual_rect(dropped_uuid)
            rect_offset = dropped_rect.topLeft() - dropped_item.pos()
            group_rect = window.canvas.group_items[group_uuid].focus_rect()
            target_left = group_rect.right() - dropped_rect.width() * 0.30
            target_top = group_rect.center().y() - dropped_rect.height() * 0.5
            dropped_item.setPos(QPointF(target_left, target_top) - rect_offset)
            self.app.processEvents()

            moved_rect = window.canvas.node_visual_rect(dropped_uuid)
            overlap = moved_rect.intersected(group_rect)
            overlap_ratio = overlap.width() * overlap.height() / (moved_rect.width() * moved_rect.height())
            self.assertGreaterEqual(overlap_ratio, 0.25)
            self.assertFalse(group_rect.contains(moved_rect.center()))
            self.assertEqual(group_uuid, window.canvas.group_for_node_drop(dropped_uuid))
            self.close_window(window)

    def test_drop_prefers_highest_overlap_then_smallest_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self.make_ready_window(temp_dir)
            node_uuid = window.controller.create_node("TouchIdle", (500.0, 400.0))
            node_rect = window.canvas.node_visual_rect(node_uuid)
            window.controller.document.groups = [
                GroupRecord(
                    uuid="low-overlap",
                    ui_position={"x": node_rect.left(), "y": node_rect.top()},
                    ui_size={"width": node_rect.width() * 0.30, "height": node_rect.height()},
                ),
                GroupRecord(
                    uuid="high-overlap",
                    ui_position={"x": node_rect.left(), "y": node_rect.top()},
                    ui_size={"width": node_rect.width() * 0.75, "height": node_rect.height()},
                ),
            ]
            window.controller.refresh_derived()
            self.app.processEvents()
            self.assertEqual("high-overlap", window.canvas.group_for_node_drop(node_uuid))

            window.controller.document.groups = [
                GroupRecord(
                    uuid="large",
                    ui_position={"x": node_rect.left() - 200.0, "y": node_rect.top() - 200.0},
                    ui_size={"width": node_rect.width() + 400.0, "height": node_rect.height() + 400.0},
                ),
                GroupRecord(
                    uuid="small",
                    ui_position={"x": node_rect.left(), "y": node_rect.top()},
                    ui_size={"width": node_rect.width(), "height": node_rect.height()},
                ),
            ]
            window.controller.refresh_derived()
            self.app.processEvents()
            self.assertEqual("small", window.canvas.group_for_node_drop(node_uuid))
            self.close_window(window)

    def test_double_clicking_group_title_renames_with_undo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self.make_ready_window(temp_dir)
            first_uuid = window.controller.create_node("TouchIdle", (260.0, 180.0))
            second_uuid = window.controller.create_node("TouchIdle", (920.0, 300.0))
            frame = window.canvas.group_bounds_for_nodes([first_uuid, second_uuid])
            group_uuid = window.controller.create_group(
                [first_uuid, second_uuid],
                title="旧标题",
                bounds=(frame.x(), frame.y(), frame.width(), frame.height()),
            )
            window.show()
            self.app.processEvents()
            group_item = window.canvas.group_items[group_uuid]
            window.canvas.centerOn(group_item)
            self.app.processEvents()
            title_point = window.canvas.mapFromScene(group_item.mapToScene(group_item._title_rect.center()))

            QTest.mouseDClick(
                window.canvas.viewport(),
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                title_point,
            )
            self.app.processEvents()
            editor = group_item._title_editor_proxy.widget()
            editor.setText("新标题")
            group_item.commit_pending_title_edit()
            self.app.processEvents()

            self.assertEqual("新标题", window.controller.get_group(group_uuid).title)
            window.controller.undo_stack.undo()
            self.assertEqual("旧标题", window.controller.get_group(group_uuid).title)
            self.close_window(window)

    def test_canvas_accepts_large_reference_image_with_bounded_embedded_size_and_undo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self.make_ready_window(temp_dir)
            source = QImage(5000, 100, QImage.Format.Format_ARGB32)
            source.fill(0xFF336699)

            image_uuid = window.canvas.add_reference_image(source, name="长截图")
            self.app.processEvents()

            record = window.controller.get_canvas_image(image_uuid)
            self.assertLessEqual(max(record.ui_size.values()), 4096.0)
            self.assertLessEqual(record.ui_size["width"] * record.ui_size["height"], 16_000_000)
            self.assertIn(image_uuid, window.canvas.image_items)
            self.assertEqual(QGraphicsItem.CacheMode.NoCache, window.canvas.image_items[image_uuid].cacheMode())
            window.controller.undo_stack.undo()
            self.app.processEvents()
            self.assertNotIn(image_uuid, window.canvas.image_items)
            self.close_window(window)

    def test_ctrl_v_on_canvas_pastes_clipboard_image_as_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self.make_ready_window(temp_dir)
            source = QImage(320, 180, QImage.Format.Format_ARGB32)
            source.fill(0xFF884422)
            QGuiApplication.clipboard().setImage(source)
            window.canvas.setFocus()
            self.app.processEvents()

            window._paste_selection()
            self.app.processEvents()

            self.assertEqual(1, len(window.controller.document.canvas_images))
            image_uuid = window.controller.document.canvas_images[0].uuid
            self.assertIn(image_uuid, window.canvas.image_items)
            self.close_window(window)

    def test_reference_image_handle_resizes_width_and_height_independently(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self.make_ready_window(temp_dir)
            source = QImage(240, 120, QImage.Format.Format_ARGB32)
            source.fill(0xFF446688)
            image_uuid = window.canvas.add_reference_image(source, position=(5000.0, 5000.0))
            window.show()
            self.app.processEvents()
            item = window.canvas.image_items[image_uuid]
            item.setSelected(True)
            window.canvas.centerOn(item)
            self.app.processEvents()
            position_before = QPointF(item.pos())
            undo_index_before = window.controller.undo_stack.index()
            handle_scene = item.mapToScene(item.resize_handle_rect().center())
            handle_center = window.canvas.mapFromScene(handle_scene)
            intermediate_one = window.canvas.mapFromScene(handle_scene + QPointF(30.0, 20.0))
            intermediate_two = window.canvas.mapFromScene(handle_scene + QPointF(60.0, 35.0))
            drag_end = window.canvas.mapFromScene(handle_scene + QPointF(90.0, 55.0))

            QTest.mousePress(
                window.canvas.viewport(),
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                handle_center,
            )
            QTest.mouseMove(window.canvas.viewport(), intermediate_one, delay=10)
            QTest.mouseMove(window.canvas.viewport(), intermediate_two, delay=10)
            QTest.mouseMove(window.canvas.viewport(), drag_end, delay=10)
            QTest.mouseRelease(
                window.canvas.viewport(),
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                drag_end,
            )
            self.app.processEvents()

            record = window.controller.get_canvas_image(image_uuid)
            self.assertAlmostEqual(330.0, record.ui_size["width"], delta=2.0)
            self.assertAlmostEqual(175.0, record.ui_size["height"], delta=2.0)
            self.assertEqual(position_before, window.canvas.image_items[image_uuid].pos())
            self.assertEqual(undo_index_before + 1, window.controller.undo_stack.index())
            self.assertFalse(window.canvas.is_busy())
            window.controller.undo_stack.undo()
            self.app.processEvents()
            restored = window.controller.get_canvas_image(image_uuid)
            self.assertEqual({"width": 240.0, "height": 120.0}, restored.ui_size)
            window.controller.undo_stack.redo()
            self.app.processEvents()
            resized = window.controller.get_canvas_image(image_uuid)
            self.assertAlmostEqual(330.0, resized.ui_size["width"], delta=2.0)
            self.assertAlmostEqual(175.0, resized.ui_size["height"], delta=2.0)
            self.assertTrue(window.canvas.image_items[image_uuid].isSelected())
            self.close_window(window)

    def test_unselected_reference_image_paints_persistent_outline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self.make_ready_window(temp_dir)
            source = QImage(80, 40, QImage.Format.Format_ARGB32)
            source.fill(Qt.GlobalColor.transparent)
            image_uuid = window.canvas.add_reference_image(source, position=(0.0, 0.0))
            item = window.canvas.image_items[image_uuid]
            window.canvas.scene_ref.clearSelection()
            self.assertFalse(item.isSelected())
            rendered = QImage(100, 60, QImage.Format.Format_ARGB32_Premultiplied)
            rendered.fill(Qt.GlobalColor.transparent)
            painter = QPainter(rendered)
            painter.translate(10.0, 10.0)
            item.paint(painter, None)
            painter.end()

            def edge_has_ink(xs, ys) -> bool:
                return any(
                    rendered.pixelColor(x, y).alpha() > 0
                    for y in ys
                    for x in xs
                )

            self.assertTrue(edge_has_ink(range(9, 92), range(8, 13)))
            self.assertTrue(edge_has_ink(range(9, 92), range(48, 53)))
            self.assertTrue(edge_has_ink(range(8, 13), range(9, 52)))
            self.assertTrue(edge_has_ink(range(88, 93), range(9, 52)))
            self.close_window(window)

    def test_interrupted_reference_image_resize_restores_geometry_and_busy_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self.make_ready_window(temp_dir)
            source = QImage(240, 120, QImage.Format.Format_ARGB32)
            source.fill(0xFF335577)
            image_uuid = window.canvas.add_reference_image(
                source,
                position=(5000.0, 5000.0),
            )
            window.show()
            self.app.processEvents()
            item = window.canvas.image_items[image_uuid]
            window.canvas.centerOn(item)
            self.app.processEvents()
            handle_center = window.canvas.mapFromScene(
                item.mapToScene(item.resize_handle_rect().center())
            )
            undo_index_before = window.controller.undo_stack.index()

            QTest.mousePress(
                window.canvas.viewport(),
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                handle_center,
            )
            self.assertTrue(item._resizing)
            self.assertTrue(window.canvas.is_busy())
            self.assertIs(item, window.canvas.scene_ref.mouseGrabberItem())
            item.ungrabMouse()
            self.app.processEvents()

            self.assertFalse(item._resizing)
            self.assertFalse(window.canvas.is_busy())
            self.assertEqual((240.0, 120.0), (item._rect.width(), item._rect.height()))
            self.assertEqual(undo_index_before, window.controller.undo_stack.index())
            self.close_window(window)

    def test_node_clipboard_payload_wins_when_clipboard_also_contains_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self.make_ready_window(temp_dir)
            source_uuid = window.controller.create_node("TouchIdle", (260.0, 180.0))
            payload = window.controller.serialize_selection([source_uuid])
            screenshot = QImage(200, 100, QImage.Format.Format_ARGB32)
            screenshot.fill(0xFF663322)
            mime = QMimeData()
            mime.setData(window.controller.clipboard_mime(), payload)
            mime.setImageData(screenshot)
            QGuiApplication.clipboard().setMimeData(mime)
            window.canvas.setFocus()
            before_nodes = len(window.controller.document.nodes)

            window._paste_selection()
            self.app.processEvents()

            self.assertEqual(before_nodes + 1, len(window.controller.document.nodes))
            self.assertEqual([], window.controller.document.canvas_images)
            self.close_window(window)

    def test_malformed_node_clipboard_payload_falls_back_to_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self.make_ready_window(temp_dir)
            screenshot = QImage(200, 100, QImage.Format.Format_ARGB32)
            screenshot.fill(0xFF335577)
            mime = QMimeData()
            mime.setData(window.controller.clipboard_mime(), b"{not valid json")
            mime.setImageData(screenshot)
            QGuiApplication.clipboard().setMimeData(mime)
            window.canvas.setFocus()

            window._paste_selection()
            self.app.processEvents()

            self.assertEqual(1, len(window.controller.document.canvas_images))
            self.close_window(window)

    def test_structurally_invalid_node_clipboard_payloads_fall_back_to_image(self) -> None:
        for payload in (b"null", b"[]", b'{"nodes":[null]}'):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as temp_dir:
                window = self.make_ready_window(temp_dir)
                screenshot = QImage(120, 60, QImage.Format.Format_ARGB32)
                screenshot.fill(0xFF224466)
                mime = QMimeData()
                mime.setData(window.controller.clipboard_mime(), payload)
                mime.setImageData(screenshot)
                QGuiApplication.clipboard().setMimeData(mime)
                window.canvas.setFocus()

                window._paste_selection()
                self.app.processEvents()

                self.assertEqual(1, len(window.controller.document.canvas_images))
                self.close_window(window)

    def test_delete_selected_reference_image_and_undo_restores_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self.make_ready_window(temp_dir)
            source = QImage(320, 180, QImage.Format.Format_ARGB32)
            source.fill(0xFF224488)
            image_uuid = window.canvas.add_reference_image(source)
            window.canvas.image_items[image_uuid].setSelected(True)

            window._delete_selection()
            self.app.processEvents()
            self.assertIsNone(window.controller.get_canvas_image(image_uuid))
            window.controller.undo_stack.undo()
            self.app.processEvents()
            self.assertIn(image_uuid, window.canvas.image_items)
            self.close_window(window)

    def test_copying_only_reference_image_writes_qimage_to_system_clipboard(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self.make_ready_window(temp_dir)
            source = QImage(240, 120, QImage.Format.Format_ARGB32)
            source.fill(0xFF225577)
            image_uuid = window.canvas.add_reference_image(source)
            window.canvas.scene_ref.clearSelection()
            window.canvas.image_items[image_uuid].setSelected(True)
            QGuiApplication.clipboard().clear()

            window._copy_selection()
            copied = QGuiApplication.clipboard().image()

            self.assertFalse(copied.isNull())
            self.assertEqual((240, 120), (copied.width(), copied.height()))
            self.close_window(window)

    def test_add_reference_image_file_action_embeds_selected_png(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "流程参考.png"
            source = QImage(360, 200, QImage.Format.Format_ARGB32)
            source.fill(0xFF557733)
            self.assertTrue(source.save(str(image_path), "PNG"))
            window = self.make_ready_window(temp_dir)

            with patch.object(
                QFileDialog,
                "getOpenFileName",
                return_value=(str(image_path), "Images (*.png)"),
            ):
                window._add_reference_image_from_file()
                self.app.processEvents()

            self.assertEqual(1, len(window.controller.document.canvas_images))
            self.assertEqual("流程参考.png", window.controller.document.canvas_images[0].name)
            # Keep the PySide6-owned menu wrapper alive explicitly; querying it
            # back through QAction.menu() after monkey-patching QFileDialog can
            # produce an invalid transient wrapper on Windows.
            tool_actions = [action.text() for action in window.tools_menu.actions()]
            self.assertIn("添加参考图…", tool_actions)
            self.close_window(window)

    def test_reset_view_layout_includes_reference_images_outside_node_area(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self.make_ready_window(temp_dir)
            source = QImage(400, 240, QImage.Format.Format_ARGB32)
            source.fill(0xFF334455)
            window.canvas.add_reference_image(source, position=(5000.0, 5000.0))

            window.canvas.reset_view_layout()
            center = window.canvas.mapToScene(window.canvas.viewport().rect().center())

            self.assertGreater(center.x(), 2000.0)
            self.assertGreater(center.y(), 2000.0)
            self.close_window(window)


if __name__ == "__main__":
    unittest.main()
