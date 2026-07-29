"""Pure document logic for the editor."""

from __future__ import annotations

import csv
import json
import math
import os
import re
import tempfile
import uuid
from dataclasses import asdict
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from .models import (
    CanvasImageRecord,
    CanvasStrokeRecord,
    CanvasViewState,
    ConnectionRecord,
    CsvPreviewRow,
    DocumentModel,
    EditorSettings,
    GroupRecord,
    MetaRecord,
    NodeRecord,
    SearchHit,
    ValidationIssue,
)
from .plan import load_plan_layout, normalize_plan_layout, serialize_plan_layout
from .schema import EditorSchema, FieldSchema, NodeSchema, load_editor_schema
from .reference_images import (
    MAX_DOCUMENT_REFERENCE_IMAGE_BYTES,
    MAX_DOCUMENT_REFERENCE_IMAGE_PIXELS,
    MAX_REFERENCE_IMAGE_COUNT,
    canonicalize_reference_image,
    validated_reference_image_display_size,
)

RANGE_PATTERN = re.compile(r"^\{\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\}$")
ACTION_NAME_PATTERN = re.compile(r"action\s*=\s*'([^']*)'")
TARGET_IDLE_PATTERN = re.compile(r"idle\s*=\s*(-?\d+)")
IGNORE_LIST_PATTERN = re.compile(r"ignore\s*=\s*\{\s*(?:\{\s*)?(?P<values>.*?)(?:\s*\})?\s*\}", re.IGNORECASE)
TRAILING_INT_PATTERN = re.compile(r"(-?\d+)\s*$")
PARTS_DATA_PATTERN = re.compile(r"^\{\s*parts\s*=\s*\{(?P<values>.*)\}\s*\}$", re.IGNORECASE)
REACT_CONDITION_PATTERN = re.compile(r"^\{\s*idle_on\s*=\s*\{(?P<values>.*)\}\s*\}$", re.IGNORECASE)
HIDDEN_NODE_FIELDS = {
    "target_idle",
    "action_trigger_kind_ui",
    "action_trigger_reserved_ui",
    "action_trigger_active_kind_ui",
    "action_trigger_active_reserved_ui",
    "_table_id",
    "_table_title",
    "_table_order",
    "_table_body_color",
    "_table_border_color",
    "_table_text_color",
}
EDITOR_DOCUMENT_SIGNATURE = "l2d_config_editor/v1"
EDITOR_DOCUMENT_FORMAT_VERSION = 4
CANVAS_STROKE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
CANVAS_STROKE_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")
MAX_CANVAS_STROKES = 10_000
MAX_CANVAS_STROKE_POINTS = 50_000
MAX_DOCUMENT_CANVAS_STROKE_POINTS = 250_000
MAX_CANVAS_COORDINATE = 1_000_000.0
MIN_CANVAS_STROKE_WIDTH = 0.25
MAX_CANVAS_STROKE_WIDTH = 64.0
CSV_TEMPLATE_FILES = ("(full)ship_l2d.csv", "ship_l2d.csv")


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
DEFAULT_GROUP_TITLE = "新建分组"
TABLE_ID_FIELD = "_table_id"
TABLE_TITLE_FIELD = "_table_title"
TABLE_ORDER_FIELD = "_table_order"
TABLE_BODY_COLOR_FIELD = "_table_body_color"
TABLE_BORDER_COLOR_FIELD = "_table_border_color"
TABLE_TEXT_COLOR_FIELD = "_table_text_color"
DEFAULT_PARAMETER_TABLE_TITLE = "参数表"
DEFAULT_PARAMETER_TABLE_COLORS = {
    TABLE_BODY_COLOR_FIELD: "#071b2d",
    TABLE_BORDER_COLOR_FIELD: "#25b7ff",
    TABLE_TEXT_COLOR_FIELD: "#e8f8ff",
}
LEGACY_PARAMETER_TABLE_COLORS = {
    TABLE_BODY_COLOR_FIELD: "#4b2c11",
    TABLE_BORDER_COLOR_FIELD: "#ffc27a",
    TABLE_TEXT_COLOR_FIELD: "#fff8ef",
}


@lru_cache(maxsize=2)
def get_default_schema(schema_path: str | None = None) -> EditorSchema:
    return load_editor_schema(schema_path)


def new_uuid() -> str:
    return uuid.uuid4().hex


def ensure_parameter_table_metadata(node: NodeRecord) -> None:
    if node.type != "ParameterTrigger":
        return
    if not str(node.fields.get(TABLE_ID_FIELD) or "").strip():
        node.fields[TABLE_ID_FIELD] = new_uuid()
    if TABLE_TITLE_FIELD not in node.fields:
        node.fields[TABLE_TITLE_FIELD] = DEFAULT_PARAMETER_TABLE_TITLE
    for key, value in DEFAULT_PARAMETER_TABLE_COLORS.items():
        current = str(node.fields.get(key) or "").strip().lower()
        legacy = LEGACY_PARAMETER_TABLE_COLORS[key].lower()
        if not _valid_color_or_none(node.fields.get(key)) or current == legacy:
            node.fields[key] = value
    try:
        node.fields[TABLE_ORDER_FIELD] = int(node.fields.get(TABLE_ORDER_FIELD, 0))
    except (OverflowError, TypeError, ValueError):
        node.fields[TABLE_ORDER_FIELD] = 0


def _coerce_position_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _restore_missing_parameter_table_groups(document: DocumentModel) -> None:
    missing_rows = [
        node
        for node in document.nodes
        if node.type == "ParameterTrigger" and not str(node.fields.get(TABLE_ID_FIELD) or "").strip()
    ]
    if not missing_rows:
        return

    table_id = new_uuid()
    rows = sorted(
        missing_rows,
        key=lambda node: (
            _coerce_position_value(node.ui_position.get("y")),
            _coerce_position_value(node.ui_position.get("x")),
            node.uuid,
        ),
    )
    for index, row in enumerate(rows):
        row.fields[TABLE_ID_FIELD] = table_id
        row.fields[TABLE_ORDER_FIELD] = index


def parameter_table_id(node: NodeRecord) -> str:
    if node.type != "ParameterTrigger":
        return ""
    ensure_parameter_table_metadata(node)
    return str(node.fields.get(TABLE_ID_FIELD) or "")


