"""Document models used by the editor."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MetaRecord:
    version: str = "2099-09-09"
    author: str = ""
    ship_skin_id: int = 0
    memo: str = ""
    react_condition: str = ""
    tips: str = ""
    CharName: str = ""


@dataclass
class CanvasViewState:
    scale: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0


@dataclass
class ConnectionRecord:
    from_uuid: str
    to_uuid: str


@dataclass
class EditorPreferences:
    global_mode: str = "simple"
    schema_path: str | None = None
    debug_json_field_names: bool = False


@dataclass
class EditorSettings:
    numeric_linkage_enabled: bool = False
    trash_enabled: bool = False


@dataclass
class DocumentState:
    is_meta_ready: bool = False
    meta_missing_fields: list[str] = field(default_factory=list)


@dataclass
class NodeRecord:
    uuid: str
    type: str
    fields: dict[str, Any] = field(default_factory=dict)
    ui_position: dict[str, float] = field(default_factory=lambda: {"x": 0.0, "y": 0.0})
    ui_size: dict[str, float] | None = None
    sequence_no: int | None = None
    type_slot: int | None = None
    export_slot: int | None = None
    locked: bool = False
    numeric_linkage_enabled: bool = False
    manual_fields: set[str] = field(default_factory=set)

    def clone(self) -> "NodeRecord":
        return NodeRecord(
            uuid=self.uuid,
            type=self.type,
            fields=dict(self.fields),
            ui_position=dict(self.ui_position),
            ui_size=dict(self.ui_size) if self.ui_size else None,
            sequence_no=self.sequence_no,
            type_slot=self.type_slot,
            export_slot=self.export_slot,
            locked=self.locked,
            numeric_linkage_enabled=self.numeric_linkage_enabled,
            manual_fields=set(self.manual_fields),
        )


@dataclass
class TrashEntry:
    entry_id: str
    node_uuid: str
    node_type: str
    title: str
    type_slot: int | None = None
    export_slot: int | None = None
    reserved_fields: dict[str, Any] = field(default_factory=dict)


@dataclass
class DocumentModel:
    meta: MetaRecord = field(default_factory=MetaRecord)
    editor_settings: EditorSettings = field(default_factory=EditorSettings)
    nodes: list[NodeRecord] = field(default_factory=list)
    connections: list[ConnectionRecord] = field(default_factory=list)
    trash_bin: list[TrashEntry] = field(default_factory=list)
    canvas_view: CanvasViewState = field(default_factory=CanvasViewState)
    state: DocumentState = field(default_factory=DocumentState)
    global_mode: str = "simple"
    interaction_creation_mode: str = "auto"
    path: str | None = None


@dataclass
class ValidationIssue:
    node_uuid: str
    message: str
    severity: str = "warning"
    field_keys: list[str] = field(default_factory=list)
    related_node_uuids: list[str] = field(default_factory=list)
    related_titles: list[str] = field(default_factory=list)


@dataclass
class CsvPreviewRow:
    values: dict[str, Any]


@dataclass
class SearchHit:
    node_uuid: str
    node_type: str
    title: str
    field_name: str
    field_label: str
    preview: str
