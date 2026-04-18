"""Pure document logic for the editor."""

from __future__ import annotations

import csv
import json
import re
import uuid
from dataclasses import asdict
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from .models import (
    CanvasViewState,
    ConnectionRecord,
    CsvPreviewRow,
    DocumentModel,
    EditorSettings,
    MetaRecord,
    NodeRecord,
    SearchHit,
    TrashEntry,
    ValidationIssue,
)
from .schema import EditorSchema, FieldSchema, NodeSchema, load_editor_schema

RANGE_PATTERN = re.compile(r"^\{\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\}$")
ACTION_NAME_PATTERN = re.compile(r"action\s*=\s*'([^']*)'")
TARGET_IDLE_PATTERN = re.compile(r"idle\s*=\s*(-?\d+)")
TRAILING_INT_PATTERN = re.compile(r"(-?\d+)\s*$")
PARTS_DATA_PATTERN = re.compile(r"^\{\s*parts\s*=\s*\{(?P<values>.*)\}\s*\}$", re.IGNORECASE)
REACT_CONDITION_PATTERN = re.compile(r"^\{\s*idle_on\s*=\s*\{(?P<values>.*)\}\s*\}$", re.IGNORECASE)
HIDDEN_NODE_FIELDS = {
    "target_idle",
    "action_trigger_kind_ui",
    "action_trigger_reserved_ui",
    "action_trigger_active_kind_ui",
    "action_trigger_active_reserved_ui",
}
EDITOR_DOCUMENT_SIGNATURE = "l2d_config_editor/v1"
RESERVED_FIELD_KEYS = (
    "draw_able_name",
    "parameter",
    "action_trigger",
    "action_trigger_active",
    "target_idle",
    "id",
)
CSV_TEMPLATE_FILES = ("(full)ship_l2d.csv", "ship_l2d.csv")
TEMPLATE_CSV_COLUMN_ALIASES = {
    "version": ("版本", "version"),
    "char_name": ("角色名", "charname", "char_name"),
    "memo": ("角色资源名", "资源名", "memo"),
    "ship_skin_id": ("角色id", "角色ID", "ship_skin_id", "shipskinid"),
}


NODE_THEME_FIELD_KEYS = ("theme_body_color", "theme_border_color", "theme_text_color")
COMMENT_LEGACY_APPEARANCE_KEYS = (
    "note_box_color",
    "note_box_alpha",
    "note_text_color",
    "note_text_alpha",
    "note_font_size",
)
DEFAULT_THEME_TEXT_COLOR = "#f7f8fa"
DRAWFRAME_DEFAULT_SIZE = {"width": 520.0, "height": 320.0}


@lru_cache(maxsize=2)
def get_default_schema(schema_path: str | None = None) -> EditorSchema:
    return load_editor_schema(schema_path)


def new_uuid() -> str:
    return uuid.uuid4().hex


def is_editor_document_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("editor_signature") == EDITOR_DOCUMENT_SIGNATURE:
        return True
    nodes = payload.get("nodes")
    meta = payload.get("meta")
    canvas_view = payload.get("canvas_view")
    connections = payload.get("connections")
    if not isinstance(nodes, list) or not isinstance(meta, dict) or not isinstance(canvas_view, dict) or not isinstance(connections, list):
        return False
    return any(isinstance(node, dict) and node.get("type") == "Initial" for node in nodes)


def is_editor_document_file(path: str | Path) -> bool:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return False
    return is_editor_document_payload(payload)


def default_fields(schema: EditorSchema, node_type: str) -> dict[str, Any]:
    definition = schema.nodes[node_type]
    return {field.key: field.default for field in definition.fields}


def _valid_color_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", text):
        return text.lower()
    return None


def default_node_theme(schema: EditorSchema, node: NodeRecord) -> dict[str, str]:
    definition = schema.nodes[node.type]
    if node.type == "Comment":
        body = _valid_color_or_none(node.fields.get("note_box_color")) or "#76808d"
        border = _valid_color_or_none(node.fields.get("note_box_color")) or "#69b070"
        text = _valid_color_or_none(node.fields.get("note_text_color")) or "#f3f5f8"
        return {
            "theme_body_color": body,
            "theme_border_color": border,
            "theme_text_color": text,
        }
    return {
        "theme_body_color": _valid_color_or_none(definition.body_color) or "#27384d",
        "theme_border_color": _valid_color_or_none(definition.accent_color) or "#78b1ff",
        "theme_text_color": DEFAULT_THEME_TEXT_COLOR,
    }


def apply_node_appearance_defaults(schema: EditorSchema, node: NodeRecord) -> None:
    defaults = default_node_theme(schema, node)
    for key, value in defaults.items():
        if not _valid_color_or_none(node.fields.get(key)):
            node.fields[key] = value
    if node.type == "Comment":
        if not _valid_color_or_none(node.fields.get("note_box_color")):
            node.fields["note_box_color"] = node.fields["theme_body_color"]
        if not _valid_color_or_none(node.fields.get("note_text_color")):
            node.fields["note_text_color"] = node.fields["theme_text_color"]
        try:
            node.fields["note_box_alpha"] = int(node.fields.get("note_box_alpha", 62))
        except (TypeError, ValueError):
            node.fields["note_box_alpha"] = 62
        try:
            node.fields["note_text_alpha"] = int(node.fields.get("note_text_alpha", 96))
        except (TypeError, ValueError):
            node.fields["note_text_alpha"] = 96
        try:
            node.fields["note_font_size"] = int(node.fields.get("note_font_size", 15))
        except (TypeError, ValueError):
            node.fields["note_font_size"] = 15