def parameter_table_title(node: NodeRecord) -> str:
    if node.type != "ParameterTrigger":
        return ""
    ensure_parameter_table_metadata(node)
    return str(node.fields.get(TABLE_TITLE_FIELD) or DEFAULT_PARAMETER_TABLE_TITLE).strip() or DEFAULT_PARAMETER_TABLE_TITLE


def parameter_table_order(node: NodeRecord) -> int:
    if node.type != "ParameterTrigger":
        return 0
    ensure_parameter_table_metadata(node)
    try:
        return int(node.fields.get(TABLE_ORDER_FIELD, 0))
    except (TypeError, ValueError):
        return 0


def set_parameter_table_order(node: NodeRecord, order: int) -> None:
    if node.type != "ParameterTrigger":
        return
    ensure_parameter_table_metadata(node)
    node.fields[TABLE_ORDER_FIELD] = int(order)


def parameter_table_colors(node: NodeRecord) -> dict[str, str]:
    ensure_parameter_table_metadata(node)
    return {
        TABLE_BODY_COLOR_FIELD: str(node.fields.get(TABLE_BODY_COLOR_FIELD) or DEFAULT_PARAMETER_TABLE_COLORS[TABLE_BODY_COLOR_FIELD]),
        TABLE_BORDER_COLOR_FIELD: str(node.fields.get(TABLE_BORDER_COLOR_FIELD) or DEFAULT_PARAMETER_TABLE_COLORS[TABLE_BORDER_COLOR_FIELD]),
        TABLE_TEXT_COLOR_FIELD: str(node.fields.get(TABLE_TEXT_COLOR_FIELD) or DEFAULT_PARAMETER_TABLE_COLORS[TABLE_TEXT_COLOR_FIELD]),
    }


def clone_group_record(group: GroupRecord) -> GroupRecord:
    return group.clone()


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
    palette_by_type = {
        "Initial": {"body": "#13251f", "border": "#5fc992", "text": "#f3fcf7"},
        "TouchIdle": {"body": "#071b2d", "border": "#25b7ff", "text": "#e8f8ff"},
        "ReturnDefaultIdle": {"body": "#13251f", "border": "#5fc992", "text": "#f3fcf7"},
        "TouchDrag": {"body": "#251f38", "border": "#b5a1ff", "text": "#faf7ff"},
        "ParameterTrigger": {"body": "#071b2d", "border": "#25b7ff", "text": "#e8f8ff"},
        "DrawFrame": {"body": "#12191f", "border": "#7aa6c2", "text": "#d9e8f3"},
        "Comment": {"body": "#2f2618", "border": "#e2b86a", "text": "#fff8eb"},
    }
    palette = palette_by_type.get(node.type)
    if node.type == "Comment":
        body = _valid_color_or_none(node.fields.get("note_box_color")) or (palette["body"] if palette else "#6c5a38")
        border = _valid_color_or_none(node.fields.get("theme_border_color")) or body or (palette["border"] if palette else "#e0b66b")
        text = _valid_color_or_none(node.fields.get("note_text_color")) or (palette["text"] if palette else "#fff8eb")
        return {
            "theme_body_color": body,
            "theme_border_color": border,
            "theme_text_color": text,
        }
    if palette:
        return {
            "theme_body_color": palette["body"],
            "theme_border_color": palette["border"],
            "theme_text_color": palette["text"],
        }
    return {
        "theme_body_color": _valid_color_or_none(definition.body_color) or "#27384d",
        "theme_border_color": _valid_color_or_none(definition.accent_color) or "#78b1ff",
        "theme_text_color": DEFAULT_THEME_TEXT_COLOR,
    }


def apply_node_appearance_defaults(schema: EditorSchema, node: NodeRecord) -> None:
    if _node_schema(schema, node.type).category == "root":
        node.fields.clear()
        return
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


def sync_comment_theme_appearance(node: NodeRecord) -> None:
    if node.type != "Comment":
        return
    body = _valid_color_or_none(node.fields.get("note_box_color"))
    text = _valid_color_or_none(node.fields.get("note_text_color"))
    if body:
        node.fields["theme_body_color"] = body
    if text:
        node.fields["theme_text_color"] = text


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


def normalize_action_trigger_active_for_csv(value: Any) -> Any:
    text = _text(value).strip()
    if not text:
        return value
    target_idle = _target_idle_from_raw(text)
    if target_idle is None:
        return value
    match = IGNORE_LIST_PATTERN.search(text)
    if not match:
        return value
    ignore_values = "" if target_idle == 0 else match.group("values").strip()
    ignore_values = ignore_values.strip("{} ")
    normalized = f"ignore = {{{ignore_values}}}"
    return text[: match.start()] + normalized + text[match.end() :]


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
    if node.type not in {"TouchIdle", "ReturnDefaultIdle", "TouchDrag", "ParameterTrigger"}:
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
    if node.type == "ReturnDefaultIdle":
        return 0
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


TOUCHDRAG_VALUE_NAMESPACE_TYPES = {"TouchDrag", "ParameterTrigger"}
TOUCHIDLE_NAMESPACE_TYPES = {"TouchIdle", "ReturnDefaultIdle"}


def _type_slot_namespace_types(node_type: str) -> set[str]:
    if node_type in TOUCHDRAG_VALUE_NAMESPACE_TYPES:
        return TOUCHDRAG_VALUE_NAMESPACE_TYPES
    if node_type in TOUCHIDLE_NAMESPACE_TYPES:
        return TOUCHIDLE_NAMESPACE_TYPES
    return {node_type}


def _type_slot_namespace_key(node_type: str) -> str:
    if node_type in TOUCHDRAG_VALUE_NAMESPACE_TYPES:
        return "TouchDrag"
    if node_type in TOUCHIDLE_NAMESPACE_TYPES:
        return "TouchIdle"
    return node_type


def _occupied_type_slots(document: DocumentModel, node_type: str, *, exclude_uuid: str | None = None) -> set[int]:
    namespace_types = _type_slot_namespace_types(node_type)
    occupied = {
        int(node.type_slot)
        for node in document.nodes
        if node.uuid != exclude_uuid and node.type in namespace_types and isinstance(node.type_slot, int) and node.type_slot > 0
    }
    return occupied


def _occupied_export_slots(document: DocumentModel, *, exclude_uuid: str | None = None) -> set[int]:
    occupied = {
        int(node.export_slot)
        for node in document.nodes
        if node.uuid != exclude_uuid and isinstance(node.export_slot, int) and node.export_slot > 0
    }
    return occupied


