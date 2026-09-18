"""Compile listener subgraphs to the game's existing ``listener_data`` format.

No Lua is executed. The small literal reader also accepts old hand-written
listener tables so that editing an existing configuration does not require
rewriting it first. Runtime contract: CN/view/ship/live2ddrag.lua, commit
9353eba5b274bc3f994e04ba49f55d136317e202 of AzurLaneTools/AzurLaneLuaScripts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Any


@dataclass(frozen=True)
class ListenerIssue:
    part_uuid: str | None
    message: str
    severity: str = "error"


@dataclass
class CompileResult:
    listener_data: str = ""
    fields: dict[str, Any] = field(default_factory=dict)
    issues: list[ListenerIssue] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)


_EVENT_TYPES = {"ActionEvent": 1, "TouchEvent": 2, "IdleEvent": 3}
_PORTS = {
    "ActionEvent": ({}, {"event": "event"}),
    "TouchEvent": ({}, {"event": "event"}),
    "IdleEvent": ({}, {"event": "event"}),
    "AnyEvent": ({"event": "event"}, {"event": "event"}),
    "AddValue": ({"event": "event"}, {"change": "change"}),
    "SetValue": ({"event": "event"}, {"change": "change"}),
    "ValueState": ({"changes": "change"}, {"value": "value"}),
    "IdleRange": ({"value": "value"}, {}),
}


def _get(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _number_literal(value: float | int) -> str:
    number = float(value)
    return str(int(number)) if number.is_integer() else repr(number)


def _string_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t") + "'"


def compile_listener_graph(graph: Any) -> CompileResult:
    """Validate an editable draft and compile it without changing the draft.

    Invalid drafts return empty output and located issues rather than emitting
    a partial listener that silently drops unconnected components.
    """
    result = CompileResult()

    def issue(part: Any, message: str, severity: str = "error") -> None:
        part_id = part if isinstance(part, str) else _get(part, "uuid")
        result.issues.append(ListenerIssue(part_id, message, severity))

    nodes = _get(graph, "nodes", [])
    wires = _get(graph, "connections", [])
    if not isinstance(nodes, (list, tuple)) or not isinstance(wires, (list, tuple)):
        issue(None, "监听器的组件或连线格式不正确。")
        return result
    if len(nodes) > 512 or len(wires) > 2048:
        issue(None, "监听器组件或连线过多，请拆分监听器。")
        return result
    by_id: dict[str, Any] = {}
    for node in nodes:
        uid, kind = _get(node, "uuid"), _get(node, "kind")
        if not isinstance(uid, str) or not uid:
            issue(None, "组件缺少有效标识。")
        elif uid in by_id:
            issue(uid, "组件标识重复。")
        else:
            by_id[uid] = node
        if not isinstance(kind, str) or kind not in _PORTS:
            issue(node, f"不支持的监听器组件：{kind}。")
        if not isinstance(_get(node, "fields", {}), dict):
            issue(node, "组件字段格式不正确。")
    if not result.valid:
        return result
    incoming: dict[str, list[str]] = {uid: [] for uid in by_id}
    outgoing: dict[str, list[str]] = {uid: [] for uid in by_id}
    seen: set[tuple[str, str, str, str]] = set()
    for wire in wires:
        source, target = _get(wire, "from_uuid"), _get(wire, "to_uuid")
        source_port, target_port = _get(wire, "from_port"), _get(wire, "to_port")
        if not all(isinstance(value, str) for value in (source, target, source_port, target_port)):
            issue(None, "连线缺少组件或端口标识。")
            continue
        key = (source, source_port, target, target_port)
        if key in seen:
            issue(target, "同一端口之间存在重复连线。")
            continue
        seen.add(key)
        if source not in by_id or target not in by_id:
            issue(target if target in by_id else None, "连线引用了不存在的组件。")
            continue
        source_kind, target_kind = _get(by_id[source], "kind"), _get(by_id[target], "kind")
        output_type = _PORTS[source_kind][1].get(source_port)
        input_type = _PORTS[target_kind][0].get(target_port)
        if output_type is None or input_type is None or output_type != input_type:
            issue(target, "连线端口不存在或类型不匹配。")
            continue
        incoming[target].append(source)
        outgoing[source].append(target)

    states = [node for node in nodes if _get(node, "kind") == "ValueState"]
    if len(states) != 1:
        issue(None, "每个监听器需要且只能有一个参数状态组件。")
    for uid, node in by_id.items():
        kind = _get(node, "kind")
        if kind in ("AddValue", "SetValue", "IdleRange") and len(incoming[uid]) != 1:
            issue(node, "此组件需要且只能连接一个输入。")
        elif kind in ("AnyEvent", "ValueState") and not incoming[uid]:
            issue(node, "此组件至少需要一个输入。")
        if kind not in ("IdleRange", "ValueState") and not outgoing[uid]:
            issue(node, "此组件未连接到结果，请连接或删除。")
        if kind in ("AddValue", "SetValue") and len(outgoing[uid]) != 1:
            issue(node, "参数操作需要且只能连接一个参数状态。")

    # Iterative topological sort avoids recursion limits for malformed drafts.
    degrees = {uid: len(parents) for uid, parents in incoming.items()}
    ready = [uid for uid in by_id if degrees[uid] == 0]
    ordered: list[str] = []
    for uid in ready:
        ordered.append(uid)
        for target in outgoing[uid]:
            degrees[target] -= 1
            if degrees[target] == 0:
                ready.append(target)
    if len(ordered) != len(nodes):
        for uid, degree in degrees.items():
            if degree:
                issue(uid, "监听器内部不能形成循环连线。")
    if not result.valid:
        return result

    state = states[0]
    state_id = _get(state, "uuid")
    reachable = {state_id}
    pending = [state_id]
    for uid in pending:
        for neighbour in incoming[uid] + outgoing[uid]:
            if neighbour not in reachable:
                reachable.add(neighbour)
                pending.append(neighbour)
    for uid in by_id.keys() - reachable:
        issue(uid, "此组件未连接到参数状态。")

    def numeric(node: Any, key: str, default: Any = None, integer: bool = False) -> float | int:
        value = _finite_number(_get(node, "fields", {}).get(key, default))
        if value is None or (integer and not value.is_integer()):
            issue(node, f"{key} 必须是有限{'整数' if integer else '数值'}。")
            return 0
        return int(value) if integer else value

    event_sets: dict[str, tuple[set[int], list[str | int]]] = {}
    for uid in ordered:
        node, kind = by_id[uid], _get(by_id[uid], "kind")
        if kind in _EVENT_TYPES:
            raw_events = _get(node, "fields", {}).get("events", "")
            if not isinstance(raw_events, str):
                issue(node, "事件列表需要使用逗号或换行分隔的文本。")
                raw_events = ""
            tokens = [value.strip() for value in re.split(r"[,，\r\n]+", raw_events) if value.strip()]
            events: list[str | int] = []
            for token in tokens:
                value: str | int = token
                if kind == "IdleEvent":
                    number = _finite_number(token)
                    if number is None or not number.is_integer() or number < 0:
                        issue(node, "待机事件需要非负整数编号。")
                        continue
                    value = int(number)
                elif any(ord(char) < 32 for char in token):
                    issue(node, "事件名称不能包含控制字符。")
                    continue
                if value not in events:
                    events.append(value)
            if not events:
                issue(node, "请至少填写一个事件。")
            event_sets[uid] = ({_EVENT_TYPES[kind]}, events)
        elif kind == "AnyEvent":
            types: set[int] = set()
            combined: list[str | int] = []
            for source in incoming[uid]:
                source_types, events = event_sets.get(source, (set(), []))
                types.update(source_types)
                for event in events:
                    if event not in combined:
                        combined.append(event)
            if len(types) != 1:
                issue(node, "任选连接器只能组合相同类型的事件。")
            event_sets[uid] = (types, combined)

    state_fields = _get(state, "fields", {})
    parameter = state_fields.get("parameter", "")
    if not isinstance(parameter, str):
        issue(state, "L2D 参数名必须为文本。")
    elif not parameter.strip():
        issue(state, "未绑定 L2D 参数；数值只保存在此监听器内部。", "warning")
    lower = numeric(state, "minimum", 0)
    upper = numeric(state, "maximum", 1)
    initial = numeric(state, "start_value", 0)
    if lower > upper:
        issue(state, "参数下限不能大于上限。")
    if not lower <= initial <= upper:
        issue(state, "参数初始值必须位于上下限内。")
    save_parameter = state_fields.get("save_parameter", True)
    if not isinstance(save_parameter, (bool, int)) or save_parameter not in (0, 1):
        issue(state, "保存参数设置必须为开关值。")

    changes: list[tuple[int, int, str]] = []
    all_types: set[int] = set()
    all_events: list[str | int] = []
    for index, node in enumerate(nodes):
        uid, kind = _get(node, "uuid"), _get(node, "kind")
        if kind not in ("AddValue", "SetValue"):
            continue
        types, events = event_sets.get(incoming[uid][0], (set(), []))
        all_types.update(types)
        all_events.extend(events)
        amount = numeric(node, "value", 0)
        queue = numeric(node, "queue_index", 1, integer=True)
        order = numeric(node, "order", 0, integer=True)
        event_literal = ",".join(_string_literal(event) if isinstance(event, str) else str(event) for event in events)
        # Omission means reset queue index to 1; explicit 0/negative means keep it.
        queue_literal = "" if queue == 1 else f",{queue}"
        changes.append((order, index, "{" + f"{1 if kind == 'AddValue' else 2},{{{event_literal}}},{_number_literal(amount)}{queue_literal}" + "}"))
    if len(all_types) != 1:
        issue(state, "一个监听器只能监听一种事件类型；不同类型请拆分监听器。")

    ranges: list[tuple[int, int, float, float, int, Any]] = []
    for index, node in enumerate(nodes):
        if _get(node, "kind") != "IdleRange":
            continue
        low, high = numeric(node, "minimum", 0), numeric(node, "maximum", 1)
        idle, order = numeric(node, "idle", 0, integer=True), numeric(node, "order", 0, integer=True)
        if low >= high:
            issue(node, "待机映射下限必须小于上限（含下限、不含上限）。")
        if idle < 0:
            issue(node, "待机编号不能为负数。")
        ranges.append((order, index, low, high, idle, node))
    ranges.sort(key=lambda item: (item[0], item[1]))
    for index, (_, _, low, high, idle, node) in enumerate(ranges):
        if any(max(low, item[2]) < min(high, item[3]) for item in ranges[:index]):
            issue(node, "待机区间存在重叠；运行时使用顺序最后的匹配项。", "warning")
        if all_types == {3} and idle in all_events:
            issue(node, "结果待机会再次触发本监听器，可能递归切换；请检查事件与结果。", "warning")
    if not result.valid:
        return result
    changes.sort(key=lambda item: (item[0], item[1]))
    literal = "{type=" + str(next(iter(all_types))) + ",change={" + ",".join(item[2] for item in changes) + "}"
    if ranges:
        literal += ",apply={1,{" + ",".join("{" + f"{_number_literal(item[2])},{_number_literal(item[3])},{item[4]}" + "}" for item in ranges) + "}}"
    literal += "}"
    result.listener_data = literal
    result.fields = {
        "parameter": parameter.strip(), "mode": 1, "start_value": initial,
        "range": "{" + _number_literal(lower) + "," + _number_literal(upper) + "}",
        "revert": -1, "save_parameter": 0 if save_parameter else -1,
        "action_trigger": "{type=7}", "listener_data": literal,
        "draw_able_name": "", "offset_x": 0, "offset_y": 0, "gyro": 0,
        "smooth": 0, "revert_smooth": 0, "range_abs": 0, "drag_direct": 0,
    }
    return result


class ListenerImportError(ValueError):
    """The legacy literal cannot be represented without losing information."""


class _LiteralReader:
    def __init__(self, source: str):
        if not isinstance(source, str) or len(source) > 1_048_576:
            raise ListenerImportError("监听参数文本无效或过长。")
        self.source, self.pos = source, 0

    def whitespace(self) -> None:
        while self.pos < len(self.source) and self.source[self.pos].isspace():
            self.pos += 1

    def value(self, depth: int = 0) -> Any:
        self.whitespace()
        if depth > 64 or self.pos >= len(self.source):
            raise ListenerImportError("监听参数不是完整的 Lua 字面量。")
        char = self.source[self.pos]
        if char == "{":
            self.pos += 1
            array: list[Any] = []
            mapping: dict[str, Any] = {}
            self.whitespace()
            while self.pos < len(self.source) and self.source[self.pos] != "}":
                match = re.match(r"([A-Za-z_]\w*)\s*=", self.source[self.pos:])
                if match:
                    key = match.group(1)
                    if key in mapping:
                        raise ListenerImportError("监听参数包含重复键。")
                    self.pos += match.end()
                    mapping[key] = self.value(depth + 1)
                else:
                    array.append(self.value(depth + 1))
                self.whitespace()
                if self.pos < len(self.source) and self.source[self.pos] in ",;":
                    self.pos += 1
                    self.whitespace()
                elif self.pos >= len(self.source) or self.source[self.pos] != "}":
                    raise ListenerImportError("Lua 表项目之间需要逗号。")
            if self.pos >= len(self.source):
                raise ListenerImportError("Lua 表缺少右括号。")
            self.pos += 1
            if mapping and array:
                raise ListenerImportError("暂不支持混合命名和数组索引的 Lua 表。")
            return mapping if mapping else array
        if char in "\"'":
            self.pos += 1
            parts: list[str] = []
            while self.pos < len(self.source):
                current = self.source[self.pos]
                self.pos += 1
                if current == char:
                    return "".join(parts)
                if current == "\\":
                    if self.pos >= len(self.source):
                        break
                    escape = self.source[self.pos]
                    self.pos += 1
                    if escape not in {"n", "r", "t", "\\", "'", '"'}:
                        raise ListenerImportError("暂不支持此 Lua 字符串转义。")
                    current = {"n": "\n", "r": "\r", "t": "\t"}.get(escape, escape)
                elif ord(current) < 32:
                    raise ListenerImportError("Lua 字符串含未转义控制字符。")
                parts.append(current)
            raise ListenerImportError("Lua 字符串没有结束引号。")
        match = re.match(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", self.source[self.pos:])
        if match:
            self.pos += match.end()
            number = _finite_number(match.group())
            if number is None:
                raise ListenerImportError("Lua 数值必须有限。")
            return int(number) if number.is_integer() else number
        for keyword, value in (("true", True), ("false", False)):
            if self.source.startswith(keyword, self.pos):
                self.pos += len(keyword)
                return value
        raise ListenerImportError("只支持 Lua 字面量，不能包含表达式或函数。")


def parse_listener_literal(source: str) -> Any:
    reader = _LiteralReader(source)
    value = reader.value()
    reader.whitespace()
    if reader.pos != len(source):
        raise ListenerImportError("Lua 字面量后存在额外内容。")
    return value


def import_listener_graph(listener_data: str, fields: dict[str, Any] | None = None):
    """Create a subgraph from a supported legacy table, or explain why not.

    Unknown keys/operations are rejected instead of being silently lost. The
    caller retains the host's normal interaction fields (action_trigger, motion
    smoothing, direction restrictions, and so on); this graph models only its
    listener, parameter bounds and persistence preference.
    """
    from uuid import uuid4
    from .listener_graph import ListenerGraph, ListenerPart, ListenerWire

    table = parse_listener_literal(listener_data)
    if not isinstance(table, dict) or set(table) - {"type", "change", "apply"}:
        raise ListenerImportError("监听参数含不支持的字段，已保留原始内容。")
    event_type = table.get("type")
    if isinstance(event_type, bool) or event_type not in (1, 2, 3):
        raise ListenerImportError("仅支持动作播放、触区点击和待机切换监听。")
    changes = table.get("change")
    if not isinstance(changes, list) or not changes:
        raise ListenerImportError("监听参数缺少变化规则。")
    if len(changes) > 255:
        raise ListenerImportError("监听参数变化规则过多，请拆分监听器。")
    host = fields or {}
    if not isinstance(host, dict):
        raise ListenerImportError("宿主字段格式不正确。")
    raw_range = host.get("range", "{0,1}")
    bounds = parse_listener_literal(raw_range) if isinstance(raw_range, str) else raw_range
    if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
        raise ListenerImportError("宿主参数范围需要两个数值。")

    def finite(value: Any) -> float:
        number = _finite_number(value)
        if number is None:
            raise ListenerImportError("监听参数包含无效数值。")
        return number

    def integer(value: Any) -> int:
        number = finite(value)
        if not number.is_integer():
            raise ListenerImportError("待机和队列编号必须为整数。")
        return int(number)

    state = ListenerPart(uuid4().hex, "ValueState", {
        "parameter": host.get("parameter", "listener_value1"),
        "minimum": finite(bounds[0]), "maximum": finite(bounds[1]),
        "start_value": finite(host.get("start_value", 0)),
        "save_parameter": host.get("save_parameter", 0) not in (-1, "-1"),
    }, {"x": 680.0, "y": 80.0})
    graph = ListenerGraph(nodes=[state])
    event_kind = {1: "ActionEvent", 2: "TouchEvent", 3: "IdleEvent"}[event_type]
    for index, rule in enumerate(changes):
        if not isinstance(rule, list) or len(rule) not in (3, 4):
            raise ListenerImportError("变化规则需要操作、事件列表、数值和可选队列位置。")
        if isinstance(rule[0], bool) or rule[0] not in (1, 2):
            raise ListenerImportError("变化规则仅支持累加和设置参数。")
        if not isinstance(rule[1], list) or not rule[1]:
            raise ListenerImportError("变化规则的事件列表不能为空。")
        events: list[str] = []
        for event in rule[1]:
            if event_type == 3:
                value = integer(event)
                if value < 0:
                    raise ListenerImportError("待机编号不能为负数。")
                events.append(str(value))
            else:
                if not isinstance(event, str) or not event or event != event.strip() or any(char in event for char in ",，\r\n") or any(ord(char) < 32 for char in event):
                    raise ListenerImportError("事件名称包含无法无损导入的分隔符或空白。")
                events.append(event)
        event_part = ListenerPart(uuid4().hex, event_kind, {"events": ", ".join(events)}, {"x": 0.0, "y": index * 230.0 + 80.0})
        change_part = ListenerPart(uuid4().hex, "AddValue" if rule[0] == 1 else "SetValue", {
            "value": finite(rule[2]), "queue_index": integer(rule[3]) if len(rule) == 4 else 1,
            "order": index,
        }, {"x": 340.0, "y": index * 230.0 + 80.0})
        graph.nodes.extend((event_part, change_part))
        graph.connections.extend((
            ListenerWire(event_part.uuid, "event", change_part.uuid, "event"),
            ListenerWire(change_part.uuid, "change", state.uuid, "changes"),
        ))
    apply = table.get("apply", [])
    if apply:
        if not isinstance(apply, list) or len(apply) != 2 or isinstance(apply[0], bool) or apply[0] != 1 or not isinstance(apply[1], list):
            raise ListenerImportError("结果规则仅支持参数区间切换待机。")
        if len(graph.nodes) + len(apply[1]) > 512:
            raise ListenerImportError("监听参数结果规则过多，请拆分监听器。")
        for index, rule in enumerate(apply[1]):
            if not isinstance(rule, list) or len(rule) != 3:
                raise ListenerImportError("待机区间需要下限、上限和待机编号。")
            range_part = ListenerPart(uuid4().hex, "IdleRange", {
                "minimum": finite(rule[0]), "maximum": finite(rule[1]),
                "idle": integer(rule[2]), "order": index,
            }, {"x": 1020.0, "y": index * 230.0 + 80.0})
            graph.nodes.append(range_part)
            graph.connections.append(ListenerWire(state.uuid, "value", range_part.uuid, "value"))
    elif not isinstance(apply, list):
        raise ListenerImportError("结果规则格式不正确。")
    compiled = compile_listener_graph(graph)
    if not compiled.valid:
        raise ListenerImportError("；".join(issue.message for issue in compiled.issues if issue.severity == "error"))
    graph.to_payload()  # Enforce persistence bounds before offering an editable graph.
    return graph
