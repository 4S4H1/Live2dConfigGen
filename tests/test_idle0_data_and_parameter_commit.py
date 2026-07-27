import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from l2d_config_editor.controller import EditorController
from l2d_config_editor.logic import (
    create_document,
    create_node,
    create_template_document,
    document_to_csv_rows,
    export_document_dict,
    get_default_schema,
    load_document,
    save_document,
)
from l2d_config_editor.main_window import MainWindow


class Idle0DocumentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = get_default_schema()

    def test_new_document_starts_with_single_idle0_root_and_hidden_default_state(self) -> None:
        document = create_document(self.schema)

        self.assertEqual(["Idle0"], [node.type for node in document.nodes])
        self.assertEqual({}, document.nodes[0].fields)
        self.assertEqual("idle0", document.meta.default_state)
    def test_template_document_writes_hidden_meta_without_initial_node(self) -> None:
        document = create_template_document(
            self.schema,
            version="2026-07-11",
            char_name="角色A",
            memo="role_a",
            ship_skin_id=1001,
        )

        self.assertEqual(["Idle0"], [node.type for node in document.nodes])
        self.assertEqual("2026-07-11", document.meta.version)
        self.assertEqual("角色A", document.meta.CharName)
        self.assertEqual("role_a", document.meta.memo)
        self.assertEqual(1001, document.meta.ship_skin_id)
        self.assertEqual("idle0", document.meta.default_state)

    def test_template_document_accepts_complete_hidden_metadata(self) -> None:
        document = create_template_document(
            self.schema,
            version="2026-07-11",
            char_name="角色A",
            memo="role_a",
            ship_skin_id=1001,
            author="asahi",
            tips="泳装",
            react_condition="0,17",
            default_state="idle0",
        )

        self.assertEqual("asahi", document.meta.author)
        self.assertEqual("泳装", document.meta.tips)
        self.assertEqual("0,17", document.meta.react_condition)
        self.assertEqual("idle0", document.meta.default_state)
        self.assertTrue(document.state.is_meta_ready)

    def test_idle0_is_not_exported_as_a_csv_function_row(self) -> None:
        document = create_document(self.schema)

        self.assertEqual([], document_to_csv_rows(self.schema, document))

    def test_new_document_exports_format_version_three_with_idle0_root(self) -> None:
        payload = export_document_dict(self.schema, create_document(self.schema))

        self.assertEqual(3, payload["format_version"])
        self.assertEqual("idle0", payload["meta"]["default_state"])
        self.assertEqual(["Idle0"], [node["type"] for node in payload["nodes"]])

    def test_idle0_can_only_be_an_immutable_outgoing_graph_root(self) -> None:
        controller = EditorController()
        controller.document = create_template_document(
            controller.schema,
            version="2026-07-11",
            char_name="角色A",
            memo="role_a",
            ship_skin_id=1001,
        )
        controller.document.meta.author = "asahi"
        controller.refresh_derived()
        root_uuid = next(node.uuid for node in controller.document.nodes if node.type == "Idle0")
        touch_uuid = controller.create_node("TouchIdle", (300.0, 120.0))

        controller.add_connection(touch_uuid, root_uuid)
        self.assertEqual([], controller.document.connections)

        controller.add_connection(root_uuid, touch_uuid)
        self.assertEqual([(root_uuid, touch_uuid)], [
            (connection.from_uuid, connection.to_uuid)
            for connection in controller.document.connections
        ])
        self.assertFalse(controller.can_copy_node(root_uuid))
        self.assertIsNone(controller.serialize_selection([root_uuid]))
        controller.remove_nodes([root_uuid])
        self.assertIsNotNone(controller.get_node(root_uuid))
        self.assertIsNone(controller.create_group([root_uuid, touch_uuid]))

    def test_external_clipboard_payload_cannot_inject_another_idle0(self) -> None:
        controller = EditorController()
        root_uuid = next(node.uuid for node in controller.document.nodes if node.type == "Idle0")
        payload = json.dumps(
            {
                "nodes": [
                    {
                        "uuid": "injected-root",
                        "type": "Idle0",
                        "ui_position": {"x": 500.0, "y": 300.0},
                    }
                ],
                "connections": [],
                "source_bounds": {"min_x": 500.0, "min_y": 300.0, "max_x": 600.0, "max_y": 360.0},
            }
        ).encode("utf-8")

        pasted = controller.paste_payload(payload, (700.0, 400.0))

        self.assertEqual([], pasted)
        self.assertEqual([root_uuid], [node.uuid for node in controller.document.nodes if node.type == "Idle0"])

    def test_legacy_initial_migrates_in_place_to_idle0_and_keeps_its_outgoing_connection(self) -> None:
        payload = {
            "editor_signature": "l2d_config_editor/v1",
            "meta": {},
            "nodes": [
                {
                    "uuid": "legacy-root",
                    "type": "Initial",
                    "ui_position": {"x": 15.0, "y": 25.0},
                    "version": "2026-07-11",
                    "author": "asahi",
                    "ship_skin_id": 1001,
                    "memo": "role_a",
                    "defaultState": "idle0",
                    "react_condition": "0,17",
                    "tips": "legacy",
                    "CharName": "角色A",
                },
                {
                    "uuid": "touch-1",
                    "type": "TouchIdle",
                    "ui_position": {"x": 300.0, "y": 25.0},
                    "draw_able_name": "TouchIdle1",
                    "parameter": "Paramtouch_idle1",
                    "action_trigger": "{type = 2 ,action = 'touch_idle1'}",
                    "action_trigger_active": "{enable = {},ignore = {},idle = 1}",
                },
            ],
            "connections": [{"from_uuid": "legacy-root", "to_uuid": "touch-1"}],
            "canvas_view": {"scale": 1.0, "offset_x": 0.0, "offset_y": 0.0},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "legacy.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            document = load_document(self.schema, path)

        self.assertEqual(["Idle0", "TouchIdle"], [node.type for node in document.nodes])
        root = document.nodes[0]
        self.assertEqual("legacy-root", root.uuid)
        self.assertEqual({"x": 15.0, "y": 25.0}, root.ui_position)
        self.assertEqual("legacy-root", document.connections[0].from_uuid)
        self.assertEqual("touch-1", document.connections[0].to_uuid)
        self.assertEqual("asahi", document.meta.author)
        self.assertEqual(1001, document.meta.ship_skin_id)
        self.assertEqual("role_a", document.meta.memo)
        self.assertEqual("角色A", document.meta.CharName)
        self.assertEqual("idle0", document.meta.default_state)

    def test_migration_merges_multiple_legacy_roots_into_one_existing_idle0(self) -> None:
        payload = {
            "editor_signature": "l2d_config_editor/v1",
            "meta": {
                "version": "2026-07-11",
                "author": "asahi",
                "ship_skin_id": 1001,
                "memo": "role_a",
                "react_condition": "0",
                "tips": "",
                "CharName": "角色A",
            },
            "nodes": [
                {"uuid": "root-a", "type": "Initial", "ui_position": {"x": 10.0, "y": 20.0}},
                {"uuid": "idle-existing", "type": "Idle0", "ui_position": {"x": 30.0, "y": 40.0}},
                {"uuid": "root-b", "type": "Initial", "ui_position": {"x": 50.0, "y": 60.0}},
                {
                    "uuid": "touch-1",
                    "type": "TouchIdle",
                    "ui_position": {"x": 300.0, "y": 20.0},
                    "draw_able_name": "TouchIdle1",
                    "parameter": "Paramtouch_idle1",
                    "action_trigger": "{type = 2 ,action = 'touch_idle1'}",
                    "action_trigger_active": "{enable = {},ignore = {},idle = 1}",
                },
            ],
            "connections": [
                {"from_uuid": "root-a", "to_uuid": "touch-1"},
                {"from_uuid": "root-b", "to_uuid": "touch-1"},
                {"from_uuid": "idle-existing", "to_uuid": "touch-1"},
                {"from_uuid": "touch-1", "to_uuid": "root-b"},
            ],
            "canvas_view": {"scale": 1.0, "offset_x": 0.0, "offset_y": 0.0},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "multiple-roots.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            document = load_document(self.schema, path)

        roots = [node for node in document.nodes if node.type == "Idle0"]
        self.assertEqual(1, len(roots))
        self.assertEqual("idle-existing", roots[0].uuid)
        self.assertEqual({"x": 30.0, "y": 40.0}, roots[0].ui_position)
        self.assertEqual(
            [("idle-existing", "touch-1")],
            [(connection.from_uuid, connection.to_uuid) for connection in document.connections],
        )

    def test_migration_merges_roots_even_when_legacy_and_idle0_share_a_uuid(self) -> None:
        payload = {
            "editor_signature": "l2d_config_editor/v1",
            "meta": {},
            "nodes": [
                {
                    "uuid": "shared-root",
                    "type": "Initial",
                    "ui_position": {"x": 10.0, "y": 20.0},
                    "author": "asahi",
                    "ship_skin_id": 1001,
                    "memo": "role_a",
                    "CharName": "角色A",
                },
                {
                    "uuid": "shared-root",
                    "type": "Idle0",
                    "ui_position": {"x": 30.0, "y": 40.0},
                },
            ],
            "connections": [],
            "canvas_view": {"scale": 1.0, "offset_x": 0.0, "offset_y": 0.0},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "same-root-uuid.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            document = load_document(self.schema, path)

        roots = [node for node in document.nodes if node.type == "Idle0"]
        self.assertEqual(1, len(roots))
        self.assertEqual("shared-root", roots[0].uuid)
        self.assertEqual({"x": 30.0, "y": 40.0}, roots[0].ui_position)

    def test_migration_drops_incoming_edge_and_group_membership_for_single_idle0(self) -> None:
        payload = {
            "editor_signature": "l2d_config_editor/v1",
            "meta": {
                "version": "2026-07-11",
                "author": "asahi",
                "ship_skin_id": 1001,
                "memo": "role_a",
                "react_condition": "0",
                "tips": "",
                "CharName": "角色A",
            },
            "nodes": [
                {"uuid": "idle-root", "type": "Idle0", "ui_position": {"x": 10.0, "y": 20.0}},
                {
                    "uuid": "touch-1",
                    "type": "TouchIdle",
                    "ui_position": {"x": 300.0, "y": 20.0},
                    "draw_able_name": "TouchIdle1",
                    "parameter": "Paramtouch_idle1",
                    "action_trigger": "{type = 2 ,action = 'touch_idle1'}",
                    "action_trigger_active": "{enable = {},ignore = {},idle = 1}",
                },
            ],
            "groups": [{"uuid": "group-1", "title": "G", "node_uuids": ["idle-root", "touch-1"]}],
            "connections": [
                {"from_uuid": "touch-1", "to_uuid": "idle-root"},
                {"from_uuid": "idle-root", "to_uuid": "touch-1"},
            ],
            "canvas_view": {"scale": 1.0, "offset_x": 0.0, "offset_y": 0.0},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "single-root.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            document = load_document(self.schema, path)

        self.assertEqual(
            [("idle-root", "touch-1")],
            [(connection.from_uuid, connection.to_uuid) for connection in document.connections],
        )
        self.assertEqual(["touch-1"], document.groups[0].node_uuids)

    def test_manual_generated_fields_roundtrip_without_reserved_key_pollution(self) -> None:
        document = create_template_document(
            self.schema,
            version="2026-07-11",
            char_name="角色A",
            memo="role_a",
            ship_skin_id=1001,
        )
        row = create_node(self.schema, document, "ParameterTrigger", (220.0, 120.0))
        row.fields["draw_able_name"] = "CustomFrame99"
        row.fields["parameter"] = "touch_drag99"
        row.manual_fields.update({"draw_able_name", "parameter"})
        document.nodes.append(row)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "manual-fields.json"
            save_document(self.schema, document, path)
            raw = json.loads(path.read_text(encoding="utf-8"))
            loaded = load_document(self.schema, path)

        raw_row = next(node for node in raw["nodes"] if node["uuid"] == row.uuid)
        loaded_row = next(node for node in loaded.nodes if node.uuid == row.uuid)
        self.assertEqual(["draw_able_name", "parameter"], raw_row["manual_fields"])
        self.assertEqual("CustomFrame99", loaded_row.fields["draw_able_name"])
        self.assertEqual("touch_drag99", loaded_row.fields["parameter"])
        self.assertEqual({"draw_able_name", "parameter"}, loaded_row.manual_fields)
        self.assertTrue({"manual_fields", "type_slot", "export_slot"}.isdisjoint(loaded_row.fields))

    def test_return_default_idle_is_touchidle_shaped_but_has_no_editable_target(self) -> None:
        document = create_template_document(
            self.schema,
            version="2026-07-11",
            char_name="角色A",
            memo="role_a",
            ship_skin_id=1001,
        )

        node = create_node(self.schema, document, "ReturnDefaultIdle", (220.0, 120.0))

        definition = self.schema.nodes["ReturnDefaultIdle"]
        self.assertEqual("function", definition.category)
        self.assertNotIn("action_trigger_active", {field.key for field in definition.fields})
        self.assertEqual(0, node.fields["target_idle"])
        self.assertEqual("TouchIdle1", node.fields["draw_able_name"])
        self.assertEqual("Paramtouch_idle1", node.fields["parameter"])
        self.assertIn("action = 'touch_idle1'", node.fields["action_trigger"])
        self.assertEqual("{enable = {},ignore = {},idle = 0}", node.fields["action_trigger_active"])

    def test_return_default_idle_rejects_target_edits_and_hard_cut_stays_zero(self) -> None:
        controller = EditorController()
        controller.document = create_template_document(
            controller.schema,
            version="2026-07-11",
            char_name="角色A",
            memo="role_a",
            ship_skin_id=1001,
        )
        controller.document.meta.author = "asahi"
        controller.refresh_derived()
        controller.set_numeric_linkage_enabled(True)
        node_uuid = controller.create_node("ReturnDefaultIdle", (220.0, 120.0))

        controller.update_field(node_uuid, "action_trigger_active", 9, "simple")
        controller.update_field(node_uuid, "target_idle", 9, "advanced")
        controller.update_field(node_uuid, "parameter", "Paramtouch_idle42", "simple")
        node = controller.get_node(node_uuid)
        self.assertEqual(0, node.fields["target_idle"])
        self.assertEqual("{enable = {},ignore = {},idle = 0}", node.fields["action_trigger_active"])
        self.assertEqual("TouchIdle1", node.fields["draw_able_name"])

        controller.update_field(node_uuid, "transition_type", "hard", "simple")
        node = controller.get_node(node_uuid)
        self.assertEqual("{type = 2 ,action = 'idle',target = 1}", node.fields["action_trigger"])
        self.assertEqual("{enable = {},ignore = {},idle = 0,idle_focus = 1}", node.fields["action_trigger_active"])

    def test_return_default_idle_copy_and_reopen_remain_fixed_to_zero(self) -> None:
        controller = EditorController()
        controller.document = create_template_document(
            controller.schema,
            version="2026-07-11",
            char_name="角色A",
            memo="role_a",
            ship_skin_id=1001,
        )
        controller.document.meta.author = "asahi"
        controller.refresh_derived()
        source_uuid = controller.create_node("ReturnDefaultIdle", (220.0, 120.0))
        controller.update_field(source_uuid, "transition_type", "hard", "simple")

        payload = controller.serialize_selection([source_uuid])
        copied_uuid = controller.paste_payload(payload, (520.0, 120.0))[0]

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "return-default-idle.json"
            controller.save_document(str(path))
            loaded = load_document(controller.schema, path)

        for node_uuid in (source_uuid, copied_uuid):
            node = next(item for item in loaded.nodes if item.uuid == node_uuid)
            self.assertEqual("ReturnDefaultIdle", node.type)
            self.assertEqual(0, node.fields["target_idle"])
            self.assertIn("idle = 0", node.fields["action_trigger_active"])

    def test_legacy_return_default_idle_repairs_active_gate_when_linkage_is_disabled(self) -> None:
        payload = {
            "editor_signature": "l2d_config_editor/v1",
            "meta": {
                "version": "2026-07-11",
                "author": "asahi",
                "ship_skin_id": 1001,
                "memo": "role_a",
                "react_condition": "0",
                "tips": "",
                "CharName": "角色A",
            },
            "nodes": [
                {"uuid": "idle-root", "type": "Idle0", "ui_position": {"x": 10.0, "y": 20.0}},
                {
                    "uuid": "return-animated",
                    "type": "ReturnDefaultIdle",
                    "ui_position": {"x": 300.0, "y": 20.0},
                    "numeric_linkage_enabled": False,
                    "manual_fields": ["action_trigger"],
                    "transition_type": "animated",
                    "draw_able_name": "TouchIdle1",
                    "parameter": "Paramtouch_idle1",
                    "target_idle": 9,
                    "action_trigger": "{type = 2 ,action = 'custom_anim'}",
                    "action_trigger_active": "{enable = {},ignore = {},idle = 9}",
                },
                {
                    "uuid": "return-hard",
                    "type": "ReturnDefaultIdle",
                    "ui_position": {"x": 600.0, "y": 20.0},
                    "numeric_linkage_enabled": False,
                    "manual_fields": ["action_trigger"],
                    "transition_type": "hard",
                    "draw_able_name": "TouchIdle2",
                    "parameter": "Paramtouch_idle2",
                    "target_idle": 7,
                    "action_trigger": "{type = 2 ,action = 'custom_hard'}",
                    "action_trigger_active": "{enable = {},ignore = {},idle = 7}",
                },
            ],
            "connections": [],
            "canvas_view": {"scale": 1.0, "offset_x": 0.0, "offset_y": 0.0},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "legacy-return.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            document = load_document(self.schema, path)

        animated = next(node for node in document.nodes if node.uuid == "return-animated")
        hard = next(node for node in document.nodes if node.uuid == "return-hard")
        self.assertFalse(animated.numeric_linkage_enabled)
        self.assertEqual(0, animated.fields["target_idle"])
        self.assertEqual("{enable = {},ignore = {},idle = 0}", animated.fields["action_trigger_active"])
        self.assertEqual("{type = 2 ,action = 'custom_anim'}", animated.fields["action_trigger"])
        self.assertEqual(0, hard.fields["target_idle"])
        self.assertEqual("{enable = {},ignore = {},idle = 0,idle_focus = 1}", hard.fields["action_trigger_active"])
        self.assertEqual("{type = 2 ,action = 'custom_hard'}", hard.fields["action_trigger"])


class ParameterTablePendingCommitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_save_commits_active_parameter_table_text_without_enter_or_blur(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(temp_dir, prefer_saved_workspace=False)
            window.controller.document.meta.author = "asahi"
            window.controller.document.meta.ship_skin_id = 1001
            window.controller.document.meta.memo = "role_a"
            window.controller.document.meta.CharName = "角色A"
            window.controller.refresh_derived()
            row_uuid = window.controller.create_node("ParameterTrigger", (220.0, 120.0))
            table = window.canvas.table_row_to_item[row_uuid]
            self.assertTrue(table.begin_cell_edit(row_uuid, "draw_able_name"))
            table._editor_proxy.widget().setText("FramePendingSave")
            path = Path(temp_dir) / "pending-table-edit.json"
            window.controller.document.path = str(path)
            window.controller.pathChanged.emit(str(path))

            saved = window._save_current_file(silent=True)
            loaded = load_document(window.controller.schema, saved)

            loaded_row = next(node for node in loaded.nodes if node.uuid == row_uuid)
            self.assertEqual("FramePendingSave", loaded_row.fields["draw_able_name"])
            window._mark_saved_checkpoint(saved=True)
            window.close()

    def test_real_double_click_parameter_edit_survives_immediate_save_and_linkage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(temp_dir, prefer_saved_workspace=False)
            window.controller.document.meta.author = "asahi"
            window.controller.document.meta.ship_skin_id = 1001
            window.controller.document.meta.memo = "role_a"
            window.controller.document.meta.CharName = "角色A"
            window.controller.refresh_derived()
            row_uuid = window.controller.create_node("ParameterTrigger", (220.0, 120.0))
            table = window.canvas.table_row_to_item[row_uuid]
            self.assertEqual(
                "touch_drag1",
                window.controller.get_node(row_uuid).fields["parameter"],
            )
            window.show()
            window.canvas.centerOn(table)
            self.app.processEvents()
            cell = table._cell_rects[(row_uuid, "parameter")]
            viewport_pos = window.canvas.mapFromScene(
                table.mapToScene(cell.center())
            )

            QTest.mouseDClick(
                window.canvas.viewport(),
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                viewport_pos,
            )
            self.app.processEvents()
            editor = table._editor_proxy.widget()
            self.assertIsNotNone(editor)
            QTest.keyClick(
                editor,
                Qt.Key.Key_A,
                Qt.KeyboardModifier.ControlModifier,
            )
            QTest.keyClicks(editor, "touch_drag11")

            path = Path(temp_dir) / "parameter-double-click.json"
            window.controller.document.path = str(path)
            window.controller.pathChanged.emit(str(path))
            saved = window._save_current_file(silent=True)
            self.assertEqual(str(path), saved)

            runtime = window.controller.get_node(row_uuid)
            self.assertEqual("touch_drag11", runtime.fields["parameter"])
            self.assertIn("parameter", runtime.manual_fields)
            raw_row = next(
                node
                for node in json.loads(path.read_text(encoding="utf-8"))["nodes"]
                if node["uuid"] == row_uuid
            )
            self.assertEqual("touch_drag11", raw_row["parameter"])
            self.assertIn("parameter", raw_row["manual_fields"])

            window.controller.undo_stack.undo()
            self.assertEqual(
                "touch_drag1",
                window.controller.get_node(row_uuid).fields["parameter"],
            )
            self.assertNotIn(
                "parameter",
                window.controller.get_node(row_uuid).manual_fields,
            )
            window.controller.undo_stack.redo()
            self.assertEqual(
                "touch_drag11",
                window.controller.get_node(row_uuid).fields["parameter"],
            )
            window.controller.set_numeric_linkage_enabled(True)
            window.controller.set_numeric_linkage_enabled(False)
            self.assertEqual(
                "touch_drag11",
                window.controller.get_node(row_uuid).fields["parameter"],
            )
            self.assertIn(
                "parameter",
                window.controller.get_node(row_uuid).manual_fields,
            )
            loaded = load_document(window.controller.schema, path)
            loaded_row = next(node for node in loaded.nodes if node.uuid == row_uuid)
            self.assertEqual("touch_drag11", loaded_row.fields["parameter"])
            self.assertIn("parameter", loaded_row.manual_fields)
            window._mark_saved_checkpoint(saved=True)
            window.close()

    def test_switching_parameter_table_cells_commits_the_previous_editor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(temp_dir, prefer_saved_workspace=False)
            window.controller.document.meta.author = "asahi"
            window.controller.document.meta.ship_skin_id = 1001
            window.controller.document.meta.memo = "role_a"
            window.controller.document.meta.CharName = "角色A"
            window.controller.refresh_derived()
            row_uuid = window.controller.create_node("ParameterTrigger", (220.0, 120.0))
            table = window.canvas.table_row_to_item[row_uuid]

            self.assertTrue(table.begin_cell_edit(row_uuid, "draw_able_name"))
            table._editor_proxy.widget().setText("FrameCellSwitch")
            self.assertTrue(table.begin_cell_edit(row_uuid, "parameter"))

            row = window.controller.get_node(row_uuid)
            self.assertEqual("FrameCellSwitch", row.fields["draw_able_name"])
            self.assertEqual((row_uuid, "parameter"), table._editor_target)
            table.commit_pending_edit()
            window._mark_saved_checkpoint(saved=True)
            window.close()

    def test_save_commits_pending_editors_in_multiple_parameter_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = MainWindow(temp_dir, prefer_saved_workspace=False)
            window.controller.document.meta.author = "asahi"
            window.controller.document.meta.ship_skin_id = 1001
            window.controller.document.meta.memo = "role_a"
            window.controller.document.meta.CharName = "角色A"
            window.controller.refresh_derived()
            first_uuid = window.controller.create_node("ParameterTrigger", (220.0, 120.0))
            second_uuid = window.controller.create_node("ParameterTrigger", (820.0, 120.0))
            first_table = window.canvas.table_row_to_item[first_uuid]
            second_table = window.canvas.table_row_to_item[second_uuid]
            self.assertIsNot(first_table, second_table)
            self.assertTrue(first_table.begin_cell_edit(first_uuid, "draw_able_name"))
            first_table._editor_proxy.widget().setText("FramePendingOne")
            self.assertTrue(second_table.begin_cell_edit(second_uuid, "draw_able_name"))
            second_table._editor_proxy.widget().setText("FramePendingTwo")
            path = Path(temp_dir) / "multiple-pending-table-edits.json"
            window.controller.document.path = str(path)
            window.controller.pathChanged.emit(str(path))

            saved = window._save_current_file(silent=True)
            loaded = load_document(window.controller.schema, saved)

            loaded_fields = {node.uuid: node.fields for node in loaded.nodes}
            self.assertEqual("FramePendingOne", loaded_fields[first_uuid]["draw_able_name"])
            self.assertEqual("FramePendingTwo", loaded_fields[second_uuid]["draw_able_name"])
            window._mark_saved_checkpoint(saved=True)
            window.close()


if __name__ == "__main__":
    unittest.main()