def allocate_type_slot(document: DocumentModel, node_type: str, *, exclude_uuid: str | None = None) -> int:
    return _next_available_slot(_occupied_type_slots(document, node_type, exclude_uuid=exclude_uuid))


def allocate_export_slot(document: DocumentModel, *, exclude_uuid: str | None = None) -> int:
    return _next_available_slot(_occupied_export_slots(document, exclude_uuid=exclude_uuid))


def backfill_slots(schema: EditorSchema, document: DocumentModel) -> None:
    function_types = set(function_node_types(schema))
    seen_type_slots: dict[str, set[int]] = {}
    seen_export_slots: set[int] = set()
    for node in document.nodes:
        if node.type not in function_types:
            continue
        namespace_key = _type_slot_namespace_key(node.type)
        namespace_seen = seen_type_slots.setdefault(namespace_key, set())
        current_slot = node.type_slot if isinstance(node.type_slot, int) else None
        if not isinstance(current_slot, int) or current_slot <= 0 or current_slot in namespace_seen:
            node.type_slot = allocate_type_slot(document, node.type, exclude_uuid=node.uuid)
        namespace_seen.add(int(node.type_slot))
        if (
            not isinstance(node.export_slot, int)
            or node.export_slot <= 0
            or node.export_slot in seen_export_slots
        ):
            node.export_slot = allocate_export_slot(document, exclude_uuid=node.uuid)
        seen_export_slots.add(int(node.export_slot))


def _next_sequence_no(document: DocumentModel, node_type: str) -> int:
    existing = [node.sequence_no or 0 for node in document.nodes if node.type == node_type]
    return (max(existing) if existing else 0) + 1


def _node_schema(schema: EditorSchema, node_type: str) -> NodeSchema:
    return schema.nodes[node_type]


def _sequence_action_name(node_schema: NodeSchema, target_idle: int, sequence: int | None = None) -> str:
    template = node_schema.auto_rules.action_name_template or "touch_idle{target_idle}"
    return template.format(target_idle=target_idle, sequence=target_idle if sequence is None else sequence)


def _expected_parameter(node_schema: NodeSchema, target_idle: int, *, sequence: int | None = None) -> str:
    template = node_schema.auto_rules.parameter_template
    return template.format(target_idle=target_idle, sequence=target_idle if sequence is None else sequence)


def _apply_parameter_table_generated_names(schema: EditorSchema, node: NodeRecord, *, force_parameter: bool = True) -> None:
    if node.type != "ParameterTrigger":
        return
    node_schema = _node_schema(schema, node.type)
    sequence = int(node.type_slot or node.sequence_no or 1)
    target_idle = sequence if node_schema.auto_rules.use_sequence_for_target_idle else 0
    if force_parameter or "draw_able_name" not in node.manual_fields:
        node.fields["draw_able_name"] = node_schema.auto_rules.draw_template.format(
            sequence=sequence,
            target_idle=target_idle,
        )
        node.manual_fields.discard("draw_able_name")
    if force_parameter or "parameter" not in node.manual_fields:
        node.fields["parameter"] = _expected_parameter(node_schema, target_idle, sequence=sequence)
        node.manual_fields.discard("parameter")


def _animated_action(
    schema: EditorSchema,
    node_schema: NodeSchema,
    target_idle: int,
    action_name: str | None = None,
    *,
    sequence: int | None = None,
) -> tuple[str, str]:
    resolved_action_name = (
        action_name
        if action_name is not None
        else _sequence_action_name(node_schema, target_idle, sequence=sequence)
    )
    ignore_values = "" if target_idle == 0 else _format_ignore_values(schema.default_ignore)
    action = (
        schema.animated_action_template.replace("{target_idle}", str(target_idle))
        .replace("{action_name}", resolved_action_name)
    )
    active = (
        schema.animated_active_template.replace("{target_idle}", str(target_idle))
        .replace("{ignore_values}", ignore_values)
    )
    return action, active


def _hard_cut_action(schema: EditorSchema, target_idle: int) -> tuple[str, str]:
    action = schema.hard_cut_action_template
    ignore_values = "" if target_idle == 0 else _format_ignore_values(schema.hard_cut_ignore)
    active = (
        schema.hard_cut_active_template.replace("{target_idle}", str(target_idle))
        .replace("{ignore_values}", ignore_values)
    )
    return action, active


def build_csv_export_filename(prefix: str = "ship_l2d_export") -> str:
    return f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"


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


def create_template_document(
    schema: EditorSchema,
    *,
    version: str,
    char_name: str,
    memo: str,
    ship_skin_id: int,
    author: str = "",
    tips: str = "",
    react_condition: str = "",
    default_state: str = "idle0",
) -> DocumentModel:
    document = create_document(schema)
    document.meta.version = str(version or "").strip()
    document.meta.author = str(author or "").strip()
    document.meta.ship_skin_id = int(ship_skin_id or 0)
    document.meta.memo = str(memo or "").strip()
    # Idle0 is the sole graph root, so legacy/default-state input is normalized
    # to the corresponding hidden metadata value.
    document.meta.default_state = "idle0"
    document.meta.react_condition = normalize_react_condition_list(react_condition)
    document.meta.tips = str(tips or "").strip()
    document.meta.CharName = str(char_name or "").strip()
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
    node_schema = _node_schema(schema, node.type)
    fixed_target = node_schema.auto_rules.fixed_target_idle
    target_idle = fixed_target if fixed_target is not None else _coerce_int(
        node.fields.get("target_idle"), normalized_target_idle(node)
    )
    if node.type in {"TouchIdle", "ReturnDefaultIdle"} and node.fields.get("transition_type") == "hard":
        return _hard_cut_action(schema, target_idle)
    sequence = int(node.type_slot or node.sequence_no or target_idle or 1)
    return _animated_action(schema, node_schema, target_idle, sequence=sequence)


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
        node.fields["parameter"] = _expected_parameter(
            node_schema,
            target_idle,
            sequence=int(node.type_slot or node.sequence_no or target_idle),
        )
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
    fixed_target = node_schema.auto_rules.fixed_target_idle
    sequence = int(node.type_slot or node.sequence_no or 1)
    if node.type == "ParameterTrigger":
        # Parameter-table rows use their stable slot to define the generated
        # parameter name.  Deriving this value from the parameter currently
        # being edited makes any numbered custom name look generated and causes
        # it to be overwritten on the next auto-rule pass.
        target_idle = sequence if node_schema.auto_rules.use_sequence_for_target_idle else 0
        node.fields["target_idle"] = 0
    else:
        target_idle = fixed_target if fixed_target is not None else normalized_target_idle(node)
        node.fields["target_idle"] = target_idle
    expected_parameter = _expected_parameter(
        node_schema,
        target_idle,
        sequence=sequence,
    )
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
    fixed_target = node_schema.auto_rules.fixed_target_idle
    target_idle = (
        fixed_target
        if fixed_target is not None
        else (
            sequence
            if node_schema.auto_rules.use_sequence_for_target_idle
            else _coerce_int(node.fields.get("target_idle"), sequence)
        )
    )
    node.fields["draw_able_name"] = node_schema.auto_rules.draw_template.format(sequence=sequence, target_idle=target_idle)
    node.fields["target_idle"] = target_idle
    node.fields["parameter"] = _expected_parameter(node_schema, target_idle, sequence=int(sequence))
    action, active = _infer_expected_actions(schema, node)
    node.fields["action_trigger"] = action
    node.fields["action_trigger_active"] = active
    node.manual_fields.difference_update({"parameter", "action_trigger"})
    _refresh_trigger_interface_fields(node)


