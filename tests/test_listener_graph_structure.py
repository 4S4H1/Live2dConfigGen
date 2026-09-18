"""Structural listener graph boundaries, cloning and stable persistence."""

import copy
import dataclasses
import json
import unittest

from l2d_config_editor.listener_graph import (
    LISTENER_GRAPH_VERSION,
    MAX_LISTENER_CONNECTIONS,
    MAX_LISTENER_COORDINATE,
    MAX_LISTENER_FIELD_BYTES,
    MAX_LISTENER_FIELD_DEPTH,
    MAX_LISTENER_FIELD_VALUES,
    MAX_LISTENER_GRAPH_BYTES,
    MAX_LISTENER_NODES,
    ListenerGraph,
    ListenerPart,
    ListenerWire,
)


class ListenerGraphStructureTests(unittest.TestCase):
    def graph(self):
        return ListenerGraph(nodes=[
            ListenerPart("source", "future.trigger", {"events": ["中文动作", "touch_idle1"]}, {"x": -25, "y": 3}),
            ListenerPart("result", "future.result", {"amount": 2, "nested": {"enabled": True, "null": None}}, {"x": 200, "y": 30}),
        ], connections=[ListenerWire("source", "event", "result", "input")],
            view={"scale": 0.8, "offset_x": -120, "offset_y": 35})

    def test_round_trip_preserves_unknown_types_fields_positions_and_order(self):
        graph = self.graph()
        payload = graph.to_payload()
        self.assertIsInstance(payload["nodes"], dict)
        self.assertIsInstance(payload["connections"], dict)
        self.assertNotIn("uuid", payload["nodes"]["source"])
        self.assertEqual(graph, ListenerGraph.from_payload(json.loads(json.dumps(payload))))
        # Stable order survives writers that sort object keys.
        self.assertEqual(graph, ListenerGraph.from_payload(json.loads(json.dumps(payload, sort_keys=True))))

    def test_clone_deepcopy_and_payload_never_share_nested_field_objects(self):
        graph = self.graph()
        for clone in (graph.clone(), copy.deepcopy(graph), ListenerGraph.from_payload(graph.to_payload())):
            clone.nodes[0].fields["events"].append("另外一个动作")
            clone.nodes[0].ui_position["x"] = 99
            clone.view["scale"] = 5
            self.assertEqual(2, len(graph.nodes[0].fields["events"]))
            self.assertEqual(-25, graph.nodes[0].ui_position["x"])
            self.assertEqual(0.8, graph.view["scale"])
        detached = graph.to_payload()
        detached["nodes"]["source"]["fields"]["events"].clear()
        self.assertEqual(2, len(graph.nodes[0].fields["events"]))

    def test_part_payload_fields_cannot_inject_record_structure(self):
        part = ListenerPart("real", "actual", {"uuid": "injected", "kind": "wrong", "ui_position": {"x": 8}, "fields": {"uuid": "nested"}})
        payload = part.to_payload()
        self.assertEqual("real", payload["uuid"])
        self.assertEqual("actual", payload["kind"])
        self.assertEqual(part, ListenerPart.from_payload(payload))
        clone = part.clone()
        clone.fields["fields"]["uuid"] = "changed"
        self.assertEqual("nested", part.fields["fields"]["uuid"])

    def test_remap_creates_independent_graph_preserving_ports_and_field_strings(self):
        graph = self.graph()
        graph.nodes[0].fields["literal"] = "source"
        copied = graph.remap_ids()
        source_ids = {part.uuid for part in graph.nodes}
        new_ids = {part.uuid for part in copied.nodes}
        self.assertTrue(source_ids.isdisjoint(new_ids))
        self.assertEqual(2, len(new_ids))
        self.assertEqual(copied.nodes[0].uuid, copied.connections[0].from_uuid)
        self.assertEqual(copied.nodes[1].uuid, copied.connections[0].to_uuid)
        self.assertEqual("event", copied.connections[0].from_port)
        self.assertEqual("input", copied.connections[0].to_port)
        self.assertEqual("source", copied.nodes[0].fields["literal"])
        copied.nodes[0].fields["events"].clear()
        self.assertEqual(2, len(graph.nodes[0].fields["events"]))
        self.assertEqual(copied, ListenerGraph.from_payload(copied.to_payload()))

    def test_wires_are_frozen_hashable_and_port_pairs_have_distinct_identity(self):
        wire = self.graph().connections[0]
        alternate = dataclasses.replace(wire, to_port="alternate")
        self.assertEqual(wire, ListenerWire.from_payload(wire.to_payload()))
        self.assertEqual(wire, wire.clone())
        self.assertEqual(2, len({wire, alternate}))
        self.assertNotEqual(wire.identity, alternate.identity)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            wire.to_port = "changed"

    def test_structural_drafts_allow_empty_disconnected_unknown_parts_and_cycles(self):
        empty = ListenerGraph()
        self.assertEqual(empty, ListenerGraph.from_payload(empty.to_payload()))
        graph = self.graph()
        graph.nodes.append(ListenerPart("draft", "not.yet.catalogued"))
        graph.connections.extend([
            ListenerWire("result", "event", "source", "input"),
            ListenerWire("draft", "future.output", "draft", "future.input"),
        ])
        self.assertEqual(graph, ListenerGraph.from_payload(graph.to_payload()))

    def test_versions_reject_future_zero_boolean_float_and_string(self):
        for version in (LISTENER_GRAPH_VERSION + 1, 0, -1, True, 1.0, "1", None):
            with self.subTest(version=version):
                payload = self.graph().to_payload()
                payload["version"] = version
                with self.assertRaises(ValueError):
                    ListenerGraph.from_payload(payload)
                graph = self.graph()
                graph.version = version
                with self.assertRaises(ValueError):
                    graph.to_payload()

    def test_malformed_structure_and_unknown_structure_keys_rejected(self):
        for payload in (None, [], "{}", {}, {"version": 1, "nodes": [], "connections": {}},
                        {"version": 1, "nodes": {}, "connections": {}, "injected": 1}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                ListenerGraph.from_payload(payload)
        for mutate in (
            lambda payload: payload["nodes"]["source"].update({"uuid": "mismatch"}),
            lambda payload: payload["nodes"]["source"].update({"unknown": 1}),
            lambda payload: payload["nodes"]["source"].update({"kind": ""}),
            lambda payload: payload["nodes"].update({"bad uuid": payload["nodes"].pop("source")}),
            lambda payload: payload.update({"node_order": ["source", "source"]}),
            lambda payload: payload.update({"connection_order": []}),
        ):
            payload = self.graph().to_payload()
            mutate(payload)
            with self.assertRaises(ValueError):
                ListenerGraph.from_payload(payload)

    def test_wire_keys_dangling_endpoints_and_duplicates_are_rejected(self):
        graph = self.graph()
        graph.connections.append(graph.connections[0])
        with self.assertRaises(ValueError):
            graph.to_payload()
        graph = self.graph()
        graph.connections = [ListenerWire("missing", "out", "result", "input")]
        with self.assertRaises(ValueError):
            graph.to_payload()
        payload = self.graph().to_payload()
        key = payload["connection_order"][0]
        payload["connections"][key]["from_port"] = "changed"
        with self.assertRaises(ValueError):
            ListenerGraph.from_payload(payload)
        payload = self.graph().to_payload()
        payload["connections"][key].pop("from_port")
        with self.assertRaises(ValueError):
            ListenerGraph.from_payload(payload)

    def test_duplicate_node_ids_rejected_without_mutating_input(self):
        graph = self.graph()
        graph.nodes.append(graph.nodes[0].clone())
        with self.assertRaises(ValueError):
            graph.to_payload()
        self.assertEqual(3, len(graph.nodes))

    def test_coordinates_and_views_require_bounded_finite_numbers(self):
        for value in (float("inf"), float("-inf"), float("nan"), True, "1", None, 10 ** 500,
                      MAX_LISTENER_COORDINATE + 1):
            with self.subTest(value=value):
                graph = self.graph()
                graph.nodes[0].ui_position["x"] = value
                with self.assertRaises(ValueError):
                    graph.to_payload()
        for view in ({"scale": 0}, {"scale": -1}, {"scale": float("nan")}, {"extra": 1},
                     {"offset_x": MAX_LISTENER_COORDINATE + 1}, None):
            graph = self.graph()
            graph.view = view
            with self.subTest(view=view), self.assertRaises(ValueError):
                graph.to_payload()
        graph = self.graph()
        graph.nodes[0].ui_position = {"x": 1}
        with self.assertRaises(ValueError):
            graph.to_payload()

    def test_fields_reject_non_json_non_finite_cycles_and_invalid_unicode(self):
        cyclic = {}
        cyclic["self"] = cyclic
        for fields in ([], {"tuple": (1, 2)}, {1: "key"}, {"set": {1}}, {"bad": object()},
                       {"bad": float("nan")}, {"bad": float("inf")}, cyclic, {"bad": "\ud800"}):
            part = ListenerPart("part", "future.kind", fields)
            with self.subTest(kind=type(fields).__name__), self.assertRaises(ValueError):
                part.to_payload()

    def test_field_depth_value_count_and_encoded_byte_limits(self):
        nested = value = {}
        for _ in range(MAX_LISTENER_FIELD_DEPTH + 1):
            value["nested"] = {}
            value = value["nested"]
        for fields in (nested, {"many": [0] * MAX_LISTENER_FIELD_VALUES},
                       {"large": "x" * MAX_LISTENER_FIELD_BYTES},
                       {"utf8": "中" * (MAX_LISTENER_FIELD_BYTES // 2)}):
            with self.assertRaises(ValueError):
                ListenerPart("part", "future.kind", fields).to_payload()

    def test_graph_count_and_total_byte_limits(self):
        graph = ListenerGraph(nodes=[ListenerPart(f"n{i}", "kind") for i in range(MAX_LISTENER_NODES + 1)])
        with self.assertRaises(ValueError):
            graph.to_payload()
        graph = self.graph()
        graph.connections = [ListenerWire("source", f"out{i}", "result", "input")
                             for i in range(MAX_LISTENER_CONNECTIONS + 1)]
        with self.assertRaises(ValueError):
            graph.to_payload()
        field_size = MAX_LISTENER_FIELD_BYTES // 2
        count = MAX_LISTENER_GRAPH_BYTES // field_size + 1
        graph = ListenerGraph(nodes=[ListenerPart(f"n{i}", "kind", {"data": "x" * field_size}) for i in range(count)])
        with self.assertRaises(ValueError):
            graph.to_payload()
        payload = self.graph().to_payload()
        payload["nodes"] = {f"n{i}": {"kind": "kind"} for i in range(MAX_LISTENER_NODES + 1)}
        with self.assertRaises(ValueError):
            ListenerGraph.from_payload(payload)

    def test_missing_optional_orders_use_object_order_and_view_defaults(self):
        payload = self.graph().to_payload()
        payload.pop("node_order")
        payload.pop("connection_order")
        payload.pop("view")
        graph = ListenerGraph.from_payload(payload)
        self.assertEqual(["source", "result"], [part.uuid for part in graph.nodes])
        self.assertEqual({"scale": 1.0, "offset_x": 0.0, "offset_y": 0.0}, graph.view)

    def test_field_edit_preserves_persisted_node_and_wire_identity(self):
        before = self.graph().to_payload()
        after = copy.deepcopy(before)
        after["nodes"]["result"]["fields"]["amount"] = 3
        self.assertEqual(before["node_order"], after["node_order"])
        self.assertEqual(before["connections"], after["connections"])
        after["nodes"]["result"]["fields"]["amount"] = 2
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