def sync_comment_legacy_appearance(node: NodeRecord) -> None:
    if node.type != "Comment":
        return
    body = _valid_color_or_none(node.fields.get("theme_body_color"))
    text = _valid_color_or_none(node.fields.get("theme_text_color"))
    border = _valid_color_or_none(node.fields.get("theme_border_color"))
    if body:
        node.fields["note_box_color"] = body
    elif border:
        node.fields["note_box_color"] = border
    if text:
        node.fields["note_text_color"] = text


def function_node_types(schema: EditorSchema) -> tuple[str, ...]:
    return tuple(type_name for type_name, node in schema.nodes.items() if node.category == "function")


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def parse_range(value: str) -> tuple[float, float] | None:
    match = RANGE_PATTERN.match(_text(value).strip())
    if not match:
        return None
    return float(match.group(1)), float(match.group(2))


def normalize_parts_data(value: str) -> list[float] | None:
    text = _text(value).strip()
    if not text:
        return []
    match = PARTS_DATA_PATTERN.match(text)
    if match:
        text = match.group("values").strip()
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


def _format_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def canonicalize_parts_data(value: Any) -> str:
    text = _text(value).strip()
    if not text:
        return ""
    parts = normalize_parts_data(text)
    if parts is None:
        return text
    return f"{{parts={{{','.join(_format_number(part) for part in parts)}}}}}"


def normalize_react_condition_list(value: Any) -> str:
    text = _text(value).strip()
    if not text:
        return ""
    match = REACT_CONDITION_PATTERN.match(text)
    if match:
        text = match.group("values").strip()
    if not text:
        return ""
    normalized: list[str] = []
    for chunk in text.split(","):
        item = chunk.strip()
        if not item:
            continue
        try:
            normalized.append(str(int(item)))
        except ValueError:
            return _text(value).strip()
    return ",".join(normalized)


def format_react_condition_for_csv(value: Any) -> str:
    raw = _text(value).strip()
    if not raw:
        return ""
    normalized = normalize_react_condition_list(raw)
    if not normalized:
        return ""
    if normalized == raw and raw.startswith("{"):
        return raw
    return f"{{idle_on={{{normalized}}}}}"


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _action_name_from_raw(value: Any) -> str:
    text = _text(value).strip()
    if not text:
        return ""
    match = ACTION_NAME_PATTERN.search(text)
    return match.group(1) if match else text


def _target_idle_from_raw(value: Any) -> int | None:
    text = _text(value).strip()
    if not text:
        return None
    match = TARGET_IDLE_PATTERN.search(text)
    if not match:
        return None
    return _coerce_int(match.group(1), 0)


def _suffix_int(value: Any) -> int | None:
    text = _text(value).strip()
    if not text:
        return None
    match = TRAILING_INT_PATTERN.search(text)
    if not match:
        return None
    return _coerce_int(match.group(1), 0)


def _classify_action_trigger(value: Any) -> str:
    text = _text(value).strip()
    if not text:
        return "empty"
    if "type" in text and "2" in text and "action" in text:
        return "type2_target" if "target" in text else "type2_action"
    return "reserved_raw"


def _classify_action_trigger_active(value: Any) -> str:
    text = _text(value).strip()
    if not text:
        return "empty"
    return "idle_gate" if "idle" in text else "reserved_raw"


def _refresh_trigger_interface_fields(node: NodeRecord) -> None:
    if node.type not in {"TouchIdle", "TouchDrag", "ParameterTrigger"}:
        return
    action_raw = _text(node.fields.get("action_trigger", "")).strip()
    active_raw = _text(node.fields.get("action_trigger_active", "")).strip()
    action_kind = _classify_action_trigger(action_raw)
    active_kind = _classify_action_trigger_active(active_raw)
    node.fields["action_trigger_kind_ui"] = action_kind
    node.fields["action_trigger_reserved_ui"] = action_raw if action_kind == "reserved_raw" else ""
    node.fields["action_trigger_active_kind_ui"] = active_kind
    node.fields["action_trigger_active_reserved_ui"] = active_raw if active_kind == "reserved_raw" else ""


def normalized_target_idle(node: NodeRecord) -> int:
    raw_target_idle = _target_idle_from_raw(node.fields.get("action_trigger_active"))
    if raw_target_idle is not None:
        return raw_target_idle
    if node.type in {"TouchDrag", "ParameterTrigger"}:
        action_target_idle = _suffix_int(_action_name_from_raw(node.fields.get("action_trigger")))
        if action_target_idle is not None:
            return action_target_idle
        parameter_target_idle = _suffix_int(node.fields.get("parameter"))
        if parameter_target_idle is not None:
            return parameter_target_idle
        if "target_idle" in node.fields:
            return _coerce_int(node.fields.get("target_idle"), 0)
        slot_target_idle = _coerce_int(node.type_slot or node.sequence_no, 0)
        if slot_target_idle > 0:
            return slot_target_idle
    return _coerce_int(node.fields.get("target_idle"), 0)