def _increment_manual_sequence_value(
    document: DocumentModel,
    key: str,
    value: Any,
    offset: int,
) -> Any:
    text = _text(value)
    action_name = _action_name_from_raw(text) if key == "action_trigger" else text
    match = TRAILING_INT_PATTERN.search(action_name)
    if not match:
        return value
    occupied = {
        _text(existing.fields.get(key))
        for existing in document.nodes
        if _text(existing.fields.get(key)).strip()
    }
    increment = max(1, int(offset))
    while True:
        replacement = str(_coerce_int(match.group(1), 0) + increment)
        incremented_name = action_name[: match.start(1)] + replacement + action_name[match.end(1) :]
        candidate = (
            text.replace(f"'{action_name}'", f"'{incremented_name}'", 1)
            if key == "action_trigger"
            else incremented_name
        )
        if candidate not in occupied:
            return candidate
        increment += 1


def apply_clone_sequence_fields(
    schema: EditorSchema,
    document: DocumentModel,
    node: NodeRecord,
    source: NodeRecord,
    *,
    source_type_slot: int | None = None,
) -> None:
    """Regenerate canonical sequence fields while advancing explicit numbered overrides."""

    if node.type not in function_node_types(schema):
        return
    node_schema = _node_schema(schema, node.type)
    sequence = int(node.type_slot or node.sequence_no or 1)
    source_sequence = int(source_type_slot or source.type_slot or source.sequence_no or 0)
    fixed_target = node_schema.auto_rules.fixed_target_idle
    source_target = fixed_target if fixed_target is not None else normalized_target_idle(source)
    canonical_source_target = (
        fixed_target
        if fixed_target is not None
        else source_sequence if node_schema.auto_rules.use_sequence_for_target_idle else source_target
    )
    inferred_manual_fields = set(source.manual_fields)
    expected_source_draw = node_schema.auto_rules.draw_template.format(
        sequence=source_sequence or 1,
        target_idle=canonical_source_target,
    )
    expected_source_parameter = _expected_parameter(
        node_schema,
        canonical_source_target,
        sequence=source_sequence or 1,
    )
    expected_source_action, _expected_source_active = _infer_expected_actions(schema, source)
    for key, expected in (
        ("draw_able_name", expected_source_draw),
        ("parameter", expected_source_parameter),
        ("action_trigger", expected_source_action),
    ):
        if _text(source.fields.get(key)) != _text(expected):
            inferred_manual_fields.add(key)
    if fixed_target is not None:
        target_idle = fixed_target
    elif node_schema.auto_rules.use_sequence_for_target_idle and source_target == canonical_source_target:
        target_idle = sequence
    else:
        target_idle = source_target

    node.fields["draw_able_name"] = node_schema.auto_rules.draw_template.format(
        sequence=sequence,
        target_idle=target_idle,
    )
    node.fields["target_idle"] = target_idle
    node.fields["parameter"] = _expected_parameter(node_schema, target_idle, sequence=sequence)
    if _is_touchdrag_value_like(node):
        node.fields["target_idle"] = 0
        node.fields["action_trigger"] = ""
        node.fields["action_trigger_active"] = ""
    else:
        action, active = _infer_expected_actions(schema, node)
        node.fields["action_trigger"] = action
        node.fields["action_trigger_active"] = active

    offset = max(1, sequence - source_sequence) if source_sequence else 1
    node.manual_fields = inferred_manual_fields
    for key in ("draw_able_name", "parameter", "action_trigger"):
        if key in inferred_manual_fields:
            node.fields[key] = _increment_manual_sequence_value(
                document,
                key,
                source.fields.get(key, ""),
                offset,
            )
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
    if _node_schema(schema, node.type).category == "root":
        node.fields.clear()
        return
    if "parts_data" in node.fields:
        node.fields["parts_data"] = canonicalize_parts_data(node.fields.get("parts_data", ""))
    _update_drag_offsets(node, changed_key, source_mode)
    _update_range_abs(node)
    if node.type not in function_node_types(schema):
        return
    node_schema = _node_schema(schema, node.type)
    fixed_target = node_schema.auto_rules.fixed_target_idle
    if fixed_target is not None:
        node.fields["target_idle"] = fixed_target
    if fixed_target is None and source_mode == "simple" and node_numeric_linkage_enabled(node):
        linked_target_idle = _linked_target_idle_for_field(node, changed_key)
        if linked_target_idle is not None:
            _apply_simple_linked_field_updates(
                schema,
                node,
                target_idle=linked_target_idle,
                preserve_key=changed_key,
            )
            return
    target_idle = fixed_target if fixed_target is not None else (
        _coerce_int(node.fields.get("target_idle"), normalized_target_idle(node))
        if changed_key in {"target_idle", "transition_type"}
        else normalized_target_idle(node)
    )
    node.fields["target_idle"] = target_idle
    if _is_touchdrag_value_like(node):
        if node.type == "ParameterTrigger":
            _apply_parameter_table_generated_names(schema, node, force_parameter=force_generated)
        if force_generated or source_mode == "simple":
            node.fields["action_trigger"] = ""
            node.fields["action_trigger_active"] = ""
        node.fields["target_idle"] = 0
        node.manual_fields.discard("action_trigger")
        _refresh_trigger_interface_fields(node)
        return
    if fixed_target is not None:
        expected_action, expected_active = _infer_expected_actions(schema, node)
        if force_generated or "action_trigger" not in node.manual_fields:
            node.fields["action_trigger"] = expected_action
        node.fields["action_trigger_active"] = expected_active
        _refresh_trigger_interface_fields(node)
        return
    if not node_numeric_linkage_enabled(node):
        _refresh_trigger_interface_fields(node)
        return
    if force_generated or "parameter" not in node.manual_fields:
        node.fields["parameter"] = _expected_parameter(
            node_schema,
            target_idle,
            sequence=int(node.type_slot or node.sequence_no or target_idle),
        )
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
        _apply_parameter_table_generated_names(schema, node)
        node.fields["result_type"] = "value"
        node.fields["action_trigger"] = ""
        node.fields["action_trigger_active"] = ""
        node.fields["target_idle"] = 0
        node.manual_fields.discard("action_trigger")
        _refresh_trigger_interface_fields(node)
        ensure_parameter_table_metadata(node)
    apply_node_appearance_defaults(schema, node)
    sync_comment_legacy_appearance(node)
    ensure_parameter_table_metadata(node)
    return node


