from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from l2d_config_editor.controller import EditorController
from l2d_config_editor.logic import (
    EDITOR_DOCUMENT_FORMAT_VERSION,
    create_document,
    create_node,
    export_document_dict,
    get_default_schema,
    load_document,
    save_document,
)
from l2d_config_editor.models import CanvasStrokeRecord


def _make_ready(controller: EditorController) -> None:
    controller.document.meta.author = "test"
    controller.document.meta.ship_skin_id = 302291
    controller.document.meta.memo = "test"
    controller.document.meta.CharName = "test"
    controller.refresh_derived()


class ParameterTriggerPersistenceTests(unittest.TestCase):
    def test_simple_edit_marks_parameter_manual_and_undo_restores_automatic_state(self) -> None:
        controller = EditorController()
        _make_ready(controller)
        node_uuid = controller.create_node("ParameterTrigger", (220.0, 120.0))
        node = controller.get_node(node_uuid)

        controller.update_field(node_uuid, "parameter", "touch_drag11", source_mode="simple")
        self.assertEqual("touch_drag11", node.fields["parameter"])
        self.assertIn("parameter", node.manual_fields)

        controller.undo_stack.undo()
        self.assertEqual("touch_drag1", node.fields["parameter"])
        self.assertNotIn("parameter", node.manual_fields)
        controller.undo_stack.redo()
        self.assertEqual("touch_drag11", node.fields["parameter"])
        self.assertIn("parameter", node.manual_fields)

    def test_custom_numbered_parameter_remains_manual_across_save_and_linkage(self) -> None:
        controller = EditorController()
        _make_ready(controller)
        node_uuid = controller.create_node("ParameterTrigger", (220.0, 120.0))
        node = controller.get_node(node_uuid)
        self.assertIsNotNone(node)
        self.assertEqual("touch_drag1", node.fields["parameter"])

        controller.update_field(node_uuid, "parameter", "touch_drag11", source_mode="advanced")
        self.assertEqual("touch_drag11", node.fields["parameter"])
        self.assertIn("parameter", node.manual_fields)

        controller.set_numeric_linkage_enabled(True)
        self.assertEqual("touch_drag11", node.fields["parameter"])
        self.assertIn("parameter", node.manual_fields)

        controller.undo_stack.undo()
        self.assertEqual("touch_drag11", node.fields["parameter"])
        controller.undo_stack.undo()
        self.assertEqual("touch_drag1", node.fields["parameter"])
        self.assertNotIn("parameter", node.manual_fields)
        controller.undo_stack.redo()
        self.assertEqual("touch_drag11", node.fields["parameter"])
        self.assertIn("parameter", node.manual_fields)

        controller.update_field(
            node_uuid,
            "parameter",
            "touch_drag12",
            source_mode="simple",
        )
        self.assertEqual("touch_drag12", node.fields["parameter"])
        controller.undo_stack.undo()
        self.assertEqual("touch_drag11", node.fields["parameter"])
        self.assertIn("parameter", node.manual_fields)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "parameter.json"
            controller.save_document(str(path))
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw_node = next(item for item in raw["nodes"] if item["uuid"] == node_uuid)
            self.assertEqual("touch_drag11", raw_node["parameter"])
            self.assertIn("parameter", raw_node["manual_fields"])
            loaded = load_document(controller.schema, path)

        loaded_node = next(item for item in loaded.nodes if item.uuid == node_uuid)
        self.assertEqual("touch_drag11", loaded_node.fields["parameter"])
        self.assertIn("parameter", loaded_node.manual_fields)


class CanvasStrokePersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = get_default_schema()

    def test_strokes_round_trip_in_format_three(self) -> None:
        document = create_document(self.schema)
        document.canvas_strokes.append(
            CanvasStrokeRecord(
                uuid="stroke-1",
                points=[(120.0, 80.0), (124.5, 84.25)],
                color="#2F80ED",
                width=4.0,
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "strokes.json"
            save_document(self.schema, document, path)
            raw = json.loads(path.read_text(encoding="utf-8"))
            loaded = load_document(self.schema, path)

        self.assertEqual(3, EDITOR_DOCUMENT_FORMAT_VERSION)
        self.assertEqual(3, raw["format_version"])
        self.assertEqual(
            {
                "id": "stroke-1",
                "points": [[120.0, 80.0], [124.5, 84.25]],
                "color": "#2F80ED",
                "width": 4.0,
            },
            raw["canvas_strokes"][0],
        )
        self.assertEqual(document.canvas_strokes, loaded.canvas_strokes)

    def test_controller_add_remove_stroke_is_undoable(self) -> None:
        controller = EditorController()
        stroke_uuid = controller.add_canvas_stroke(
            [(0.0, 0.0), (10.0, 12.0)],
            color="#2F80ED",
            width=4.0,
        )
        self.assertEqual(stroke_uuid, controller.document.canvas_strokes[0].uuid)
        controller.remove_canvas_strokes([stroke_uuid])
        self.assertEqual([], controller.document.canvas_strokes)
        controller.undo_stack.undo()
        self.assertEqual(stroke_uuid, controller.document.canvas_strokes[0].uuid)
        controller.undo_stack.undo()
        self.assertEqual([], controller.document.canvas_strokes)
        controller.undo_stack.redo()
        self.assertEqual(stroke_uuid, controller.document.canvas_strokes[0].uuid)

    def test_controller_rejects_stroke_before_document_budget_breaks_save(self) -> None:
        controller = EditorController()
        controller.document.canvas_strokes = [
            CanvasStrokeRecord(
                uuid="existing",
                points=[(0.0, 0.0), (1.0, 1.0)],
                color="#2F80ED",
                width=4.0,
            )
        ]
        with patch(
            "l2d_config_editor.controller.validate_canvas_strokes",
            side_effect=ValueError("document stroke limit"),
        ):
            result = controller.add_canvas_stroke(
                [(2.0, 2.0), (3.0, 3.0)],
                color="#2F80ED",
                width=4.0,
            )
        self.assertIsNone(result)
        self.assertEqual(["existing"], [stroke.uuid for stroke in controller.document.canvas_strokes])
        self.assertEqual(0, controller.undo_stack.count())

    def test_invalid_strokes_are_rejected_instead_of_silently_normalized(self) -> None:
        invalid_strokes = (
            {"uuid": "one-point", "points": [[0, 0]], "color": "#2F80ED", "width": 4},
            {"uuid": "nan", "points": [[0, 0], ["NaN", 1]], "color": "#2F80ED", "width": 4},
            {"uuid": "color", "points": [[0, 0], [1, 1]], "color": "blue", "width": 4},
            {"uuid": "width", "points": [[0, 0], [1, 1]], "color": "#2F80ED", "width": 0},
        )
        for stroke in invalid_strokes:
            with self.subTest(stroke=stroke["uuid"]), tempfile.TemporaryDirectory() as tmp:
                payload = export_document_dict(self.schema, create_document(self.schema))
                payload["canvas_strokes"] = [stroke]
                path = Path(tmp) / "invalid.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_document(self.schema, path)

    def test_v1_and_v2_load_without_strokes_and_future_version_is_rejected(self) -> None:
        payload = export_document_dict(self.schema, create_document(self.schema))
        payload.pop("canvas_strokes")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "version.json"
            for version in (None, 1, 2):
                with self.subTest(version=version):
                    if version is None:
                        payload.pop("format_version", None)
                    else:
                        payload["format_version"] = version
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    self.assertEqual([], load_document(self.schema, path).canvas_strokes)

            payload["format_version"] = EDITOR_DOCUMENT_FORMAT_VERSION + 1
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "format|版本"):
                load_document(self.schema, path)


class AtomicSaveAndLegacyTrashTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = get_default_schema()

    def test_failed_replace_preserves_existing_file_and_cleans_temp(self) -> None:
        document = create_document(self.schema)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "document.json"
            path.write_text("original", encoding="utf-8")
            with patch("l2d_config_editor.logic.os.replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    save_document(self.schema, document, path)
            self.assertEqual("original", path.read_text(encoding="utf-8"))
            self.assertEqual([], list(root.glob(".document.json.*.tmp")))

    def test_save_refuses_to_overwrite_future_editor_document(self) -> None:
        document = create_document(self.schema)
        payload = export_document_dict(self.schema, create_document(self.schema))
        payload["format_version"] = EDITOR_DOCUMENT_FORMAT_VERSION + 1
        original = json.dumps(payload, ensure_ascii=False, indent=2)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "future.json"
            path.write_text(original, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "newer|format"):
                save_document(self.schema, document, path)

            self.assertEqual(original, path.read_text(encoding="utf-8"))
            self.assertIsNone(document.path)
            self.assertEqual([], list(path.parent.glob(".future.json.*.tmp")))

    def test_save_can_replace_non_editor_or_malformed_json(self) -> None:
        for original in ("not json", '{"other": true}'):
            with self.subTest(original=original), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "ordinary.json"
                path.write_text(original, encoding="utf-8")

                save_document(self.schema, create_document(self.schema), path)

                saved = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual("l2d_config_editor/v1", saved["editor_signature"])

    def test_legacy_trash_is_ignored_and_removed_slot_is_immediately_reused(self) -> None:
        payload = export_document_dict(self.schema, create_document(self.schema))
        payload["format_version"] = 2
        payload["editor_settings"]["trash_enabled"] = True
        payload["trash_bin"] = [
            {
                "entry_id": "old",
                "node_uuid": "deleted",
                "node_type": "TouchIdle",
                "title": "deleted",
                "type_slot": 1,
                "export_slot": 1,
                "reserved_fields": {},
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy-trash.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            loaded = load_document(self.schema, path)
            exported = export_document_dict(self.schema, loaded)

        self.assertNotIn("trash_bin", exported)
        self.assertNotIn("trash_enabled", exported["editor_settings"])
        legacy_created = create_node(self.schema, loaded, "TouchIdle")
        self.assertEqual(1, legacy_created.type_slot)

        controller = EditorController()
        _make_ready(controller)
        first_uuid = controller.create_node("TouchIdle", (100.0, 100.0))
        first_slot = controller.get_node(first_uuid).type_slot
        controller.remove_nodes([first_uuid])
        second_uuid = controller.create_node("TouchIdle", (200.0, 100.0))
        self.assertEqual(first_slot, controller.get_node(second_uuid).type_slot)


if __name__ == "__main__":
    unittest.main()
