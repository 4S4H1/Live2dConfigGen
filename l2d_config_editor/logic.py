"""Pure document logic for the editor."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .constants import ACTION_TRIGGER_IGNORE, CSV_COLUMNS, FUNCTION_NODE_TYPES
from .definitions import NODE_DEFINITIONS
from .models import CanvasViewState, ConnectionRecord, CsvPreviewRow, DocumentModel, MetaRecord, NodeRecord, SearchHit, ValidationIssue

RANGE_PATTERN = re.compile(r"^\{\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\}$")


def new_uuid() -> str:
    return uuid.uuid4().hex


def default_fields(node_type: str) -> dict[str, Any]:
    definition = NODE_DEFINITIONS[node_type]
    return {field.key: field.default for field in definition.fields if field.key != "auto_preview"}


def create_node(node_type: str, position: tuple[float, float] = (0.0, 0.0)) -> NodeRecord:
    fields = default_fields(node_type)
    node = NodeRecord(
        uuid=new_uuid(),
        type=node_type,
        fields=fields,
        ui_position={"x": float(position[0]), "y": float(position[1])},
        ui_size={"width": 260.0, "height": 140.0} if node_type == "Comment" else None,
        mode_variant="simple",
    )
    apply_auto_rules(node)
    return node


def create_document() -> DocumentModel:
    document = DocumentModel()
    document.nodes.append(create_node("Initial", (60.0, 60.0)))
    sync_meta_from_initial(document)
    reassign_function_ids(document)
    return document


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


def action_trigger_string(target_idle: int) -> str:
    return "{type = 2 ,action = 'touch_idle%s'}" % target_idle


def action_trigger_active_string(target_idle: int) -> str:
    if target_idle == 0:
        return "{enable = {},ignore = {},idle = 0}"
    ignore = ",".join("'%s'" % value for value in ACTION_TRIGGER_IGNORE)
    return "{enable = {},ignore = {%s},idle = %s}" % (ignore, target_idle)


def _update_drag_offsets(fields: dict[str, Any]) -> None:
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


def _update_range_abs(fields: dict[str, Any]) -> None:
    parsed = parse_range(fields.get("range", ""))
    if parsed and parsed[0] < 0:
        fields["range_abs"] = 0


def apply_auto_rules(node: NodeRecord) -> None:
    fields = node.fields
    if node.type == "Initial":
        return
    target_idle = int(fields.get("target_idle") or 0)
    if fields.get("control_type") == "drag":
        fields["drag_direct"] = 1 if fields.get("drag_direct") in (0, 1, "", None) else fields.get("drag_direct", 1)
        _update_drag_offsets(fields)
    elif fields.get("drag_direct") in (None, "", 1, 0):
        fields["drag_direct"] = 0
    _update_range_abs(fields)
    if node.type == "TouchIdle":
        fields["parameter"] = f"Paramtouch_idle{target_idle}"
        if node.mode_variant == "simple" or not fields.get("action_trigger"):
            fields["action_trigger"] = action_trigger_string(target_idle)
        if node.mode_variant == "simple" or not fields.get("action_trigger_active"):
            fields["action_trigger_active"] = action_trigger_active_string(target_idle)
        fields["auto_preview"] = (
            f"action_trigger: {fields['action_trigger']}\n"
            f"action_trigger_active: {fields['action_trigger_active']}"
        )
    if node.type == "TouchDrag":
        fields["parameter"] = f"Touch_drag{target_idle}"
        if fields.get("result_type", "action") == "action":
            if node.mode_variant == "simple" or not fields.get("action_trigger"):
                fields["action_trigger"] = action_trigger_string(target_idle)
            if node.mode_variant == "simple" or not fields.get("action_trigger_active"):
                fields["action_trigger_active"] = action_trigger_active_string(target_idle)


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


def sync_initial_from_meta(document: DocumentModel) -> None:
    initial = next((node for node in document.nodes if node.type == "Initial"), None)
    if not initial:
        initial = create_node("Initial", (60.0, 60.0))
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


def reassign_function_ids(document: DocumentModel) -> None:
    sync_meta_from_initial(document)
    base = document.meta.ship_skin_id or 0
    counter = 1
    for node in document.nodes:
        if node.type not in FUNCTION_NODE_TYPES:
            continue
        node.fields["id"] = int(f"{base}{counter:02d}") if base else counter
        counter += 1
        apply_auto_rules(node)


def node_title(node: NodeRecord) -> str:
    if node.type == "Initial":
        return "Initial"
    if node.type == "Comment":
        return node.fields.get("content", "").splitlines()[0][:24] or "Comment"
    name = node.fields.get("draw_able_name") or node.fields.get("parameter") or node.type
    return f"{node.type}: {name}"


def export_document_dict(document: DocumentModel) -> dict[str, Any]:
    sync_meta_from_initial(document)
    reassign_function_ids(document)
    function_nodes = [node for node in document.nodes if node.type in FUNCTION_NODE_TYPES]
    function_nodes.sort(key=lambda node: int(node.fields.get("id") or 0))
    other_nodes = [node for node in document.nodes if node.type not in FUNCTION_NODE_TYPES]
    serialized_nodes = []
    for node in [*other_nodes, *function_nodes]:
        payload: dict[str, Any] = {
            "uuid": node.uuid,
            "type": node.type,
            "mode_variant": node.mode_variant,
            "ui_position": node.ui_position,
        }
        if node.ui_size:
            payload["ui_size"] = node.ui_size
        for key, value in node.fields.items():
            if key == "auto_preview":
                continue
            payload[key] = value
        serialized_nodes.append(payload)
    return {
        "meta": asdict(document.meta),
        "nodes": serialized_nodes,
        "connections": [asdict(connection) for connection in document.connections],
        "canvas_view": asdict(document.canvas_view),
    }


def save_document(document: DocumentModel, path: str | Path) -> None:
    data = export_document_dict(document)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    document.path = str(path)


def load_document(path: str | Path) -> DocumentModel:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    document = DocumentModel(
        meta=MetaRecord(**payload.get("meta", {})),
        connections=[ConnectionRecord(**item) for item in payload.get("connections", [])],
        canvas_view=CanvasViewState(**payload.get("canvas_view", {})),
        path=str(path),
    )
    for raw in payload.get("nodes", []):
        fields = {key: value for key, value in raw.items() if key not in {"uuid", "type", "mode_variant", "ui_position", "ui_size"}}
        node = NodeRecord(
            uuid=raw.get("uuid") or new_uuid(),
            type=raw["type"],
            fields=fields,
            ui_position=raw.get("ui_position", {"x": 0.0, "y": 0.0}),
            ui_size=raw.get("ui_size"),
            mode_variant=raw.get("mode_variant", "simple"),
        )
        apply_auto_rules(node)
        document.nodes.append(node)
    sync_initial_from_meta(document)
    reassign_function_ids(document)
    return document


def document_to_csv_rows(document: DocumentModel) -> list[CsvPreviewRow]:
    sync_meta_from_initial(document)
    reassign_function_ids(document)
    desc = document.meta.memo or document.meta.CharName
    rows: list[CsvPreviewRow] = []
    for node in sorted((node for node in document.nodes if node.type in FUNCTION_NODE_TYPES), key=lambda item: int(item.fields.get("id") or 0)):
        values = {column: "" for column in CSV_COLUMNS}
        values.update(
            {
                "id": node.fields.get("id", ""),
                "desc": desc,
                "memo": document.meta.memo,
                "ship_skin_id": document.meta.ship_skin_id,
                "draw_able_name": node.fields.get("draw_able_name", ""),
                "parameter": node.fields.get("parameter", ""),
                "mode": node.fields.get("mode", 1),
                "start_value": node.fields.get("start_value", 0),
                "range": node.fields.get("range", ""),
                "parts_data": node.fields.get("parts_data", ""),
                "ignore_react": node.fields.get("ignore_react", 1),
                "react_condition": document.meta.react_condition,
                "ignore_action": node.fields.get("ignore_action", 1),
                "range_abs": node.fields.get("range_abs", 1),
                "drag_direct": node.fields.get("drag_direct", 0),
                "react_pos_x": node.fields.get("react_pos_x", ""),
                "react_pos_y": node.fields.get("react_pos_y", ""),
                "offset_x": node.fields.get("offset_x", 0),
                "offset_y": node.fields.get("offset_y", 0),
                "smooth": node.fields.get("smooth", 100),
                "revert_smooth": node.fields.get("revert_smooth", 100),
                "revert": node.fields.get("revert", -1),
                "gyro": node.fields.get("gyro", 0),
                "gyro_x": node.fields.get("gyro_x", 0),
                "gyro_y": node.fields.get("gyro_y", 0),
                "gyro_z": node.fields.get("gyro_z", 0),
                "limit_time": node.fields.get("limit_time", 1),
                "action_trigger": node.fields.get("action_trigger", ""),
                "action_trigger_active": node.fields.get("action_trigger_active", ""),
                "shop_action": node.fields.get("shop_action", 0),
            }
        )
        rows.append(CsvPreviewRow(values=values))
    return rows


def validate_document(document: DocumentModel) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    initial_nodes = [node for node in document.nodes if node.type == "Initial"]
    if len(initial_nodes) > 1:
        for node in initial_nodes:
            issues.append(ValidationIssue(node_uuid=node.uuid, message="存在多个初始节点"))
    parameter_map: dict[str, list[NodeRecord]] = {}
    draw_map: dict[str, list[NodeRecord]] = {}
    for node in document.nodes:
        if node.type in FUNCTION_NODE_TYPES:
            parameter = str(node.fields.get("parameter", "")).strip()
            if parameter:
                parameter_map.setdefault(parameter, []).append(node)
        if node.type == "TouchIdle":
            draw_name = str(node.fields.get("draw_able_name", "")).strip()
            if draw_name:
                draw_map.setdefault(draw_name, []).append(node)
        if node.type in FUNCTION_NODE_TYPES:
            parts = normalize_parts_data(node.fields.get("parts_data", ""))
            if parts is None:
                issues.append(ValidationIssue(node_uuid=node.uuid, message="parts_data 不是合法的逗号分隔数字列表"))
            parsed_range = parse_range(node.fields.get("range", ""))
            if parts is not None and parsed_range is not None:
                low, high = parsed_range
                if any(part < low or part > high for part in parts):
                    issues.append(ValidationIssue(node_uuid=node.uuid, message="parts_data 超出了 range 定义范围"))
    for duplicates in parameter_map.values():
        if len(duplicates) > 1:
            for node in duplicates:
                issues.append(ValidationIssue(node_uuid=node.uuid, message="parameter 重复"))
    for duplicates in draw_map.values():
        if len(duplicates) <= 1:
            continue
        baseline = (
            duplicates[0].fields.get("action_trigger", ""),
            duplicates[0].fields.get("action_trigger_active", ""),
        )
        if any(
            (node.fields.get("action_trigger", ""), node.fields.get("action_trigger_active", "")) != baseline
            for node in duplicates[1:]
        ):
            for node in duplicates:
                issues.append(ValidationIssue(node_uuid=node.uuid, message="相同 draw_able_name 的 TouchIdle 节点动作内容不一致"))
    return issues


def search_document(document: DocumentModel, text: str) -> list[SearchHit]:
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
                        title=node_title(node),
                        field_name=key,
                        preview=haystack[:120],
                    )
                )
    return hits
