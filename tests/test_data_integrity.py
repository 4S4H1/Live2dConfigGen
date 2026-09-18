import copy
import unittest

from l2d_config_editor.logic import (
    apply_auto_rules, create_document, create_node, document_to_csv_rows,
    export_document_dict, get_default_schema, load_document_payload, validate_document,
)


class DocumentSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = get_default_schema()
        self.document = create_document(self.schema)
        self.node = create_node(self.schema, self.document, "TouchIdle", (120.0, 80.0))
        self.node.fields["extension_data"] = {"values": [1, 2]}
        self.document.nodes.append(self.node)
        self.document.history = {"legacy": {"revisions": [1]}}

    def test_node_clone_owns_nested_field_values(self) -> None:
        cloned = self.node.clone()
        cloned.fields["extension_data"]["values"].append(3)
        self.assertEqual([1, 2], self.node.fields["extension_data"]["values"])

    def test_node_created_from_base_owns_nested_field_values(self) -> None:
        cloned = create_node(self.schema, self.document, self.node.type, base_node=self.node)
        cloned.fields["extension_data"]["values"].append(3)
        self.assertEqual([1, 2], self.node.fields["extension_data"]["values"])

    def test_exported_payload_is_independent_of_subsequent_model_edits(self) -> None:
        payload = export_document_dict(self.schema, self.document)
        expected = copy.deepcopy(payload)
        self.node.fields["extension_data"]["values"].append(3)
        self.node.ui_position["x"] = 999.0
        self.document.history["legacy"]["revisions"].append(2)
        self.assertEqual(expected, payload)

    def test_loaded_mapping_is_independent_of_caller_owned_payload(self) -> None:
        payload = copy.deepcopy(export_document_dict(self.schema, self.document))
        loaded = load_document_payload(self.schema, payload)
        raw_node = next(raw for raw in payload["nodes"] if raw["uuid"] == self.node.uuid)
        raw_node["extension_data"]["values"].append(3)
        raw_node["ui_position"]["x"] = 999.0
        loaded_node = next(node for node in loaded.nodes if node.uuid == self.node.uuid)
        self.assertEqual([1, 2], loaded_node.fields["extension_data"]["values"])
        self.assertEqual(120.0, loaded_node.ui_position["x"])

    def test_parts_precision_survives_edit_save_load_and_csv(self) -> None:
        self.node.fields["parts_data"] = "0.123456789012345,0.999999999"
        expected = "{parts={0.123456789012345,0.999999999}}"
        apply_auto_rules(self.schema, self.document, self.node, source_mode="advanced", changed_key="parts_data")
        self.assertEqual(expected, self.node.fields["parts_data"])
        loaded = load_document_payload(self.schema, export_document_dict(self.schema, self.document))
        self.assertEqual(expected, document_to_csv_rows(self.schema, loaded)[0].values["parts_data"])

    def test_nonfinite_parts_are_reported_as_invalid(self) -> None:
        for value in ("nan", "inf", "-inf", "1e309"):
            with self.subTest(value=value):
                self.node.fields["parts_data"] = value
                issues = validate_document(self.schema, self.document)
                self.assertTrue(any(issue.field_keys == ["parts_data"] for issue in issues))

    def test_parts_bounds_support_scientific_and_fractional_range_literals(self) -> None:
        for value in ("{-1e-8,1e-8}", "{-.5,+.5}"):
            with self.subTest(value=value):
                self.node.fields["range"] = value
                self.node.fields["parts_data"] = "0.75"
                issues = validate_document(self.schema, self.document)
                self.assertTrue(any(issue.field_keys == ["parts_data", "range"] for issue in issues))


if __name__ == "__main__":
    unittest.main()
