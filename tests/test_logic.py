import os
import sys
import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

if sys.platform != "win32":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPointF, QSettings, Qt
from PyQt6.QtWidgets import QApplication, QMessageBox

from l2d_config_editor.controller import EditorController
from l2d_config_editor.logic import (
    apply_auto_rules,
    build_template_version_folder_name,
    create_document,
    create_node,
    create_template_document,
    display_value_for_field,
    document_to_csv_rows,
    export_document_dict,
    get_default_schema,
    load_document,
    load_template_csv_rows,
    normalize_field_input,
    node_title,
    reassign_function_ids,
    save_document,
    search_document,
    validate_document,
)
from l2d_config_editor.main_window import MainWindow
from l2d_config_editor.models import ConnectionRecord
from l2d_config_editor.schema import load_editor_schema
from l2d_config_editor.widgets import NodeFormWidget


class LogicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.schema = get_default_schema()

    def make_ready_document(self):
        document = create_document(self.schema)
        document.editor_settings.numeric_linkage_enabled = True
        initial = next(node for node in document.nodes if node.type == "Initial")
        initial.fields["author"] = "asahi"
        initial.fields["ship_skin_id"] = 302291
        initial.fields["memo"] = "mingji_2"
        initial.fields["CharName"] = "??"
        reassign_function_ids(self.schema, document)
        return document

    def test_schema_loads(self) -> None:
        self.assertIn("TouchIdle", self.schema.nodes)
        self.assertGreater(len(self.schema.csv_columns), 10)

    def test_default_document_contains_initial_and_gate(self) -> None:
        document = create_document(self.schema)
        self.assertEqual(1, len([node for node in document.nodes if node.type == "Initial"]))
        self.assertEqual("simple", document.global_mode)
        self.assertFalse(document.editor_settings.numeric_linkage_enabled)
        self.assertFalse(document.editor_settings.trash_enabled)
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
        self.assertEqual("touch_drag1", node.fields["parameter"])
        self.assertIn("touch_drag1", node.fields["action_trigger"])

    def test_touchidle_target_idle_regenerates_simple_fields(self) -> None:
        document = self.make_ready_document()
        node = create_node(self.schema, document, "TouchIdle")
        node.fields["target_idle"] = 13
        apply_auto_rules(self.schema, document, node, source_mode="simple", changed_key="target_idle")
        self.assertEqual("TouchIdle13", node.fields["draw_able_name"])
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

    def test_validation_reports_duplicate_parameter_values(self) -> None:
        document = self.make_ready_document()
        first = create_node(self.schema, document, "TouchIdle")
        document.nodes.append(first)
        second = create_node(self.schema, document, "TouchDrag")
        second.fields["parameter"] = first.fields["parameter"]
        second.manual_fields.add("parameter")
        document.nodes.append(second)
        issues = validate_document(self.schema, document)
        duplicate_issues = [issue for issue in issues if issue.field_keys == ["parameter"] and "重复" in issue.message]
        self.assertEqual(2, len(duplicate_issues))

    def test_validation_messages_no_longer_contain_question_marks(self) -> None:
        document = self.make_ready_document()
        first = create_node(self.schema, document, "TouchIdle")
        document.nodes.append(first)
        second = create_node(self.schema, document, "TouchIdle")
        second.fields["draw_able_name"] = first.fields["draw_able_name"]
        second.fields["action_trigger"] = "{type = 2 ,action = 'touch_idle99'}"
        second.fields["action_trigger_active"] = "{enable = {},ignore = {'main_1'},idle = 99}"
        document.nodes.append(second)
        issues = validate_document(self.schema, document)
        self.assertFalse(issues)

    def test_numeric_linkage_disabled_keeps_creation_defaults(self) -> None:
        document = self.make_ready_document()
        document.editor_settings.numeric_linkage_enabled = False
        node = create_node(self.schema, document, "TouchIdle")
        self.assertEqual("TouchIdle1", node.fields["draw_able_name"])
        self.assertEqual("Paramtouch_idle1", node.fields["parameter"])
        self.assertIn("touch_idle1", node.fields["action_trigger"])
        self.assertIn("idle = 1", node.fields["action_trigger_active"])

    def test_numeric_linkage_disabled_does_not_sync_simple_fields(self) -> None:
        document = self.make_ready_document()
        document.editor_settings.numeric_linkage_enabled = False
        node = create_node(self.schema, document, "TouchDrag")
        node.fields["parameter"] = "manual_parameter"
        node.fields["action_trigger"] = "{type = 2 ,action = 'manual_action'}"
        node.fields["action_trigger_active"] = "{enable = {},idle = 1}"
        node.fields["target_idle"] = 9
        apply_auto_rules(self.schema, document, node, source_mode="simple", changed_key="target_idle")
        self.assertEqual("manual_parameter", node.fields["parameter"])
        self.assertEqual("{type = 2 ,action = 'manual_action'}", node.fields["action_trigger"])
        self.assertEqual("{enable = {},idle = 1}", node.fields["action_trigger_active"])

    def test_parameter_trigger_defaults_to_value_mode(self) -> None:
        document = self.make_ready_document()
        node = create_node(self.schema, document, "ParameterTrigger")
        self.assertEqual("TouchDrag1", node.fields["draw_able_name"])
        self.assertEqual("value", node.fields["result_type"])
        self.assertEqual("", node.fields["action_trigger"])
        self.assertEqual("", node.fields["action_trigger_active"])

    def test_manual_mode_touchdrag_defaults_use_unsuffixed_values(self) -> None:
        document = self.make_ready_document()
        document.interaction_creation_mode = "manual"
        node = create_node(self.schema, document, "TouchDrag")
        self.assertEqual("TouchDrag", node.fields["draw_able_name"])
        self.assertEqual("touch_drag", node.fields["parameter"])
        self.assertEqual(0, node.fields["target_idle"])
        self.assertIn("touch_drag", node.fields["action_trigger"])
        self.assertIn("idle = 0", node.fields["action_trigger_active"])

    def test_manual_touchdrag_zero_target_idle_survives_roundtrip(self) -> None:
        document = self.make_ready_document()
        document.interaction_creation_mode = "manual"
        node = create_node(self.schema, document, "TouchDrag")
        document.nodes.append(node)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "manual_touchdrag.json"
            save_document(self.schema, document, path)
            loaded = load_document(self.schema, path)
        loaded_node = next(item for item in loaded.nodes if item.uuid == node.uuid)
        self.assertEqual("TouchDrag", loaded_node.fields["draw_able_name"])
        self.assertEqual("touch_drag", loaded_node.fields["parameter"])
        self.assertEqual(0, loaded_node.fields["target_idle"])

    def test_save_roundtrip_preserves_editor_settings_and_locked_nodes(self) -> None:
        document = self.make_ready_document()
        document.editor_settings.numeric_linkage_enabled = False
        document.editor_settings.trash_enabled = False
        node = create_node(self.schema, document, "TouchIdle")
        node.locked = True
        document.nodes.append(node)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings_roundtrip.json"
            save_document(self.schema, document, path)
            loaded = load_document(self.schema, path)
        loaded_node = next(item for item in loaded.nodes if item.uuid == node.uuid)
        self.assertFalse(loaded.editor_settings.numeric_linkage_enabled)
        self.assertFalse(loaded.editor_settings.trash_enabled)
        self.assertTrue(loaded_node.locked)

    def test_load_document_without_editor_settings_defaults_linkage_to_disabled(self) -> None:
        payload = {
            "editor_signature": "l2d_config_editor/v1",
            "global_mode": "simple",
            "interaction_creation_mode": "auto",
            "meta": {
                "version": "2099-09-09",
                "author": "",
                "ship_skin_id": 0,
                "memo": "",
                "react_condition": "",
                "tips": "",
                "CharName": "",
            },
            "nodes": [],
            "connections": [],
            "trash_bin": [],
            "canvas_view": {"scale": 1.0, "offset_x": 0.0, "offset_y": 0.0},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "legacy.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            loaded = load_document(self.schema, path)
        self.assertFalse(loaded.editor_settings.numeric_linkage_enabled)
        self.assertFalse(loaded.editor_settings.trash_enabled)

    def test_empty_action_fields_stay_empty_after_display_roundtrip(self) -> None:
        document = create_document(self.schema)
        node = create_node(self.schema, document, "TouchIdle")
        document.editor_settings.numeric_linkage_enabled = False
        node.fields["action_trigger"] = ""
        node.fields["action_trigger_active"] = ""
        self.assertEqual("", node.fields["action_trigger"])
        self.assertEqual("", node.fields["action_trigger_active"])
        self.assertEqual("", display_value_for_field(self.schema, node, "action_trigger"))
        self.assertEqual("", display_value_for_field(self.schema, node, "action_trigger_active"))
        self.assertEqual("", normalize_field_input(self.schema, node, "action_trigger", ""))
        self.assertEqual("", normalize_field_input(self.schema, node, "action_trigger_active", ""))

    def test_template_version_folder_name_normalizes_date(self) -> None:
        self.assertEqual("20260520", build_template_version_folder_name("2026-05-20"))
        self.assertEqual("20260528", build_template_version_folder_name("2026-05-28"))

    def test_load_template_csv_rows_reads_required_columns(self) -> None:
        csv_text = "版本,角色名,角色资源名,角色id\n2026-05-20,测试角色,test_role,123456\n"
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "template.csv"
            csv_path.write_text(csv_text, encoding="utf-8-sig")
            rows = load_template_csv_rows(csv_path)
        self.assertEqual(
            [{"version": "2026-05-20", "CharName": "测试角色", "memo": "test_role", "ship_skin_id": 123456}],
            rows,
        )

    def test_create_template_document_prefills_initial_node_only(self) -> None:
        document = create_template_document(
            self.schema,
            version="2026-05-20",
            char_name="测试角色",
            memo="test_role",
            ship_skin_id=123456,
        )
        self.assertFalse(document.editor_settings.numeric_linkage_enabled)
        self.assertEqual(1, len(document.nodes))
        initial = next(node for node in document.nodes if node.type == "Initial")
        self.assertEqual("2026-05-20", initial.fields["version"])
        self.assertEqual("测试角色", initial.fields["CharName"])
        self.assertEqual("test_role", initial.fields["memo"])
        self.assertEqual(123456, initial.fields["ship_skin_id"])
        self.assertEqual("", initial.fields["author"])

    def test_validation_catches_invalid_parts_data(self) -> None:
        document = self.make_ready_document()
        node = create_node(self.schema, document, "TouchDrag")
        node.fields["parts_data"] = "1,hello"
        document.nodes.append(node)
        issues = validate_document(self.schema, document)
        self.assertTrue(any(issue.field_keys == ["parts_data"] for issue in issues))

    def test_validation_catches_parts_out_of_range(self) -> None:
        document = self.make_ready_document()
        node = create_node(self.schema, document, "TouchDrag")
        node.fields["parts_data"] = "2"
        node.fields["range"] = "{0,1}"
        document.nodes.append(node)
        issues = validate_document(self.schema, document)
        self.assertTrue(any(issue.field_keys == ["parts_data", "range"] for issue in issues))

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

    def test_csv_preview_uses_node_desc(self) -> None:
        document = self.make_ready_document()
        node = create_node(self.schema, document, "TouchIdle")
        node.fields["desc"] = "节点说明"
        document.nodes.append(node)
        rows = document_to_csv_rows(self.schema, document)
        self.assertEqual("节点说明", rows[0].values["desc"])
        self.assertEqual(302291, rows[0].values["ship_skin_id"])

    def test_parts_data_is_canonicalized_to_wrapped_format(self) -> None:
        document = self.make_ready_document()
        node = create_node(self.schema, document, "TouchDrag")
        node.fields["parts_data"] = "1,2.5"
        apply_auto_rules(self.schema, document, node, source_mode="advanced", changed_key="parts_data")
        self.assertEqual("{parts={1,2.5}}", node.fields["parts_data"])

    def test_csv_preview_wraps_react_condition_idle_list(self) -> None:
        document = self.make_ready_document()
        initial = next(node for node in document.nodes if node.type == "Initial")
        initial.fields["react_condition"] = "0,17"
        node = create_node(self.schema, document, "TouchIdle")
        document.nodes.append(node)
        rows = document_to_csv_rows(self.schema, document)
        self.assertEqual("{idle_on={0,17}}", rows[0].values["react_condition"])

    def test_range_abs_tracks_signed_range(self) -> None:
        document = self.make_ready_document()
        node = create_node(self.schema, document, "TouchDrag")
        node.fields["range_abs"] = 0
        node.fields["range"] = "{0,2}"
        apply_auto_rules(self.schema, document, node, source_mode="advanced", changed_key="range")
        self.assertEqual(1, node.fields["range_abs"])
        node.fields["range"] = "{-1,2}"
        apply_auto_rules(self.schema, document, node, source_mode="advanced", changed_key="range")
        self.assertEqual(0, node.fields["range_abs"])

    def test_search_uses_human_readable_action_fields(self) -> None:
        document = self.make_ready_document()
        node = create_node(self.schema, document, "TouchIdle")
        node.fields["target_idle"] = 13
        apply_auto_rules(self.schema, document, node, source_mode="simple", changed_key="target_idle")
        document.nodes.append(node)
        hits = search_document(self.schema, document, "13")
        self.assertTrue(any(hit.field_name == "action_trigger_active" and hit.preview == "13" for hit in hits))

    def test_search_hides_touchdrag_internal_target_idle(self) -> None:
        document = self.make_ready_document()
        node = create_node(self.schema, document, "TouchDrag")
        node.fields["action_trigger"] = "{type = 2 ,action = 'touch_idle13'}"
        node.fields["action_trigger_active"] = "{enable = {},ignore = {'main_1'},idle = 13}"
        apply_auto_rules(self.schema, document, node, source_mode="advanced", changed_key="action_trigger")
        document.nodes.append(node)
        hits = search_document(self.schema, document, "13")
        self.assertFalse(any(hit.field_name == "action_trigger_active" for hit in hits if hit.node_uuid == node.uuid))

    def test_search_can_use_json_field_names(self) -> None:
        document = self.make_ready_document()
        node = create_node(self.schema, document, "TouchIdle")
        node.fields["tips"] = "备注"
        document.nodes.append(node)
        hits = search_document(self.schema, document, "备注", use_json_field_names=True)
        self.assertTrue(any(hit.field_label == "tips" for hit in hits))

    def test_node_title_uses_draw_name_and_tips(self) -> None:
        document = self.make_ready_document()
        node = create_node(self.schema, document, "TouchIdle")
        node.fields["draw_able_name"] = "CustomFrame"
        node.fields["tips"] = "备注"
        document.nodes.append(node)
        self.assertEqual("CustomFrame-备注", node_title(self.schema, node))

    def test_export_payload_contains_meta_and_nodes(self) -> None:
        document = self.make_ready_document()
        document.nodes.append(create_node(self.schema, document, "TouchDrag"))
        document.nodes.append(create_node(self.schema, document, "Comment"))
        payload = export_document_dict(self.schema, document)
        self.assertEqual("simple", payload["global_mode"])
        self.assertIn("meta", payload)
        self.assertIn("nodes", payload)
        self.assertIn("canvas_view", payload)
        self.assertNotIn("target_idle", payload["nodes"][0])
        touchdrag_payload = next(node for node in payload["nodes"] if node["type"] == "TouchDrag")
        self.assertNotIn("action_trigger_active", touchdrag_payload)

    def test_document_mode_roundtrip(self) -> None:
        document = self.make_ready_document()
        document.global_mode = "advanced"
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "mode.json"
            save_document(self.schema, document, path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            loaded = load_document(self.schema, path)
        self.assertEqual("advanced", payload["global_mode"])
        self.assertEqual("advanced", loaded.global_mode)

    def test_old_document_without_global_mode_defaults_to_simple(self) -> None:
        document = self.make_ready_document()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "legacy.json"
            payload = export_document_dict(self.schema, document)
            payload.pop("global_mode", None)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            loaded = load_document(self.schema, path)
        self.assertEqual("simple", loaded.global_mode)

    def test_controller_translates_display_action_fields_to_raw(self) -> None:
        controller = EditorController()
        controller.document.editor_settings.numeric_linkage_enabled = True
        initial = next(node for node in controller.document.nodes if node.type == "Initial")
        controller.update_field(initial.uuid, "author", "asahi", "simple")
        controller.update_field(initial.uuid, "ship_skin_id", 302291, "simple")
        controller.update_field(initial.uuid, "memo", "mingji_2", "simple")
        controller.update_field(initial.uuid, "CharName", "??", "simple")
        node_uuid = controller.create_node("TouchIdle", (100, 100))
        self.assertIsNotNone(node_uuid)
        node = controller.get_node(node_uuid)
        controller.update_field(node.uuid, "action_trigger", "touch_idle7", "simple")
        controller.update_field(node.uuid, "action_trigger_active", 7, "simple")
        node = controller.get_node(node.uuid)
        self.assertEqual("{type = 2 ,action = 'touch_idle7'}", node.fields["action_trigger"])
        self.assertIn("idle = 7", node.fields["action_trigger_active"])
        self.assertEqual("Paramtouch_idle7", node.fields["parameter"])

    def test_touchidle_zero_target_idle_is_not_treated_as_empty(self) -> None:
        controller = EditorController()
        controller.document.editor_settings.numeric_linkage_enabled = True
        initial = next(node for node in controller.document.nodes if node.type == "Initial")
        controller.update_field(initial.uuid, "author", "asahi", "simple")
        controller.update_field(initial.uuid, "ship_skin_id", 302291, "simple")
        controller.update_field(initial.uuid, "memo", "mingji_2", "simple")
        controller.update_field(initial.uuid, "CharName", "??", "simple")
        node_uuid = controller.create_node("TouchIdle", (100, 100))
        controller.update_field(node_uuid, "action_trigger_active", 0, "simple")
        node = controller.get_node(node_uuid)
        self.assertEqual("TouchIdle0", node.fields["draw_able_name"])
        self.assertEqual("Paramtouch_idle0", node.fields["parameter"])
        self.assertIn("touch_idle0", node.fields["action_trigger"])
        self.assertIn("idle = 0", node.fields["action_trigger_active"])
        self.assertEqual(0, display_value_for_field(self.schema, node, "action_trigger_active"))

    def test_touchdrag_action_trigger_edit_updates_internal_target_idle(self) -> None:
        controller = EditorController()
        controller.document.editor_settings.numeric_linkage_enabled = True
        initial = next(node for node in controller.document.nodes if node.type == "Initial")
        controller.update_field(initial.uuid, "author", "asahi", "simple")
        controller.update_field(initial.uuid, "ship_skin_id", 302291, "simple")
        controller.update_field(initial.uuid, "memo", "mingji_2", "simple")
        controller.update_field(initial.uuid, "CharName", "??", "simple")
        node_uuid = controller.create_node("TouchDrag", (100, 100))
        self.assertIsNotNone(node_uuid)
        node = controller.get_node(node_uuid)
        controller.update_field(node.uuid, "action_trigger", "touch_idle7", "simple")
        node = controller.get_node(node.uuid)
        self.assertEqual("TouchDrag7", node.fields["draw_able_name"])
        self.assertEqual("{type = 2 ,action = 'touch_idle7'}", node.fields["action_trigger"])
        self.assertEqual("touch_drag7", node.fields["parameter"])
        self.assertEqual(7, node.fields["target_idle"])
        self.assertIn("idle = 7", node.fields["action_trigger_active"])

    def test_touchidle_parameter_edit_updates_linked_fields_in_simple_mode(self) -> None:
        controller = EditorController()
        controller.document.editor_settings.numeric_linkage_enabled = True
        initial = next(node for node in controller.document.nodes if node.type == "Initial")
        controller.update_field(initial.uuid, "author", "asahi", "simple")
        controller.update_field(initial.uuid, "ship_skin_id", 302291, "simple")
        controller.update_field(initial.uuid, "memo", "mingji_2", "simple")
        controller.update_field(initial.uuid, "CharName", "??", "simple")
        node_uuid = controller.create_node("TouchIdle", (100, 100))
        controller.update_field(node_uuid, "parameter", "Paramtouch_idle11", "simple")
        node = controller.get_node(node_uuid)
        self.assertEqual("TouchIdle11", node.fields["draw_able_name"])
        self.assertEqual("Paramtouch_idle11", node.fields["parameter"])
        self.assertEqual(11, node.fields["target_idle"])
        self.assertIn("touch_idle11", node.fields["action_trigger"])
        self.assertIn("idle = 11", node.fields["action_trigger_active"])

    def test_numeric_linkage_toggle_only_affects_future_nodes(self) -> None:
        controller = EditorController()
        controller.document.editor_settings.numeric_linkage_enabled = True
        initial = next(node for node in controller.document.nodes if node.type == "Initial")
        controller.update_field(initial.uuid, "author", "asahi", "simple")
        controller.update_field(initial.uuid, "ship_skin_id", 302291, "simple")
        controller.update_field(initial.uuid, "memo", "mingji_2", "simple")
        controller.update_field(initial.uuid, "CharName", "??", "simple")

        first_uuid = controller.create_node("TouchIdle", (100, 100))
        controller.set_numeric_linkage_enabled(False)
        second_uuid = controller.create_node("TouchIdle", (260, 100))

        controller.update_field(first_uuid, "action_trigger_active", 5, "simple")
        first = controller.get_node(first_uuid)
        self.assertEqual("TouchIdle5", first.fields["draw_able_name"])
        self.assertEqual("Paramtouch_idle5", first.fields["parameter"])
        self.assertIn("touch_idle5", first.fields["action_trigger"])

        controller.update_field(second_uuid, "action_trigger_active", 7, "simple")
        second = controller.get_node(second_uuid)
        self.assertEqual("TouchIdle2", second.fields["draw_able_name"])
        self.assertEqual("Paramtouch_idle2", second.fields["parameter"])
        self.assertIn("touch_idle2", second.fields["action_trigger"])
        self.assertIn("idle = 7", second.fields["action_trigger_active"])

    def test_touchdrag_roundtrip_without_action_trigger_active(self) -> None:
        controller = EditorController()
        controller.document.editor_settings.numeric_linkage_enabled = True
        initial = next(node for node in controller.document.nodes if node.type == "Initial")
        controller.update_field(initial.uuid, "author", "asahi", "simple")
        controller.update_field(initial.uuid, "ship_skin_id", 302291, "simple")
        controller.update_field(initial.uuid, "memo", "mingji_2", "simple")
        controller.update_field(initial.uuid, "CharName", "??", "simple")
        node_uuid = controller.create_node("TouchDrag", (100, 100))
        node = controller.get_node(node_uuid)
        controller.update_field(node.uuid, "action_trigger", "touch_idle9", "simple")
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "touchdrag_roundtrip.json"
            controller.save_document(str(path))
            payload = json.loads(path.read_text(encoding="utf-8"))
            loaded = load_document(self.schema, path)
        saved_node = next(item for item in payload["nodes"] if item["type"] == "TouchDrag")
        loaded_node = next(item for item in loaded.nodes if item.type == "TouchDrag")
        self.assertNotIn("action_trigger_active", saved_node)
        self.assertEqual(9, loaded_node.fields["target_idle"])
        self.assertIn("idle = 9", loaded_node.fields["action_trigger_active"])

    def test_schema_supports_label_html(self) -> None:
        schema_payload = json.loads(Path("l2d_config_editor/editor_schema.json").read_text(encoding="utf-8"))
        schema_payload["nodes"]["Initial"]["fields"][0]["label_html"] = "<b><font color='#ffcc66'>备注</font></b>"
        with tempfile.TemporaryDirectory() as temp_dir:
            schema_path = Path(temp_dir) / "schema.json"
            schema_path.write_text(json.dumps(schema_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            schema = load_editor_schema(schema_path)
        document = create_document(schema)
        initial = next(node for node in document.nodes if node.type == "Initial")
        form = NodeFormWidget(schema, inline=False)
        form.set_node(initial, "simple")
        label = form._form.itemAt(0, form._form.ItemRole.LabelRole).widget()
        self.assertEqual(Qt.TextFormat.RichText, label.textFormat())
        self.assertEqual("<b><font color='#ffcc66'>备注</font></b>", label.text())

    def test_touchidle_hard_hides_action_trigger_editor(self) -> None:
        document = self.make_ready_document()
        node = create_node(self.schema, document, "TouchIdle")
        node.fields["transition_type"] = "hard"
        apply_auto_rules(self.schema, document, node, source_mode="simple", changed_key="transition_type")
        form = NodeFormWidget(self.schema, inline=False)
        form.set_node(node, "simple")
        self.assertNotIn("action_trigger", form._bindings)
        self.assertIn("action_trigger_active", form._bindings)

    def test_form_can_show_json_field_names(self) -> None:
        document = self.make_ready_document()
        initial = next(node for node in document.nodes if node.type == "Initial")
        form = NodeFormWidget(self.schema, inline=False)
        form.set_node(initial, "simple", show_json_field_names=True)
        labels = []
        for row in range(form._form.rowCount()):
            item = form._form.itemAt(row, form._form.ItemRole.LabelRole)
            if item and item.widget():
                labels.append(item.widget().text())
        self.assertIn("author", labels)

    def test_clipboard_copy_strips_tips_from_function_nodes(self) -> None:
        controller = EditorController()
        initial = next(node for node in controller.document.nodes if node.type == "Initial")
        controller.update_field(initial.uuid, "author", "asahi", "simple")
        controller.update_field(initial.uuid, "ship_skin_id", 302291, "simple")
        controller.update_field(initial.uuid, "memo", "mingji_2", "simple")
        controller.update_field(initial.uuid, "CharName", "??", "simple")
        node_uuid = controller.create_node("TouchIdle", (100, 100))
        node = controller.get_node(node_uuid)
        controller.update_field(node.uuid, "tips", "不要复制", "simple")
        payload = controller.serialize_selection([node.uuid])
        raw = json.loads(payload.decode("utf-8"))
        self.assertNotIn("tips", raw["nodes"][0])

    def test_remove_nodes_writes_trash_bin_and_persists(self) -> None:
        controller = EditorController()
        controller.document.editor_settings.trash_enabled = True
        initial = next(node for node in controller.document.nodes if node.type == "Initial")
        controller.update_field(initial.uuid, "author", "asahi", "simple")
        controller.update_field(initial.uuid, "ship_skin_id", 302291, "simple")
        controller.update_field(initial.uuid, "memo", "mingji_2", "simple")
        controller.update_field(initial.uuid, "CharName", "??", "simple")
        node_uuid = controller.create_node("TouchIdle", (100, 100))
        controller.remove_nodes([node_uuid])
        self.assertEqual(1, len(controller.document.trash_bin))
        self.assertEqual("TouchIdle", controller.document.trash_bin[0].node_type)
        self.assertEqual(1, controller.document.trash_bin[0].type_slot)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "trash.json"
            controller.save_document(str(path))
            loaded = load_document(controller.schema, path)
        self.assertEqual(1, len(loaded.trash_bin))
        self.assertEqual("TouchIdle", loaded.trash_bin[0].node_type)

    def test_disabling_trash_clears_bin_and_reuses_slots(self) -> None:
        controller = EditorController()
        controller.document.editor_settings.trash_enabled = True
        initial = next(node for node in controller.document.nodes if node.type == "Initial")
        controller.update_field(initial.uuid, "author", "asahi", "simple")
        controller.update_field(initial.uuid, "ship_skin_id", 302291, "simple")
        controller.update_field(initial.uuid, "memo", "mingji_2", "simple")
        controller.update_field(initial.uuid, "CharName", "??", "simple")
        first_uuid = controller.create_node("TouchIdle", (100, 100))
        controller.remove_nodes([first_uuid])
        self.assertEqual(1, len(controller.document.trash_bin))
        controller.set_trash_enabled(False)
        self.assertFalse(controller.document.editor_settings.trash_enabled)
        self.assertEqual([], controller.document.trash_bin)
        second_uuid = controller.create_node("TouchIdle", (120, 120))
        second = controller.get_node(second_uuid)
        self.assertEqual(1, second.type_slot)

    def test_locked_node_rejects_field_updates_and_moves(self) -> None:
        controller = EditorController()
        initial = next(node for node in controller.document.nodes if node.type == "Initial")
        controller.update_field(initial.uuid, "author", "asahi", "simple")
        controller.update_field(initial.uuid, "ship_skin_id", 302291, "simple")
        controller.update_field(initial.uuid, "memo", "mingji_2", "simple")
        controller.update_field(initial.uuid, "CharName", "??", "simple")
        node_uuid = controller.create_node("TouchIdle", (100, 100))
        node = controller.get_node(node_uuid)
        controller.set_node_locked(node_uuid, True)
        controller.update_field(node_uuid, "tips", "should_not_change", "simple")
        controller.move_node(node_uuid, (100.0, 100.0), (220.0, 220.0))
        self.assertTrue(node.locked)
        self.assertNotEqual("should_not_change", node.fields.get("tips"))
        self.assertEqual({"x": 100.0, "y": 100.0}, node.ui_position)

    def test_manual_mode_paste_preserves_explicit_sequence_values(self) -> None:
        controller = EditorController()
        controller.document.editor_settings.numeric_linkage_enabled = True
        initial = next(node for node in controller.document.nodes if node.type == "Initial")
        controller.update_field(initial.uuid, "author", "asahi", "simple")
        controller.update_field(initial.uuid, "ship_skin_id", 302291, "simple")
        controller.update_field(initial.uuid, "memo", "mingji_2", "simple")
        controller.update_field(initial.uuid, "CharName", "测试", "simple")
        controller.set_interaction_creation_mode("manual")
        node_uuid = controller.create_node("TouchIdle", (100, 100))
        source_node = controller.get_node(node_uuid)
        payload = controller.serialize_selection([node_uuid])
        pasted = controller.paste_payload(payload, (260, 160))
        self.assertEqual(1, len(pasted))
        new_node = controller.get_node(pasted[0])
        self.assertEqual("TouchIdle", source_node.fields["draw_able_name"])
        self.assertEqual("TouchIdle", new_node.fields["draw_able_name"])
        self.assertEqual("Paramtouch_idle", new_node.fields["parameter"])
        self.assertIn("action = 'touch_idle'", new_node.fields["action_trigger"])
        self.assertIn("idle = 0", new_node.fields["action_trigger_active"])

    def test_file_list_recurses_nested_directories(self) -> None:
        controller = EditorController()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            save_document(self.schema, create_document(self.schema), root / "root.json")
            (root / "sub").mkdir()
            save_document(self.schema, create_document(self.schema), root / "sub" / "child.json")
            (root / "sub" / "deep").mkdir()
            save_document(self.schema, create_document(self.schema), root / "sub" / "deep" / "nested.json")
            files = controller.file_list(root)
        self.assertIn("root.json", files)
        self.assertIn("sub/child.json", files)
        self.assertIn("sub/deep/nested.json", files)

    def test_file_list_without_directory_uses_workspace_root(self) -> None:
        controller = EditorController()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            save_document(self.schema, create_document(self.schema), root / "a.json")
            controller.set_workspace_root(root)
            files_default = controller.file_list()
            files_explicit = controller.file_list(root)
        self.assertEqual(files_default, files_explicit)
        self.assertIn("a.json", files_default)

    def test_file_list_returns_empty_without_workspace_when_no_directory(self) -> None:
        controller = EditorController()
        self.assertEqual(controller.file_list(), [])

    def test_file_list_ignores_non_editor_json(self) -> None:
        controller = EditorController()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            save_document(self.schema, create_document(self.schema), root / "valid.json")
            (root / "random.json").write_text('{"hello": "world"}', encoding="utf-8")
            files = controller.file_list(root)
        self.assertIn("valid.json", files)
        self.assertNotIn("random.json", files)


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
        window = MainWindow("/Users/asahi/Live2dConfigGen", prefer_saved_workspace=False)
        initial = next(node for node in window.controller.document.nodes if node.type == "Initial")
        window.controller.update_field(initial.uuid, "author", "asahi", "simple")
        window.controller.update_field(initial.uuid, "ship_skin_id", 302291, "simple")
        window.controller.update_field(initial.uuid, "memo", "mingji_2", "simple")
        window.controller.update_field(initial.uuid, "CharName", "??", "simple")
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
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("advanced", payload["global_mode"])
        window._mark_saved_checkpoint(saved=True)
        window.close()

    def test_main_window_restores_last_opened_document(self) -> None:
        settings = QSettings("OpenAI", "L2DConfigEditor")
        settings.clear()
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                path = root / "restore.json"
                save_document(get_default_schema(), create_document(get_default_schema()), path)

                first = MainWindow(root, prefer_saved_workspace=False)
                first._open_existing_session_or_file(path)
                self.assertEqual(str(path), first.controller.document.path)
                first.close()
                settings.sync()

                second = MainWindow(root, prefer_saved_workspace=False)
                self.assertEqual(str(path), second.controller.document.path)
                second.close()
        finally:
            settings.clear()

    def test_main_window_restores_local_trash_preference_for_blank_document(self) -> None:
        settings = QSettings("OpenAI", "L2DConfigEditor")
        settings.clear()
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                first = MainWindow(root, prefer_saved_workspace=False)
                self.assertFalse(first.controller.document.editor_settings.trash_enabled)
                first._toggle_trash_enabled(True)
                self.assertTrue(first.controller.document.editor_settings.trash_enabled)
                first.close()

                second = MainWindow(root, prefer_saved_workspace=False)
                self.assertTrue(second.controller.document.editor_settings.trash_enabled)
                second.close()
        finally:
            settings.clear()

    def test_node_title_updates_when_draw_name_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(temp_dir, prefer_saved_workspace=False)
            initial = next(node for node in window.controller.document.nodes if node.type == "Initial")
            window.controller.update_field(initial.uuid, "author", "asahi", "simple")
            window.controller.update_field(initial.uuid, "ship_skin_id", 302291, "simple")
            window.controller.update_field(initial.uuid, "memo", "mingji_2", "simple")
            window.controller.update_field(initial.uuid, "CharName", "测试", "simple")
            created = window.controller.create_node("TouchIdle", (200, 120))
            item = window.canvas.node_items[created]

            window.controller.update_field(created, "draw_able_name", "CustomFrame", "simple")

            self.assertEqual("CustomFrame", item._full_title_text())
            window._mark_saved_checkpoint(saved=True)
        window.close()

    def test_node_form_reuses_editors_for_same_node_updates(self) -> None:
        schema = get_default_schema()
        document = create_document(schema)
        initial = next(node for node in document.nodes if node.type == "Initial")
        form = NodeFormWidget(schema, inline=True)
        form.set_node(initial, "simple")

        original_tips = form._bindings["tips"].widget
        original_author = form._bindings["author"].widget

        initial.fields["tips"] = "updated"
        initial.fields["author"] = "tester"
        form.set_node(initial, "simple")

        self.assertIs(original_tips, form._bindings["tips"].widget)
        self.assertIs(original_author, form._bindings["author"].widget)

    def test_node_frame_encloses_inline_form_content(self) -> None:
        window = MainWindow("/Users/asahi/Live2dConfigGen", prefer_saved_workspace=False)
        window.controller.set_global_mode("simple")
        initial = next(node for node in window.controller.document.nodes if node.type == "Initial")
        window.controller.update_field(initial.uuid, "author", "asahi", "simple")
        window.controller.update_field(initial.uuid, "ship_skin_id", 302291, "simple")
        window.controller.update_field(initial.uuid, "memo", "mingji_2", "simple")
        window.controller.update_field(initial.uuid, "CharName", "??", "simple")
        created = window.controller.create_node("TouchIdle", (200, 120))
        item = window.canvas.node_items[created]

        item.form.ensurePolished()
        item.form.layout().activate()
        item.form.adjustSize()
        self.app.processEvents()

        required_height = item.proxy.pos().y() + item.form.height() + item._margin
        self.assertGreaterEqual(item.boundingRect().height(), required_height)
        window._mark_saved_checkpoint(saved=True)
        window.close()

    def test_inline_combo_selection_updates_without_breaking_form(self) -> None:
        window = MainWindow("/Users/asahi/Live2dConfigGen", prefer_saved_workspace=False)
        window.controller.set_global_mode("simple")
        initial = next(node for node in window.controller.document.nodes if node.type == "Initial")
        window.controller.update_field(initial.uuid, "author", "asahi", "simple")
        window.controller.update_field(initial.uuid, "ship_skin_id", 302291, "simple")
        window.controller.update_field(initial.uuid, "memo", "mingji_2", "simple")
        window.controller.update_field(initial.uuid, "CharName", "??", "simple")
        created = window.controller.create_node("TouchIdle", (200, 120))
        item = window.canvas.node_items[created]
        combo = item.form._bindings["control_type"].widget

        self.assertEqual(combo.sizePolicy().horizontalPolicy(), combo.sizePolicy().Policy.Expanding)
        combo.setCurrentIndex(1)
        combo.activated.emit(1)
        self.app.processEvents()

        self.assertEqual("drag", item.node.fields["control_type"])
        self.assertIn("drag_ui_direction", item.form._bindings)
        window._mark_saved_checkpoint(saved=True)
        window.close()

    def test_touchdrag_hides_legacy_target_idle_field(self) -> None:
        window = MainWindow("/Users/asahi/Live2dConfigGen", prefer_saved_workspace=False)
        window.controller.set_global_mode("simple")
        initial = next(node for node in window.controller.document.nodes if node.type == "Initial")
        window.controller.update_field(initial.uuid, "author", "asahi", "simple")
        window.controller.update_field(initial.uuid, "ship_skin_id", 302291, "simple")
        window.controller.update_field(initial.uuid, "memo", "mingji_2", "simple")
        window.controller.update_field(initial.uuid, "CharName", "??", "simple")
        created = window.controller.create_node("TouchDrag", (200, 120))
        item = window.canvas.node_items[created]

        self.assertNotIn("target_idle", item.form._bindings)
        self.assertIn("action_trigger", item.form._bindings)
        self.assertNotIn("action_trigger_active", item.form._bindings)
        window._mark_saved_checkpoint(saved=True)
        window.close()

    def test_template_creation_groups_json_by_version_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(temp_dir, prefer_saved_workspace=False)
            csv_path = Path(temp_dir) / "template.csv"
            csv_path.write_text(
                "版本,角色名,角色资源名,角色id\n"
                "2026-05-20,角色A,res_a,1001\n"
                "2026-05-20,角色B,res_b,1002\n"
                "2026-05-28,角色C,res_c,1003\n",
                encoding="utf-8-sig",
            )
            created_files, created_folders = window._create_templates_from_csv(csv_path)
            self.assertEqual(3, created_files)
            self.assertEqual(2, created_folders)
            self.assertTrue((Path(temp_dir) / "20260520" / "角色A.json").exists())
            self.assertTrue((Path(temp_dir) / "20260520" / "角色B.json").exists())
            self.assertTrue((Path(temp_dir) / "20260528" / "角色C.json").exists())
            window.close()

    def test_node_directory_click_keeps_dialog_open(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(temp_dir, prefer_saved_workspace=False)
            initial = next(node for node in window.controller.document.nodes if node.type == "Initial")
            window.controller.update_field(initial.uuid, "author", "asahi", "simple")
            window.controller.update_field(initial.uuid, "ship_skin_id", 302291, "simple")
            window.controller.update_field(initial.uuid, "memo", "mingji_2", "simple")
            window.controller.update_field(initial.uuid, "CharName", "测试角色", "simple")
            created = window.controller.create_node("TouchIdle", (200, 120))
            self.assertIsNotNone(created)
            window._show_node_directory_dialog()
            self.assertIsNotNone(window.node_directory_dialog)
            dialog = window.node_directory_dialog
            self.assertTrue(dialog.isVisible())
            target_item = next(
                dialog.list_widget.item(index)
                for index in range(dialog.list_widget.count())
                if dialog.list_widget.item(index).data(Qt.ItemDataRole.UserRole) == created
            )
            dialog._emit_current_node(target_item)
            self.assertTrue(dialog.isVisible())
            window.close()

    def test_node_bounding_rect_covers_full_pin_hit_area(self) -> None:
        window = MainWindow("/Users/asahi/Live2dConfigGen", prefer_saved_workspace=False)
        window.controller.set_global_mode("simple")
        initial = next(node for node in window.controller.document.nodes if node.type == "Initial")
        window.controller.update_field(initial.uuid, "author", "asahi", "simple")
        window.controller.update_field(initial.uuid, "ship_skin_id", 302291, "simple")
        window.controller.update_field(initial.uuid, "memo", "mingji_2", "simple")
        window.controller.update_field(initial.uuid, "CharName", "??", "simple")
        created = window.controller.create_node("TouchDrag", (200, 120))
        item = window.canvas.node_items[created]

        self.assertTrue(item.boundingRect().contains(item.input_pin_rect()))
        self.assertTrue(item.boundingRect().contains(item.output_pin_rect()))

        output_center = item.output_pin_scene_pos()
        right_half_scene = output_center + QPointF(item._pin_radius * 0.75, 0.0)
        left_half_scene = output_center + QPointF(-item._pin_radius * 0.75, 0.0)
        self.assertEqual("output", item.pin_hit(right_half_scene))
        self.assertEqual("output", item.pin_hit(left_half_scene))
        window._mark_saved_checkpoint(saved=True)
        window.close()

    def test_touchdrag_value_mode_shows_revert_fields_in_advanced(self) -> None:
        window = MainWindow("/Users/asahi/Live2dConfigGen", prefer_saved_workspace=False)
        window.controller.set_global_mode("advanced")
        initial = next(node for node in window.controller.document.nodes if node.type == "Initial")
        window.controller.update_field(initial.uuid, "author", "asahi", "simple")
        window.controller.update_field(initial.uuid, "ship_skin_id", 302291, "simple")
        window.controller.update_field(initial.uuid, "memo", "mingji_2", "simple")
        window.controller.update_field(initial.uuid, "CharName", "??", "simple")
        created = window.controller.create_node("TouchDrag", (200, 120))
        window.controller.update_field(created, "result_type", "value", "simple")
        item = window.canvas.node_items[created]

        self.assertIn("revert_action_index", item.form._bindings)
        self.assertIn("revert_idle_index", item.form._bindings)
        self.assertIn("action_trigger_kind_ui", item.form._bindings)
        window._mark_saved_checkpoint(saved=True)
        window.close()

    def test_touchdrag_value_result_keeps_action_fields_empty(self) -> None:
        schema = get_default_schema()
        document = create_document(schema)
        initial = next(node for node in document.nodes if node.type == "Initial")
        initial.fields["author"] = "asahi"
        initial.fields["ship_skin_id"] = 302291
        initial.fields["memo"] = "mingji_2"
        initial.fields["CharName"] = "??"
        node = create_node(schema, document, "TouchDrag")
        node.fields["result_type"] = "value"
        node.fields["target_value"] = 3.5
        apply_auto_rules(schema, document, node, source_mode="simple", changed_key="result_type")
        self.assertEqual("", node.fields["action_trigger"])
        self.assertEqual("", node.fields["action_trigger_active"])

    def test_manual_save_keeps_undo_history_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(temp_dir, prefer_saved_workspace=False)
            initial = next(node for node in window.controller.document.nodes if node.type == "Initial")
            window.controller.update_field(initial.uuid, "author", "asahi", "simple")
            window.controller.update_field(initial.uuid, "ship_skin_id", 302291, "simple")
            window.controller.update_field(initial.uuid, "memo", "mingji_2", "simple")
            window.controller.update_field(initial.uuid, "CharName", "??", "simple")
            created = window.controller.create_node("TouchIdle", (200, 120))
            self.assertIsNotNone(created)
            window.controller.document.path = str(Path(temp_dir) / "undo_after_save.json")
            saved = window._save_current_file(silent=True)
            self.assertIsNotNone(saved)
            self.assertTrue(window.controller.undo_stack.canUndo())
            window.controller.undo_stack.undo()
            self.assertIsNone(window.controller.get_node(created))
            window._mark_saved_checkpoint(saved=True)
        window.close()

    def test_auto_save_waits_until_canvas_not_busy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(temp_dir, prefer_saved_workspace=False)
            initial = next(node for node in window.controller.document.nodes if node.type == "Initial")
            window.controller.update_field(initial.uuid, "author", "asahi", "simple")
            window.controller.update_field(initial.uuid, "ship_skin_id", 302291, "simple")
            window.controller.update_field(initial.uuid, "memo", "mingji_2", "simple")
            window.controller.update_field(initial.uuid, "CharName", "??", "simple")
            window.controller.document.path = str(Path(temp_dir) / "busy_autosave.json")
            window.controller.pathChanged.emit(window.controller.document.path)
            window._mark_saved_checkpoint(saved=True)
            window.controller.update_field(initial.uuid, "memo", "changed", "simple")
            window.canvas._set_interaction_busy("drag", True)
            window._run_auto_save()
            self.assertFalse(Path(window.controller.document.path).exists())
            window.canvas._set_interaction_busy("drag", False)
            window._run_auto_save()
            self.assertTrue(Path(window.controller.document.path).exists())
            window._mark_saved_checkpoint(saved=True)
        window.close()

    def test_save_commits_pending_line_edit_without_enter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(temp_dir, prefer_saved_workspace=False)
            initial = next(node for node in window.controller.document.nodes if node.type == "Initial")
            window.controller.update_field(initial.uuid, "author", "asahi", "simple")
            window.controller.update_field(initial.uuid, "ship_skin_id", 302291, "simple")
            window.controller.update_field(initial.uuid, "memo", "mingji_2", "simple")
            window.controller.update_field(initial.uuid, "CharName", "??", "simple")
            created = window.controller.create_node("TouchIdle", (200, 120))
            node = window.controller.get_node(created)
            window.controller.document.path = str(Path(temp_dir) / "pending_input_save.json")
            window.controller.pathChanged.emit(window.controller.document.path)
            draw_name_edit = window.inspector_form._bindings["draw_able_name"].widget
            draw_name_edit.setText("TouchIdlePendingSave")

            saved = window._save_current_file(silent=True)

            self.assertIsNotNone(saved)
            loaded = load_document(window.controller.schema, saved)
            loaded_node = next(item for item in loaded.nodes if item.uuid == node.uuid)
            self.assertEqual("TouchIdlePendingSave", loaded_node.fields["draw_able_name"])
            window._mark_saved_checkpoint(saved=True)
        window.close()

    def test_close_prompts_to_save_dirty_document(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(temp_dir, prefer_saved_workspace=False)
            initial = next(node for node in window.controller.document.nodes if node.type == "Initial")
            window.controller.update_field(initial.uuid, "author", "asahi", "simple")
            window.controller.update_field(initial.uuid, "ship_skin_id", 302291, "simple")
            window.controller.update_field(initial.uuid, "memo", "mingji_2", "simple")
            window.controller.update_field(initial.uuid, "CharName", "??", "simple")
            window.controller.document.path = str(Path(temp_dir) / "close_prompt.json")
            window.controller.pathChanged.emit(window.controller.document.path)
            window._mark_saved_checkpoint(saved=True)
            window.controller.update_field(initial.uuid, "memo", "changed_before_close", "simple")

            def choose_save(box):
                for button in box.buttons():
                    if button.text() == "保存":
                        box._forced_clicked_button = button
                        break

            with patch.object(QMessageBox, "exec", choose_save), patch.object(
                QMessageBox, "clickedButton", lambda box: getattr(box, "_forced_clicked_button", None)
            ), patch.object(window, "_save_current_file", wraps=window._save_current_file) as save_mock:
                self.assertTrue(window._confirm_safe_to_close())
                self.assertTrue(save_mock.called)
            window._mark_saved_checkpoint(saved=True)
        window.close()

    def test_close_does_not_prompt_when_initial_meta_is_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(temp_dir, prefer_saved_workspace=False)
            initial = next(node for node in window.controller.document.nodes if node.type == "Initial")
            window.controller.update_field(initial.uuid, "tips", "draft only", "simple")

            with patch.object(QMessageBox, "exec", side_effect=AssertionError("should not prompt")), patch.object(
                window, "_save_current_file", wraps=window._save_current_file
            ) as save_mock:
                self.assertTrue(window._confirm_safe_to_close())
                self.assertFalse(save_mock.called)
            window._mark_saved_checkpoint(saved=True)
        window.close()

    def test_switching_file_auto_saves_incomplete_draft_without_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            save_document(get_default_schema(), create_document(get_default_schema()), root / "target.json")

            window = MainWindow(root, prefer_saved_workspace=False)
            draft_path = root / "draft.json"
            window.controller.document.path = str(draft_path)
            window.controller.pathChanged.emit(str(draft_path))
            window._mark_saved_checkpoint(saved=False)

            target_item = None
            for index in range(window.file_list.count()):
                item = window.file_list.item(index)
                payload = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(payload, dict) and payload.get("kind") == "file" and payload.get("path") == "target.json":
                    target_item = item
                    break

            self.assertIsNotNone(target_item)
            window._open_selected_file(target_item)

            self.assertTrue(draft_path.exists())
            saved_payload = json.loads(draft_path.read_text(encoding="utf-8"))
            self.assertEqual("l2d_config_editor/v1", saved_payload["editor_signature"])
            self.assertEqual(str(root / "target.json"), window.controller.document.path)
            window._mark_saved_checkpoint(saved=True)
        window.close()

    def test_group_header_click_toggles_collapsed_children(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "group1").mkdir()
            save_document(get_default_schema(), create_document(get_default_schema()), root / "group1" / "a.json")
            window = MainWindow(root, prefer_saved_workspace=False)
            initial_count = window.file_list.count()
            header = window.file_list.item(0)
            window._handle_file_list_item_clicked(header)
            collapsed_count = window.file_list.count()
            self.assertLess(collapsed_count, initial_count)
            window._mark_saved_checkpoint(saved=True)
        window.close()

    def test_zooming_out_expands_node_header_height(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(temp_dir, prefer_saved_workspace=False)
            initial = next(node for node in window.controller.document.nodes if node.type == "Initial")
            window.controller.update_field(initial.uuid, "author", "asahi", "simple")
            window.controller.update_field(initial.uuid, "ship_skin_id", 302291, "simple")
            window.controller.update_field(initial.uuid, "memo", "mingji_2", "simple")
            window.controller.update_field(initial.uuid, "CharName", "??", "simple")
            created = window.controller.create_node("TouchIdle", (200, 120))
            item = window.canvas.node_items[created]
            window.controller.update_field(created, "tips", "这是一个很长很长的标题备注用于测试缩小画布时的头部高度自适应", "simple")
            original_header_height = item._header_height
            window.canvas.scale(0.5, 0.5)
            window.canvas._refresh_scale_sensitive_nodes()
            self.app.processEvents()
            self.assertGreater(item._header_height, original_header_height)
            window._mark_saved_checkpoint(saved=True)
        window.close()

    def test_zooming_in_pushes_node_title_below_accent_bar(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(temp_dir, prefer_saved_workspace=False)
            initial = next(node for node in window.controller.document.nodes if node.type == "Initial")
            window.controller.update_field(initial.uuid, "author", "asahi", "simple")
            window.controller.update_field(initial.uuid, "ship_skin_id", 302291, "simple")
            window.controller.update_field(initial.uuid, "memo", "mingji_2", "simple")
            window.controller.update_field(initial.uuid, "CharName", "??", "simple")
            created = window.controller.create_node("TouchDrag", (200, 120))
            item = window.canvas.node_items[created]
            window.controller.update_field(created, "tips", "大方向深V的鬼斧神工都是方法是大哥", "simple")
            baseline_title_y = item._title_rect.y()
            window.canvas.scale(1.8, 1.8)
            window.canvas._refresh_scale_sensitive_nodes()
            self.app.processEvents()
            self.assertGreater(item._title_rect.y(), baseline_title_y)
            self.assertGreaterEqual(item._title_rect.y(), 14.0)
            window._mark_saved_checkpoint(saved=True)
        window.close()

    def test_new_file_starts_as_unsaved_draft_without_filename_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(temp_dir, prefer_saved_workspace=False)
            window._create_new_file()
            self.assertIsNone(window.controller.document.path)
            self.assertFalse(window.save_action.isEnabled())
            window._mark_saved_checkpoint(saved=True)
        window.close()

    def test_switching_file_preserves_undo_history_after_auto_save(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            doc_a = create_document(get_default_schema())
            initial_a = next(node for node in doc_a.nodes if node.type == "Initial")
            initial_a.fields["author"] = "asahi"
            initial_a.fields["ship_skin_id"] = 302291
            initial_a.fields["memo"] = "file_a"
            initial_a.fields["CharName"] = "??A"
            reassign_function_ids(get_default_schema(), doc_a)
            save_document(get_default_schema(), doc_a, root / "a.json")

            doc_b = create_document(get_default_schema())
            initial_b = next(node for node in doc_b.nodes if node.type == "Initial")
            initial_b.fields["author"] = "asahi"
            initial_b.fields["ship_skin_id"] = 302292
            initial_b.fields["memo"] = "file_b"
            initial_b.fields["CharName"] = "??B"
            reassign_function_ids(get_default_schema(), doc_b)
            save_document(get_default_schema(), doc_b, root / "b.json")

            window = MainWindow(root, prefer_saved_workspace=False)
            window._open_existing_session_or_file(root / "a.json")
            created = window.controller.create_node("TouchIdle", (200, 120))
            self.assertIsNotNone(created)

            window._open_existing_session_or_file(root / "b.json")
            window._open_existing_session_or_file(root / "a.json")

            self.assertTrue(window.controller.undo_stack.canUndo())
            window.controller.undo_stack.undo()
            self.assertIsNone(window.controller.get_node(created))
            window._mark_saved_checkpoint(saved=True)
        window.close()

    def test_optimize_layout_keeps_unconnected_nodes_fixed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(temp_dir, prefer_saved_workspace=False)
            initial = next(node for node in window.controller.document.nodes if node.type == "Initial")
            window.controller.update_field(initial.uuid, "author", "asahi", "simple")
            window.controller.update_field(initial.uuid, "ship_skin_id", 302291, "simple")
            window.controller.update_field(initial.uuid, "memo", "mingji_2", "simple")
            window.controller.update_field(initial.uuid, "CharName", "??", "simple")

            first = window.controller.create_node("TouchIdle", (420, 280))
            second = window.controller.create_node("TouchDrag", (80, 120))
            isolated = window.controller.create_node("TouchDrag", (880, 520))
            window.controller.add_connection(first, second)
            isolated_before = dict(window.controller.get_node(isolated).ui_position)

            changed = window.canvas.optimize_connection_layout()

            self.assertTrue(changed)
            self.assertEqual(isolated_before, window.controller.get_node(isolated).ui_position)
            self.assertEqual(1, len(window.controller.document.connections))
            window._mark_saved_checkpoint(saved=True)
        window.close()

    def test_optimize_layout_moves_attached_comment_with_component(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(temp_dir, prefer_saved_workspace=False)
            initial = next(node for node in window.controller.document.nodes if node.type == "Initial")
            window.controller.update_field(initial.uuid, "author", "asahi", "simple")
            window.controller.update_field(initial.uuid, "ship_skin_id", 302291, "simple")
            window.controller.update_field(initial.uuid, "memo", "mingji_2", "simple")
            window.controller.update_field(initial.uuid, "CharName", "??", "simple")

            first = window.controller.create_node("TouchIdle", (420, 280))
            second = window.controller.create_node("TouchDrag", (80, 120))
            comment_uuid = window.controller.create_node("Comment", (450, 110))
            window.controller.add_connection(first, second)

            comment_node = window.controller.get_node(comment_uuid)
            comment_node.ui_size = {"width": 420.0, "height": 220.0}
            window.controller.nodeUpdated.emit(comment_uuid)
            comment_before = dict(comment_node.ui_position)
            size_before = dict(comment_node.ui_size)

            changed = window.canvas.optimize_connection_layout()

            self.assertTrue(changed)
            comment_after = window.controller.get_node(comment_uuid)
            self.assertNotEqual(comment_before, comment_after.ui_position)
            self.assertEqual(size_before, comment_after.ui_size)
            window._mark_saved_checkpoint(saved=True)
        window.close()

    def test_optimize_layout_still_works_when_some_nodes_are_locked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(temp_dir, prefer_saved_workspace=False)
            initial = next(node for node in window.controller.document.nodes if node.type == "Initial")
            window.controller.update_field(initial.uuid, "author", "asahi", "simple")
            window.controller.update_field(initial.uuid, "ship_skin_id", 302291, "simple")
            window.controller.update_field(initial.uuid, "memo", "mingji_2", "simple")
            window.controller.update_field(initial.uuid, "CharName", "测试", "simple")

            first = window.controller.create_node("TouchIdle", (420, 280))
            second = window.controller.create_node("TouchDrag", (640, 420))
            third = window.controller.create_node("TouchIdle", (80, 120))
            window.controller.add_connection(first, second)
            window.controller.add_connection(second, third)
            locked_before = dict(window.controller.get_node(first).ui_position)
            second_before = dict(window.controller.get_node(second).ui_position)
            third_before = dict(window.controller.get_node(third).ui_position)
            window.controller.set_node_locked(first, True)

            changed = window.canvas.optimize_connection_layout()

            self.assertTrue(changed)
            self.assertEqual(locked_before, window.controller.get_node(first).ui_position)
            self.assertTrue(
                second_before != window.controller.get_node(second).ui_position
                or third_before != window.controller.get_node(third).ui_position
            )
            window._mark_saved_checkpoint(saved=True)
        window.close()


if __name__ == "__main__":
    unittest.main()
