"""External editor schema loader."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Option:
    label: str
    value: Any


@dataclass(frozen=True)
class VisibilitySpec:
    field: str
    equals: Any


@dataclass(frozen=True)
class FieldSchema:
    key: str
    label: str
    editor: str
    label_html: str | None = None
    default: Any = ""
    show_in_modes: tuple[str, ...] = ("simple", "advanced")
    options: tuple[Option, ...] = ()
    read_only: bool = False
    multiline: bool = False
    placeholder: str = ""
    visibility: VisibilitySpec | None = None
    csv_field: str | None = None


@dataclass(frozen=True)
class AutoRuleSpec:
    draw_template: str = ""
    parameter_template: str = ""
    action_name_template: str = ""
    use_sequence_for_target_idle: bool = False
    supports_quick_create: bool = False


@dataclass(frozen=True)
class ValidationRuleSpec:
    name: str
    fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class NodeSchema:
    type_name: str
    title: str
    fields: tuple[FieldSchema, ...]
    header_color: str
    body_color: str
    accent_color: str
    copyable: bool = True
    quick_create: bool = True
    resizable: bool = False
    category: str = "function"
    auto_rules: AutoRuleSpec = field(default_factory=AutoRuleSpec)


@dataclass(frozen=True)
class CsvMappingSpec:
    column: str
    kind: str
    field: str | None = None
    fields: tuple[str, ...] = ()
    default: Any = ""


@dataclass(frozen=True)
class EditorSchema:
    required_meta_fields: tuple[str, ...]
    required_meta_labels: dict[str, str]
    csv_columns: tuple[str, ...]
    csv_mapping: tuple[CsvMappingSpec, ...]
    validation_rules: tuple[ValidationRuleSpec, ...]
    nodes: dict[str, NodeSchema]
    default_ignore: tuple[str, ...]
    hard_cut_ignore: tuple[str, ...]
    animated_action_template: str
    animated_active_template: str
    hard_cut_action_template: str
    hard_cut_active_template: str


SCHEMA_FILE = Path(__file__).with_name("editor_schema.json")


def _parse_field(raw: dict[str, Any]) -> FieldSchema:
    visibility = None
    if "visibility" in raw and raw["visibility"]:
        visibility = VisibilitySpec(
            field=str(raw["visibility"]["field"]),
            equals=raw["visibility"]["equals"],
        )
    options = tuple(Option(label=item["label"], value=item["value"]) for item in raw.get("options", []))
    return FieldSchema(
        key=str(raw["key"]),
        label=str(raw["label"]),
        label_html=str(raw["label_html"]) if raw.get("label_html") is not None else None,
        editor=str(raw["editor"]),
        default=raw.get("default", ""),
        show_in_modes=tuple(raw.get("show_in_modes", ["simple", "advanced"])),
        options=options,
        read_only=bool(raw.get("read_only", False)),
        multiline=bool(raw.get("multiline", False)),
        placeholder=str(raw.get("placeholder", "")),
        visibility=visibility,
        csv_field=raw.get("csv_field"),
    )


def _parse_node(type_name: str, raw: dict[str, Any]) -> NodeSchema:
    return NodeSchema(
        type_name=type_name,
        title=str(raw["title"]),
        fields=tuple(_parse_field(field) for field in raw["fields"]),
        header_color=str(raw["header_color"]),
        body_color=str(raw["body_color"]),
        accent_color=str(raw["accent_color"]),
        copyable=bool(raw.get("copyable", True)),
        quick_create=bool(raw.get("quick_create", True)),
        resizable=bool(raw.get("resizable", False)),
        category=str(raw.get("category", "function")),
        auto_rules=AutoRuleSpec(
            draw_template=str(raw.get("auto_rules", {}).get("draw_template", "")),
            parameter_template=str(raw.get("auto_rules", {}).get("parameter_template", "")),
            action_name_template=str(raw.get("auto_rules", {}).get("action_name_template", "")),
            use_sequence_for_target_idle=bool(raw.get("auto_rules", {}).get("use_sequence_for_target_idle", False)),
            supports_quick_create=bool(raw.get("auto_rules", {}).get("supports_quick_create", False)),
        ),
    )


def load_editor_schema(path: str | Path | None = None) -> EditorSchema:
    schema_path = Path(path) if path else SCHEMA_FILE
    payload = json.loads(schema_path.read_text(encoding="utf-8"))
    nodes = {type_name: _parse_node(type_name, raw) for type_name, raw in payload["nodes"].items()}
    csv_mapping = tuple(
        CsvMappingSpec(
            column=str(item["column"]),
            kind=str(item["kind"]),
            field=item.get("field"),
            fields=tuple(item.get("fields", [])),
            default=item.get("default", ""),
        )
        for item in payload["csv_mapping"]
    )
    validation_rules = tuple(
        ValidationRuleSpec(name=str(item["name"]), fields=tuple(item.get("fields", [])))
        for item in payload.get("validation_rules", [])
    )
    return EditorSchema(
        required_meta_fields=tuple(payload["required_meta_fields"]),
        required_meta_labels={str(key): str(value) for key, value in payload["required_meta_labels"].items()},
        csv_columns=tuple(payload["csv_columns"]),
        csv_mapping=csv_mapping,
        validation_rules=validation_rules,
        nodes=nodes,
        default_ignore=tuple(payload["ignore_lists"]["default"]),
        hard_cut_ignore=tuple(payload["ignore_lists"]["hard"]),
        animated_action_template=str(payload["templates"]["animated_action"]),
        animated_active_template=str(payload["templates"]["animated_active"]),
        hard_cut_action_template=str(payload["templates"]["hard_cut_action"]),
        hard_cut_active_template=str(payload["templates"]["hard_cut_active"]),
    )


def field_visible(field: FieldSchema, node_fields: dict[str, Any], global_mode: str) -> bool:
    # 高级模式应覆盖简易模式可见字段，避免“高级模式反而看不到字段”
    if global_mode == "advanced":
        mode_visible = "advanced" in field.show_in_modes or "simple" in field.show_in_modes
    else:
        mode_visible = global_mode in field.show_in_modes
    if not mode_visible:
        return False
    if not field.visibility:
        return True
    return node_fields.get(field.visibility.field) == field.visibility.equals