def _format_ignore_values(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


def _function_nodes(schema: EditorSchema, document: DocumentModel) -> list[NodeRecord]:
    node_types = set(function_node_types(schema))
    return [node for node in document.nodes if node.type in node_types]


def _next_available_slot(used_slots: set[int]) -> int:
    slot = 1
    while slot in used_slots:
        slot += 1
    return slot


def _occupied_type_slots(document: DocumentModel, node_type: str, *, exclude_uuid: str | None = None) -> set[int]:
    occupied = {
        int(node.type_slot)
        for node in document.nodes
        if node.uuid != exclude_uuid and node.type == node_type and isinstance(node.type_slot, int) and node.type_slot > 0
    }
    if document.editor_settings.trash_enabled:
        occupied.update(
            int(entry.type_slot)
            for entry in document.trash_bin
            if entry.node_type == node_type and isinstance(entry.type_slot, int) and entry.type_slot > 0
        )
    return occupied


def _occupied_export_slots(document: DocumentModel, *, exclude_uuid: str | None = None) -> set[int]:
    occupied = {
        int(node.export_slot)
        for node in document.nodes
        if node.uuid != exclude_uuid and isinstance(node.export_slot, int) and node.export_slot > 0
    }
    if document.editor_settings.trash_enabled:
        occupied.update(
            int(entry.export_slot)
            for entry in document.trash_bin
            if isinstance(entry.export_slot, int) and entry.export_slot > 0
        )
    return occupied


def allocate_type_slot(document: DocumentModel, node_type: str, *, exclude_uuid: str | None = None) -> int:
    return _next_available_slot(_occupied_type_slots(document, node_type, exclude_uuid=exclude_uuid))


def allocate_export_slot(document: DocumentModel, *, exclude_uuid: str | None = None) -> int:
    return _next_available_slot(_occupied_export_slots(document, exclude_uuid=exclude_uuid))


def backfill_slots(schema: EditorSchema, document: DocumentModel) -> None:
    function_types = set(function_node_types(schema))
    for node in document.nodes:
        if node.type not in function_types:
            continue
        if not isinstance(node.type_slot, int) or node.type_slot <= 0:
            node.type_slot = allocate_type_slot(document, node.type, exclude_uuid=node.uuid)
        if not isinstance(node.export_slot, int) or node.export_slot <= 0:
            node.export_slot = allocate_export_slot(document, exclude_uuid=node.uuid)


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


def _animated_action(
    schema: EditorSchema, node_schema: NodeSchema, target_idle: int, action_name: str | None = None
) -> tuple[str, str]:
    resolved_action_name = action_name if action_name is not None else _sequence_action_name(node_schema, target_idle)
    action = (
        schema.animated_action_template.replace("{target_idle}", str(target_idle))
        .replace("{action_name}", resolved_action_name)
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


def build_csv_export_filename(prefix: str = "ship_l2d_export") -> str:
    return f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"


def _normalized_template_header(value: Any) -> str:
    return str(value or "").strip().replace(" ", "").replace("_", "").lower()


def build_template_version_folder_name(version: Any) -> str:
    text = str(version or "").strip()
    if not text:
        return "unknown_version"
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.strftime("%Y%m%d")
        except ValueError:
            continue
    sanitized = re.sub(r'[<>:"/\\|?*]+', "_", text).strip(" ._")
    return sanitized or "unknown_version"


def load_template_csv_rows(path: str | Path) -> list[dict[str, Any]]:
    last_error: Exception | None = None
    rows: list[list[str]] | None = None
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            with Path(path).open("r", encoding=encoding, newline="") as handle:
                rows = list(csv.reader(handle))
            break
        except UnicodeDecodeError as exc:
            last_error = exc
    if rows is None:
        raise ValueError(f"无法读取 CSV：{last_error}") from last_error
    if not rows:
        return []
    header_map = {_normalized_template_header(value): index for index, value in enumerate(rows[0])}
    resolved_indexes: dict[str, int] = {}
    missing_columns: list[str] = []
    for key, aliases in TEMPLATE_CSV_COLUMN_ALIASES.items():
        index = next((header_map[alias] for alias in (_normalized_template_header(item) for item in aliases) if alias in header_map), None)
        if index is None:
            missing_columns.append(aliases[0])
            continue
        resolved_indexes[key] = index
    if missing_columns:
        raise ValueError(f"CSV 缺少必要列：{'、'.join(missing_columns)}")
    result: list[dict[str, Any]] = []
    for row in rows[1:]:
        if not any(str(cell or "").strip() for cell in row):
            continue
        version = str(row[resolved_indexes["version"]] if resolved_indexes["version"] < len(row) else "").strip()
        char_name = str(row[resolved_indexes["char_name"]] if resolved_indexes["char_name"] < len(row) else "").strip()
        memo = str(row[resolved_indexes["memo"]] if resolved_indexes["memo"] < len(row) else "").strip()
        ship_skin_id_text = str(row[resolved_indexes["ship_skin_id"]] if resolved_indexes["ship_skin_id"] < len(row) else "").strip()
        if not version and not char_name and not memo and not ship_skin_id_text:
            continue
        try:
            ship_skin_id = int(ship_skin_id_text or "0")
        except ValueError as exc:
            raise ValueError(f"角色 ID 不是有效整数：{ship_skin_id_text}") from exc
        result.append(
            {
                "version": version,
                "CharName": char_name,
                "memo": memo,
                "ship_skin_id": ship_skin_id,
            }
        )
    return result


def create_template_document(
    schema: EditorSchema,
    *,
    version: str,
    char_name: str,
    memo: str,
    ship_skin_id: int,
) -> DocumentModel:
    document = create_document(schema)
    initial = next(node for node in document.nodes if node.type == "Initial")
    initial.fields["version"] = str(version or "").strip()
    initial.fields["author"] = ""
    initial.fields["ship_skin_id"] = int(ship_skin_id or 0)
    initial.fields["memo"] = str(memo or "").strip()
    initial.fields["react_condition"] = ""
    initial.fields["tips"] = ""
    initial.fields["CharName"] = str(char_name or "").strip()
    sync_meta_from_initial(document)
    reassign_function_ids(schema, document)
    recompute_document_state(schema, document)
    return document


def numeric_linkage_enabled(document: DocumentModel) -> bool:
    return bool(document.editor_settings.numeric_linkage_enabled)


def node_numeric_linkage_enabled(node: NodeRecord) -> bool:
    return bool(node.numeric_linkage_enabled)


def _is_touchdrag_value_like(node: NodeRecord) -> bool:
    return node.type == "ParameterTrigger" or (node.type == "TouchDrag" and node.fields.get("result_type") == "value")


def display_value_for_field(schema: EditorSchema, node: NodeRecord, key: str, raw_value: Any | None = None) -> Any:
    del schema
    value = node.fields.get(key) if raw_value is None else raw_value
    if key == "action_trigger":
        if not _text(value).strip():
            return ""
        return _action_name_from_raw(value)
    if key == "action_trigger_active":
        if not _text(value).strip():
            return ""
        return normalized_target_idle(node)
    if key == "parts_data":
        return canonicalize_parts_data(value)
    if key == "react_condition":
        return normalize_react_condition_list(value)
    return value


def normalize_field_input(schema: EditorSchema, node: NodeRecord, key: str, display_value: Any) -> Any:
    if key in NODE_THEME_FIELD_KEYS:
        return _valid_color_or_none(display_value) or default_node_theme(schema, node)[key]
    if key == "action_trigger":
        if not _text(display_value).strip():
            return ""
        target_idle = normalized_target_idle(node)
        action_name = _text(display_value).strip()
        if node.type == "TouchIdle" and node.fields.get("transition_type") == "hard":
            return _hard_cut_action(schema, target_idle)[0]
        return _animated_action(schema, _node_schema(schema, node.type), target_idle, action_name=action_name)[0]
    if key == "action_trigger_active":
        if _text(display_value).strip() == "":
            return ""
        target_idle = _coerce_int(display_value, 0)
        if node.type == "TouchIdle" and node.fields.get("transition_type") == "hard":
            return _hard_cut_action(schema, target_idle)[1]
        return _animated_action(schema, _node_schema(schema, node.type), target_idle)[1]
    if key == "parts_data":
        return canonicalize_parts_data(display_value)
    if key == "react_condition":
        return normalize_react_condition_list(display_value)
    return display_value


def _template_without_suffix(template: str, **values: Any) -> str:
    result = _text(template)
    replacements = {"sequence": "", "target_idle": "", **values}
    for key, value in replacements.items():
        result = result.replace(f"{{{key}}}", str(value))
    return result.strip()


def _manual_sequence_defaults(schema: EditorSchema, node: NodeRecord) -> None:
    if node.type not in function_node_types(schema):
        return
    node_schema = _node_schema(schema, node.type)
    target_idle = 0
    action_name = _template_without_suffix(node_schema.auto_rules.action_name_template or "touch_idle{target_idle}")
    node.fields["draw_able_name"] = _template_without_suffix(node_schema.auto_rules.draw_template)
    node.fields["target_idle"] = target_idle
    node.fields["parameter"] = _template_without_suffix(node_schema.auto_rules.parameter_template)
    node.fields["action_trigger"] = _animated_action(schema, node_schema, target_idle, action_name=action_name)[0]
    node.fields["action_trigger_active"] = _animated_action(schema, node_schema, target_idle)[1]
    node.manual_fields.update({"parameter", "action_trigger"})
    _refresh_trigger_interface_fields(node)


def _infer_expected_actions(schema: EditorSchema, node: NodeRecord) -> tuple[str, str]:
    target_idle = _coerce_int(node.fields.get("target_idle"), normalized_target_idle(node))
    node_schema = _node_schema(schema, node.type)
    if node.type == "TouchIdle" and node.fields.get("transition_type") == "hard":
        return _hard_cut_action(schema, target_idle)
    return _animated_action(schema, node_schema, target_idle)


def _linked_draw_name(node_schema: NodeSchema, target_idle: int) -> str:
    return node_schema.auto_rules.draw_template.format(sequence=target_idle, target_idle=target_idle)


def _linked_target_idle_for_field(node: NodeRecord, changed_key: str | None) -> int | None:
    if changed_key == "target_idle":
        return _coerce_int(node.fields.get("target_idle"), normalized_target_idle(node))
    if changed_key == "action_trigger_active":
        return normalized_target_idle(node)
    if changed_key == "draw_able_name":
        return _suffix_int(node.fields.get("draw_able_name"))
    if changed_key == "parameter":
        return _suffix_int(node.fields.get("parameter"))
    if changed_key == "action_trigger":
        return _suffix_int(_action_name_from_raw(node.fields.get("action_trigger")))
    return None


def _apply_simple_linked_field_updates(
    schema: EditorSchema,
    node: NodeRecord,
    *,
    target_idle: int,
    preserve_key: str | None,
) -> None:
    node_schema = _node_schema(schema, node.type)
    if preserve_key != "draw_able_name":
        node.fields["draw_able_name"] = _linked_draw_name(node_schema, target_idle)
    if preserve_key == "parameter":
        node.manual_fields.add("parameter")
    else:
        node.fields["parameter"] = _expected_parameter(node_schema, target_idle)
        node.manual_fields.discard("parameter")
    if _is_touchdrag_value_like(node):
        node.fields["target_idle"] = 0
        node.fields["action_trigger"] = ""
        node.fields["action_trigger_active"] = ""
        node.manual_fields.discard("action_trigger")
        _refresh_trigger_interface_fields(node)
        return
    node.fields["target_idle"] = target_idle
    expected_action, expected_active = _infer_expected_actions(schema, node)
    if preserve_key == "action_trigger":
        node.manual_fields.add("action_trigger")
    else:
        node.fields["action_trigger"] = expected_action
        node.manual_fields.discard("action_trigger")
    node.fields["action_trigger_active"] = expected_active
    _refresh_trigger_interface_fields(node)


def _unlinked_sequence_defaults(node: NodeRecord) -> None:
    node.fields["draw_able_name"] = ""
    node.fields["target_idle"] = 0
    node.fields["parameter"] = ""
    node.fields["action_trigger"] = ""
    node.fields["action_trigger_active"] = ""
    _refresh_trigger_interface_fields(node)


def infer_manual_fields(schema: EditorSchema, node: NodeRecord, document: DocumentModel | None = None) -> None:
    del document
    if node.type not in function_node_types(schema):
        return
    node_schema = _node_schema(schema, node.type)
    target_idle = normalized_target_idle(node)
    node.fields["target_idle"] = target_idle
    expected_parameter = _expected_parameter(node_schema, target_idle)
    if str(node.fields.get("parameter", "")) != expected_parameter:
        node.manual_fields.add("parameter")
    else:
        node.manual_fields.discard("parameter")
    if _is_touchdrag_value_like(node):
        node.manual_fields.discard("action_trigger")
        if node_numeric_linkage_enabled(node):
            node.fields["action_trigger"] = ""
            node.fields["action_trigger_active"] = ""
            node.fields["target_idle"] = 0
        _refresh_trigger_interface_fields(node)
        return
    expected_action, expected_active = _infer_expected_actions(schema, node)
    if str(node.fields.get("action_trigger", "")) != expected_action:
        node.manual_fields.add("action_trigger")
    else:
        node.manual_fields.discard("action_trigger")
    if node_numeric_linkage_enabled(node):
        node.fields["action_trigger_active"] = expected_active
    _refresh_trigger_interface_fields(node)


def apply_sequence_defaults(schema: EditorSchema, document: DocumentModel, node: NodeRecord) -> None:
    if node.type not in function_node_types(schema):
        return
    node_schema = _node_schema(schema, node.type)
    sequence = node.type_slot or node.sequence_no or 1
    target_idle = sequence if node_schema.auto_rules.use_sequence_for_target_idle else _coerce_int(node.fields.get("target_idle"), sequence)
    node.fields["draw_able_name"] = node_schema.auto_rules.draw_template.format(sequence=sequence, target_idle=target_idle)
    node.fields["target_idle"] = target_idle
    node.fields["parameter"] = _expected_parameter(node_schema, target_idle)
    action, active = _infer_expected_actions(schema, node)
    node.fields["action_trigger"] = action
    node.fields["action_trigger_active"] = active
    node.manual_fields.difference_update({"parameter", "action_trigger"})
    _refresh_trigger_interface_fields(node)


def _preserve_function_fields_from_base(schema: EditorSchema, document: DocumentModel, node: NodeRecord, base_node: NodeRecord) -> None:
    node.manual_fields = set(base_node.manual_fields)
    infer_manual_fields(schema, node, document)
    apply_auto_rules(schema, document, node, source_mode="advanced", force_generated=False)


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
    node.fields["range_abs"] = 0 if parsed[0] < 0 else 1


def apply_auto_rules(
    schema: EditorSchema,
    document: DocumentModel,
    node: NodeRecord,
    *,
    source_mode: str = "simple",
    changed_key: str | None = None,
    force_generated: bool = False,
) -> None:
    if node.type == "Initial":
        return
    if "parts_data" in node.fields:
        node.fields["parts_data"] = canonicalize_parts_data(node.fields.get("parts_data", ""))
    _update_drag_offsets(node, changed_key, source_mode)
    _update_range_abs(node)
    if node.type not in function_node_types(schema):
        return
    if source_mode == "simple" and node_numeric_linkage_enabled(node):
        linked_target_idle = _linked_target_idle_for_field(node, changed_key)
        if linked_target_idle is not None:
            _apply_simple_linked_field_updates(
                schema,
                node,
                target_idle=linked_target_idle,
                preserve_key=changed_key,
            )
            return
    target_idle = _coerce_int(node.fields.get("target_idle"), normalized_target_idle(node))
    node.fields["target_idle"] = target_idle
    if _is_touchdrag_value_like(node):
        if force_generated or source_mode == "simple":
            node.fields["action_trigger"] = ""
            node.fields["action_trigger_active"] = ""
        node.fields["target_idle"] = 0
        node.manual_fields.discard("action_trigger")
        _refresh_trigger_interface_fields(node)
        return
    if not node_numeric_linkage_enabled(node):
        _refresh_trigger_interface_fields(node)
        return
    node_schema = _node_schema(schema, node.type)
    if force_generated or "parameter" not in node.manual_fields:
        node.fields["parameter"] = _expected_parameter(node_schema, target_idle)
    expected_action, expected_active = _infer_expected_actions(schema, node)
    if force_generated or "action_trigger" not in node.manual_fields:
        node.fields["action_trigger"] = expected_action
    node.fields["action_trigger_active"] = expected_active
    _refresh_trigger_interface_fields(node)


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
        ui_size=(
            dict(base_node.ui_size)
            if base_node and base_node.ui_size
            else (
                {"width": 360.0, "height": 180.0}
                if node_type == "Comment"
                else (dict(DRAWFRAME_DEFAULT_SIZE) if node_type == "DrawFrame" else None)
            )
        ),
        sequence_no=_next_sequence_no(document, node_type),
        type_slot=base_node.type_slot if base_node and node_type not in function_node_types(schema) else None,
        export_slot=base_node.export_slot if base_node and node_type not in function_node_types(schema) else None,
        numeric_linkage_enabled=(
            base_node.numeric_linkage_enabled
            if base_node is not None
            else bool(document.editor_settings.numeric_linkage_enabled and node_type in function_node_types(schema))
        ),
        manual_fields=set(),
    )
    if node.type in function_node_types(schema):
        node.type_slot = allocate_type_slot(document, node.type)
        node.export_slot = allocate_export_slot(document)
        if base_node is not None and base_node.manual_fields:
            _preserve_function_fields_from_base(schema, document, node, base_node)
        elif base_node is None and document.interaction_creation_mode == "manual":
            _manual_sequence_defaults(schema, node)
        else:
            apply_sequence_defaults(schema, document, node)
    else:
        apply_auto_rules(schema, document, node, force_generated=False)
    if node.type == "ParameterTrigger":
        node.fields["result_type"] = "value"
        node.fields["action_trigger"] = ""
        node.fields["action_trigger_active"] = ""
        node.fields["target_idle"] = 0
        node.manual_fields.discard("action_trigger")
        _refresh_trigger_interface_fields(node)
    apply_node_appearance_defaults(schema, node)
    sync_comment_legacy_appearance(node)
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
        react_condition=normalize_react_condition_list(initial.fields.get("react_condition", "")),
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
            "react_condition": document.meta.react_condition,
            "tips": document.meta.tips,
            "CharName": document.meta.CharName,
        }
    )
    initial.fields.pop("defaultState", None)


