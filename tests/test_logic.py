import os
import sys
import tempfile
import unittest
from pathlib import Path

if sys.platform != "win32":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from l2d_config_editor.controller import EditorController
from l2d_config_editor.logic import (
    apply_auto_rules,
    create_document,
    create_node,
    document_to_csv_rows,
    export_document_dict,
    get_default_schema,
    load_document,
    reassign_function_ids,
    save_document,
    search_document,
    validate_document,
)
from l2d_config_editor.main_window import MainWindow
from l2d_config_editor.models import ConnectionRecord


class LogicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.schema = get_default_schema()

    def make_ready_document(self):
        document = create_document(self.schema)
        initial = next(node for node in document.nodes if node.type == "Initial")
        initial.fields["author"] = "asahi"
        initial.fields["ship_skin_id"] = 302291
        initial.fields["memo"] = "mingji_2"
        reassign_function_ids(self.schema, document)
        return document

    def test_schema_loads(self) -> None:
        self.assertIn("TouchIdle", self.schema.nodes)
        self.assertGreater(len(self.schema.csv_columns), 10)

    def test_default_document_contains_initial_and_gate(self) -> None:
        document = create_document(self.schema)
        self.assertEqual(1, len([node for node in document.nodes if node.type == "Initial"]))
        self.assertFalse(document.state.is_meta_ready)
        self.assertIn("作者", document.state.meta_missing_fields)

    def test_new_touchidle_defaults_use_sequence(self) -> None:
        document = self.make_ready_document()
        node = create_node(self.schema, document, "TouchIdle")
        self.assertEqual("TouchIdle1", node.fields["draw_able_name"])
        self.assertEqual(1, node.fields["target_idle"])
        self.assertEqual("Paramtouch_idle1", node.fields["parameter"])
        self.assertIn("touch_idle1", node.fields["action_trigger"])

    def test_new_touchdrag_defaults_use_sequence(self) -> None:
        document = self.make_ready_document()
        node = create_node(self.schema, document, "TouchDrag")
        self.assertEqual("TouchDrag1", node.fields["draw_able_name"])
        self.assertEqual(1, node.fields["target_idle"])
        self.assertEqual("Touch_drag1", node.fields["parameter"])
        self.assertIn("touch_idle1", node.fields["action_trigger"])

    def test_touchidle_target_idle_regenerates_simple_fields(self) -> None:
        document = self.make_ready_document()
        node = create_node(self.schema, document, "TouchIdle")
        node.fields["target_idle"] = 13
        apply_auto_rules(self.schema, document, node, source_mode="simple", changed_key="target_idle")
        self.assertEqual("Paramtouch_idle13", node.fields["parameter"])
        self.assertIn("touch_idle13", node.fields["action_trigger"])
        self.assertIn("idle = 13", node.fields["action_trigger_active"])

    def test_touchidle_hard_cut_uses_required_templates(self) -> None:
        document = self.make_ready_document()
        node = create_node(self.schema, document, "TouchIdle")
        node.fields["target_idle"] = 7
        node.fields["transition_type"] = "hard"
        apply_auto_rules(self.schema, document, node, source_mode="simple", changed_key="transition_type")
        self.assertEqual("{type = 2 ,action = 'idle',target = 1}", node.fields["action_trigger"])
        self.assertIn("idle = 7", node.fields["action_trigger_active"])
        self.assertIn("idle_focus = 1", node.fields["action_trigger_active"])
        self.assertNotIn("'main_4'", node.fields["action_trigger_active"])

    def test_drag_direction_updates_offsets(self) -> None:
        document = self.make_ready_document()
        node = create_node(self.schema, document, "TouchDrag")
        node.fields["control_type"] = "drag"
        node.fields["drag_ui_direction"] = "left"
        apply_auto_rules(self.schema, document, node, source_mode="simple", changed_key="drag_ui_direction")
        self.assertEqual(-100, node.fields["offset_x"])
        self.assertEqual(0, node.fields["offset_y"])
        self.assertEqual(1, node.fields["drag_direct"])

    def test_validation_catches_duplicate_parameter_with_related_titles(self) -> None:
        document = self.make_ready_document()
        first = create_node(self.schema, document, "TouchIdle")
        document.nodes.append(first)
        second = create_node(self.schema, document, "TouchDrag")
        second.fields["parameter"] = first.fields["parameter"]
        second.manual_fields.add("parameter")
        document.nodes.append(second)
        issues = validate_document(self.schema, document)
        duplicate_issues = [issue for issue in issues if issue.message == "parameter 重复"]
        self.assertTrue(duplicate_issues)
        self.assertTrue(any(issue.related_titles for issue in duplicate_issues))

    def test_validation_catches_invalid_parts_data(self) -> None:
        document = self.make_ready_document()
        node = create_node(self.schema, document, "TouchDrag")
        node.fields["parts_data"] = "1,hello"
        document.nodes.append(node)
        issues = validate_document(self.schema, document)
        self.assertTrue(any(issue.message == "parts_data 不是合法的逗号分隔数字列表" for issue in issues))

    def test_validation_catches_parts_out_of_range(self) -> None:
        document = self.make_ready_document()
        node = create_node(self.schema, document, "TouchDrag")
        node.fields["parts_data"] = "2"
        node.fields["range"] = "{0,1}"
        document.nodes.append(node)
        issues = validate_document(self.schema, document)
        self.assertTrue(any(issue.message == "parts_data 超出了 range 定义范围" for issue in issues))

    def test_export_roundtrip_preserves_connections(self) -> None:
        document = self.make_ready_document()
        idle = create_node(self.schema, document, "TouchIdle", (100, 120))
        drag = create_node(self.schema, document, "TouchDrag", (220, 120))
        document.nodes.extend([idle, drag])
        document.connections.append(ConnectionRecord(from_uuid=idle.uuid, to_uuid=drag.uuid))
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.json"
            save_document(self.schema, document, path)
            loaded = load_document(self.schema, path)
        self.assertEqual(1, len(loaded.connections))
        self.assertEqual(idle.uuid, loaded.connections[0].from_uuid)
        self.assertEqual(drag.uuid, loaded.connections[0].to_uuid)

    def test_csv_preview_uses_meta_desc(self) -> None:
        document = self.make_ready_document()
        initial = next(node for node in document.nodes if node.type == "Initial")
        initial.fields["CharName"] = "名取"
        node = create_node(self.schema, document, "TouchIdle")
        document.nodes.append(node)
        rows = document_to_csv_rows(self.schema, document)
        self.assertEqual("mingji_2", rows[0].values["desc"])
        self.assertEqual(302291, rows[0].values["ship_skin_id"])

    def test_search_hits_target_idle(self) -> None:
        document = self.make_ready_document()
        node = create_node(self.schema, document, "TouchIdle")
        node.fields["target_idle"] = 13
        document.nodes.append(node)
        hits = search_document(self.schema, document, "13")
        self.assertTrue(any(hit.field_name == "target_idle" for hit in hits))

    def test_export_payload_contains_meta_and_nodes(self) -> None:
        document = self.make_ready_document()
        document.nodes.append(create_node(self.schema, document, "Comment"))
        payload = export_document_dict(self.schema, document)
        self.assertIn("meta", payload)
        self.assertIn("nodes", payload)
        self.assertIn("canvas_view", payload)


class ControllerAndGuiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_controller_blocks_creation_until_meta_ready(self) -> None:
        controller = EditorController()
        result = controller.create_node("TouchIdle", (100, 100))
        self.assertIsNone(result)
        self.assertEqual(1, len(controller.document.nodes))

    def test_main_window_smoke(self) -> None:
        window = MainWindow("/Users/asahi/Live2dConfigGen")
        initial = next(node for node in window.controller.document.nodes if node.type == "Initial")
        window.controller.update_field(initial.uuid, "author", "asahi", "simple")
        window.controller.update_field(initial.uuid, "ship_skin_id", 302291, "simple")
        window.controller.update_field(initial.uuid, "memo", "mingji_2", "simple")
        created = window.controller.create_node("TouchIdle", (200, 120))
        self.assertIsNotNone(created)
        window.controller.set_global_mode("advanced")
        self.assertEqual("advanced", window.controller.preferences.global_mode)
        window._show_csv_preview()
        self.assertTrue(window.csv_dialog.isVisible())
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "gui_smoke.json"
            saved = window.controller.save_document(str(path))
            self.assertEqual(str(path), saved)
            self.assertTrue(path.exists())
        window.close()


if __name__ == "__main__":
    unittest.main()
