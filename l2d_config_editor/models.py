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
    react_condition: int = 0
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
class NodeRecord:
    uuid: str
    type: str
    fields: dict[str, Any] = field(default_factory=dict)
    ui_position: dict[str, float] = field(default_factory=lambda: {"x": 0.0, "y": 0.0})
    ui_size: dict[str, float] | None = None
    mode_variant: str = "simple"

    def clone(self) -> "NodeRecord":
        return NodeRecord(
            uuid=self.uuid,
            type=self.type,
            fields=dict(self.fields),
            ui_position=dict(self.ui_position),
            ui_size=dict(self.ui_size) if self.ui_size else None,
            mode_variant=self.mode_variant,
        )


@dataclass
class DocumentModel:
    meta: MetaRecord = field(default_factory=MetaRecord)
    nodes: list[NodeRecord] = field(default_factory=list)
    connections: list[ConnectionRecord] = field(default_factory=list)
    canvas_view: CanvasViewState = field(default_factory=CanvasViewState)
    path: str | None = None


@dataclass
class ValidationIssue:
    node_uuid: str
    message: str
    severity: str = "warning"


@dataclass
class CsvPreviewRow:
    values: dict[str, Any]


@dataclass
class SearchHit:
    node_uuid: str
    node_type: str
    title: str
    field_name: str
    preview: str