def recompute_document_state(schema: EditorSchema, document: DocumentModel) -> None:
    sync_meta_from_initial(document)
    missing: list[str] = []
    for field in schema.required_meta_fields:
        value = getattr(document.meta, field, None)
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
    document = DocumentModel(global_mode="simple")
    document.nodes.append(create_node(active_schema, document, "Initial", (72.0, 72.0)))
    sync_meta_from_initial(document)
    reassign_function_ids(active_schema, document)
    recompute_document_state(active_schema, document)
    return document


def reassign_function_ids(schema: EditorSchema, document: DocumentModel) -> None:
    sync_meta_from_initial(document)
    base = document.meta.ship_skin_id or 0
    backfill_slots(schema, document)
    for node in document.nodes:
        if node.type not in function_node_types(schema):
            continue
        export_slot = node.export_slot or allocate_export_slot(document, exclude_uuid=node.uuid)
        node.export_slot = export_slot
        node.fields["id"] = int(f"{base}{export_slot:02d}") if base else export_slot
        apply_auto_rules(schema, document, node, source_mode="advanced", force_generated=False)
    recompute_document_state(schema, document)


def node_title(schema: EditorSchema, node: NodeRecord) -> str:
    definition = _node_schema(schema, node.type)
    if node.type in function_node_types(schema):
        slot = node.type_slot or node.sequence_no or 1
        draw_name = _text(node.fields.get("draw_able_name", "")).strip()
        prefix = draw_name or f"{definition.title}{slot}"
        tips = _text(node.fields.get("tips", "")).strip()
        return f"{prefix}-{tips}" if tips else prefix
    if node.type == "Comment":
        content = _text(node.fields.get("content", "")).strip().splitlines()
        first_line = content[0][:24] if content else ""
        return f"{definition.title}-{first_line}" if first_line else definition.title
    if node.type == "DrawFrame":
        title = _text(node.fields.get("title", "")).strip()
        return f"{definition.title}-{title}" if title else definition.title
    tips = _text(node.fields.get("tips", "")).strip()
    return f"{definition.title}-{tips}" if tips else definition.title


