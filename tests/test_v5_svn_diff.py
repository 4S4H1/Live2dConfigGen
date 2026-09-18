from __future__ import annotations

import copy
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, QProcess, Signal

from l2d_config_editor.controller import EditorController
from l2d_config_editor.graph_diff import canonical_graph_snapshot, diff_documents
from l2d_config_editor.logic import (
    display_value_for_field,
    document_to_csv_rows,
    export_document_dict,
    get_default_schema,
    load_document,
    load_document_payload,
    node_title,
    save_document,
)
from l2d_config_editor.models import CanvasStrokeRecord, GroupRecord, PlanTopicRecord
from l2d_config_editor.plan import (
    PLAN_NEUTRAL_COLOR,
    PLAN_TOUCHDRAG_COLOR,
    PLAN_TOUCHIDLE_COLOR,
    plan_formal_positions,
    plan_topic_type_from_color,
    parse_touchidle_plan_title,
)
from l2d_config_editor.svn_tools import (
    SvnCommitRunner,
    SvnHistoryRunner,
    parse_info_xml,
    parse_log_xml,
    svn_error_message,
)


_QT_APP = QCoreApplication.instance() or QCoreApplication([])


class _StubProcess:
    def state(self):
        return QProcess.ProcessState.NotRunning


class _StubSvnExecutor(QObject):
    stdoutReceived = Signal(object)
    stderrReceived = Signal(object)
    finished = Signal(int, object)
    failedToStart = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.process = _StubProcess()
        self.starts: list[tuple[str, list[str]]] = []
        self.cancelled = False
        self.killed = False

    def start(self, executable, arguments) -> None:
        self.starts.append((str(executable), list(arguments)))

    def cancel(self) -> None:
        self.cancelled = True

    def kill(self) -> None:
        self.killed = True


def ready_controller() -> EditorController:
    controller = EditorController()
    controller.document.meta.author = "tester"
    controller.document.meta.ship_skin_id = 100
    controller.document.meta.memo = "asset"
    controller.document.meta.CharName = "character"
    controller.refresh_derived()
    return controller


def mark_plan_topic_untyped(controller: EditorController, node_uuid: str) -> None:
    """Simulate a legacy topic that has no green/purple semantic color."""

    for topic in controller.document.plan_layout.topics:
        if topic.node_uuid == node_uuid:
            topic.branch_color = "#F57C00"
            return
    raise AssertionError(f"missing plan topic: {node_uuid}")


