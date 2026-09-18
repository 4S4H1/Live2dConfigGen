"""The listener editor must preserve the shipped Lua-table contract."""
import copy
import unittest

from l2d_config_editor.listener_catalog import new_listener_graph
from l2d_config_editor.listener_compiler import (
    ListenerImportError,
    compile_listener_graph,
    import_listener_graph,
    parse_listener_literal,
)
from l2d_config_editor.listener_graph import ListenerGraph, ListenerPart, ListenerWire


class ListenerCompilerTests(unittest.TestCase):
    def setUp(self):
        self.graph = new_listener_graph("ParamScore")
        self.event, self.change, self.state = self.graph.nodes

    def assert_invalid(self, graph, fragment=None):
        result = compile_listener_graph(graph)
        self.assertFalse(result.valid, result.listener_data)
        self.assertEqual(result.listener_data, "")
        self.assertEqual(result.fields, {})
        if fragment:
            self.assertTrue(any(fragment in issue.message for issue in result.issues), result.issues)
        return result

    def test_default_graph_compiles_without_mutating_graph(self):
        before = copy.deepcopy(self.graph)
        result = compile_listener_graph(self.graph)
        self.assertTrue(result.valid, result.issues)
        self.assertEqual(result.listener_data, "{type=1,change={{1,{'touch_head'},1}}}")
        self.assertEqual(result.fields["parameter"], "ParamScore")
        self.assertEqual(result.fields["action_trigger"], "{type=7}")
        self.assertEqual(result.fields["draw_able_name"], "")
        self.assertEqual(result.fields["range"], "{0,1}")
        self.assertEqual(self.graph, before)

    def test_ui_persistence_switch_is_converted_to_legacy_zero_minus_one(self):
        for value, expected in ((False, -1), (0, -1), (True, 0), (1, 0)):
            with self.subTest(value=value):
                self.state.fields["save_parameter"] = value
                result = compile_listener_graph(self.graph)
                self.assertTrue(result.valid, result.issues)
                self.assertEqual(result.fields["save_parameter"], expected)
                self.assertEqual(result.fields["revert"], -1)
        self.state.fields["save_parameter"] = "false"
        self.assert_invalid(self.graph, "开关")

    def test_any_event_combines_same_family_without_duplicate_execution(self):
        another = ListenerPart("evt2", "ActionEvent", {"events": "touch_head, touch_body"})
        union = ListenerPart("any", "AnyEvent")
        self.graph.nodes += [another, union]
        self.graph.connections = [
            ListenerWire(self.event.uuid, "event", union.uuid, "event"),
            ListenerWire(another.uuid, "event", union.uuid, "event"),
            ListenerWire(union.uuid, "event", self.change.uuid, "event"),
            ListenerWire(self.change.uuid, "change", self.state.uuid, "changes"),
        ]
        result = compile_listener_graph(self.graph)
        self.assertTrue(result.valid, result.issues)
        self.assertEqual(parse_listener_literal(result.listener_data), {
            "type": 1, "change": [[1, ["touch_head", "touch_body"], 1]],
        })
        another.kind = "TouchEvent"
        self.assert_invalid(self.graph, "相同类型")

    def test_different_event_types_cannot_share_one_state(self):
        other_event = ListenerPart("touch", "TouchEvent", {"events": "TouchDrag1"})
        other_change = ListenerPart("set", "SetValue", {"value": 0})
        self.graph.nodes += [other_event, other_change]
        self.graph.connections += [
            ListenerWire("touch", "event", "set", "event"),
            ListenerWire("set", "change", self.state.uuid, "changes"),
        ]
        self.assert_invalid(self.graph, "一种事件类型")

    def test_runtime_operation_order_and_explicit_queue_keep(self):
        self.change.fields.update(value=0.35, queue_index=0, order=9)
        reset = ListenerPart("reset", "SetValue", {"value": -1.5, "queue_index": -2, "order": -1})
        self.graph.nodes.append(reset)
        self.graph.connections += [
            ListenerWire(self.event.uuid, "event", reset.uuid, "event"),
            ListenerWire(reset.uuid, "change", self.state.uuid, "changes"),
        ]
        self.state.fields.update(minimum=-2, maximum=2)
        result = compile_listener_graph(self.graph)
        self.assertTrue(result.valid, result.issues)
        self.assertEqual(parse_listener_literal(result.listener_data)["change"], [
            [2, ["touch_head"], -1.5, -2], [1, ["touch_head"], 0.35, 0],
        ])
        reset.fields["order"] = 9
        self.assertEqual(parse_listener_literal(compile_listener_graph(self.graph).listener_data)["change"][0][0], 1)

    def test_idle_zero_and_case_sensitive_event_names(self):
        self.event.kind = "IdleEvent"
        self.event.fields["events"] = "0, 2\n3"
        result = compile_listener_graph(self.graph)
        self.assertEqual(parse_listener_literal(result.listener_data)["change"][0][1], [0, 2, 3])
        self.event.kind = "TouchEvent"
        self.event.fields["events"] = "TouchDrag1,touchdrag1"
        result = compile_listener_graph(self.graph)
        self.assertEqual(parse_listener_literal(result.listener_data)["change"][0][1], ["TouchDrag1", "touchdrag1"])

    def test_ranges_preserve_half_open_last_match_order_with_warning(self):
        self.state.fields.update(minimum=-1, maximum=2)
        for uid, low, high, idle, order in (("late", 0, 2, 0, 3), ("early", -1, 1, 8, 0)):
            self.graph.nodes.append(ListenerPart(uid, "IdleRange", {"minimum": low, "maximum": high, "idle": idle, "order": order}))
            self.graph.connections.append(ListenerWire(self.state.uuid, "value", uid, "value"))
        result = compile_listener_graph(self.graph)
        self.assertTrue(result.valid, result.issues)
        self.assertEqual(parse_listener_literal(result.listener_data)["apply"], [1, [[-1, 1, 8], [0, 2, 0]]])
        self.assertTrue(any(issue.part_uuid == "late" and issue.severity == "warning" for issue in result.issues))

    def test_possible_idle_event_feedback_is_warning(self):
        self.event.kind = "IdleEvent"
        self.event.fields["events"] = "1"
        self.graph.nodes.append(ListenerPart("range", "IdleRange", {"minimum": 0, "maximum": 2, "idle": 1}))
        self.graph.connections.append(ListenerWire(self.state.uuid, "value", "range", "value"))
        result = compile_listener_graph(self.graph)
        self.assertTrue(result.valid, result.issues)
        self.assertTrue(any("再次触发" in issue.message for issue in result.issues))

    def test_unknown_or_orphan_parts_are_never_silently_dropped(self):
        for kind, fragment in (("AndEvent", "不支持"), ("ActionEvent", "未连接")):
            graph = self.graph.clone()
            graph.nodes.append(ListenerPart("orphan", kind, {"events": "touch_head"}))
            result = self.assert_invalid(graph, fragment)
            self.assertTrue(any(issue.part_uuid == "orphan" for issue in result.issues))

    def test_structural_errors_return_issues(self):
        graphs = []
        graph = self.graph.clone()
        graph.connections.append(graph.connections[0])
        graphs.append((graph, "重复连线"))
        graph = self.graph.clone()
        graph.connections[0] = ListenerWire("missing", "event", self.change.uuid, "event")
        graphs.append((graph, "不存在"))
        graph = self.graph.clone()
        graph.connections[0] = ListenerWire(self.event.uuid, "wrong", self.change.uuid, "event")
        graphs.append((graph, "端口"))
        graph = self.graph.clone()
        graph.nodes.append(graph.nodes[0].clone())
        graphs.append((graph, "标识重复"))
        graph = self.graph.clone()
        graph.connections.clear()
        graphs.append((graph, "输入"))
        graph = self.graph.clone()
        graph.nodes.append(ListenerPart("state2", "ValueState", dict(self.state.fields)))
        graphs.append((graph, "只能有一个"))
        graphs.append((ListenerGraph(), "只能有一个"))
        graphs.append(({"nodes": "invalid", "connections": []}, "格式"))
        for graph, fragment in graphs:
            with self.subTest(fragment=fragment):
                self.assert_invalid(graph, fragment)

    def test_any_event_cycle_and_multiple_single_inputs_are_rejected(self):
        a, b = ListenerPart("a", "AnyEvent"), ListenerPart("b", "AnyEvent")
        self.graph.nodes += [a, b]
        self.graph.connections += [ListenerWire("a", "event", "b", "event"), ListenerWire("b", "event", "a", "event")]
        self.assert_invalid(self.graph, "循环")
        self.graph.connections += [ListenerWire("a", "event", self.change.uuid, "event")]
        self.assert_invalid(self.graph, "只能连接一个")

    def test_invalid_numeric_and_event_drafts_are_located(self):
        cases = [
            (self.change.uuid, "value", float("nan")),
            (self.change.uuid, "value", float("inf")),
            (self.change.uuid, "queue_index", 1.5),
            (self.change.uuid, "order", "bad"),
            (self.state.uuid, "minimum", 3),
            (self.state.uuid, "start_value", -1),
            (self.state.uuid, "parameter", None),
            (self.event.uuid, "events", " , \n"),
        ]
        for uid, key, value in cases:
            with self.subTest(key=key, value=value):
                graph = self.graph.clone()
                next(node for node in graph.nodes if node.uuid == uid).fields[key] = value
                result = self.assert_invalid(graph)
                self.assertTrue(any(issue.part_uuid == uid for issue in result.issues))
        self.event.kind = "IdleEvent"
        for value in ("1.5", "-1", "nan", "idle1"):
            self.event.fields["events"] = value
            self.assert_invalid(self.graph, "非负整数")

    def test_zero_width_parameter_range_is_valid_fixed_runtime_value(self):
        self.state.fields.update(minimum=0, maximum=0, start_value=0)
        self.assertTrue(compile_listener_graph(self.graph).valid)
        self.graph.nodes.append(ListenerPart("range", "IdleRange", {"minimum": 0, "maximum": 0, "idle": 0}))
        self.graph.connections.append(ListenerWire(self.state.uuid, "value", "range", "value"))
        self.assert_invalid(self.graph, "不含上限")

    def test_empty_parameter_retains_internal_state_without_model_binding(self):
        # Shipped row 40303725 stores a listener with no Cubism parameter.
        self.state.fields["parameter"] = ""
        result = compile_listener_graph(self.graph)
        self.assertTrue(result.valid, result.issues)
        self.assertEqual(result.fields["parameter"], "")
        self.assertTrue(any(issue.severity == "warning" and "内部" in issue.message for issue in result.issues))