def make_trash_entry(schema: EditorSchema, node: NodeRecord) -> TrashEntry:
    reserved_fields = {
        key: value
        for key, value in node.fields.items()
        if key in RESERVED_FIELD_KEYS and value not in (None, "")
    }
    return TrashEntry(
        entry_id=new_uuid(),
        node_uuid=node.uuid,
        node_type=node.type,
        title=node_title(schema, node),
        type_slot=node.type_slot,
        export_slot=node.export_slot,
        reserved_fields=reserved_fields,
    )


def _export_node_fields(node: NodeRecord) -> dict[str, Any]:
    hidden_fields = set(HIDDEN_NODE_FIELDS)
    if node.type in {"TouchDrag", "ParameterTrigger"}:
        hidden_fields.add("action_trigger_active")
    return {key: value for key, value in node.fields.items() if key not in hidden_fields}


def _derived_target_idle_from_fields(node: NodeRecord) -> int | None:
    raw_target_idle = _target_idle_from_raw(node.fields.get("action_trigger_active"))
    if raw_target_idle is not None and node.type not in {"TouchDrag", "ParameterTrigger"}:
        return raw_target_idle
    if node.type not in {"TouchDrag", "ParameterTrigger"}:
        return None
    action_target_idle = _suffix_int(_action_name_from_raw(node.fields.get("action_trigger")))
    if action_target_idle is not None:
        return action_target_idle
    parameter_target_idle = _suffix_int(node.fields.get("parameter"))
    if parameter_target_idle is not None:
        return parameter_target_idle
    return None