class PlanFormalizationV5Tests(unittest.TestCase):
    def test_plan_topic_colors_are_the_only_new_type_signal(self) -> None:
        self.assertEqual(
            "TouchIdle",
            plan_topic_type_from_color(
                PlanTopicRecord("green", branch_color=PLAN_TOUCHIDLE_COLOR)
            ),
        )
        self.assertEqual(
            "TouchDrag",
            plan_topic_type_from_color(
                PlanTopicRecord("purple", branch_color=PLAN_TOUCHDRAG_COLOR)
            ),
        )
        self.assertIsNone(
            plan_topic_type_from_color(
                PlanTopicRecord("legacy", branch_color="#43A047")
            )
        )

    def test_green_and_purple_topics_auto_number_their_formal_nodes(self) -> None:
        controller = ready_controller()
        root_uuid = controller.document.nodes[0].uuid
        green_uuid = controller.create_plan_topic(root_uuid, "摸头")
        purple_uuid = controller.create_plan_topic(green_uuid, "拉袖子")
        self.assertTrue(
            controller.set_plan_topic_color(purple_uuid, PLAN_TOUCHDRAG_COLOR)
        )

        self.assertTrue(controller.materialize_plan_topics())
        green = controller.get_node(green_uuid)
        purple = controller.get_node(purple_uuid)
        self.assertEqual(("TouchIdle", "TouchDrag"), (green.type, purple.type))
        self.assertEqual("摸头", green.fields["tips"])
        self.assertTrue(green.fields["draw_able_name"].startswith("TouchIdle"))
        self.assertIn("touch_idle", green.fields["action_trigger"])
        self.assertIn("idle = 1", green.fields["action_trigger_active"])
        self.assertEqual("拉袖子", purple.fields["tips"])
        self.assertTrue(purple.fields["draw_able_name"].startswith("TouchDrag"))
        self.assertIn("touch_drag", purple.fields["action_trigger"])
        self.assertEqual("", purple.fields["action_trigger_active"])
        self.assertLess(green.ui_position["x"], purple.ui_position["x"])

    def test_every_colored_plan_title_is_resynchronized_to_formal_tips(self) -> None:
        controller = ready_controller()
        root_uuid = controller.document.nodes[0].uuid
        first_uuid = controller.create_plan_topic(root_uuid, "摸头")
        second_uuid = controller.create_plan_topic(first_uuid, "摸头")
        controller.materialize_plan_topics()

        # Reproduce an older converted graph whose visible formal titles were
        # empty even though the plan titles were present.
        controller.get_node(first_uuid).fields["tips"] = ""
        controller.get_node(second_uuid).fields["tips"] = ""
        self.assertTrue(controller.materialize_plan_topics())

        self.assertEqual("摸头", controller.get_node(first_uuid).fields["tips"])
        self.assertEqual("摸头", controller.get_node(second_uuid).fields["tips"])

    def test_generated_numbers_follow_upper_path_before_lower_sibling(self) -> None:
        controller = ready_controller()
        root_uuid = controller.document.nodes[0].uuid
        parent_uuid = controller.create_plan_topic(root_uuid, "趴下1")
        next_branch_uuid = controller.create_plan_topic(root_uuid, "下一分支")
        upper_uuid = controller.create_plan_topic(parent_uuid, "摸头1")
        lower_uuid = controller.create_plan_topic(parent_uuid, "摸头2")
        upper_right_uuid = controller.create_plan_topic(upper_uuid, "摸头3")

        controller.materialize_plan_topics()

        parent = controller.get_node(parent_uuid)
        upper = controller.get_node(upper_uuid)
        upper_right = controller.get_node(upper_right_uuid)
        lower = controller.get_node(lower_uuid)
        next_branch = controller.get_node(next_branch_uuid)
        self.assertEqual(
            (1, 2, 3, 4, 5),
            (
                parent.type_slot,
                upper.type_slot,
                upper_right.type_slot,
                lower.type_slot,
                next_branch.type_slot,
            ),
        )
        self.assertEqual(
            (
                "TouchIdle1",
                "TouchIdle2",
                "TouchIdle3",
                "TouchIdle4",
                "TouchIdle5",
            ),
            (
                parent.fields["draw_able_name"],
                upper.fields["draw_able_name"],
                upper_right.fields["draw_able_name"],
                lower.fields["draw_able_name"],
                next_branch.fields["draw_able_name"],
            ),
        )
        self.assertLess(parent.ui_position["x"], upper.ui_position["x"])
        self.assertLess(upper.ui_position["x"], upper_right.ui_position["x"])
        self.assertLess(upper.ui_position["y"], lower.ui_position["y"])

    def test_fixed_sequence_survives_reordering_and_other_nodes_skip_it(self) -> None:
        controller = ready_controller()
        root_uuid = controller.document.nodes[0].uuid
        fixed_uuid = controller.create_plan_topic(root_uuid, "固定为1")
        second_uuid = controller.create_plan_topic(root_uuid, "自动节点A")
        third_uuid = controller.create_plan_topic(root_uuid, "自动节点B")
        controller.materialize_plan_topics()
        self.assertEqual(1, controller.get_node(fixed_uuid).type_slot)

        self.assertTrue(
            controller.set_nodes_sequence_locked([fixed_uuid], True)
        )
        self.assertTrue(controller.get_node(fixed_uuid).sequence_locked)
        controller.reorder_plan_topic(fixed_uuid, 2)
        controller.materialize_plan_topics()

        fixed = controller.get_node(fixed_uuid)
        second = controller.get_node(second_uuid)
        third = controller.get_node(third_uuid)
        self.assertEqual((2, 3, 1), (second.type_slot, third.type_slot, fixed.type_slot))
        self.assertEqual("TouchIdle2", second.fields["draw_able_name"])
        self.assertIn("touch_idle2", second.fields["action_trigger"])
        self.assertIn("idle = 2", second.fields["action_trigger_active"])
        self.assertEqual("TouchIdle3", third.fields["draw_able_name"])
        self.assertIn("touch_idle3", third.fields["action_trigger"])
        self.assertIn("idle = 3", third.fields["action_trigger_active"])
        self.assertEqual("TouchIdle1", fixed.fields["draw_able_name"])
        self.assertIn("touch_idle1", fixed.fields["action_trigger"])
        self.assertIn("idle = 1", fixed.fields["action_trigger_active"])

        payload = export_document_dict(controller.schema, controller.document)
        loaded = load_document_payload(controller.schema, payload)
        loaded_fixed = next(node for node in loaded.nodes if node.uuid == fixed_uuid)
        self.assertTrue(loaded_fixed.sequence_locked)

        self.assertTrue(
            controller.set_nodes_sequence_locked([fixed_uuid], False)
        )
        controller.materialize_plan_topics()
        self.assertEqual(
            (1, 2, 3),
            (
                controller.get_node(second_uuid).type_slot,
                controller.get_node(third_uuid).type_slot,
                controller.get_node(fixed_uuid).type_slot,
            ),
        )

    def test_purple_color_overrides_a_touchidle_looking_title(self) -> None:
        controller = ready_controller()
        root_uuid = controller.document.nodes[0].uuid
        node_uuid = controller.create_plan_topic(
            root_uuid,
            "touchidle77-touch_idle88",
        )
        controller.set_plan_topic_color(node_uuid, PLAN_TOUCHDRAG_COLOR)

        controller.materialize_plan_topics()

        node = controller.get_node(node_uuid)
        self.assertEqual("TouchDrag", node.type)
        self.assertNotEqual("TouchIdle77", node.fields["draw_able_name"])

    def test_recoloring_a_materialized_topic_changes_its_formal_type(self) -> None:
        controller = ready_controller()
        root_uuid = controller.document.nodes[0].uuid
        node_uuid = controller.create_plan_topic(root_uuid, "互动")
        controller.materialize_plan_topics()
        self.assertEqual("TouchIdle", controller.get_node(node_uuid).type)

        self.assertTrue(
            controller.set_plan_topic_color(node_uuid, PLAN_TOUCHDRAG_COLOR)
        )
        self.assertEqual("draft", controller.plan_topic(node_uuid).formalization_state)
        controller.materialize_plan_topics()
        self.assertEqual("TouchDrag", controller.get_node(node_uuid).type)

    def test_child_semantic_color_does_not_inherit_parent_recolor(self) -> None:
        controller = ready_controller()
        root_uuid = controller.document.nodes[0].uuid
        parent_uuid = controller.create_plan_topic(root_uuid, "父节点")
        child_uuid = controller.create_plan_topic(parent_uuid, "子节点")
        controller.set_plan_topic_color(child_uuid, PLAN_TOUCHDRAG_COLOR)
        controller.ensure_plan_layout()

        self.assertEqual(
            PLAN_TOUCHIDLE_COLOR,
            controller.plan_topic(parent_uuid).branch_color.upper(),
        )
        self.assertEqual(
            PLAN_TOUCHDRAG_COLOR,
            controller.plan_topic(child_uuid).branch_color.upper(),
        )
        self.assertEqual(3, len(plan_formal_positions(controller.document)))

    def test_title_parser_is_case_insensitive_and_keeps_visible_segments(self) -> None:
        parsed = parse_touchidle_plan_title("  ToUcHiDlE7  -  TOUCH_idle19 ")
        self.assertIsNotNone(parsed)
        self.assertEqual((7, 19), (parsed.draw_index, parsed.action_index))
        self.assertEqual("ToUcHiDlE7", parsed.draw_text)
        self.assertEqual("  -  ", parsed.separator_text)
        self.assertEqual("TOUCH_idle19", parsed.action_text)
        for title in ("touchidle-touch_idle1", "touchidle1_touch_idle1", "x-y"):
            self.assertIsNone(parse_touchidle_plan_title(title))

    def test_title_parser_accepts_an_optional_note_after_the_formal_pair(self) -> None:
        parsed = parse_touchidle_plan_title(
            "  ToUcHiDlE7  -  TOUCH_idle19 - 进场淡入 "
        )

        self.assertIsNotNone(parsed)
        self.assertEqual((7, 19), (parsed.draw_index, parsed.action_index))
        self.assertEqual("ToUcHiDlE7", parsed.draw_text)
        self.assertEqual("TOUCH_idle19", parsed.action_text)
        self.assertEqual("进场淡入", parsed.note_text)

    def test_materialization_is_atomic_preserves_edges_and_uses_placeholders(self) -> None:
        controller = ready_controller()
        root_uuid = controller.document.nodes[0].uuid
        valid_uuid = controller.create_plan_topic(root_uuid, "TouchIdle7-touch_idle19")
        invalid_uuid = controller.create_plan_topic(valid_uuid, "待设计")
        mark_plan_topic_untyped(controller, valid_uuid)
        mark_plan_topic_untyped(controller, invalid_uuid)
        controller.add_connection(root_uuid, invalid_uuid)
        valid = controller.get_node(valid_uuid)
        valid.locked = True
        valid.ui_size = {"width": 321.0, "height": 123.0}
        controller.document.groups = [
            GroupRecord(uuid="group-1", title="计划", node_uuids=[valid_uuid, invalid_uuid])
        ]
        connection_pairs = {
            (item.from_uuid, item.to_uuid) for item in controller.document.connections
        }
        before_index = controller.undo_stack.index()

        self.assertTrue(controller.materialize_plan_topics())

        self.assertEqual(before_index + 1, controller.undo_stack.index())
        converted = controller.get_node(valid_uuid)
        placeholder = controller.get_node(invalid_uuid)
        self.assertEqual("TouchIdle", converted.type)
        self.assertEqual("TouchIdle7", converted.fields["draw_able_name"])
        self.assertEqual("empty", converted.fields["parameter"])
        self.assertEqual(
            "{type = 2 ,action = 'touch_idle19'}",
            converted.fields["action_trigger"],
        )
        self.assertEqual("animated", converted.fields["transition_type"])
        self.assertTrue(converted.locked)
        self.assertEqual({"width": 321.0, "height": 123.0}, converted.ui_size)
        self.assertEqual("PlanPlaceholder", placeholder.type)
        self.assertEqual("待设计", placeholder.fields["plan_source_title"])
        self.assertEqual("materialized", controller.plan_topic(valid_uuid).formalization_state)
        self.assertEqual("virtual", controller.plan_topic(invalid_uuid).formalization_state)
        self.assertEqual(
            connection_pairs,
            {(item.from_uuid, item.to_uuid) for item in controller.document.connections},
        )
        self.assertEqual(
            [valid_uuid, invalid_uuid], controller.document.groups[0].node_uuids
        )
        csv_rows = document_to_csv_rows(controller.schema, controller.document)
        self.assertEqual(1, len(csv_rows))
        self.assertEqual("TouchIdle7", csv_rows[0].values["draw_able_name"])

        controller.undo_stack.undo()
        self.assertEqual("PlanPlaceholder", controller.get_node(valid_uuid).type)
        self.assertEqual("draft", controller.plan_topic(valid_uuid).formalization_state)
        self.assertEqual(
            connection_pairs,
            {(item.from_uuid, item.to_uuid) for item in controller.document.connections},
        )

    def test_materialization_uses_the_optional_plan_note_as_touchidle_title(self) -> None:
        controller = ready_controller()
        root_uuid = controller.document.nodes[0].uuid
        formal_uuid = controller.create_plan_topic(
            root_uuid,
            "TouchIdle7-touch_idle19-进场淡入",
        )
        virtual_uuid = controller.create_plan_topic(root_uuid, "纯备注条目")
        mark_plan_topic_untyped(controller, formal_uuid)
        mark_plan_topic_untyped(controller, virtual_uuid)

        self.assertTrue(controller.materialize_plan_topics())

        formal = controller.get_node(formal_uuid)
        virtual = controller.get_node(virtual_uuid)
        self.assertEqual("TouchIdle", formal.type)
        self.assertEqual("TouchIdle7", formal.fields["draw_able_name"])
        self.assertEqual("进场淡入", formal.fields["tips"])
        self.assertEqual("TouchIdle7-进场淡入", node_title(controller.schema, formal))
        self.assertEqual("PlanPlaceholder", virtual.type)

    def test_virtual_placeholder_expected_names_are_writable(self) -> None:
        controller = ready_controller()
        root_uuid = controller.document.nodes[0].uuid
        node_uuid = controller.create_plan_topic(root_uuid, "纯备注条目")
        mark_plan_topic_untyped(controller, node_uuid)
        controller.materialize_plan_topics()

        fields = {
            field.key: field
            for field in controller.schema.nodes["PlanPlaceholder"].fields
        }
        self.assertTrue(fields["plan_source_title"].read_only)
        self.assertFalse(fields["planned_draw_name"].read_only)
        self.assertFalse(fields["planned_action_name"].read_only)

        self.assertEqual("PlanPlaceholder", controller.get_node(node_uuid).type)

    def test_virtual_placeholder_auto_materializes_when_a_planned_name_is_entered(self) -> None:
        """Either expected field promotes a virtual topic in one undo step."""

        for values, expected_draw, expected_action in (
            (
                {"planned_draw_name": "TouchIdle22"},
                "TouchIdle22",
                None,
            ),
            (
                {"planned_action_name": "touch_idle31"},
                None,
                "touch_idle31",
            ),
        ):
            with self.subTest(values=values):
                controller = ready_controller()
                root_uuid = controller.document.nodes[0].uuid
                node_uuid = controller.create_plan_topic(root_uuid, "纯备注条目")
                mark_plan_topic_untyped(controller, node_uuid)
                controller.materialize_plan_topics()
                before_index = controller.undo_stack.index()

                controller.update_fields(node_uuid, values, "advanced")

                converted = controller.get_node(node_uuid)
                self.assertEqual(before_index + 1, controller.undo_stack.index())
                self.assertEqual("TouchIdle", converted.type)
                self.assertEqual("materialized", controller.plan_topic(node_uuid).formalization_state)
                self.assertEqual("纯备注条目", converted.fields["tips"])
                if expected_draw is not None:
                    self.assertEqual(expected_draw, converted.fields["draw_able_name"])
                if expected_action is not None:
                    self.assertEqual(
                        expected_action,
                        display_value_for_field(
                            controller.schema,
                            converted,
                            "action_trigger",
                        ),
                    )

                controller.undo_stack.undo()
                reverted = controller.get_node(node_uuid)
                self.assertEqual("PlanPlaceholder", reverted.type)
                self.assertEqual("virtual", controller.plan_topic(node_uuid).formalization_state)
                for key in values:
                    self.assertEqual("", reverted.fields[key])

    def test_editing_a_colored_topic_keeps_its_color_semantics(self) -> None:
        controller = ready_controller()
        root_uuid = controller.document.nodes[0].uuid
        node_uuid = controller.create_plan_topic(root_uuid, "touchidle1-touch_idle2")
        controller.materialize_plan_topics()
        self.assertEqual("materialized", controller.plan_topic(node_uuid).formalization_state)

        controller.set_plan_title(node_uuid, "尚未确定")
        self.assertEqual("draft", controller.plan_topic(node_uuid).formalization_state)
        controller.materialize_plan_topics()
        self.assertEqual("TouchIdle", controller.get_node(node_uuid).type)
        self.assertEqual("尚未确定", controller.get_node(node_uuid).fields["tips"])
        self.assertEqual("materialized", controller.plan_topic(node_uuid).formalization_state)

    def test_formal_nodes_reverse_sync_type_color_and_title_to_plan(self) -> None:
        controller = ready_controller()
        idle_uuid = controller.create_node("TouchIdle", (777.0, 333.0))
        drag_uuid = controller.create_node("TouchDrag", (999.0, 555.0))
        controller.ensure_plan_layout()

        controller.update_field(idle_uuid, "tips", "head pat", "advanced")
        controller.update_field(drag_uuid, "tips", "sleeve pull", "advanced")

        idle_topic = controller.plan_topic(idle_uuid)
        drag_topic = controller.plan_topic(drag_uuid)
        self.assertEqual(PLAN_TOUCHIDLE_COLOR, idle_topic.branch_color.upper())
        self.assertEqual(PLAN_TOUCHDRAG_COLOR, drag_topic.branch_color.upper())
        self.assertEqual("head pat", idle_topic.plan_title)
        self.assertEqual("sleeve pull", drag_topic.plan_title)
        self.assertEqual("formal", idle_topic.formalization_state)
        self.assertEqual("formal", drag_topic.formalization_state)

    def test_old_formal_topic_uses_real_type_and_title(self) -> None:
        controller = ready_controller()
        node_uuid = controller.create_node("TouchIdle", (321.0, 654.0))
        node = controller.get_node(node_uuid)
        node.fields["tips"] = "legacy title"
        controller.ensure_plan_layout()
        topic = next(
            item
            for item in controller.document.plan_layout.topics
            if item.node_uuid == node_uuid
        )
        topic.branch_color = PLAN_TOUCHDRAG_COLOR
        topic.plan_title = "stale title"
        topic.formalization_state = "formal"

        controller.ensure_plan_layout()

        topic = controller.plan_topic(node_uuid)
        self.assertEqual(PLAN_TOUCHIDLE_COLOR, topic.branch_color.upper())
        self.assertEqual("legacy title", topic.plan_title)

    def test_old_formal_non_semantic_topic_drops_legacy_decorative_color(self) -> None:
        controller = ready_controller()
        node_uuid = controller.create_node("Comment", (321.0, 654.0))
        payload = export_document_dict(controller.schema, controller.document)
        payload["format_version"] = 4
        for topic in payload["plan_layout"]["topics"]:
            topic.pop("formalization_state", None)
            topic.pop("structure_dirty", None)
            if topic["node_uuid"] == node_uuid:
                topic["branch_color"] = "#E53935"

        loaded = load_document_payload(controller.schema, payload)
        loaded_topic = next(
            topic for topic in loaded.plan_layout.topics if topic.node_uuid == node_uuid
        )

        self.assertEqual(
            PLAN_NEUTRAL_COLOR,
            loaded_topic.branch_color.upper(),
        )

    def test_rematerializing_plan_node_preserves_formal_appearance_colors(self) -> None:
        controller = ready_controller()
        root_uuid = controller.document.nodes[0].uuid
        node_uuid = controller.create_plan_topic(root_uuid, "head pat")
        controller.materialize_plan_topics()
        appearance = {
            "theme_body_color": "#102030",
            "theme_border_color": "#405060",
            "theme_text_color": "#F0E0D0",
        }
        controller.update_fields(node_uuid, appearance, "advanced")
        expected = {
            key: controller.get_node(node_uuid).fields[key]
            for key in appearance
        }

        controller.ensure_plan_layout()
        controller.materialize_plan_topics()

        node = controller.get_node(node_uuid)
        self.assertEqual(
            expected,
            {key: node.fields[key] for key in appearance},
        )

    def test_switching_views_preserves_unchanged_formal_positions(self) -> None:
        controller = ready_controller()
        root_uuid = controller.document.nodes[0].uuid
        node_uuid = controller.create_plan_topic(root_uuid, "head pat")
        controller.materialize_plan_topics()
        controller._move_node(node_uuid, (1234.0, 876.0))

        controller.ensure_plan_layout()
        controller.materialize_plan_topics()

        node = controller.get_node(node_uuid)
        self.assertEqual(
            {"x": 1234.0, "y": 876.0},
            node.ui_position,
        )
        self.assertFalse(controller.plan_topic(node_uuid).structure_dirty)

    def test_plan_reorder_marks_only_the_moved_subtree_for_layout(self) -> None:
        controller = ready_controller()
        root_uuid = controller.document.nodes[0].uuid
        first_uuid = controller.create_plan_topic(root_uuid, "first")
        second_uuid = controller.create_plan_topic(root_uuid, "second")
        child_uuid = controller.create_plan_topic(first_uuid, "child")
        controller.materialize_plan_topics()
        controller._move_node(first_uuid, (1200.0, 100.0))
        controller._move_node(second_uuid, (1300.0, 200.0))
        controller._move_node(child_uuid, (1400.0, 300.0))

        self.assertTrue(controller.reorder_plan_topic(first_uuid, 1))
        self.assertTrue(controller.plan_topic(first_uuid).structure_dirty)
        self.assertTrue(controller.plan_topic(child_uuid).structure_dirty)
        self.assertFalse(controller.plan_topic(second_uuid).structure_dirty)
        controller.materialize_plan_topics()

        self.assertEqual(
            {"x": 1300.0, "y": 200.0},
            controller.get_node(second_uuid).ui_position,
        )
        self.assertNotEqual(
            {"x": 1200.0, "y": 100.0},
            controller.get_node(first_uuid).ui_position,
        )

    def test_v4_migration_marks_existing_topics_formal_without_heuristics(self) -> None:
        controller = ready_controller()
        root_uuid = controller.document.nodes[0].uuid
        node_uuid = controller.create_plan_topic(root_uuid, "touchidle7-touch_idle7")
        payload = export_document_dict(controller.schema, controller.document)
        payload["format_version"] = 4
        payload.pop("plan_canvas_strokes", None)
        for topic in payload["plan_layout"]["topics"]:
            topic.pop("formalization_state", None)
            topic.pop("structure_dirty", None)

        loaded = load_document_payload(controller.schema, payload)

        self.assertEqual("PlanPlaceholder", next(node for node in loaded.nodes if node.uuid == node_uuid).type)
        self.assertTrue(all(topic.formalization_state == "formal" for topic in loaded.plan_layout.topics))


