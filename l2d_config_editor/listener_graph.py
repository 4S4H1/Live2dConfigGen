"""Bounded, Qt-independent records for an editable listener subgraph.

The graph intentionally knows nothing about runtime listener semantics. Unknown
part kinds and ports survive loading; the compiler checks their meaning. Drafts
may contain disconnected parts and cycles, but broken references and malformed
JSON are rejected at serialization boundaries.

Collections are persisted as identity-keyed objects so document history can
record an individual field edit without copying an entire node/connection list.
Explicit order lists preserve order when a reverse history delta restores a
previously removed object. Fields stay nested and cannot replace record keys.
"""

from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


LISTENER_GRAPH_VERSION = 1
MAX_LISTENER_NODES = 512
MAX_LISTENER_CONNECTIONS = 2048
MAX_LISTENER_COORDINATE = 1_000_000.0
MAX_LISTENER_FIELD_DEPTH = 12
MAX_LISTENER_FIELD_VALUES = 4096
MAX_LISTENER_FIELD_BYTES = 64 * 1024
MAX_LISTENER_GRAPH_BYTES = 2 * 1024 * 1024
_IDENTIFIER = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label}必须为 JSON 对象")
    return value


def _keys(value: dict, allowed: set[str], label: str, required: set[str] | None = None) -> None:
    if set(value) - allowed:
        raise ValueError(f"{label}包含未知结构字段")
    if required and required - set(value):
        raise ValueError(f"{label}缺少必要字段")


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{label}必须是 1–128 位字母、数字、点、下划线或连字符")
    return value


def _number(value: Any, label: str, *, minimum: float = -MAX_LISTENER_COORDINATE,
            maximum: float = MAX_LISTENER_COORDINATE) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}必须为有限数值")
    try:
        result = float(value)
    except (ValueError, OverflowError) as error:
        raise ValueError(f"{label}必须为有限数值") from error
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(f"{label}超出允许范围")
    return result


def _encoded_size(value: Any, label: str) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                              allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise ValueError(f"{label}不是有效 JSON") from error


def _fields(value: Any) -> dict[str, Any]:
    """Validate before copying/encoding to bound depth and traversal work."""
    _object(value, "监听器节点字段")
    remaining = MAX_LISTENER_FIELD_VALUES
    active: set[int] = set()

    def visit(item: Any, depth: int) -> None:
        nonlocal remaining
        remaining -= 1
        if remaining < 0:
            raise ValueError("监听器节点字段数量超出限制")
        if depth > MAX_LISTENER_FIELD_DEPTH:
            raise ValueError("监听器节点字段嵌套过深")
        if item is None or isinstance(item, bool):
            return
        if isinstance(item, str):
            if len(item) > MAX_LISTENER_FIELD_BYTES:
                raise ValueError("监听器节点字段体积超出限制")
            return
        if isinstance(item, int):
            # Avoid passing pathological integers to the decimal JSON encoder.
            if item.bit_length() > MAX_LISTENER_FIELD_BYTES * 3:
                raise ValueError("监听器节点字段体积超出限制")
            return
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("监听器节点字段不能包含非有限数值")
            return
        if not isinstance(item, (dict, list)):
            raise ValueError("监听器节点字段只能包含 JSON 数据")
        if len(item) > MAX_LISTENER_FIELD_VALUES:
            raise ValueError("监听器节点字段数量超出限制")
        identity = id(item)
        if identity in active:
            raise ValueError("监听器节点字段不能循环引用")
        active.add(identity)
        try:
            if isinstance(item, dict):
                for key, child in item.items():
                    if not isinstance(key, str):
                        raise ValueError("监听器节点字段键必须为文本")
                    visit(key, depth + 1)
                    visit(child, depth + 1)
            else:
                for child in item:
                    visit(child, depth + 1)
        finally:
            active.remove(identity)

    visit(value, 0)
    if _encoded_size(value, "监听器节点字段") > MAX_LISTENER_FIELD_BYTES:
        raise ValueError("监听器节点字段体积超出限制")
    return copy.deepcopy(value)