def _should_persist_target_idle(node: NodeRecord) -> bool:
    if "target_idle" not in node.fields:
        return False
    explicit_target_idle = _coerce_int(node.fields.get("target_idle"), 0)
    derived_target_idle = _derived_target_idle_from_fields(node)
    if derived_target_idle is None:
        return node.type in {"TouchDrag", "ParameterTrigger"}
    return explicit_target_idle != derived_target_idle


def export_document_dict(schema: EditorSchema, document: DocumentModel) -> dict[str, Any]:
    sync_meta_from_initial(document)
    reassign_function_ids(schema, document)
    function_types = set(function_node_types(schema))
    serialized_nodes = []
    for node in document.nodes:
        payload: dict[str, Any] = {
            "uuid": node.uuid,
            "type": node.type,
            "ui_position": node.ui_position,
        }
        if node.type_slot is not None:
            payload["type_slot"] = node.type_slot
        if node.export_slot is not None:
            payload["export_slot"] = node.export_slot
        payload["locked"] = node.locked
        if node.ui_size:
            payload["ui_size"] = node.ui_size
        if node.type in function_types:
            payload["numeric_linkage_enabled"] = bool(node.numeric_linkage_enabled)
        payload.update(_export_node_fields(node))
        if _should_persist_target_idle(node):
            payload["target_idle"] = _coerce_int(node.fields.get("target_idle"), 0)
        serialized_nodes.append(payload)
    return {
        "editor_signature": EDITOR_DOCUMENT_SIGNATURE,
        "global_mode": document.global_mode,
        "interaction_creation_mode": document.interaction_creation_mode,
        "editor_settings": asdict(document.editor_settings),
        "meta": asdict(document.meta),
        "nodes": serialized_nodes,
        "connections": [asdict(connection) for connection in document.connections],
        "trash_bin": [asdict(entry) for entry in document.trash_bin],
        "canvas_view": asdict(document.canvas_view),
    }