class StrokeLayerAndPayloadTests(unittest.TestCase):
    def test_formal_and_plan_strokes_roundtrip_and_do_not_convert(self) -> None:
        controller = ready_controller()
        formal_id = controller.add_canvas_stroke([(0, 0), (10, 10)], "#112233", 3, "formal")
        plan_id = controller.add_canvas_stroke([(20, 20), (30, 30)], "#445566", 5, "plan")
        root_uuid = controller.document.nodes[0].uuid
        controller.create_plan_topic(root_uuid, "touchidle4-touch_idle5")
        controller.materialize_plan_topics()
        self.assertEqual([formal_id], [stroke.uuid for stroke in controller.document.canvas_strokes])
        self.assertEqual([plan_id], [stroke.uuid for stroke in controller.document.plan_canvas_strokes])

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v5.json"
            save_document(controller.schema, controller.document, path)
            loaded = load_document(controller.schema, path)

        self.assertEqual([formal_id], [stroke.uuid for stroke in loaded.canvas_strokes])
        self.assertEqual([plan_id], [stroke.uuid for stroke in loaded.plan_canvas_strokes])

    def test_load_document_payload_accepts_bytes_and_rejects_future_or_bad_data(self) -> None:
        controller = ready_controller()
        payload = export_document_dict(controller.schema, controller.document)
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.assertEqual(len(controller.document.nodes), len(load_document_payload(controller.schema, encoded).nodes))
        payload["format_version"] = 999
        with self.assertRaisesRegex(ValueError, "newer"):
            load_document_payload(controller.schema, payload)
        with self.assertRaises((ValueError, json.JSONDecodeError)):
            load_document_payload(controller.schema, b"not-json")


