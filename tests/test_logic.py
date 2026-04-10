import tempfile
import unittest
from pathlib import Path

from l2d_config_editor.logic import (
    create_document,
    create_node,
    document_to_csv_rows,
    export_document_dict,
    load_document,
    reassign_function_ids,
    save_document,
    search_document,
    validate_document,
)
from l2d_config_editor.models import ConnectionRecord


class LogicTests(unittest.TestCase):
    def test_default_document_contains_initial(self) -> None:
        document = create_document()
        self.assertEqual(1, len([node for node in document.nodes if node.type == "Initial"]))

    def test_reassign_ids_uses_ship_skin_id_prefix(self) -> None:
        document = create_document()
        initial = document.nodes[0]
        initial.fields["ship_skin_id"] = 302291
        document.nodes.append(create_node("TouchIdle"))
        document.nodes.append(create_node("TouchDrag"))
        reassign_function_ids(document)
        function_ids = [node.fields["id"] for node in document.nodes if node.type in {"TouchIdle", "TouchDrag"}]
        self.assertEqual([30229101, 30229102], function_ids)

    def test_touchidle_rules_sync_target_idle(self) -> None:
        node = create_node("TouchIdle")
        node.fields["target_idle"] = 13
        reassign_function_ids(create_document())
        from l2d_config_editor.logic import apply_auto_rules

        apply_auto_rules(node)
        self.assertEqual("Paramtouch_idle13", node.fields["parameter"])
        self.assertIn("touch_idle13", node.fields["action_trigger"])
        self.assertIn("idle = 13", node.fields["action_trigger_active"])

    def test_touchdrag_rules_sync_target_idle(self) -> None:
        node = create_node("TouchDrag")
        node.fields["target_idle"] = 7
        from l2d_config_editor.logic import apply_auto_rules

        apply_auto_rules(node)
        self.assertEqual("Touch_drag7", node.fields["parameter"])
        self.assertIn("touch_idle7", node.fields["action_trigger"])
        self.assertIn("idle = 7", node.fields["action_trigger_active"])

    def test_drag_direction_updates_offsets(self) -> None:
        node = create_node("TouchDrag")
        node.fields["control_type"] = "drag"
        node.fields["drag_ui_direction"] = "left"
        from l2d_config_editor.logic import apply_auto_rules

        apply_auto_rules(node)
        self.assertEqual(-100, node.fields["offset_x"])
        self.assertEqual(0, node.fields["offset_y"])
        self.assertEqual(1, node.fields["drag_direct"])

    def test_validation_catches_duplicate_parameter(self) -> None:
        document = create_document()
        first = create_node("TouchIdle")
        second = create_node("TouchDrag")
        first.fields["target_idle"] = 5
        second.fields["target_idle"] = 5
        second.fields["parameter"] = first.fields["parameter"]
        document.nodes.extend([first, second])
        issues = validate_document(document)
        self.assertTrue(any("parameter 重复" == issue.message for issue in issues))

    def test_validation_catches_invalid_parts_data(self) -> None:
        document = create_document()
        node = create_node("TouchDrag")
        node.fields["parts_data"] = "1,hello"
        document.nodes.append(node)
        issues = validate_document(document)
        self.assertTrue(any("parts_data 不是合法的逗号分隔数字列表" == issue.message for issue in issues))

    def test_validation_catches_parts_out_of_range(self) -> None:
        document = create_document()
        node = create_node("TouchDrag")
        node.fields["parts_data"] = "2"
        node.fields["range"] = "{0,1}"
        document.nodes.append(node)
        issues = validate_document(document)
        self.assertTrue(any("parts_data 超出了 range 定义范围" == issue.message for issue in issues))

    def test_export_roundtrip_preserves_connections(self) -> None:
        document = create_document()
        idle = create_node("TouchIdle", (100, 120))
        drag = create_node("TouchDrag", (220, 120))
        document.nodes.extend([idle, drag])
        document.connections.append(ConnectionRecord(from_uuid=idle.uuid, to_uuid=drag.uuid))
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.json"
            save_document(document, path)
            loaded = load_document(path)
        self.assertEqual(1, len(loaded.connections))
        self.assertEqual(idle.uuid, loaded.connections[0].from_uuid)
        self.assertEqual(drag.uuid, loaded.connections[0].to_uuid)

    def test_csv_preview_uses_meta_desc(self) -> None:
        document = create_document()
        initial = document.nodes[0]
        initial.fields["ship_skin_id"] = 302291
        initial.fields["memo"] = "mingji_2"
        initial.fields["CharName"] = "名取"
        idle = create_node("TouchIdle")
        document.nodes.append(idle)
        rows = document_to_csv_rows(document)
        self.assertEqual("mingji_2", rows[0].values["desc"])
        self.assertEqual(302291, rows[0].values["ship_skin_id"])

    def test_search_hits_target_idle(self) -> None:
        document = create_document()
        node = create_node("TouchIdle")
        node.fields["target_idle"] = 13
        document.nodes.append(node)
        hits = search_document(document, "13")
        self.assertTrue(any(hit.field_name == "target_idle" for hit in hits))

    def test_export_payload_contains_meta_and_nodes(self) -> None:
        document = create_document()
        document.nodes.append(create_node("Comment"))
        payload = export_document_dict(document)
        self.assertIn("meta", payload)
        self.assertIn("nodes", payload)
        self.assertIn("canvas_view", payload)


if __name__ == "__main__":
    unittest.main()