def save_document(schema: EditorSchema, document: DocumentModel, path: str | Path) -> None:
    data = export_document_dict(schema, document)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    document.path = str(path)


def load_document(schema: EditorSchema, path: str | Path) -> DocumentModel:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not is_editor_document_payload(payload):
        raise ValueError("不是 L2D Config Editor 配置文件")
    meta_payload = payload.get("meta", {})
    if not isinstance(meta_payload, dict):
        meta_payload = {}
    settings_payload = payload.get("editor_settings", {})
    if not isinstance(settings_payload, dict):
        settings_payload = {}
    meta_keys = set(MetaRecord.__dataclass_fields__.keys())
    document = DocumentModel(
        global_mode=str(payload.get("global_mode", "simple")),
        interaction_creation_mode=str(payload.get("interaction_creation_mode", "auto") or "auto"),
        editor_settings=EditorSettings(
            numeric_linkage_enabled=bool(settings_payload.get("numeric_linkage_enabled", False)),
            trash_enabled=bool(settings_payload.get("trash_enabled", False)),
        ),
        meta=MetaRecord(**{key: value for key, value in meta_payload.items() if key in meta_keys}),
        connections=[ConnectionRecord(**item) for item in payload.get("connections", [])],
        trash_bin=[TrashEntry(**item) for item in payload.get("trash_bin", [])],
        canvas_view=CanvasViewState(**payload.get("canvas_view", {})),
        path=str(path),
    )
    function_types = set(function_node_types(schema))
    sequence_map: dict[str, int] = {}
    for raw in payload.get("nodes", []):
        fields = {
            key: value
            for key, value in raw.items()
            if key not in {"uuid", "type", "ui_position", "ui_size", "mode_variant", "locked", "numeric_linkage_enabled"}
        }
        node_type = raw["type"]
        if node_type == "Initial":
            fields.pop("defaultState", None)
        if "target_idle" not in fields:
            raw_target_idle = _target_idle_from_raw(fields.get("action_trigger_active"))
            if raw_target_idle is not None:
                fields["target_idle"] = raw_target_idle
        sequence_map[node_type] = sequence_map.get(node_type, 0) + 1
        node = NodeRecord(
            uuid=raw.get("uuid") or new_uuid(),
            type=node_type,
            fields=fields,
            ui_position=raw.get("ui_position", {"x": 0.0, "y": 0.0}),
            ui_size=raw.get("ui_size"),
            sequence_no=sequence_map[node_type],
            type_slot=raw.get("type_slot"),
            export_slot=raw.get("export_slot"),
            locked=bool(raw.get("locked", False)),
            numeric_linkage_enabled=bool(
                raw.get("numeric_linkage_enabled", settings_payload.get("numeric_linkage_enabled", False))
                if node_type in function_types
                else False
            ),
        )
        apply_node_appearance_defaults(schema, node)
        sync_comment_legacy_appearance(node)
        infer_manual_fields(schema, node, document)
        apply_auto_rules(schema, document, node, source_mode="advanced", force_generated=False)
        document.nodes.append(node)
    sync_initial_from_meta(schema, document)
    backfill_slots(schema, document)
    reassign_function_ids(schema, document)
    recompute_document_state(schema, document)
    return document


def _csv_value_for_mapping(mapping, document: DocumentModel, node: NodeRecord) -> Any:
    if mapping.column == "parts_data":
        return canonicalize_parts_data(node.fields.get(mapping.field or "", mapping.default))
    if mapping.column == "react_condition":
        return format_react_condition_for_csv(getattr(document.meta, mapping.field or "", mapping.default))
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


def csv_template_header_rows(schema: EditorSchema, search_roots: list[str | Path] | tuple[str | Path, ...]) -> list[list[str]]:
    for root in search_roots:
        base_path = Path(root)
        for filename in CSV_TEMPLATE_FILES:
            template_path = base_path / filename
            if not template_path.is_file():
                continue
            try:
                with template_path.open("r", encoding="utf-8-sig", newline="") as handle:
                    rows = list(csv.reader(handle))
            except OSError:
                continue
            if rows and rows[0] == list(schema.csv_columns):
                return rows[:4] if len(rows) >= 4 else rows[:1]
    return [list(schema.csv_columns)]


