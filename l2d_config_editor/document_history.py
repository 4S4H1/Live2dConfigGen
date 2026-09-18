"""Bounded reverse deltas anchored in the JSON's current graph, not full copies.

History is optional v5 editor metadata. Old documents need no migration; old
editors can still read the graph (but may discard this optional extension).
No compression/decompression or eager replay is needed on the startup path.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

HISTORY_VERSION = 1
MAX_HISTORY_VERSIONS = 50
MAX_HISTORY_BYTES = 512 * 1024
_COLLECTIONS = {
    "nodes": "uuid", "groups": "uuid", "canvas_images": "uuid",
    "canvas_strokes": "id", "plan_canvas_strokes": "id", "connections": None,
}


def _indexed(items: list, key: str | None) -> dict:
    pairs = [(str(item[key]) if key else f"{item['from_uuid']}->{item['to_uuid']}", item) for item in items]
    return {"order": [identity for identity, _ in pairs], "items": dict(pairs)}


def history_snapshot(payload: dict) -> dict:
    value = copy.deepcopy({key: item for key, item in payload.items()
                           if key not in {"history", "canvas_view", "global_mode"}})
    for name, key in _COLLECTIONS.items():
        value[name] = _indexed(value.get(name, []), key)
    plan = value.get("plan_layout", {})
    plan.pop("view", None)
    plan["topics"] = _indexed(plan.get("topics", []), "node_uuid")
    value["plan_layout"] = plan
    return value


def snapshot_payload(snapshot: dict) -> dict:
    value = copy.deepcopy(snapshot)
    for name in _COLLECTIONS:
        collection = value[name]
        value[name] = [collection["items"][key] for key in collection["order"]]
    topics = value["plan_layout"]["topics"]
    value["plan_layout"]["topics"] = [topics["items"][key] for key in topics["order"]]
    return value


def snapshot_digest(snapshot: dict) -> str:
    return hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def history_size(history: dict) -> int:
    # Include the enclosing document's additional indentation in the bound.
    return len(json.dumps({"history": history}, ensure_ascii=False, indent=2).encode("utf-8"))


def read_history(value: Any) -> dict:
    """Validate the small envelope only; deltas are verified on demand."""
    if value is None:
        return {}
    if not isinstance(value, dict) or history_size(value) > MAX_HISTORY_BYTES:
        return {}
    if value.get("version") != HISTORY_VERSION:
        return copy.deepcopy(value)  # Preserve an unknown extension, without interpreting it.
    entries = value.get("revisions")
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_HISTORY_VERSIONS:
        return {}
    for entry in entries:
        if (not isinstance(entry, dict) or not isinstance(entry.get("reverse"), list)
                or not isinstance(entry.get("digest"), str)
                or not isinstance(entry.get("id"), str)
                or not isinstance(entry.get("saved_at"), str)
                or not isinstance(entry.get("author"), str)):
            return {}
    return copy.deepcopy(value)


def _reverse_delta(before: Any, after: Any, path: list[str], result: list) -> None:
    if before == after:
        return
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(before.keys() | after.keys()):
            location = [*path, key]
            if key not in before:
                result.append({"path": location, "exists": False})
            elif key not in after:
                result.append({"path": location, "exists": True, "value": copy.deepcopy(before[key])})
            else:
                _reverse_delta(before[key], after[key], location, result)
    else:
        result.append({"path": path, "exists": True, "value": copy.deepcopy(before)})


def _revision(snapshot: dict, reverse: list, author: str, label: str) -> dict:
    return {"id": uuid4().hex, "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "author": str(author)[:160], "label": label, "digest": snapshot_digest(snapshot), "reverse": reverse}


def append_history(history: dict, before: dict | None, after: dict, author: str) -> dict:
    if history and history.get("version") != HISTORY_VERSION:
        return copy.deepcopy(history)
    entries = copy.deepcopy(history.get("revisions", []))
    if entries and (before is None or entries[-1]["digest"] != snapshot_digest(before)):
        entries = []  # Stale history after a non-history-aware edit: start a new, truthful baseline.
    if not entries:
        entries.append(_revision(before if before is not None else after, [], author, "历史基线"))
    if before is not None and before != after:
        reverse: list = []
        _reverse_delta(before, after, [], reverse)
        entries.append(_revision(after, reverse, author, "保存"))
    result = {"version": HISTORY_VERSION, "revisions": entries}
    while len(entries) > MAX_HISTORY_VERSIONS or (history_size(result) > MAX_HISTORY_BYTES and len(entries) > 1):
        entries.pop(0)
        entries[0]["reverse"] = []
        entries[0]["label"] = "保留历史起点"
    return result


def history_is_anchored(history: dict, snapshot: dict) -> bool:
    return bool(history.get("version") == HISTORY_VERSION and history.get("revisions")
                and history["revisions"][-1]["digest"] == snapshot_digest(snapshot))


def revision_snapshot(history: dict, current: dict, index: int) -> dict:
    if not history_is_anchored(history, current):
        raise ValueError("历史与当前文件不匹配，无法还原；下次保存将建立新的历史基线。")
    entries = history["revisions"]
    if not 0 <= index < len(entries):
        raise ValueError("历史版本不存在")
    value = copy.deepcopy(current)
    try:
        for number in range(len(entries) - 1, index, -1):
            for operation in entries[number]["reverse"]:
                path = operation["path"]
                if not isinstance(path, list) or not path or not all(isinstance(key, str) for key in path):
                    raise ValueError("历史路径无效")
                parent = value
                for key in path[:-1]:
                    parent = parent[key]
                if operation["exists"] is True:
                    parent[path[-1]] = copy.deepcopy(operation["value"])
                elif operation["exists"] is False:
                    del parent[path[-1]]
                else:
                    raise ValueError("历史操作无效")
            if snapshot_digest(value) != entries[number - 1]["digest"]:
                raise ValueError("历史内容校验失败")
    except (KeyError, TypeError, IndexError) as exc:
        raise ValueError("历史记录损坏，当前图表不受影响") from exc
    return value