def sync_meta_from_initial(document: DocumentModel) -> None:
    """Legacy API retained; metadata is now the sole runtime source of truth."""

    del document


def sync_initial_from_meta(schema: EditorSchema, document: DocumentModel) -> None:
    """Legacy API retained without recreating the removed Initial node."""

    idle0 = next((node for node in document.nodes if node.type == "Idle0"), None)
    legacy_initials = [node for node in document.nodes if node.type == "Initial"]
    if idle0 is None and legacy_initials:
        idle0 = legacy_initials[0]
        idle0.type = "Idle0"
        idle0.fields.clear()
    document.nodes = [node for node in document.nodes if node.type != "Initial"]
    if idle0 is None:
        document.nodes.insert(0, create_node(schema, document, "Idle0", (72.0, 72.0)))


def recompute_document_state(schema: EditorSchema, document: DocumentModel) -> None:
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
    document.nodes.append(create_node(active_schema, document, "Idle0", (72.0, 72.0)))
    normalize_plan_layout(document)
    reassign_function_ids(active_schema, document)
    recompute_document_state(active_schema, document)
    return document


def normalized_document_groups(document: DocumentModel) -> list[GroupRecord]:
    existing_node_ids = {
        node.uuid
        for node in document.nodes
        if node.type not in {"Initial", "Idle0"}
    }
    claimed_node_ids: set[str] = set()
    normalized: list[GroupRecord] = []
    for group in document.groups:
        member_ids: list[str] = []
        for node_uuid in group.node_uuids:
            if node_uuid not in existing_node_ids or node_uuid in claimed_node_ids:
                continue
            claimed_node_ids.add(node_uuid)
            member_ids.append(node_uuid)
        if not member_ids and not (group.ui_position and group.ui_size):
            continue
        normalized.append(
            GroupRecord(
                uuid=group.uuid,
                title=str(group.title or "").strip(),
                node_uuids=member_ids,
                theme_body_color=group.theme_body_color,
                theme_border_color=group.theme_border_color,
                theme_text_color=group.theme_text_color,
                ui_position=dict(group.ui_position) if group.ui_position else None,
                ui_size=dict(group.ui_size) if group.ui_size else None,
            )
        )
    return normalized


def _legacy_node_rect(node_type: str, ui_position: dict[str, Any], ui_size: dict[str, Any] | None) -> tuple[float, float, float, float]:
    x = float(ui_position.get("x", 0.0))
    y = float(ui_position.get("y", 0.0))
    if ui_size:
        width = float(ui_size.get("width", 380.0))
        height = float(ui_size.get("height", 180.0))
    elif node_type == "Comment":
        width = 360.0
        height = 180.0
    elif node_type == "DrawFrame":
        width = DRAWFRAME_DEFAULT_SIZE["width"]
        height = DRAWFRAME_DEFAULT_SIZE["height"]
    else:
        width = 380.0
        height = 180.0
    return x, y, width, height


def groups_from_legacy_drawframes(raw_nodes: list[dict[str, Any]], document: DocumentModel) -> list[GroupRecord]:
    legacy_frames = [raw for raw in raw_nodes if raw.get("type") == "DrawFrame"]
    if not legacy_frames:
        return []
    groups: list[GroupRecord] = []
    node_rects: dict[str, tuple[float, float, float, float]] = {
        node.uuid: _legacy_node_rect(node.type, node.ui_position, node.ui_size)
        for node in document.nodes
    }
    for raw in legacy_frames:
        frame_x, frame_y, frame_width, frame_height = _legacy_node_rect(
            "DrawFrame",
            raw.get("ui_position", {"x": 0.0, "y": 0.0}),
            raw.get("ui_size"),
        )
        member_ids: list[str] = []
        for node in document.nodes:
            if node.type == "DrawFrame":
                continue
            node_x, node_y, node_width, node_height = node_rects[node.uuid]
            intersects = not (
                node_x + node_width < frame_x
                or node_x > frame_x + frame_width
                or node_y + node_height < frame_y
                or node_y > frame_y + frame_height
            )
            if intersects:
                member_ids.append(node.uuid)
        if not member_ids:
            continue
        fields = raw if isinstance(raw, dict) else {}
        groups.append(
            GroupRecord(
                uuid=str(fields.get("uuid") or new_uuid()),
                title=str(fields.get("title") or ""),
                node_uuids=member_ids,
                theme_body_color=str(fields.get("theme_body_color") or "#dfeada"),
                theme_border_color=str(fields.get("theme_border_color") or "#69b070"),
                theme_text_color=str(fields.get("theme_text_color") or "#ffffff"),
            )
        )
    return groups


def reassign_function_ids(schema: EditorSchema, document: DocumentModel) -> None:
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


