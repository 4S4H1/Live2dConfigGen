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
    default_state: str = "idle0"
    react_condition: str = ""
    tips: str = ""
    CharName: str = ""


@dataclass
class CanvasViewState:
    scale: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0


@dataclass
class PlanTopicRecord:
    """Plan-only metadata for one real graph node."""

    node_uuid: str
    parent_uuid: str | None = None
    order: int = 0
    plan_title: str = ""
    collapsed: bool = False
    branch_color: str = ""
    formalization_state: str = "formal"
    structure_dirty: bool = False

    def clone(self) -> "PlanTopicRecord":
        return PlanTopicRecord(
            node_uuid=self.node_uuid,
            parent_uuid=self.parent_uuid,
            order=int(self.order),
            plan_title=self.plan_title,
            collapsed=bool(self.collapsed),
            branch_color=self.branch_color,
            formalization_state=self.formalization_state,
            structure_dirty=bool(self.structure_dirty),
        )


@dataclass
class PlanLayout:
    """Plan-view hierarchy and viewport, kept separate from formal coordinates."""

    topics: list[PlanTopicRecord] = field(default_factory=list)
    view: CanvasViewState = field(default_factory=CanvasViewState)

    def clone(self) -> "PlanLayout":
        return PlanLayout(
            topics=[topic.clone() for topic in self.topics],
            view=CanvasViewState(
                scale=float(self.view.scale),
                offset_x=float(self.view.offset_x),
                offset_y=float(self.view.offset_y),
            ),
        )


@dataclass
class ConnectionRecord:
    from_uuid: str
    to_uuid: str


@dataclass
class GroupRecord:
    uuid: str
    title: str = ""
    node_uuids: list[str] = field(default_factory=list)
    theme_body_color: str = "#dfeada"
    theme_border_color: str = "#69b070"
    theme_text_color: str = "#ffffff"
    ui_position: dict[str, float] | None = None
    ui_size: dict[str, float] | None = None

    def clone(self) -> "GroupRecord":
        return GroupRecord(
            uuid=self.uuid,
            title=self.title,
            node_uuids=list(self.node_uuids),
            theme_body_color=self.theme_body_color,
            theme_border_color=self.theme_border_color,
            theme_text_color=self.theme_text_color,
            ui_position=dict(self.ui_position) if self.ui_position else None,
            ui_size=dict(self.ui_size) if self.ui_size else None,
        )


@dataclass
class CanvasImageRecord:
    uuid: str
    data_base64: str
    mime_type: str = "image/png"
    name: str = "参考图"
    ui_position: dict[str, float] = field(default_factory=lambda: {"x": 0.0, "y": 0.0})
    ui_size: dict[str, float] = field(default_factory=lambda: {"width": 1.0, "height": 1.0})
    opacity: float = 1.0
    locked: bool = False

    def clone(self) -> "CanvasImageRecord":
        return CanvasImageRecord(
            uuid=self.uuid,
            data_base64=self.data_base64,
            mime_type=self.mime_type,
            name=self.name,
            ui_position=dict(self.ui_position),
            ui_size=dict(self.ui_size),
            opacity=self.opacity,
            locked=self.locked,
        )


@dataclass
class CanvasStrokeRecord:
    """A complete freehand stroke in scene coordinates."""

    uuid: str
    points: list[tuple[float, float]]
    color: str = "#2F80ED"
    width: float = 4.0

    def clone(self) -> "CanvasStrokeRecord":
        return CanvasStrokeRecord(
            uuid=self.uuid,
            points=[(float(x), float(y)) for x, y in self.points],
            color=self.color,
            width=float(self.width),
        )


@dataclass
class EditorPreferences:
    global_mode: str = "simple"
    schema_path: str | None = None
    debug_json_field_names: bool = False


@dataclass
class EditorSettings:
    numeric_linkage_enabled: bool = False


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
    sequence_locked: bool = False
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
            sequence_locked=self.sequence_locked,
            numeric_linkage_enabled=self.numeric_linkage_enabled,
            manual_fields=set(self.manual_fields),
        )


@dataclass
class DocumentModel:
    meta: MetaRecord = field(default_factory=MetaRecord)
    editor_settings: EditorSettings = field(default_factory=EditorSettings)
    nodes: list[NodeRecord] = field(default_factory=list)
    connections: list[ConnectionRecord] = field(default_factory=list)
    groups: list[GroupRecord] = field(default_factory=list)
    canvas_images: list[CanvasImageRecord] = field(default_factory=list)
    canvas_strokes: list[CanvasStrokeRecord] = field(default_factory=list)
    plan_canvas_strokes: list[CanvasStrokeRecord] = field(default_factory=list)
    canvas_view: CanvasViewState = field(default_factory=CanvasViewState)
    plan_layout: PlanLayout = field(default_factory=PlanLayout)
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