class ListenerImportTests(unittest.TestCase):
    # Verified against local ship_l2d.csv rows 515, 1062 and 1498.
    EXAMPLES = [
        ("{type=1,change={{1,{'touch_drag1','touch_drag5','touch_drag9'},0},{1,{'touch_drag2','touch_drag6','touch_drag7'},1},{1,{'touch_drag3','touch_drag4','touch_drag8'},-1}},apply= {1,{{-1,0,8},{0,1,0},{1,2,9}}}}", "{-1,1}"),
        ("{type=2,change={{1,{'TouchDrag10'},10},{1,{'TouchDrag11'},5},{1,{'TouchDrag12','TouchDrag13'},2}}}", "{0,200}"),
        ("{type=3,change={{2,{1},0}}}", "{0,1}"),
    ]

    def test_real_configurations_round_trip_as_equivalent_runtime_tables(self):
        for literal, bounds in self.EXAMPLES:
            with self.subTest(literal=literal):
                graph = import_listener_graph(literal, {"parameter": "TestParam", "range": bounds, "start_value": 0, "save_parameter": 0})
                result = compile_listener_graph(graph)
                self.assertTrue(result.valid, result.issues)
                self.assertEqual(parse_listener_literal(result.listener_data), parse_listener_literal(literal))
                self.assertEqual(result.fields["save_parameter"], 0)
                self.assertEqual(result.fields["range"], bounds)
                self.assertEqual(ListenerGraph.from_payload(graph.to_payload()), graph)

    def test_legacy_explicit_queue_and_fractional_changes_survive(self):
        literal = "{type=1, change={{1,{'touch_drag1'},0.35,0},{2,{'touch_idle1'},1.5,3}}}"
        graph = import_listener_graph(literal, {"parameter": "p", "range": "{0,2}", "save_parameter": -1})
        result = compile_listener_graph(graph)
        self.assertEqual(parse_listener_literal(result.listener_data), parse_listener_literal(literal))
        self.assertEqual(result.fields["save_parameter"], -1)

    def test_lua_trailing_commas_quotes_and_escapes_are_literals(self):
        source = r'''{type=1,change={{2,{'touch_idle19','touch_idle18','touch_idle17',},1},},}'''
        self.assertEqual(parse_listener_literal(compile_listener_graph(import_listener_graph(source)).listener_data), parse_listener_literal(source))
        self.assertEqual(parse_listener_literal(r'''{'a\'b',"c\\d",-1.2e-2,true,false}'''), ["a'b", "c\\d", -0.012, True, False])

    def test_import_never_discards_unknown_fields_or_executes_lua(self):
        invalid = [
            "os.execute('calc')", "{type=1+2,change={}}", "{type=1,change={}}; print(1)",
            "{type=4,change={{1,{'a'},1}}}", "{type=true,change={{1,{'a'},1}}}",
            "{type=1,change={{3,{'a'},1}}}", "{type=1,change={{1,{'a'},1,1,99}}}",
            "{type=1,change={{1,{'a'},1}},future=2}",
            "{type=1,change={{1,{'a'},1}},apply={2,{}}}",
            "{type=1,change={{1,{'a,b'},1}}}", "{type=1,type=2,change={}}",
            "{type=1,change={{1,{'a'},1e999}}}", "{type=3,change={{1,{'idle1'},1}}}",
        ]
        for source in invalid:
            with self.subTest(source=source):
                with self.assertRaises(ListenerImportError):
                    import_listener_graph(source)

    def test_parser_rejects_unbounded_nesting_and_incomplete_literals(self):
        for source in ("{" * 80 + "}" * 80, "{1,", "{'broken}", "{one=1,2}"):
            with self.subTest(source=source):
                with self.assertRaises(ListenerImportError):
                    parse_listener_literal(source)


if __name__ == "__main__":
    unittest.main()