class GraphDiffTests(unittest.TestCase):
    def test_snapshot_excludes_view_state_and_reports_all_structural_categories(self) -> None:
        before_controller = ready_controller()
        before = before_controller.document
        after = copy.deepcopy(before)
        after.canvas_view.scale = 4.0
        after.plan_layout.view.offset_x = 999.0
        self.assertEqual(canonical_graph_snapshot(before), canonical_graph_snapshot(after))

        changed = copy.deepcopy(before)
        root = changed.nodes[0]
        root.ui_position["x"] += 5
        changed.connections = []
        changed.groups.append(GroupRecord(uuid="g", title="group", node_uuids=[]))
        changed.canvas_strokes.append(
            CanvasStrokeRecord("formal-stroke", [(0, 0), (1, 1)], "#123456", 2)
        )
        changed.plan_canvas_strokes.append(
            CanvasStrokeRecord("plan-stroke", [(2, 2), (3, 3)], "#654321", 4)
        )
        diff = diff_documents(before, changed)
        categories = {entry.category for entry in diff.entries}
        self.assertTrue({"nodes", "groups", "formal_strokes", "plan_strokes"} <= categories)
        self.assertFalse(diff.is_empty)


class SvnParsingTests(unittest.TestCase):
    def test_history_runner_uses_argument_arrays_for_info_log_and_cat(self) -> None:
        executor = _StubSvnExecutor()
        runner = SvnHistoryRunner("svn", executor=executor)
        infos = []
        revisions_seen = []
        contents = []
        runner.infoReady.connect(infos.append)
        runner.revisionsReady.connect(
            lambda revisions, has_more: revisions_seen.append((revisions, has_more))
        )
        runner.contentReady.connect(
            lambda revision, payload: contents.append((revision, payload))
        )

        with tempfile.TemporaryDirectory() as directory:
            graph_path = Path(directory) / "graph.json"
            runner.query_info(graph_path)
            self.assertEqual(
                ["info", "--xml", "--depth", "empty", "--", str(graph_path.resolve())],
                executor.starts[-1][1],
            )
            executor.stdoutReceived.emit(
                (
                    '<info><entry kind="file" path="graph.json" revision="8">'
                    '<url>file:///repo/trunk/graph.json</url><repository>'
                    '<root>file:///repo</root><uuid>repo-id</uuid></repository>'
                    '<wc-info><schedule>normal</schedule></wc-info></entry></info>'
                ).encode("utf-8")
            )
            executor.finished.emit(0, QProcess.ExitStatus.NormalExit)
            self.assertEqual(1, len(infos))

            runner.query_revisions()
            self.assertEqual(
                [
                    "log", "--xml", "-r", "HEAD:0", "--limit", "100",
                    "--", "file:///repo/trunk/graph.json@8",
                ],
                executor.starts[-1][1],
            )
            executor.stdoutReceived.emit(
                b'<log><logentry revision="8"><author>a</author></logentry></log>'
            )
            executor.finished.emit(0, QProcess.ExitStatus.NormalExit)
            self.assertEqual([8], [item.revision for item in revisions_seen[0][0]])
            self.assertFalse(revisions_seen[0][1])

            runner.query_content(8)
            self.assertEqual(
                [
                    "cat", "--non-interactive", "-r", "8", "--",
                    "file:///repo/trunk/graph.json@8",
                ],
                executor.starts[-1][1],
            )
            executor.stdoutReceived.emit(b'{"format_version":5}')
            executor.finished.emit(0, QProcess.ExitStatus.NormalExit)
            self.assertEqual([(8, b'{"format_version":5}')], contents)

    def test_history_runner_timeout_and_cancel_are_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            graph_path = Path(directory) / "graph.json"

            timeout_executor = _StubSvnExecutor()
            timeout_runner = SvnHistoryRunner("svn", executor=timeout_executor)
            failures: list[str] = []
            timeout_runner.failed.connect(failures.append)
            timeout_runner.query_info(graph_path)
            timeout_runner._timeout()
            self.assertTrue(timeout_executor.killed)
            timeout_executor.finished.emit(-1, QProcess.ExitStatus.CrashExit)
            self.assertTrue(any("60" in message for message in failures))

            cancel_executor = _StubSvnExecutor()
            cancel_runner = SvnHistoryRunner("svn", executor=cancel_executor)
            cancelled: list[bool] = []
            cancel_runner.cancelled.connect(lambda: cancelled.append(True))
            cancel_runner.query_info(graph_path)
            cancel_runner.cancel()
            self.assertTrue(cancel_executor.cancelled)
            cancel_executor.finished.emit(-1, QProcess.ExitStatus.CrashExit)
            self.assertEqual([True], cancelled)

    def test_commit_runner_reuses_actionable_error_mapping(self) -> None:
        runner = SvnCommitRunner("svn")
        seen: list[tuple[bool, str]] = []
        runner.finished.connect(lambda success, message: seen.append((success, message)))
        runner._phase = "commit"
        runner._error_buffer.extend(b"E170001 authentication failed")
        runner._process_finished(1, None)
        self.assertEqual(
            [(False, svn_error_message("E170001 authentication failed", "commit"))],
            seen,
        )

    def test_info_parses_unicode_and_local_copy_source(self) -> None:
        xml = """noise before
<info><entry kind="file" path="图.json" revision="42"><url>file:///repo/branches/图.json</url>
<repository><root>file:///repo</root><uuid>repo-uuid</uuid></repository>
<wc-info><schedule>added</schedule><copy-from-url>file:///repo/trunk/图.json</copy-from-url>
<copy-from-rev>37</copy-from-rev></wc-info></entry></info>noise after"""
        info = parse_info_xml(xml, "图.json")
        self.assertTrue(info.has_history)
        self.assertEqual("file:///repo/trunk/图.json@37", info.history_target)
        self.assertEqual("repo-uuid", info.repository_uuid)

    def test_info_normalizes_svn_add_schedule_and_new_files_have_no_history(self) -> None:
        xml = """<info><entry kind="file" path="new.json" revision="0">
<url>file:///repo/trunk/new.json</url><repository><root>file:///repo</root>
<uuid>repo-uuid</uuid></repository><wc-info><schedule>add</schedule></wc-info>
</entry></info>"""
        info = parse_info_xml(xml, "new.json")
        self.assertEqual("added", info.working_copy_status)
        self.assertFalse(info.has_history)

    def test_log_parses_orders_and_deduplicates_revisions(self) -> None:
        xml = """warning
<log><logentry revision="8"><author>甲</author><date>2026-01-01</date><msg>新</msg></logentry>
<logentry revision="3"><author>乙</author><date>2025-01-01</date><msg>旧</msg></logentry>
<logentry revision="8"><author>甲</author><msg>重复</msg></logentry></log>"""
        revisions = parse_log_xml(xml)
        self.assertEqual([8, 3], [item.revision for item in revisions])
        self.assertEqual("甲", revisions[0].author)

    def test_errors_are_actionable(self) -> None:
        self.assertIn("凭据", svn_error_message("E170001 authentication failed", "cat"))
        self.assertIn("证书", svn_error_message("server certificate verification failed", "log"))
        self.assertIn("网络", svn_error_message("could not resolve hostname", "info"))
        self.assertIn("没有 SVN 历史", svn_error_message("is not a working copy", "info"))

    @unittest.skipUnless(
        shutil.which("svn") and shutil.which("svnadmin"),
        "svn and svnadmin are not installed",
    )
    def test_file_repository_history_can_be_loaded_and_diffed_without_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repository"
            imported = root / "imported"
            working_copy = root / "working-copy"
            imported.mkdir()
            subprocess.run(["svnadmin", "create", str(repository)], check=True)

            controller = ready_controller()
            imported_graph = imported / "graph.json"
            save_document(controller.schema, controller.document, imported_graph)
            repository_url = repository.as_uri()
            subprocess.run(
                ["svn", "import", str(imported), repository_url, "-m", "initial"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["svn", "checkout", repository_url, str(working_copy)],
                check=True,
                capture_output=True,
            )

            checked_out_graph = working_copy / "graph.json"
            loaded = load_document(controller.schema, checked_out_graph)
            changed = EditorController()
            changed.document = loaded
            changed.create_node("Comment", (300.0, 200.0))
            save_document(changed.schema, changed.document, checked_out_graph)
            subprocess.run(
                ["svn", "commit", str(checked_out_graph), "-m", "add note"],
                check=True,
                capture_output=True,
            )

            info_payload = subprocess.run(
                [
                    "svn", "info", "--xml", "--depth", "empty", "--",
                    str(checked_out_graph),
                ],
                check=True,
                capture_output=True,
            ).stdout.decode("utf-8")
            info = parse_info_xml(info_payload, checked_out_graph)
            log_payload = subprocess.run(
                [
                    "svn", "log", "--xml", "-r", "HEAD:0", "--limit", "100",
                    "--", info.history_target,
                ],
                check=True,
                capture_output=True,
            ).stdout.decode("utf-8")
            revisions = parse_log_xml(log_payload)
            self.assertGreaterEqual(len(revisions), 2)

            documents = []
            for revision in (revisions[-1].revision, revisions[0].revision):
                content = subprocess.run(
                    [
                        "svn", "cat", "--non-interactive", "-r", str(revision),
                        "--", info.history_target,
                    ],
                    check=True,
                    capture_output=True,
                ).stdout
                documents.append(load_document_payload(controller.schema, content))
            self.assertFalse(diff_documents(*documents).is_empty)
            self.assertFalse(
                any("diff" in path.name.lower() for path in working_copy.iterdir())
            )


if __name__ == "__main__":
    unittest.main()
