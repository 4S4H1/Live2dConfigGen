"""Listener subgraphs through document, controller, CSV and history workflows."""

from __future__ import annotations

import copy
import csv
import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from l2d_config_editor.controller import EditorController
from l2d_config_editor.csv_export import export_current_document_csv
from l2d_config_editor.graph_diff import diff_documents
from l2d_config_editor.listener_catalog import new_listener_graph
from l2d_config_editor.listener_compiler import compile_listener_graph
from l2d_config_editor.logic import (
    document_to_csv_rows,
    export_document_dict,
    export_documents_to_csv,
    load_document,
    load_document_payload,
    save_document,
    validate_document,
    validate_listener_export,
)
from l2d_config_editor.plan import PLAN_TOUCHDRAG_COLOR, PLAN_TOUCHIDLE_COLOR
from l2d_config_editor.tool_service import EditorToolService


class ListenerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from unittest.mock import patch
        feature = patch("l2d_config_editor.features.LISTENER_EDITOR_ENABLED", True)
        feature.start()
        self.addCleanup(feature.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.path = self.workspace / "listener.json"
        self.controller = EditorController()
        self.schema = self.controller.schema
        self.controller.document.meta.ship_skin_id = 101
        self.controller.document.meta.CharName = "监听器测试"
        self.controller.document.meta.author = "测试作者"
        self.controller.document.meta.memo = "asset/path"
        self.controller.refresh_derived()

    @property
    def document(self):
        return self.controller.document

    def add_listener(self):
        uuid = self.controller.create_node("Listener", (180.0, 80.0))
        self.assertIsNotNone(uuid)
        node = self.controller.get_node(uuid)
        self.assertIsNotNone(node.listener_graph)
        return uuid

    def set_amount(self, owner_uuid, amount):
        graph = self.controller.get_node(owner_uuid).listener_graph.clone()
        part = next(part for part in graph.nodes if part.kind == "AddValue")
        part.fields["value"] = amount
        self.assertTrue(self.controller.set_listener_graph(owner_uuid, graph))
        return part.uuid

    def amount(self, owner_uuid):
        return next(part.fields["value"] for part in self.controller.get_node(owner_uuid).listener_graph.nodes
                    if part.kind == "AddValue")

    def save(self):
        self.controller.save_document(str(self.path))
        # The main window marks the saved checkpoint after the successful write.
        self.controller.undo_stack.setClean()

    def test_default_parameter_stays_unique_after_delete_save_and_reload(self):
        first = self.add_listener()
        self.add_listener()
        self.add_listener()
        self.controller.remove_nodes([first])
        self.save()
        self.controller.document = load_document(self.schema, self.path)
        self.add_listener()
        parameters = [node.fields["parameter"] for node in self.document.nodes if node.type == "Listener"]
        self.assertEqual(3, len(set(parameters)))

    def test_conflicting_listener_parameters_are_reported(self):
        first = self.add_listener()
        second = self.add_listener()
        graph = self.controller.get_node(second).listener_graph.clone()
        next(part for part in graph.nodes if part.kind == "ValueState").fields["parameter"] = self.controller.get_node(first).fields["parameter"]
        self.controller.set_listener_graph(second, graph)
        conflicts = [issue for issue in validate_document(self.schema, self.document)
                     if "parameter" in issue.field_keys and "重复" in issue.message]
        self.assertEqual({first, second}, {issue.node_uuid for issue in conflicts})

    def test_new_host_is_one_outer_node_and_round_trips_as_v6(self):
        original_ids = {node.uuid for node in self.document.nodes}
        owner_uuid = self.add_listener()
        graph = self.controller.get_node(owner_uuid).listener_graph
        self.assertEqual("listener", self.schema.nodes["Listener"].category)
        self.assertTrue(compile_listener_graph(graph).valid)
        self.assertGreaterEqual(len(graph.nodes), 3)
        self.assertEqual(original_ids | {owner_uuid}, {node.uuid for node in self.document.nodes})
        internal_ids = {part.uuid for part in graph.nodes}
        self.assertTrue(internal_ids.isdisjoint({node.uuid for node in self.document.nodes}))
        self.assertTrue(internal_ids.isdisjoint({topic.node_uuid for topic in self.document.plan_layout.topics}))
        self.save()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(6, payload["format_version"])
        raw_host = next(node for node in payload["nodes"] if node["uuid"] == owner_uuid)
        self.assertIsInstance(raw_host["listener_graph"]["nodes"], dict)
        loaded = load_document(self.schema, self.path)
        restored = next(node for node in loaded.nodes if node.uuid == owner_uuid)
        self.assertEqual(graph.to_payload(), restored.listener_graph.to_payload())
        self.assertEqual(2, len(loaded.nodes))

    def test_v1_through_v5_documents_without_subgraphs_stay_compatible(self):
        legacy = export_document_dict(self.schema, self.document)
        self.assertEqual(5, legacy["format_version"])
        for version in range(1, 6):
            with self.subTest(version=version):
                payload = copy.deepcopy(legacy)
                payload["format_version"] = version
                if version < 4:
                    payload.pop("plan_layout")
                loaded = load_document_payload(self.schema, payload)
                self.assertTrue(all(node.listener_graph is None for node in loaded.nodes))
                path = self.workspace / f"legacy-{version}.json"
                save_document(self.schema, loaded, path)
                self.assertEqual(5, json.loads(path.read_text(encoding="utf-8"))["format_version"])
                self.assertEqual(1, len(load_document(self.schema, path).nodes))

    def test_new_subgraph_version_is_rejected_without_overwriting_saved_document(self):
        owner_uuid = self.add_listener()
        self.save()
        saved = self.path.read_bytes()
        payload = json.loads(saved)
        host = next(node for node in payload["nodes"] if node["uuid"] == owner_uuid)
        host["listener_graph"]["version"] = 2
        with self.assertRaises(ValueError):
            load_document_payload(self.schema, payload)
        self.assertEqual(saved, self.path.read_bytes())

    def test_incomplete_draft_saves_and_previews_but_strict_exports_leave_files_untouched(self):
        owner_uuid = self.add_listener()
        graph = self.controller.get_node(owner_uuid).listener_graph.clone()
        graph.connections.clear()
        self.assertTrue(self.controller.set_listener_graph(owner_uuid, graph))
        self.assertFalse(compile_listener_graph(graph).valid)
        rows = document_to_csv_rows(self.schema, self.document)
        self.assertEqual(1, len(rows))
        self.assertTrue(any(issue.node_uuid == owner_uuid and issue.severity == "error"
                            for issue in validate_document(self.schema, self.document)))
        self.save()
        self.assertEqual([], next(node for node in load_document(self.schema, self.path).nodes
                                 if node.uuid == owner_uuid).listener_graph.connections)
        saved = self.path.read_bytes()
        output = self.workspace / "existing.csv"
        sentinel = b"existing complete CSV\n"
        output.write_bytes(sentinel)
        with self.assertRaises(ValueError):
            export_documents_to_csv(self.schema, [self.document], output)
        self.assertEqual(sentinel, output.read_bytes())
        names_before = {item.name for item in self.workspace.iterdir()}
        with self.assertRaises(ValueError):
            export_current_document_csv(self.schema, self.document, self.workspace)
        self.assertEqual(names_before, {item.name for item in self.workspace.iterdir()})
        self.assertEqual(saved, self.path.read_bytes())

    def test_graph_edits_share_undo_stack_and_keep_saved_disk_baseline(self):
        owner_uuid = self.add_listener()
        self.save()
        self.assertTrue(self.controller.undo_stack.isClean())
        old_index = self.controller.undo_stack.index()
        self.set_amount(owner_uuid, 2)
        self.assertEqual(old_index + 1, self.controller.undo_stack.index())
        self.assertFalse(self.controller.undo_stack.isClean())
        self.save()
        saved_bytes = self.path.read_bytes()
        baseline = (self.document.path, self.document.disk_stamp, self.document.disk_digest,
                    copy.deepcopy(self.document.history))
        self.controller.undo_stack.undo()
        self.assertEqual(1.0, self.amount(owner_uuid))
        self.assertFalse(self.controller.undo_stack.isClean())
        self.assertEqual(baseline, (self.document.path, self.document.disk_stamp, self.document.disk_digest,
                                   self.document.history))
        self.controller.undo_stack.redo()
        self.assertEqual(2, self.amount(owner_uuid))
        self.assertTrue(self.controller.undo_stack.isClean())
        self.controller.undo_stack.undo()
        self.set_amount(owner_uuid, 3)
        self.assertFalse(self.controller.undo_stack.isClean())
        self.assertFalse(self.controller.undo_stack.canRedo())
        self.assertEqual(saved_bytes, self.path.read_bytes())
        self.save()
        self.assertEqual(3, self.amount(owner_uuid))

    def test_controller_graph_command_detaches_caller_and_undo_snapshots(self):
        owner_uuid = self.add_listener()
        graph = self.controller.get_node(owner_uuid).listener_graph.clone()
        part = next(part for part in graph.nodes if part.kind == "AddValue")
        part.fields["value"] = 4
        self.assertTrue(self.controller.set_listener_graph(owner_uuid, graph, label="组合监听规则"))
        part.fields["value"] = 999
        self.assertEqual(4, self.amount(owner_uuid))
        self.controller.undo_stack.undo()
        self.assertEqual(1.0, self.amount(owner_uuid))
        self.controller.undo_stack.redo()
        self.assertEqual(4, self.amount(owner_uuid))
        index = self.controller.undo_stack.index()
        self.assertFalse(self.controller.set_listener_graph(owner_uuid, self.controller.get_node(owner_uuid).listener_graph.clone()))
        self.assertEqual(index, self.controller.undo_stack.index())

    def test_copy_paste_remaps_internal_ids_and_keeps_graphs_independent(self):
        owner_uuid = self.add_listener()
        payload = self.controller.serialize_selection([owner_uuid])
        copied_uuid = self.controller.paste_payload(payload, (600.0, 80.0))[0]
        original = self.controller.get_node(owner_uuid).listener_graph
        copied = self.controller.get_node(copied_uuid).listener_graph
        self.assertIsNotNone(copied)
        original_ids = {part.uuid for part in original.nodes}
        copied_ids = {part.uuid for part in copied.nodes}
        self.assertTrue(original_ids.isdisjoint(copied_ids))
        self.assertEqual(len(original_ids), len(copied_ids))
        self.assertTrue(all(wire.from_uuid in copied_ids and wire.to_uuid in copied_ids for wire in copied.connections))
        self.assertEqual(3, len(self.document.nodes))
        self.set_amount(copied_uuid, 7)
        self.assertEqual(1.0, self.amount(owner_uuid))
        self.assertEqual(7, self.amount(copied_uuid))
        self.controller.undo_stack.undo()
        self.assertEqual(1.0, self.amount(copied_uuid))
        self.controller.undo_stack.undo()
        self.assertIsNone(self.controller.get_node(copied_uuid))
        self.controller.undo_stack.redo()
        self.assertEqual(copied_ids, {part.uuid for part in self.controller.get_node(copied_uuid).listener_graph.nodes})

    def test_deleting_and_restoring_host_keeps_internal_graph(self):
        owner_uuid = self.add_listener()
        self.set_amount(owner_uuid, 5)
        expected = self.controller.get_node(owner_uuid).listener_graph.to_payload()
        self.controller.remove_nodes([owner_uuid])
        self.assertIsNone(self.controller.get_node(owner_uuid))
        self.controller.undo_stack.undo()
        self.assertEqual(expected, self.controller.get_node(owner_uuid).listener_graph.to_payload())
        self.controller.undo_stack.redo()
        self.assertIsNone(self.controller.get_node(owner_uuid))

    def test_attached_graph_preserves_function_type_and_original_action_fields(self):
        owner_uuid = self.controller.create_node("TouchIdle", (180, 80))
        self.controller.update_field(owner_uuid, "action_trigger", "touch_custom_existing", source_mode="advanced")
        before_fields = copy.deepcopy(self.controller.get_node(owner_uuid).fields)
        action = before_fields["action_trigger"]
        graph = new_listener_graph("ListenerCounter")
        state = next(part for part in graph.nodes if part.kind == "ValueState")
        state.fields.update({"minimum": -3, "maximum": 8, "start_value": 2, "save_parameter": 1})
        self.assertTrue(self.controller.set_listener_graph(owner_uuid, graph))
        node = self.controller.get_node(owner_uuid)
        self.assertEqual("TouchIdle", node.type)
        # range_abs is an existing derived sign flag, refreshed from range.
        owned = {"listener_data", "parameter", "range", "range_abs", "start_value", "save_parameter"}
        self.assertEqual({key: value for key, value in before_fields.items() if key not in owned},
                         {key: value for key, value in node.fields.items() if key not in owned})
        attached_fields = copy.deepcopy(node.fields)
        compiled = compile_listener_graph(graph)
        self.assertTrue(compiled.valid, compiled.issues)
        values = document_to_csv_rows(self.schema, self.document)[0].values
        self.assertEqual(action, values["action_trigger"])
        for key in ("parameter", "range", "start_value", "save_parameter"):
            self.assertEqual(compiled.fields[key], values[key])
        self.assertEqual(compiled.listener_data, values["listener_data"])
        self.assertEqual(attached_fields, node.fields)
        self.save()
        self.assertEqual(6, json.loads(self.path.read_text(encoding="utf-8"))["format_version"])
        restored = next(node for node in load_document(self.schema, self.path).nodes if node.uuid == owner_uuid)
        self.assertEqual("TouchIdle", restored.type)
        self.assertEqual(action, restored.fields["action_trigger"])
        self.assertEqual(graph.to_payload(), restored.listener_graph.to_payload())

    def test_valid_csv_export_is_compiled_and_does_not_mutate_live_document(self):
        owner_uuid = self.add_listener()
        graph = self.controller.get_node(owner_uuid).listener_graph
        compiled = compile_listener_graph(graph)
        before = export_document_dict(self.schema, copy.deepcopy(self.document))
        undo_index = self.controller.undo_stack.index()
        output = export_current_document_csv(self.schema, self.document, self.workspace)
        self.assertTrue(output.read_bytes().startswith(b"\xef\xbb\xbf"))
        with output.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(1, len(rows))
        self.assertEqual(compiled.listener_data, rows[0]["listener_data"])
        self.assertEqual(compiled.fields["parameter"], rows[0]["parameter"])
        self.assertEqual(before, export_document_dict(self.schema, copy.deepcopy(self.document)))
        self.assertEqual(undo_index, self.controller.undo_stack.index())

    def test_plan_retyping_does_not_discard_standalone_or_attached_graphs(self):
        listener_uuid = self.add_listener()
        function_uuid = self.controller.create_node("TouchIdle", (600, 80))
        self.controller.set_listener_graph(function_uuid, new_listener_graph("attached_counter"))
        expected = {uuid: self.controller.get_node(uuid).listener_graph.to_payload()
                    for uuid in (listener_uuid, function_uuid)}
        index = self.controller.undo_stack.index()
        self.assertFalse(self.controller.set_plan_topic_color(listener_uuid, PLAN_TOUCHIDLE_COLOR))
        self.assertFalse(self.controller.set_plan_topic_color(function_uuid, PLAN_TOUCHDRAG_COLOR))
        self.assertFalse(self.controller.materialize_plan_topics())
        self.assertEqual(index, self.controller.undo_stack.index())
        for uuid, graph_payload in expected.items():
            node = self.controller.get_node(uuid)
            self.assertIsNotNone(node.listener_graph)
            self.assertEqual(graph_payload, node.listener_graph.to_payload())
        # Editing a plan title cannot implicitly convert the host either.
        self.assertTrue(self.controller.set_plan_title(listener_uuid, "TouchIdle5"))
        self.assertFalse(self.controller.materialize_plan_topics())
        self.controller.undo_stack.undo()
        for uuid, graph_payload in expected.items():
            self.assertEqual(graph_payload, self.controller.get_node(uuid).listener_graph.to_payload())

    def test_plan_materialization_also_protects_loaded_draft_listener_topics(self):
        owner_uuid = self.add_listener()
        expected = self.controller.get_node(owner_uuid).listener_graph.to_payload()
        topic = self.controller.plan_topic(owner_uuid)
        topic.formalization_state = "draft"
        topic.branch_color = PLAN_TOUCHDRAG_COLOR
        topic.plan_title = "TouchDrag3"
        self.assertFalse(self.controller.materialize_plan_topics())
        self.assertEqual("Listener", self.controller.get_node(owner_uuid).type)
        self.assertEqual(expected, self.controller.get_node(owner_uuid).listener_graph.to_payload())

    def test_standalone_host_rejects_outer_edges_but_attached_graph_keeps_them(self):
        owner_uuid = self.add_listener()
        root_uuid = next(node.uuid for node in self.document.nodes if node.type == "Idle0")
        function_uuid = self.controller.create_node("TouchIdle", (600, 80))
        self.controller.set_listener_graph(function_uuid, new_listener_graph("attached"))
        index = self.controller.undo_stack.index()
        self.controller.add_connection(root_uuid, owner_uuid)
        self.controller.add_connection(owner_uuid, function_uuid)
        self.assertFalse(self.controller.reparent_plan_topic(owner_uuid, root_uuid))
        self.assertFalse(self.controller.reparent_plan_topic(function_uuid, owner_uuid))
        self.assertEqual(index, self.controller.undo_stack.index())
        self.assertEqual([], self.document.connections)
        self.controller.add_connection(root_uuid, function_uuid)
        self.assertEqual([(root_uuid, function_uuid)], [(edge.from_uuid, edge.to_uuid) for edge in self.document.connections])

    def test_bulk_fields_cannot_bypass_subgraph_owned_values(self):
        owner_uuid = self.controller.create_node("TouchIdle", (180, 80))
        self.controller.set_listener_graph(owner_uuid, new_listener_graph("actual_parameter"))
        original = copy.deepcopy(self.controller.get_node(owner_uuid).fields)
        graph = self.controller.get_node(owner_uuid).listener_graph.to_payload()
        self.controller.update_fields_for_nodes([owner_uuid], {
            "listener_data": "bad", "parameter": "bad", "range": "{-99,99}",
            "start_value": 99, "save_parameter": 1, "tips": "允许修改备注",
            "listener_graph": {"version": 999},
        })
        node = self.controller.get_node(owner_uuid)
        for key in ("listener_data", "parameter", "range", "start_value", "save_parameter"):
            self.assertEqual(original.get(key), node.fields.get(key))
        self.assertEqual("允许修改备注", node.fields["tips"])
        self.assertNotIn("listener_graph", node.fields)
        self.assertEqual(graph, node.listener_graph.to_payload())

    def test_quick_create_plan_child_and_paste_do_not_bypass_host_port_rules(self):
        host_uuid = self.add_listener()
        function_uuid = self.controller.create_node("TouchIdle", (600, 80))
        root_uuid = next(node.uuid for node in self.document.nodes if node.type == "Idle0")
        host_payload = self.controller.serialize_selection([host_uuid])
        function_payload = self.controller.serialize_selection([function_uuid])
        index = self.controller.undo_stack.index()
        self.assertIsNone(self.controller.create_node_with_connection(root_uuid, "Listener", (1000, 80)))
        self.assertIsNone(self.controller.create_node_with_connection(host_uuid, "TouchIdle", (1000, 80)))
        self.assertIsNone(self.controller.create_plan_topic(host_uuid, "禁止的外部子主题"))
        self.assertEqual([], self.controller.paste_payload(host_payload, connect_from=function_uuid))
        self.assertEqual([], self.controller.paste_payload(function_payload, connect_from=host_uuid))
        self.assertEqual([], self.controller.paste_payload(host_payload, override_plan_parent=True, plan_parent_uuid=root_uuid))
        self.assertEqual([], self.controller.paste_payload(function_payload, override_plan_parent=True, plan_parent_uuid=host_uuid))
        self.assertEqual(index, self.controller.undo_stack.index())
        self.assertEqual(3, len(self.document.nodes))
        self.assertEqual([], self.document.connections)

    def test_creating_from_attached_function_template_keeps_listener_parameter(self):
        function_uuid = self.controller.create_node("TouchIdle", (180, 80))
        self.controller.set_listener_graph(function_uuid, new_listener_graph("attached_parameter"))
        source = self.controller.get_node(function_uuid)
        created_uuid = self.controller.create_node("TouchIdle", (600, 80), base_node=source)
        created = self.controller.get_node(created_uuid)
        self.assertEqual("attached_parameter", created.fields["parameter"])
        self.assertIsNotNone(created.listener_graph)
        self.assertTrue({part.uuid for part in source.listener_graph.nodes}.isdisjoint(
            {part.uuid for part in created.listener_graph.nodes}))
        self.assertEqual(compile_listener_graph(created.listener_graph).fields["parameter"], created.fields["parameter"])

    def test_attached_return_default_still_applies_explicit_transition_mode(self):
        owner_uuid = self.controller.create_node("ReturnDefaultIdle", (180, 80))
        self.controller.set_listener_graph(owner_uuid, new_listener_graph("Counter23"))
        graph = self.controller.get_node(owner_uuid).listener_graph.to_payload()
        self.controller.update_field(owner_uuid, "transition_type", "hard", source_mode="simple")
        node = self.controller.get_node(owner_uuid)
        self.assertIn("action='idle'", node.fields["action_trigger"].replace(" ", ""))
        self.assertIn("idle_focus=1", node.fields["action_trigger_active"].replace(" ", ""))
        self.assertIn("idle=0", node.fields["action_trigger_active"].replace(" ", ""))
        self.assertEqual("Counter23", node.fields["parameter"])
        self.assertEqual(graph, node.listener_graph.to_payload())
        self.controller.update_field(owner_uuid, "transition_type", "animated", source_mode="simple")
        self.assertNotIn("action='idle'", node.fields["action_trigger"].replace(" ", ""))
        self.assertNotIn("idle_focus", node.fields["action_trigger_active"])
        self.assertEqual("Counter23", node.fields["parameter"])

    def test_attached_touchidle_keeps_target_and_draw_linkage_while_preserving_graph_fields(self):
        self.controller.set_numeric_linkage_enabled(True)
        for key, value in (("target_idle", 7), ("draw_able_name", "TouchIdle7")):
            with self.subTest(key=key):
                owner_uuid = self.controller.create_node("TouchIdle", (180, 80))
                self.controller.set_listener_graph(owner_uuid, new_listener_graph("Counter23"))
                node = self.controller.get_node(owner_uuid)
                graph = node.listener_graph.to_payload()
                owned = {name: node.fields[name] for name in ("parameter", "range", "start_value", "save_parameter", "listener_data")}
                self.controller.update_field(owner_uuid, key, value, source_mode="simple")
                self.assertEqual(7, node.fields["target_idle"])
                self.assertEqual("TouchIdle7", node.fields["draw_able_name"])
                self.assertIn("action='touch_idle7'", node.fields["action_trigger"].replace(" ", ""))
                self.assertIn("idle=7", node.fields["action_trigger_active"].replace(" ", ""))
                self.assertEqual(owned, {name: node.fields[name] for name in owned})
                self.assertEqual(graph, node.listener_graph.to_payload())

    def test_attached_touchidle_transition_changes_runtime_action_and_keeps_parameter(self):
        self.controller.set_numeric_linkage_enabled(True)
        owner_uuid = self.controller.create_node("TouchIdle", (180, 80))
        self.controller.set_listener_graph(owner_uuid, new_listener_graph("Counter23"))
        self.controller.update_field(owner_uuid, "transition_type", "hard", source_mode="simple")
        node = self.controller.get_node(owner_uuid)
        row = document_to_csv_rows(self.schema, self.document)[0].values
        self.assertIn("action='idle'", row["action_trigger"].replace(" ", ""))
        self.assertIn("idle_focus=1", node.fields["action_trigger_active"].replace(" ", ""))
        self.assertEqual("Counter23", row["parameter"])

    def test_attached_touchdrag_value_mode_clears_action_without_losing_listener(self):
        owner_uuid = self.controller.create_node("TouchDrag", (180, 80))
        self.controller.set_listener_graph(owner_uuid, new_listener_graph("Counter23"))
        graph = self.controller.get_node(owner_uuid).listener_graph.to_payload()
        self.controller.update_field(owner_uuid, "result_type", "value", source_mode="simple")
        node = self.controller.get_node(owner_uuid)
        self.assertEqual("", node.fields["action_trigger"])
        self.assertEqual("", node.fields["action_trigger_active"])
        self.assertEqual(0, node.fields["target_idle"])
        self.assertEqual("Counter23", node.fields["parameter"])
        self.assertEqual(graph, node.listener_graph.to_payload())
        self.assertEqual("", document_to_csv_rows(self.schema, self.document)[0].values["action_trigger"])

    def test_attached_touchdrag_trigger_edit_uses_action_number_not_listener_parameter_suffix(self):
        self.controller.set_numeric_linkage_enabled(True)
        owner_uuid = self.controller.create_node("TouchDrag", (180, 80))
        self.controller.set_listener_graph(owner_uuid, new_listener_graph("Counter23"))
        graph = self.controller.get_node(owner_uuid).listener_graph.to_payload()
        self.controller.update_field(owner_uuid, "action_trigger", "touch_drag7", source_mode="simple")
        node = self.controller.get_node(owner_uuid)
        self.assertIn("action='touch_drag7'", node.fields["action_trigger"].replace(" ", ""))
        self.assertEqual(7, node.fields["target_idle"])
        self.assertEqual("Counter23", node.fields["parameter"])
        self.assertEqual(graph, node.listener_graph.to_payload())

    def test_explicit_multi_field_edits_keep_host_rules_and_listener_owned_fields(self):
        self.controller.set_numeric_linkage_enabled(True)
        uuids = [self.controller.create_node(kind, (180 + index * 400, 80))
                 for index, kind in enumerate(("ReturnDefaultIdle", "TouchIdle"))]
        snapshots = {}
        for index, owner_uuid in enumerate(uuids):
            self.controller.set_listener_graph(owner_uuid, new_listener_graph(f"Counter{index + 20}"))
            node = self.controller.get_node(owner_uuid)
            snapshots[owner_uuid] = {key: node.fields[key] for key in
                                     ("parameter", "range", "start_value", "save_parameter", "listener_data")}
        self.controller.update_fields(uuids[0], {"transition_type": "hard", "tips": "单节点多字段"}, source_mode="simple")
        self.assertIn("action='idle'", self.controller.get_node(uuids[0]).fields["action_trigger"].replace(" ", ""))
        self.controller.undo_stack.undo()
        self.controller.update_fields_for_nodes(uuids, {"transition_type": "hard", "tips": "多节点多字段"}, source_mode="simple")
        for owner_uuid in uuids:
            node = self.controller.get_node(owner_uuid)
            self.assertIn("action='idle'", node.fields["action_trigger"].replace(" ", ""))
            self.assertIn("idle_focus=1", node.fields["action_trigger_active"].replace(" ", ""))
            self.assertEqual(snapshots[owner_uuid], {key: node.fields[key] for key in snapshots[owner_uuid]})
        self.controller.undo_stack.undo()
        for owner_uuid in uuids:
            node = self.controller.get_node(owner_uuid)
            self.assertNotIn("action='idle'", node.fields["action_trigger"].replace(" ", ""))
            self.assertEqual(snapshots[owner_uuid], {key: node.fields[key] for key in snapshots[owner_uuid]})

    def test_external_subgraphs_on_non_exportable_owners_are_rejected(self):
        uuids = [self.document.nodes[0].uuid,
                 self.controller.create_node("Comment", (180, 80)),
                 self.controller.create_node("PlanPlaceholder", (600, 80))]
        base = export_document_dict(self.schema, self.document)
        base["format_version"] = 6
        graph = new_listener_graph("Counter23")
        for owner_uuid in uuids:
            with self.subTest(owner=self.controller.get_node(owner_uuid).type):
                payload = copy.deepcopy(base)
                raw = next(node for node in payload["nodes"] if node["uuid"] == owner_uuid)
                raw["listener_graph"] = graph.to_payload()
                with self.assertRaises(ValueError):
                    load_document_payload(self.schema, payload)
                clipboard = json.dumps({"nodes": [raw], "connections": []}).encode("utf-8")
                with self.assertRaises(ValueError):
                    self.controller.clipboard_bounds(clipboard)
                with self.assertRaises(ValueError):
                    self.controller.paste_payload(clipboard)
                document = copy.deepcopy(self.document)
                next(node for node in document.nodes if node.uuid == owner_uuid).listener_graph = graph.clone()
                with self.assertRaises(ValueError):
                    validate_listener_export(document, self.schema)
                self.assertTrue(any(issue.node_uuid == owner_uuid and issue.severity == "error"
                                    for issue in validate_document(self.schema, document)))
                errors = EditorToolService(self.controller, self.workspace)._validate_structure(document)
                self.assertTrue(any(error["code"] == "INVALID_LISTENER_OWNER" for error in errors))

    def test_malformed_clipboard_subgraph_fails_before_bounds_or_mutation(self):
        owner_uuid = self.add_listener()
        raw = json.loads(self.controller.serialize_selection([owner_uuid]))
        before = export_document_dict(self.schema, copy.deepcopy(self.document))
        index = self.controller.undo_stack.index()
        variants = []
        future = copy.deepcopy(raw)
        future["nodes"][0]["listener_graph"]["version"] = 99
        variants.append(future)
        missing = copy.deepcopy(raw)
        missing["nodes"][0].pop("listener_graph")
        variants.append(missing)
        outer_edge = copy.deepcopy(raw)
        outer_edge["connections"] = [{"from_uuid": owner_uuid, "to_uuid": "external"}]
        variants.append(outer_edge)
        for variant in variants:
            payload = json.dumps(variant).encode("utf-8")
            with self.assertRaises(ValueError):
                self.controller.clipboard_bounds(payload)
            with self.assertRaises(ValueError):
                self.controller.paste_payload(payload)
        self.assertEqual(index, self.controller.undo_stack.index())
        self.assertEqual(before, export_document_dict(self.schema, copy.deepcopy(self.document)))

    def test_tool_reads_detached_subgraphs_and_rejects_owned_field_and_host_edge_edits(self):
        host_uuid = self.add_listener()
        attached_uuid = self.controller.create_node("TouchIdle", (600, 80))
        self.controller.set_listener_graph(attached_uuid, new_listener_graph("attached"))
        root_uuid = next(node.uuid for node in self.document.nodes if node.type == "Idle0")
        service = EditorToolService(self.controller, self.workspace)
        response = service.invoke_tool("get_current_graph", {})
        self.assertTrue(response["ok"], response)
        dto = next(node for node in response["result"]["graph"]["nodes"] if node["uuid"] == host_uuid)
        part_uuid = next(iter(dto["listener_graph"]["nodes"]))
        dto["listener_graph"]["nodes"][part_uuid]["fields"]["events"] = "not-live"
        self.assertNotEqual("not-live", self.controller.get_node(host_uuid).listener_graph.nodes[0].fields["events"])
        found = service.invoke_tool("find_nodes", {"node_type": "Listener"})
        self.assertTrue(found["ok"], found)
        self.assertIn("parameter", found["result"]["nodes"][0]["listener_owned_fields"])
        for operation, code in (
            ({"op": "update_node", "node_uuid": attached_uuid, "fields": {"parameter": "bypass"}}, "FIELD_NOT_WRITABLE"),
            ({"op": "add_connection", "from_uuid": root_uuid, "to_uuid": host_uuid}, "INVALID_CONNECTION"),
            ({"op": "update_plan_topic", "node_uuid": attached_uuid, "branch_color": PLAN_TOUCHDRAG_COLOR}, "FIELD_NOT_WRITABLE"),
        ):
            index = self.controller.undo_stack.index()
            result = service.invoke_tool("apply_graph_edits", {"expected_revision": service.revision, "operations": [operation]})
            self.assertFalse(result["ok"], result)
            self.assertEqual(code, result["error"]["code"])
            self.assertEqual(index, self.controller.undo_stack.index())

    def test_tool_callback_blocks_queries_mutations_previews_and_confirmed_commit(self):
        owner_uuid = self.add_listener()
        calls = []
        service = EditorToolService(self.controller, self.workspace, before_invocation=lambda: calls.append("blocked") or False)
        signal_calls = []
        service.beforeInvocation.connect(lambda: signal_calls.append("legacy"))
        args = {"expected_revision": service.revision,
                "operations": [{"op": "update_node", "node_uuid": owner_uuid, "fields": {"tips": "尚未应用"}}]}
        before = export_document_dict(self.schema, copy.deepcopy(self.document))
        for response in (service.invoke_tool("get_current_graph", {}),
                         service.invoke_tool("apply_graph_edits", args),
                         service.preview_tool("apply_graph_edits", args)):
            self.assertFalse(response["ok"], response)
            self.assertEqual("PENDING_EDITOR_INPUT", response["error"]["code"])
        self.assertEqual(3, len(calls))
        self.assertEqual([], signal_calls)
        self.assertEqual(before, export_document_dict(self.schema, copy.deepcopy(self.document)))
        service.before_invocation = lambda: True
        preview = service.preview_tool("apply_graph_edits", args)
        self.assertTrue(preview["ok"], preview)
        token = preview["result"]["preview_token"]
        service.before_invocation = lambda: False
        blocked = service.invoke_prepared_preview(token)
        self.assertEqual("PENDING_EDITOR_INPUT", blocked["error"]["code"])
        self.assertEqual(before, export_document_dict(self.schema, copy.deepcopy(self.document)))
        service.before_invocation = lambda: True
        self.assertTrue(service.invoke_prepared_preview(token)["ok"])
        self.assertEqual("尚未应用", self.controller.get_node(owner_uuid).fields["tips"])
        service.before_invocation = None
        self.assertTrue(service.invoke_tool("get_current_graph", {})["ok"])
        self.assertEqual(["legacy"], signal_calls)

    def test_confirmed_tool_preview_rechecks_revision_after_committing_editor_input(self):
        owner_uuid = self.add_listener()
        service = EditorToolService(self.controller, self.workspace)
        preview = service.preview_tool("apply_graph_edits", {
            "expected_revision": service.revision,
            "operations": [{"op": "update_node", "node_uuid": owner_uuid, "fields": {"tips": "过期预览"}}],
        })
        self.assertTrue(preview["ok"], preview)
        def commit_editor():
            self.set_amount(owner_uuid, 5)
            return True
        service.before_invocation = commit_editor
        result = service.invoke_prepared_preview(preview["result"]["preview_token"])
        self.assertFalse(result["ok"])
        self.assertEqual("REVISION_CONFLICT", result["error"]["code"])
        self.assertEqual(5, self.amount(owner_uuid))
        self.assertNotEqual("过期预览", self.controller.get_node(owner_uuid).fields["tips"])

    def test_graph_diff_reports_internal_field_change_without_view_noise(self):
        owner_uuid = self.add_listener()
        before = copy.deepcopy(self.document)
        part_uuid = self.set_amount(owner_uuid, 3)
        diff = diff_documents(before, self.document)
        changes = [entry for entry in diff.entries if entry.before == 1.0 and entry.after == 3]
        self.assertTrue(changes, diff.entries)
        self.assertTrue(any(part_uuid in entry.field_path or part_uuid in entry.identity for entry in changes))
        after_fields = copy.deepcopy(self.document)
        view_graph = self.controller.get_node(owner_uuid).listener_graph.clone()
        view_graph.view.update({"scale": 1.3, "offset_x": 85, "offset_y": -42})
        self.controller.set_listener_graph(owner_uuid, view_graph)
        self.assertTrue(diff_documents(after_fields, self.document).is_empty)

    def test_saved_subgraph_versions_load_without_creating_embedded_history(self):
        owner_uuid = self.add_listener()
        self.save()
        versions = [self.path.read_bytes()]
        graph = self.controller.get_node(owner_uuid).listener_graph.clone()
        graph.view.update({"scale": 1.5, "offset_x": 100, "offset_y": -50})
        self.controller.set_listener_graph(owner_uuid, graph)
        self.save()
        before = load_document_payload(self.schema, versions[0])
        self.assertTrue(diff_documents(before, self.document).is_empty)
        self.set_amount(owner_uuid, 2)
        self.save()
        versions.append(self.path.read_bytes())
        self.set_amount(owner_uuid, 4)
        self.save()
        versions.append(self.path.read_bytes())
        for payload, amount in zip(versions, (1, 2, 4)):
            self.assertNotIn("history", json.loads(payload))
            restored = load_document_payload(self.schema, payload)
            host = next(node for node in restored.nodes if node.uuid == owner_uuid)
            self.assertTrue(compile_listener_graph(host.listener_graph).valid)
            self.assertEqual(amount, next(part.fields["value"] for part in host.listener_graph.nodes
                                          if part.kind == "AddValue"))


if __name__ == "__main__":
    unittest.main()