def _export_node_fields(node: NodeRecord) -> dict[str, Any]:
    hidden_fields = set(HIDDEN_NODE_FIELDS)
    if node.type == "ParameterTrigger":
        hidden_fields.difference_update(
            {
                TABLE_ID_FIELD,
                TABLE_TITLE_FIELD,
                TABLE_ORDER_FIELD,
                TABLE_BODY_COLOR_FIELD,
                TABLE_BORDER_COLOR_FIELD,
                TABLE_TEXT_COLOR_FIELD,
            }
        )
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
    reassign_function_ids(schema, document)
    function_types = set(function_node_types(schema))
    serialized_nodes = []
    for node in document.nodes:
        if node.type == "DrawFrame":
            continue
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
        if node.manual_fields:
            payload["manual_fields"] = sorted(node.manual_fields)
        payload.update(_export_node_fields(node))
        if _should_persist_target_idle(node):
            payload["target_idle"] = _coerce_int(node.fields.get("target_idle"), 0)
        serialized_nodes.append(payload)
    serialized_groups = [asdict(group) for group in normalized_document_groups(document)]
    serialized_strokes = [
        {
            "id": stroke.uuid,
            "points": [[x, y] for x, y in stroke.points],
            "color": stroke.color,
            "width": stroke.width,
        }
        for stroke in validate_canvas_strokes(document.canvas_strokes)
    ]
    return {
        "editor_signature": EDITOR_DOCUMENT_SIGNATURE,
        "format_version": EDITOR_DOCUMENT_FORMAT_VERSION,
        "global_mode": document.global_mode,
        "interaction_creation_mode": document.interaction_creation_mode,
        "editor_settings": asdict(document.editor_settings),
        "meta": asdict(document.meta),
        "nodes": serialized_nodes,
        "groups": serialized_groups,
        "canvas_images": [asdict(image) for image in document.canvas_images],
        "canvas_strokes": serialized_strokes,
        "connections": [asdict(connection) for connection in document.connections],
        "canvas_view": asdict(document.canvas_view),
        "plan_layout": serialize_plan_layout(document),
    }


def _document_format_version(payload: dict[str, Any]) -> int:
    raw_format_version = payload.get("format_version", 1)
    if isinstance(raw_format_version, int) and not isinstance(raw_format_version, bool):
        format_version = raw_format_version
    elif isinstance(raw_format_version, str) and raw_format_version.isdigit():
        format_version = int(raw_format_version)
    else:
        raise ValueError("Invalid document format version")
    if format_version < 1:
        raise ValueError("Invalid document format version")
    return format_version


def _reject_future_document_overwrite(target: Path) -> None:
    if not target.is_file():
        return
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return
    if not is_editor_document_payload(payload):
        return
    format_version = _document_format_version(payload)
    if format_version > EDITOR_DOCUMENT_FORMAT_VERSION:
        raise ValueError(
            f"Document format version {format_version} is newer than supported "
            f"version {EDITOR_DOCUMENT_FORMAT_VERSION}; refusing to overwrite"
        )


def save_document(schema: EditorSchema, document: DocumentModel, path: str | Path) -> None:
    target = Path(path)
    _reject_future_document_overwrite(target)
    data = export_document_dict(schema, document)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            json.dump(data, temp_file, ensure_ascii=False, indent=2)
            temp_file.write("\n")
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, target)
    finally:
        if temp_path is not None and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
    document.path = str(target)


def _finite_float(value: Any, default: float) -> float:
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return default
    return resolved if math.isfinite(resolved) else default