def _position(value: Any) -> dict[str, float]:
    value = _object(value, "监听器节点位置")
    _keys(value, {"x", "y"}, "监听器节点位置", {"x", "y"})
    return {key: _number(value[key], f"监听器节点坐标 {key}") for key in ("x", "y")}


def _view(value: Any) -> dict[str, float]:
    value = _object(value, "监听器视角")
    _keys(value, {"scale", "offset_x", "offset_y"}, "监听器视角")
    return {
        "scale": _number(value.get("scale", 1.0), "监听器缩放", minimum=0.01, maximum=100.0),
        "offset_x": _number(value.get("offset_x", 0.0), "监听器视角 X"),
        "offset_y": _number(value.get("offset_y", 0.0), "监听器视角 Y"),
    }


@dataclass
class ListenerPart:
    uuid: str
    kind: str
    fields: dict[str, Any] = field(default_factory=dict)
    ui_position: dict[str, float] = field(default_factory=lambda: {"x": 0.0, "y": 0.0})

    def clone(self) -> "ListenerPart":
        return copy.deepcopy(self)

    def to_payload(self) -> dict[str, Any]:
        return {
            "uuid": _identifier(self.uuid, "监听器节点标识"),
            "kind": _identifier(self.kind, "监听器节点类型"),
            "fields": _fields(self.fields),
            "ui_position": _position(self.ui_position),
        }

    @classmethod
    def from_payload(cls, payload: Any, *, uuid: str | None = None) -> "ListenerPart":
        value = _object(payload, "监听器节点")
        _keys(value, {"uuid", "kind", "fields", "ui_position"}, "监听器节点", {"kind"})
        identity = _identifier(uuid if uuid is not None else value.get("uuid"), "监听器节点标识")
        if "uuid" in value and value["uuid"] != identity:
            raise ValueError("监听器节点标识与集合键不一致")
        return cls(
            uuid=identity,
            kind=_identifier(value["kind"], "监听器节点类型"),
            fields=_fields(value.get("fields", {})),
            ui_position=_position(value.get("ui_position", {"x": 0.0, "y": 0.0})),
        )


@dataclass(frozen=True)
class ListenerWire:
    from_uuid: str
    from_port: str
    to_uuid: str
    to_port: str

    @property
    def identity(self) -> str:
        """Stable, unambiguous identity including both ports."""
        return json.dumps([self.from_uuid, self.from_port, self.to_uuid, self.to_port],
                          ensure_ascii=False, separators=(",", ":"))

    def clone(self) -> "ListenerWire":
        return copy.deepcopy(self)

    def to_payload(self) -> dict[str, str]:
        return {key: _identifier(getattr(self, key), f"监听器连线 {key}")
                for key in ("from_uuid", "from_port", "to_uuid", "to_port")}

    @classmethod
    def from_payload(cls, payload: Any) -> "ListenerWire":
        value = _object(payload, "监听器连线")
        names = {"from_uuid", "from_port", "to_uuid", "to_port"}
        _keys(value, names, "监听器连线", names)
        return cls(**{key: _identifier(value[key], f"监听器连线 {key}") for key in names})


def _ordered_ids(value: Any, members: dict, label: str) -> list[str]:
    if value is None:
        return list(members)
    if (not isinstance(value, list) or len(value) != len(members)
            or any(not isinstance(item, str) for item in value)
            or len(set(value)) != len(value) or set(value) != set(members)):
        raise ValueError(f"{label}必须恰好包含集合中的每个标识一次")
    return list(value)