def export_documents_to_csv(
    schema: EditorSchema,
    documents: list[DocumentModel],
    output_path: str | Path,
    *,
    template_search_roots: list[str | Path] | tuple[str | Path, ...] = (),
) -> Path:
    header_rows = csv_template_header_rows(schema, template_search_roots)
    with Path(output_path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter=",", lineterminator="\n")
        for row in header_rows:
            writer.writerow(row)
        for document in documents:
            for preview_row in document_to_csv_rows(schema, document):
                writer.writerow([preview_row.values.get(column, "") for column in schema.csv_columns])
    return Path(output_path)


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


def _display_field_value(schema: EditorSchema, node: NodeRecord, key: str) -> str:
    value = display_value_for_field(schema, node, key, node.fields.get(key))
    return str(value).strip()


def _duplicate_field_issues(schema: EditorSchema, group: list[NodeRecord], field_key: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for node in group:
        related = [other for other in group if other.uuid != node.uuid]
        value = _display_field_value(schema, node, field_key)
        label = _field_label(schema, node, field_key)
        message = f"{label} 重复：{value}" if value else f"{label} 重复"
        issues.append(
            ValidationIssue(
                node_uuid=node.uuid,
                message=message,
                field_keys=[field_key],
                related_node_uuids=[item.uuid for item in related],
                related_titles=[node_title(schema, item) for item in related],
            )
        )
    return issues


def _draw_conflict_issues(schema: EditorSchema, group: list[NodeRecord]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for node in group:
        related = [other for other in group if other.uuid != node.uuid]
        if node.type == "TouchDrag":
            message = (
                "同名框体的触发配置不一致："
                f"框={_display_field_value(schema, node, 'draw_able_name') or '空'}，"
                f"播放动画={_display_field_value(schema, node, 'action_trigger') or '空'}"
            )
            field_keys = ["draw_able_name", "action_trigger"]
        else:
            message = (
                "同名框体的触发配置不一致："
                f"框={_display_field_value(schema, node, 'draw_able_name') or '空'}，"
                f"播放动画={_display_field_value(schema, node, 'action_trigger') or '空'}，"
                f"目标idle={_display_field_value(schema, node, 'action_trigger_active') or '空'}"
            )
            field_keys = ["draw_able_name", "action_trigger", "action_trigger_active"]
        issues.append(
            ValidationIssue(
                node_uuid=node.uuid,
                message=message,
                field_keys=field_keys,
                related_node_uuids=[item.uuid for item in related],
                related_titles=[node_title(schema, item) for item in related],
            )
        )
    return issues




def validate_document(schema: EditorSchema, document: DocumentModel) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    initial_nodes = [node for node in document.nodes if node.type == "Initial"]
    if len(initial_nodes) > 1:
        issues.extend(_issue_for_group(schema, "Duplicate Initial nodes", [], initial_nodes))

    function_types = set(function_node_types(schema))
    function_nodes = [node for node in document.nodes if node.type in function_types]
    duplicate_parameters: dict[str, list[NodeRecord]] = {}
    for node in function_nodes:
        parameter_value = _display_field_value(schema, node, "parameter")
        if parameter_value:
            duplicate_parameters.setdefault(parameter_value, []).append(node)
    for group in duplicate_parameters.values():
        if len(group) > 1:
            issues.extend(_duplicate_field_issues(schema, group, "parameter"))

    for node in function_nodes:
        parts = normalize_parts_data(node.fields.get("parts_data", ""))
        if parts is None:
            issues.append(
                ValidationIssue(
                    node_uuid=node.uuid,
                    message=f"{_field_label(schema, node, 'parts_data')} has invalid format; use comma-separated numbers",
                    field_keys=["parts_data"],
                )
            )
        parsed_range = parse_range(node.fields.get("range", ""))
        if parts is not None and parsed_range is not None and any(part < parsed_range[0] or part > parsed_range[1] for part in parts):
            issues.append(
                ValidationIssue(
                    node_uuid=node.uuid,
                    message=f"{_field_label(schema, node, 'parts_data')} exceeds {_field_label(schema, node, 'range')}",
                    field_keys=["parts_data", "range"],
                )
            )
    return issues



def _field_label(schema: EditorSchema, node: NodeRecord, key: str, *, use_json_field_names: bool = False) -> str:
    if use_json_field_names:
        return key
    definition = schema.nodes[node.type]
    field = next((item for item in definition.fields if item.key == key), None)
    return field.label if field else key


def search_document(
    schema: EditorSchema,
    document: DocumentModel,
    text: str,
    *,
    use_json_field_names: bool = False,
) -> list[SearchHit]:
    needle = text.strip().lower()
    if not needle:
        return []
    hits: list[SearchHit] = []
    for node in document.nodes:
        for key, value in node.fields.items():
            if key in HIDDEN_NODE_FIELDS or (node.type in {"TouchDrag", "ParameterTrigger"} and key == "action_trigger_active"):
                continue
            display_value = display_value_for_field(schema, node, key, value)
            haystacks = [str(display_value)]
            if key in {"action_trigger", "action_trigger_active"}:
                haystacks.append(str(value))
            if not any(needle in haystack.lower() for haystack in haystacks):
                continue
            hits.append(
                SearchHit(
                    node_uuid=node.uuid,
                    node_type=node.type,
                    title=node_title(schema, node),
                    field_name=key,
                    field_label=_field_label(schema, node, key, use_json_field_names=use_json_field_names),
                    preview=str(display_value)[:120],
                )
            )
    return hits
