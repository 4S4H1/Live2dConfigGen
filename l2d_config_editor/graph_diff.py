"""Canonical, persistence-free structural diffs for editor documents."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping

from .models import CanvasStrokeRecord, DocumentModel


@dataclass(frozen=True)
class GraphDiffEntry:
    category: str
    change: str
    identity: str
    field_path: str = ""
    before: Any = None
    after: Any = None


@dataclass
class GraphDiff:
    entries: list[GraphDiffEntry] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.entries

    def counts(self) -> dict[str, int]:
        result = {"added": 0, "deleted": 0, "modified": 0}
        for entry in self.entries:
            result[entry.change] = result.get(entry.change, 0) + 1
        return result


def _json_value(value: Any) -> Any:
    """Return a stable JSON-compatible value without leaking mutable state."""

    if isinstance(value, Mapping):
        return {
            str(key): _json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, set):
        return sorted((_json_value(item) for item in value), key=repr)
    if isinstance(value, float):
        return round(value, 6)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return str(value)


def _stroke_snapshot(stroke: CanvasStrokeRecord) -> dict[str, Any]:
    points = [[round(float(x), 6), round(float(y), 6)] for x, y in stroke.points]
    encoded = json.dumps(points, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return {
        "color": stroke.color.upper(),
        "width": round(float(stroke.width), 6),
        "point_count": len(points),
        "points_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _image_content_digest(data_base64: str) -> str:
    try:
        payload = base64.b64decode(data_base64, validate=True)
    except (ValueError, TypeError):
        payload = str(data_base64 or "").encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()


def canonical_graph_snapshot(document: DocumentModel) -> dict[str, dict[str, Any]]:
    """Normalize graph content while excluding transient viewport/UI state."""

    nodes = {
        node.uuid: {
            "type": node.type,
            "fields": _json_value(node.fields),
            "position": _json_value(node.ui_position),
            "size": _json_value(node.ui_size),
            "locked": bool(node.locked),
            "sequence_locked": bool(node.sequence_locked),
            "type_slot": node.type_slot,
            "export_slot": node.export_slot,
            "numeric_linkage_enabled": bool(node.numeric_linkage_enabled),
            "manual_fields": sorted(node.manual_fields),
        }
        for node in document.nodes
        if node.type != "DrawFrame"
    }
    connections = {
        f"{connection.from_uuid}->{connection.to_uuid}": {
            "from_uuid": connection.from_uuid,
            "to_uuid": connection.to_uuid,
        }
        for connection in document.connections
    }
    groups = {
        group.uuid: {
            "title": group.title,
            "node_uuids": list(group.node_uuids),
            "theme_body_color": group.theme_body_color,
            "theme_border_color": group.theme_border_color,
            "theme_text_color": group.theme_text_color,
            "position": _json_value(group.ui_position),
            "size": _json_value(group.ui_size),
        }
        for group in document.groups
    }
    plan_topics = {
        topic.node_uuid: {
            "title": topic.plan_title,
            "parent_uuid": topic.parent_uuid,
            "order": int(topic.order),
            "collapsed": bool(topic.collapsed),
            "branch_color": topic.branch_color,
            "formalization_state": topic.formalization_state,
        }
        for topic in document.plan_layout.topics
    }
    images = {
        image.uuid: {
            "name": image.name,
            "mime_type": image.mime_type,
            "position": _json_value(image.ui_position),
            "size": _json_value(image.ui_size),
            "opacity": round(float(image.opacity), 6),
            "locked": bool(image.locked),
            "content_sha256": _image_content_digest(image.data_base64),
        }
        for image in document.canvas_images
    }
    return {
        "nodes": nodes,
        "connections": connections,
        "groups": groups,
        "plan_topics": plan_topics,
        "formal_strokes": {
            stroke.uuid: _stroke_snapshot(stroke) for stroke in document.canvas_strokes
        },
        "plan_strokes": {
            stroke.uuid: _stroke_snapshot(stroke) for stroke in document.plan_canvas_strokes
        },
        "images": images,
    }


def _append_modified_fields(
    entries: list[GraphDiffEntry],
    category: str,
    identity: str,
    before: Any,
    after: Any,
    prefix: str = "",
) -> None:
    if before == after:
        return
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            field_path = f"{prefix}.{key}" if prefix else key
            if key not in before:
                entries.append(GraphDiffEntry(category, "modified", identity, field_path, None, after[key]))
            elif key not in after:
                entries.append(GraphDiffEntry(category, "modified", identity, field_path, before[key], None))
            else:
                _append_modified_fields(
                    entries,
                    category,
                    identity,
                    before[key],
                    after[key],
                    field_path,
                )
        return
    entries.append(GraphDiffEntry(category, "modified", identity, prefix, before, after))


def diff_graph_snapshots(
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
) -> GraphDiff:
    entries: list[GraphDiffEntry] = []
    for category in sorted(set(before) | set(after)):
        old_items = before.get(category, {})
        new_items = after.get(category, {})
        for identity in sorted(set(old_items) | set(new_items)):
            if identity not in old_items:
                entries.append(
                    GraphDiffEntry(category, "added", identity, before=None, after=new_items[identity])
                )
            elif identity not in new_items:
                entries.append(
                    GraphDiffEntry(category, "deleted", identity, before=old_items[identity], after=None)
                )
            else:
                _append_modified_fields(
                    entries,
                    category,
                    identity,
                    old_items[identity],
                    new_items[identity],
                )
    return GraphDiff(entries)


def diff_documents(before: DocumentModel, after: DocumentModel) -> GraphDiff:
    return diff_graph_snapshots(
        canonical_graph_snapshot(before),
        canonical_graph_snapshot(after),
    )
