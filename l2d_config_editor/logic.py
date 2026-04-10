"""Pure document logic for the editor."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
from typing import Any

from .models import (
    CanvasViewState,
    ConnectionRecord,
    CsvPreviewRow,
    DocumentModel,
    MetaRecord,
    NodeRecord,
    SearchHit,
    ValidationIssue,
)
from .schema import EditorSchema, FieldSchema, NodeSchema, load_editor_schema

RANGE_PATTERN = re.compile(r"^\{\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\}$")


@lru_cache(maxsize=2)
def get_default_schema(schema_path: str | None = None) -> EditorSchema:
    return load_editor_schema(schema_path)


def new_uuid() -> str:
    return uuid.uuid4().hex


def default_fields(schema: EditorSchema, node_type: str) -> dict[str, Any]:
    definition = schema.nodes[node_type]
    return {field.key: field.default for field in definition.fields}


def function_node_types(schema: EditorSchema) -> tuple[str, ...]:
    return tuple(type_name for type_name, node in schema.nodes.items() if node.category == "function")


def parse_range(value: str) -> tuple[float, float] | None:
    match = RANGE_PATTERN.match(str(value or "").strip())
    if not match:
        return None
    return float(match.group(1)), float(match.group(2))


def normalize_parts_data(value: str) -> list[float] | None:
    text = str(value or "").strip()
    if not text:
        return []
    result: list[float] = []
    for chunk in text.split(","):
        chunk = chunk.strip()
        try:
            result.append(float(chunk))
        except ValueError:
            return None
    return result


def _format_ignore_values(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


def _function_nodes(schema: EditorSchema, document: DocumentModel) -> list[NodeRecord]:
    node_types = set(function_node_types(schema))
    return [node for node in document.nodes if node.type in node_types]


def _next_sequence_no(document: DocumentModel, node_type: str) -> int:
    existing = [node.sequence_no or 0 for node in document.nodes if node.type == node_type]
    return (max(existing) if existing else 0) + 1


def _node_schema(schema: EditorSchema, node_type: str) -> NodeSchema:
    return schema.nodes[node_type]


def _sequence_action_name(node_schema: NodeSchema, target_idle: int) -> str:
    template = node_schema.auto_rules.action_name_template or "touch_idle{target_idle}"
    return template.format(target_idle=target_idle)


def _expected_parameter(node_schema: NodeSchema, target_idle: int) -> str:
    template = node_schema.auto_rules.parameter_template
    return template.format(target_idle=target_idle, sequence=target_idle)


def _animated_action(schema: EditorSchema, node_schema: NodeSchema, target_idle: int) -> tuple[str, str]:
    action_name = _sequence_action_name(node_schema, target_idle)
    action = (
        schema.animated_action_template.replace("{target_idle}", str(target_idle))
        .replace("{action_name}", action_name)
    )
    active = (
        schema.animated_active_template.replace("{target_idle}", str(target_idle))
        .replace("{ignore_values}", _format_ignore_values(schema.default_ignore))
    )
    return action, active


def _hard_cut_action(schema: EditorSchema, target_idle: int) -> tuple[str, str]:
    action = schema.hard_cut_action_template
    active = (
        schema.hard_cut_active_template.replace("{target_idle}", str(target_idle))
        .replace("{ignore_values}", _format_ignore_values(schema.hard_cut_ignore))
    )
    return action, active


def _infer_expected_actions(schema: EditorSchema, node: NodeRecord) -> tuple[str, str]:
    target_idle = int(node.fields.get("target_idle") or 0)
    node_schema = _node_schema(schema, node.type)
    if node.type == "TouchIdle" and node.fields.get("transition_type") == "hard":
        return _hard_cut_action(schema, target_idle)
    return _animated_action(schema, node_schema, target_idle)


def infer_manual_fields(schema: EditorSchema, node: NodeRecord) -> None:
    if node.type not in function_node_types(schema):
        return
    node_schema = _node_schema(schema, node.type)
    target_idle = int(node.fields.get("target_idle") or 0)
    expected_parameter = _expected_parameter(node_schema, target_idle)
    if str(node.fields.get("parameter", "")) != expected_parameter:
        node.manual_fields.add("parameter")
    if node.type == "TouchDrag" and node.fields.get("result_type") == "value":
        return
    expected_action, expected_active = _infer_expected_actions(schema, node)
    if str(node.fields.get("action_trigger", "")) != expected_action:
        node.manual_fields.add("action_trigger")
    if str(node.fields.get("action_trigger_active", "")) != expected_active:
        node.manual_fields.add("action_trigger_active")


def apply_sequence_defaults(schema: EditorSchema, node: NodeRecord) -> None:
    if node.type not in function_node_types(schema):
        return
    node_schema = _node_schema(schema, node.type)
    sequence = node.sequence_no or 1
    target_idle = sequence if node_schema.auto_rules.use_sequence_for_target_idle else int(node.fields.get("target_idle") or sequence)
    node.fields["draw_able_name"] = node_schema.auto_rules.draw_template.format(sequence=sequence, target_idle=target_idle)
    node.fields["target_idle"] = target_idle
    node.fields["parameter"] = _expected_parameter(node_schema, target_idle)
    action, active = _infer_expected_actions(schema, node)
    node.fields["action_trigger"] = action
    node.fields["action_trigger_active"] = active
    node.manual_fields.difference_update({"parameter", "action_trigger", "action_trigger_active"})


def _update_drag_offsets(node: NodeRecord, changed_key: str | None, source_mode: str) -> None:
    fields = node.fields
    if changed_key == "drag_direct" and source_mode == "advanced":
        node.manual_fields.add("drag_direct")
    if changed_key in {"offset_x", "offset_y"} and source_mode == "advanced":
        node.manual_fields.update({"offset_x", "offset_y"})
    if fields.get("control_type") == "drag" and (source_mode == "simple" or changed_key in {"control_type", "drag_ui_direction"}):
        fields["drag_direct"] = 1
        direction = fields.get("drag_ui_direction", "")
        if direction == "up":
            fields["offset_x"] = 0
            fields["offset_y"] = 100
        elif direction == "down":
            fields["offset_x"] = 0
            fields["offset_y"] = -100
        elif direction == "left":
            fields["offset_x"] = -100
            fields["offset_y"] = 0
        elif direction == "right":
            fields["offset_x"] = 100
            fields["offset_y"] = 0
    elif fields.get("control_type") != "drag" and (source_mode == "simple" or changed_key == "control_type"):
        fields["drag_direct"] = 0


def _update_range_abs(node: NodeRecord) -> None:
    parsed = parse_range(node.fields.get("range", ""))
    if not parsed:
        return
    node.fields["range_abs"] = 0 if parsed[0] < 0 else node.fields.get("range_abs", 1)


def apply_auto_rules(
    schema: EditorSchema,
    document: DocumentModel,
    node: NodeRecord,
    *,
    source_mode: str = "simple",
    changed_key: str | None = None,
    force_generated: bool = False,
) -> None:
    del document
    if node.type == "Initial":
        return
    if changed_key == "target_idle" and source_mode == "simple":
        node.manual_fields.difference_update({"parameter", "action_trigger", "action_trigger_active"})
    _update_drag_offsets(node, changed_key, source_mode)
    _update_range_abs(node)
    if node.type not in function_node_types(schema):
        return
    target_idle = int(node.fields.get("target_idle") or 0)
    node_schema = _node_schema(schema, node.type)
    if force_generated or "parameter" not in node.manual_fields:
        node.fields["parameter"] = _expected_parameter(node_schema, target_idle)
    if node.type == "TouchDrag" and node.fields.get("result_type") == "value":
        if force_generated or source_mode == "simple":
            node.fields["action_trigger"] = ""
            node.fields["action_trigger_active"] = ""
        return
    expected_action, expected_active = _infer_expected_actions(schema, node)
    if force_generated or "action_trigger" not in node.manual_fields:
        node.fields["action_trigger"] = expected_action
    if force_generated or "action_trigger_active" not in node.manual_fields:
        node.fields["action_trigger_active"] = expected_active


def create_node(
    schema: EditorSchema,
    document: DocumentModel,
    node_type: str,
    position: tuple[float, float] = (0.0, 0.0),
    base_node: NodeRecord | None = None,
) -> NodeRecord:
    fields = default_fields(schema, node_type)
    if base_node:
        fields.update(dict(base_node.fields))
    node = NodeRecord(
        uuid=new_uuid(),
        type=node_type,
        fields=fields,
        ui_position={"x": float(position[0]), "y": float(position[1])},
        ui_size=dict(base_node.ui_size) if base_node and base_node.ui_size else ({"width": 360.0, "height": 180.0} if node_type == "Comment" else None),
        sequence_no=_next_sequence_no(document, node_type),
        manual_fields=set(),
    )
    if node.type in function_node_types(schema):
        apply_sequence_defaults(schema, node)
    else:
        apply_auto_rules(schema, document, node, force_generated=False)
    return node


def sync_meta_from_initial(document: DocumentModel) -> None:
    initial = next((node for node in document.nodes if node.type == "Initial"), None)
    if not initial:
        return
    document.meta = MetaRecord(
        version=str(initial.fields.get("version", "2099-09-09")),
        author=str(initial.fields.get("author", "")),
        ship_skin_id=int(initial.fields.get("ship_skin_id") or 0),
        memo=str(initial.fields.get("memo", "")),
        default_state=str(initial.fields.get("defaultState", "idle0")),
        react_condition=int(initial.fields.get("react_condition") or 0),
        tips=str(initial.fields.get("tips", "")),
        CharName=str(initial.fields.get("CharName", "")),
    )


def sync_initial_from_meta(schema: EditorSchema, document: DocumentModel) -> None:
    initial = next((node for node in document.nodes if node.type == "Initial"), None)
    if not initial:
        initial = create_node(schema, document, "Initial", (60.0, 60.0))
        document.nodes.insert(0, initial)
    initial.fields.update(
        {
            "version": document.meta.version,
            "author": document.meta.author,
            "ship_skin_id": document.meta.ship_skin_id,
            "memo": document.meta.memo,
            "defaultState": document.meta.default_state,
            "react_condition": document.meta.react_condition,
            "tips": document.meta.tips,
            "CharName": document.meta.CharName,
        }
    )


def recompute_document_state(schema: EditorSchema, document: DocumentModel) -> None:
    sync_meta_from_initial(document)
    missing: list[str] = []
    for field in schema.required_meta_fields:
        value = getattr(document.meta, field if field != "defaultState" else "default_state", None)
        if field == "ship_skin_id":
            if not isinstance(value, int) or value <= 0:
                missing.append(schema.required_meta_labels[field])
            continue
        if not str(value or "").strip():
            missing.append(schema.required_meta_labels[field])
    document.state.is_meta_ready = not missing
    document.state.meta_missing_fields = missing


def create_document(schema: EditorSchema | None = None) -> DocumentModel:
    active_schema = schema or get_default_schema()
    document = DocumentModel()
    document.nodes.append(create_node(active_schema, document, "Initial", (72.0, 72.0)))
    sync_meta_from_initial(document)
    reassign_function_ids(active_schema, document)
    recompute_document_state(active_schema, document)
    return document


def reassign_function_ids(schema: EditorSchema, document: DocumentModel) -> None:
    sync_meta_from_initial(document)
    base = document.meta.ship_skin_id or 0
    counter = 1
    for node in document.nodes:
        if node.type not in function_node_types(schema):
            continue
        node.fields["id"] = int(f"{base}{counter:02d}") if base else counter
        counter += 1
        apply_auto_rules(schema, document, node, source_mode="advanced", force_generated=False)
    recompute_document_state(schema, document)


def node_title(schema: EditorSchema, node: NodeRecord) -> str:
    definition = _node_schema(schema, node.type)
    if node.type == "Initial":
        return definition.title
    if node.type == "Comment":
        return node.fields.get("content", "").splitlines()[0][:24] or definition.title
    draw_name = node.fields.get("draw_able_name") or node.fields.get("parameter") or definition.title
    return f"{definition.title}: {draw_name}"


def export_document_dict(schema: EditorSchema, document: DocumentModel) -> dict[str, Any]:
    sync_meta_from_initial(document)
    reassign_function_ids(schema, document)
    serialized_nodes = []
    for node in document.nodes:
        payload: dict[str, Any] = {
            "uuid": node.uuid,
            "type": node.type,
            "ui_position": node.ui_position,
        }
        if node.ui_size:
            payload["ui_size"] = node.ui_size
        payload.update(node.fields)
        serialized_nodes.append(payload)
    return {
        "meta": asdict(document.meta),
        "nodes": serialized_nodes,
        "connections": [asdict(connection) for connection in document.connections],
        "canvas_view": asdict(document.canvas_view),
    }


def save_document(schema: EditorSchema, document: DocumentModel, path: str | Path) -> None:
    data = export_document_dict(schema, document)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    document.path = str(path)


def load_document(schema: EditorSchema, path: str | Path) -> DocumentModel:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    document = DocumentModel(
        meta=MetaRecord(**payload.get("meta", {})),
        connections=[ConnectionRecord(**item) for item in payload.get("connections", [])],
        canvas_view=CanvasViewState(**payload.get("canvas_view", {})),
        path=str(path),
    )
    sequence_map: dict[str, int] = {}
    for raw in payload.get("nodes", []):
        fields = {key: value for key, value in raw.items() if key not in {"uuid", "type", "ui_position", "ui_size", "mode_variant"}}
        node_type = raw["type"]
        sequence_map[node_type] = sequence_map.get(node_type, 0) + 1
        node = NodeRecord(
            uuid=raw.get("uuid") or new_uuid(),
            type=node_type,
            fields=fields,
            ui_position=raw.get("ui_position", {"x": 0.0, "y": 0.0}),
            ui_size=raw.get("ui_size"),
            sequence_no=sequence_map[node_type],
        )
        infer_manual_fields(schema, node)
        apply_auto_rules(schema, document, node, source_mode="advanced", force_generated=False)
        document.nodes.append(node)
    sync_initial_from_meta(schema, document)
    reassign_function_ids(schema, document)
    recompute_document_state(schema, document)
    return document


def _csv_value_for_mapping(mapping, document: DocumentModel, node: NodeRecord) -> Any:
    if mapping.kind == "node":
        return node.fields.get(mapping.field, mapping.default)
    if mapping.kind == "meta":
        return getattr(document.meta, mapping.field or "", mapping.default)
    if mapping.kind == "meta_fallback":
        for field in mapping.fields:
            value = getattr(document.meta, field, "")
            if str(value).strip():
                return value
        return mapping.default
    return mapping.default


def document_to_csv_rows(schema: EditorSchema, document: DocumentModel) -> list[CsvPreviewRow]:
    sync_meta_from_initial(document)
    reassign_function_ids(schema, document)
    rows: list[CsvPreviewRow] = []
    for node in _function_nodes(schema, document):
        values = {column: "" for column in schema.csv_columns}
        for mapping in schema.csv_mapping:
            values[mapping.column] = _csv_value_for_mapping(mapping, document, node)
        rows.append(CsvPreviewRow(values=values))
    return rows


def _issue_for_group(
    schema: EditorSchema,
    message: str,
    field_keys: list[str],
    group: list[NodeRecord],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for node in group:
        related = [other for other in group if other.uuid != node.uuid]
        issues.append(
            ValidationIssue(
                node_uuid=node.uuid,
                message=message,
                field_keys=list(field_keys),
                related_node_uuids=[item.uuid for item in related],
                related_titles=[node_title(schema, item) for item in related],
            )
        )
    return issues


def validate_document(schema: EditorSchema, document: DocumentModel) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    initial_nodes = [node for node in document.nodes if node.type == "Initial"]
    if len(initial_nodes) > 1:
        issues.extend(_issue_for_group(schema, "存在多个初始节点", [], initial_nodes))

    parameter_map: dict[str, list[NodeRecord]] = {}
    draw_map: dict[str, list[NodeRecord]] = {}
    for node in document.nodes:
        if node.type in function_node_types(schema):
            parameter = str(node.fields.get("parameter", "")).strip()
            if parameter:
                parameter_map.setdefault(parameter, []).append(node)
            parts = normalize_parts_data(node.fields.get("parts_data", ""))
            if parts is None:
                issues.append(
                    ValidationIssue(
                        node_uuid=node.uuid,
                        message="parts_data 不是合法的逗号分隔数字列表",
                        field_keys=["parts_data"],
                    )
                )
            parsed_range = parse_range(node.fields.get("range", ""))
            if parts is not None and parsed_range is not None and any(part < parsed_range[0] or part > parsed_range[1] for part in parts):
                issues.append(
                    ValidationIssue(
                        node_uuid=node.uuid,
                        message="parts_data 超出了 range 定义范围",
                        field_keys=["parts_data", "range"],
                    )
                )
        if node.type == "TouchIdle":
            draw_name = str(node.fields.get("draw_able_name", "")).strip()
            if draw_name:
                draw_map.setdefault(draw_name, []).append(node)

    for group in parameter_map.values():
        if len(group) > 1:
            issues.extend(_issue_for_group(schema, "parameter 重复", ["parameter"], group))

    for group in draw_map.values():
        if len(group) <= 1:
            continue
        baseline = (group[0].fields.get("action_trigger", ""), group[0].fields.get("action_trigger_active", ""))
        if any((node.fields.get("action_trigger", ""), node.fields.get("action_trigger_active", "")) != baseline for node in group[1:]):
            issues.extend(
                _issue_for_group(
                    schema,
                    "相同 draw_able_name 的 TouchIdle 节点动作内容不一致",
                    ["draw_able_name", "action_trigger", "action_trigger_active"],
                    group,
                )
            )
    return issues


def search_document(schema: EditorSchema, document: DocumentModel, text: str) -> list[SearchHit]:
    needle = text.strip().lower()
    if not needle:
        return []
    hits: list[SearchHit] = []
    for node in document.nodes:
        for key, value in node.fields.items():
            haystack = str(value)
            if needle in haystack.lower():
                hits.append(
                    SearchHit(
                        node_uuid=node.uuid,
                        node_type=node.type,
                        title=node_title(schema, node),
                        field_name=key,
                        preview=haystack[:120],
                    )
                )
    return hits