@dataclass
class ListenerGraph:
    version: int = LISTENER_GRAPH_VERSION
    nodes: list[ListenerPart] = field(default_factory=list)
    connections: list[ListenerWire] = field(default_factory=list)
    view: dict[str, float] = field(default_factory=lambda: {
        "scale": 1.0, "offset_x": 0.0, "offset_y": 0.0,
    })

    def clone(self) -> "ListenerGraph":
        return copy.deepcopy(self)

    def to_payload(self) -> dict[str, Any]:
        """Return a detached canonical payload, rejecting structural damage."""
        if type(self.version) is not int or self.version != LISTENER_GRAPH_VERSION:
            raise ValueError(f"不支持的监听器子图版本：{self.version!r}")
        if not isinstance(self.nodes, list) or len(self.nodes) > MAX_LISTENER_NODES:
            raise ValueError("监听器子图节点数量超出限制或格式无效")
        if not isinstance(self.connections, list) or len(self.connections) > MAX_LISTENER_CONNECTIONS:
            raise ValueError("监听器子图连线数量超出限制或格式无效")
        nodes: dict[str, dict[str, Any]] = {}
        byte_count = 0
        for part in self.nodes:
            if not isinstance(part, ListenerPart):
                raise ValueError("监听器子图包含无效节点")
            value = part.to_payload()
            identity = value.pop("uuid")
            if identity in nodes:
                raise ValueError("监听器子图节点标识重复")
            byte_count += _encoded_size(value, "监听器节点")
            if byte_count > MAX_LISTENER_GRAPH_BYTES:
                raise ValueError("监听器子图体积超出限制")
            nodes[identity] = value
        connections: dict[str, dict[str, str]] = {}
        for wire in self.connections:
            if not isinstance(wire, ListenerWire):
                raise ValueError("监听器子图包含无效连线")
            value = wire.to_payload()
            if wire.from_uuid not in nodes or wire.to_uuid not in nodes:
                raise ValueError("监听器子图连线引用不存在的节点")
            identity = wire.identity
            if identity in connections:
                raise ValueError("监听器子图连线重复")
            connections[identity] = value
        payload = {
            "version": self.version,
            "nodes": nodes,
            "node_order": list(nodes),
            "connections": connections,
            "connection_order": list(connections),
            "view": _view(self.view),
        }
        if _encoded_size(payload, "监听器子图") > MAX_LISTENER_GRAPH_BYTES:
            raise ValueError("监听器子图体积超出限制")
        return payload

    @classmethod
    def from_payload(cls, payload: Any) -> "ListenerGraph":
        value = _object(payload, "监听器子图")
        _keys(value, {"version", "nodes", "node_order", "connections", "connection_order", "view"},
              "监听器子图", {"version", "nodes", "connections"})
        version = value["version"]
        if type(version) is not int or version != LISTENER_GRAPH_VERSION:
            raise ValueError(f"不支持的监听器子图版本：{version!r}")
        nodes = _object(value["nodes"], "监听器节点集合")
        connections = _object(value["connections"], "监听器连线集合")
        if len(nodes) > MAX_LISTENER_NODES or len(connections) > MAX_LISTENER_CONNECTIONS:
            raise ValueError("监听器子图节点或连线数量超出限制")
        node_order = _ordered_ids(value.get("node_order"), nodes, "监听器节点顺序")
        wire_order = _ordered_ids(value.get("connection_order"), connections, "监听器连线顺序")
        parts = []
        byte_count = 0
        for identity in node_order:
            part = ListenerPart.from_payload(nodes[identity], uuid=identity)
            byte_count += _encoded_size(part.fields, "监听器节点字段")
            if byte_count > MAX_LISTENER_GRAPH_BYTES:
                raise ValueError("监听器子图体积超出限制")
            parts.append(part)
        wires = []
        for identity in wire_order:
            wire = ListenerWire.from_payload(connections[identity])
            if identity != wire.identity:
                raise ValueError("监听器连线标识与集合键不一致")
            wires.append(wire)
        graph = cls(version=version, nodes=parts, connections=wires, view=_view(value.get("view", {})))
        # One shared boundary also catches dangling references and total size.
        graph.to_payload()
        return graph

    def remap_ids(self) -> "ListenerGraph":
        """Copy the subgraph with fresh internal identities and matching wires.

        Arbitrary field strings are never rewritten: they may be runtime event
        names rather than graph references. Typed semantic references belong in
        a compiler/catalog migration, not a blanket string replacement.
        """
        result = ListenerGraph.from_payload(self.to_payload())
        mapping = {part.uuid: str(uuid4()) for part in result.nodes}
        for part in result.nodes:
            part.uuid = mapping[part.uuid]
        result.connections = [ListenerWire(mapping[wire.from_uuid], wire.from_port,
                                           mapping[wire.to_uuid], wire.to_port)
                              for wire in result.connections]
        return result