def _strict_canvas_stroke_number(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Canvas stroke {label} must be a number")
    resolved = float(value)
    if not math.isfinite(resolved):
        raise ValueError(f"Canvas stroke {label} must be finite")
    return resolved


def validate_canvas_stroke(stroke: CanvasStrokeRecord) -> CanvasStrokeRecord:
    """Validate and clone one stroke without silently repairing its data."""

    if not isinstance(stroke.uuid, str):
        raise ValueError("Canvas stroke id is invalid")
    stroke_id = stroke.uuid
    if not CANVAS_STROKE_ID_PATTERN.fullmatch(stroke_id):
        raise ValueError("Canvas stroke id is invalid")
    if not isinstance(stroke.points, list):
        raise ValueError("Canvas stroke points must be a list")
    if not 2 <= len(stroke.points) <= MAX_CANVAS_STROKE_POINTS:
        raise ValueError("Canvas stroke point count is invalid")
    points: list[tuple[float, float]] = []
    for point in stroke.points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("Canvas stroke point is invalid")
        x = _strict_canvas_stroke_number(point[0], label="x")
        y = _strict_canvas_stroke_number(point[1], label="y")
        if abs(x) > MAX_CANVAS_COORDINATE or abs(y) > MAX_CANVAS_COORDINATE:
            raise ValueError("Canvas stroke coordinate is out of range")
        points.append((x, y))
    color = str(stroke.color or "")
    if not CANVAS_STROKE_COLOR_PATTERN.fullmatch(color):
        raise ValueError("Canvas stroke color is invalid")
    width = _strict_canvas_stroke_number(stroke.width, label="width")
    if not MIN_CANVAS_STROKE_WIDTH <= width <= MAX_CANVAS_STROKE_WIDTH:
        raise ValueError("Canvas stroke width is out of range")
    return CanvasStrokeRecord(
        uuid=stroke_id,
        points=points,
        color=color.upper(),
        width=width,
    )


def validate_canvas_strokes(strokes: list[CanvasStrokeRecord]) -> list[CanvasStrokeRecord]:
    if not isinstance(strokes, list) or len(strokes) > MAX_CANVAS_STROKES:
        raise ValueError("Canvas stroke collection is invalid")
    validated: list[CanvasStrokeRecord] = []
    seen_ids: set[str] = set()
    total_points = 0
    for stroke in strokes:
        if not isinstance(stroke, CanvasStrokeRecord):
            raise ValueError("Canvas stroke record is invalid")
        resolved = validate_canvas_stroke(stroke)
        if resolved.uuid in seen_ids:
            raise ValueError("Canvas stroke ids must be unique")
        seen_ids.add(resolved.uuid)
        total_points += len(resolved.points)
        if total_points > MAX_DOCUMENT_CANVAS_STROKE_POINTS:
            raise ValueError("Canvas stroke document point limit exceeded")
        validated.append(resolved)
    return validated


def _load_canvas_stroke_records(payload: Any) -> list[CanvasStrokeRecord]:
    if not isinstance(payload, list):
        raise ValueError("canvas_strokes must be a list")
    if len(payload) > MAX_CANVAS_STROKES:
        raise ValueError("Canvas stroke collection is invalid")
    records: list[CanvasStrokeRecord] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("Canvas stroke entry must be an object")
        stroke_id = item.get("id", item.get("uuid"))
        points = item.get("points")
        if not isinstance(points, list):
            raise ValueError("Canvas stroke points must be a list")
        records.append(
            CanvasStrokeRecord(
                uuid=stroke_id if isinstance(stroke_id, str) else "",
                points=points,
                color=item.get("color") if isinstance(item.get("color"), str) else "",
                width=item.get("width"),
            )
        )
    return validate_canvas_strokes(records)


def _load_canvas_image_records(payload: list[Any]) -> list[CanvasImageRecord]:
    records: list[CanvasImageRecord] = []
    total_bytes = 0
    total_pixels = 0.0
    for item in payload[: MAX_REFERENCE_IMAGE_COUNT * 4]:
        if len(records) >= MAX_REFERENCE_IMAGE_COUNT:
            break
        if not isinstance(item, dict):
            continue
        canonical = canonicalize_reference_image(
            str(item.get("data_base64") or ""),
            str(item.get("mime_type") or "image/png"),
        )
        if canonical is None:
            continue
        data_base64, size, byte_size = canonical
        if total_bytes + byte_size > MAX_DOCUMENT_REFERENCE_IMAGE_BYTES:
            continue
        pixels = size[0] * size[1]
        if total_pixels + pixels > MAX_DOCUMENT_REFERENCE_IMAGE_PIXELS:
            continue
        position = item.get("ui_position") if isinstance(item.get("ui_position"), dict) else {}
        persisted_size = item.get("ui_size") if isinstance(item.get("ui_size"), dict) else {}
        display_size = validated_reference_image_display_size(
            (
                persisted_size.get("width", size[0]),
                persisted_size.get("height", size[1]),
            )
        )
        if display_size is None:
            display_size = size
        opacity = min(1.0, max(0.0, _finite_float(item.get("opacity", 1.0), 1.0)))
        records.append(
            CanvasImageRecord(
                uuid=str(item.get("uuid") or new_uuid()),
                data_base64=data_base64,
                mime_type="image/png",
                name=str(item.get("name") or "参考图")[:256],
                ui_position={
                    "x": _finite_float(position.get("x", 0.0), 0.0),
                    "y": _finite_float(position.get("y", 0.0), 0.0),
                },
                ui_size={"width": display_size[0], "height": display_size[1]},
                opacity=opacity,
                locked=bool(item.get("locked", False)),
            )
        )
        total_bytes += byte_size
        total_pixels += pixels
    return records


def load_document(schema: EditorSchema, path: str | Path) -> DocumentModel:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not is_editor_document_payload(payload):
        raise ValueError("不是 L2D Config Editor 配置文件")
    format_version = _document_format_version(payload)
    if format_version > EDITOR_DOCUMENT_FORMAT_VERSION:
        raise ValueError(
            f"Document format version {format_version} is newer than supported "
            f"version {EDITOR_DOCUMENT_FORMAT_VERSION}"
        )
    meta_payload = payload.get("meta", {})
    if not isinstance(meta_payload, dict):
        meta_payload = {}
    raw_nodes = payload.get("nodes", [])
    if not isinstance(raw_nodes, list):
        raw_nodes = []
    legacy_initial = next(
        (raw for raw in raw_nodes if isinstance(raw, dict) and raw.get("type") == "Initial"),
        None,
    )
    existing_idle = next(
        (raw for raw in raw_nodes if isinstance(raw, dict) and raw.get("type") == "Idle0"),
        None,
    )
    legacy_meta = {
        "version": (legacy_initial or {}).get("version", "2099-09-09"),
        "author": (legacy_initial or {}).get("author", ""),
        "ship_skin_id": (legacy_initial or {}).get("ship_skin_id", 0),
        "memo": (legacy_initial or {}).get("memo", ""),
        "default_state": (legacy_initial or {}).get("defaultState", "idle0"),
        "react_condition": (legacy_initial or {}).get("react_condition", ""),
        "tips": (legacy_initial or {}).get("tips", ""),
        "CharName": (legacy_initial or {}).get("CharName", ""),
    }
    settings_payload = payload.get("editor_settings", {})
    if not isinstance(settings_payload, dict):
        settings_payload = {}
    groups_payload = payload.get("groups", [])
    if not isinstance(groups_payload, list):
        groups_payload = []
    canvas_images_payload = payload.get("canvas_images", [])
    if not isinstance(canvas_images_payload, list):
        canvas_images_payload = []
    canvas_strokes_payload = payload.get("canvas_strokes", [])
    plan_layout = load_plan_layout(
        payload.get("plan_layout"),
        required=format_version >= 4,
    )
    meta_keys = set(MetaRecord.__dataclass_fields__.keys())
    resolved_meta = {
        key: meta_payload[key] if key in meta_payload else legacy_meta[key]
        for key in meta_keys
    }
    resolved_meta["default_state"] = "idle0"
    document = DocumentModel(
        global_mode=str(payload.get("global_mode", "simple")),
        interaction_creation_mode=str(payload.get("interaction_creation_mode", "auto") or "auto"),
        editor_settings=EditorSettings(
            numeric_linkage_enabled=bool(settings_payload.get("numeric_linkage_enabled", False)),
        ),
        meta=MetaRecord(**resolved_meta),
        canvas_images=_load_canvas_image_records(canvas_images_payload),
        canvas_strokes=_load_canvas_stroke_records(canvas_strokes_payload),
        connections=[ConnectionRecord(**item) for item in payload.get("connections", [])],
        canvas_view=CanvasViewState(**payload.get("canvas_view", {})),
        plan_layout=plan_layout,
        path=str(path),
    )
    function_types = set(function_node_types(schema))
    sequence_map: dict[str, int] = {}
    legacy_return_action_uuids: set[str] = set()
    preferred_root_node: NodeRecord | None = None
    for raw in raw_nodes:
        if not isinstance(raw, dict) or "type" not in raw:
            continue
        raw_node_type = raw["type"]
        node_type = "Idle0" if raw_node_type == "Initial" else raw_node_type
        fields = {
            key: value
            for key, value in raw.items()
            if key
            not in {
                "uuid",
                "type",
                "ui_position",
                "ui_size",
                "mode_variant",
                "type_slot",
                "export_slot",
                "locked",
                "numeric_linkage_enabled",
                "manual_fields",
            }
        }
        if node_type == "Idle0":
            fields = {}
        if node_type == "DrawFrame":
            continue
        missing_parameter_table_id = node_type == "ParameterTrigger" and not str(fields.get(TABLE_ID_FIELD) or "").strip()
        if "target_idle" not in fields:
            raw_target_idle = _target_idle_from_raw(fields.get("action_trigger_active"))
            if raw_target_idle is not None:
                fields["target_idle"] = raw_target_idle
        sequence_map[node_type] = sequence_map.get(node_type, 0) + 1
        node_uuid = raw.get("uuid") or new_uuid()
        node = NodeRecord(
            uuid=node_uuid,
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
            manual_fields={str(key) for key in raw.get("manual_fields", []) if str(key or "").strip()},
        )
        if (
            node.type == "ReturnDefaultIdle"
            and node.fields.get("transition_type", "animated") != "hard"
            and "action_trigger" not in node.manual_fields
            and str(node.fields.get("action_trigger", "")).strip()
            == _animated_action(schema, _node_schema(schema, node.type), 0, action_name="touch_idle0")[0]
        ):
            legacy_return_action_uuids.add(node.uuid)
        apply_node_appearance_defaults(schema, node)
        sync_comment_legacy_appearance(node)
        ensure_parameter_table_metadata(node)
        if missing_parameter_table_id:
            node.fields.pop(TABLE_ID_FIELD, None)
        if "manual_fields" not in raw:
            infer_manual_fields(schema, node, document)
        if node.uuid in legacy_return_action_uuids:
            node.manual_fields.discard("action_trigger")
        apply_auto_rules(schema, document, node, source_mode="advanced", force_generated=False)
        document.nodes.append(node)
        if raw is existing_idle:
            preferred_root_node = node
    _restore_missing_parameter_table_groups(document)
    idle_roots = [node for node in document.nodes if node.type == "Idle0"]
    if not idle_roots:
        primary_root = create_node(schema, document, "Idle0", (60.0, 60.0))
        document.nodes.insert(0, primary_root)
    else:
        primary_root = preferred_root_node if preferred_root_node is not None else idle_roots[0]
    duplicate_roots = [node for node in idle_roots if node is not primary_root]
    duplicate_root_uuids = {node.uuid for node in duplicate_roots}
    if duplicate_roots:
        duplicate_root_object_ids = {id(node) for node in duplicate_roots}
        document.nodes = [node for node in document.nodes if id(node) not in duplicate_root_object_ids]
    existing_node_uuids = {node.uuid for node in document.nodes}
    normalized_connections: list[ConnectionRecord] = []
    seen_connections: set[tuple[str, str]] = set()
    for connection in document.connections:
        from_uuid = primary_root.uuid if connection.from_uuid in duplicate_root_uuids else connection.from_uuid
        to_uuid = primary_root.uuid if connection.to_uuid in duplicate_root_uuids else connection.to_uuid
        pair = (from_uuid, to_uuid)
        if (
            from_uuid not in existing_node_uuids
            or to_uuid not in existing_node_uuids
            or to_uuid == primary_root.uuid
            or from_uuid == to_uuid
            or pair in seen_connections
        ):
            continue
        seen_connections.add(pair)
        normalized_connections.append(ConnectionRecord(from_uuid=from_uuid, to_uuid=to_uuid))
    document.connections = normalized_connections
    parsed_groups: list[GroupRecord] = []
    for raw_group in groups_payload:
        if not isinstance(raw_group, dict):
            continue
        parsed_groups.append(
            GroupRecord(
                uuid=str(raw_group.get("uuid") or new_uuid()),
                title=str(raw_group.get("title") or ""),
                node_uuids=[str(node_uuid) for node_uuid in raw_group.get("node_uuids", []) if str(node_uuid or "").strip()],
                theme_body_color=str(raw_group.get("theme_body_color") or "#dfeada"),
                theme_border_color=str(raw_group.get("theme_border_color") or "#69b070"),
                theme_text_color=str(raw_group.get("theme_text_color") or "#ffffff"),
                ui_position=dict(raw_group["ui_position"]) if isinstance(raw_group.get("ui_position"), dict) else None,
                ui_size=dict(raw_group["ui_size"]) if isinstance(raw_group.get("ui_size"), dict) else None,
            )
        )
    if parsed_groups:
        document.groups = parsed_groups
    else:
        document.groups = groups_from_legacy_drawframes(raw_nodes, document)
    document.groups = normalized_document_groups(document)
    backfill_slots(schema, document)
    for node in document.nodes:
        if node.uuid in legacy_return_action_uuids:
            node.manual_fields.discard("action_trigger")
            node.fields["action_trigger"] = _infer_expected_actions(schema, node)[0]
    reassign_function_ids(schema, document)
    recompute_document_state(schema, document)
    normalize_plan_layout(document)
    return document


def _csv_value_for_mapping(mapping, document: DocumentModel, node: NodeRecord) -> Any:
    if mapping.column == "parts_data":
        return canonicalize_parts_data(node.fields.get(mapping.field or "", mapping.default))
    if mapping.column == "react_condition":
        return format_react_condition_for_csv(getattr(document.meta, mapping.field or "", mapping.default))
    if mapping.column == "action_trigger_active":
        return normalize_action_trigger_active_for_csv(node.fields.get(mapping.field or "", mapping.default))
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
    reassign_function_ids(schema, document)
    rows: list[CsvPreviewRow] = []
    for row_index, node in enumerate(_function_nodes(schema, document)):
        values = {column: "" for column in schema.csv_columns}
        for mapping in schema.csv_mapping:
            values[mapping.column] = _csv_value_for_mapping(mapping, document, node)
        if row_index > 0:
            values["react_condition"] = ""
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
    idle0_nodes = [node for node in document.nodes if node.type == "Idle0"]
    if len(idle0_nodes) != 1:
        issues.extend(_issue_for_group(schema, "Idle0 root must be unique", [], idle0_nodes))

    function_types = set(function_node_types(schema))
    function_nodes = [node for node in document.nodes if node.type in function_types]
    duplicate_parameters: dict[str, list[NodeRecord]] = {}
    for node in function_nodes:
        parameter_value = _display_field_value(schema, node, "parameter")
        if parameter_value and parameter_value.lower() != "empty":
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
