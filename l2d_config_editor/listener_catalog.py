"""Components supported by the game's listener_data interpreter.

These are declarative building blocks of one listener, not a general-purpose
execution language. See docs/LISTENER_BLUEPRINTS.md for the runtime reference.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass

from .schema import FieldSchema


@dataclass(frozen=True)
class PortSpec:
    key: str
    label: str
    data_type: str
    multiple: bool = False


@dataclass(frozen=True)
class ComponentSpec:
    kind: str
    title: str
    category: str
    color: str
    fields: tuple[FieldSchema, ...] = ()
    inputs: tuple[PortSpec, ...] = ()
    outputs: tuple[PortSpec, ...] = ()


def _field(key, label, editor="text", default="", placeholder=""):
    return FieldSchema(key=key, label=label, editor=editor, default=default,
                       show_in_modes=("simple", "advanced"), placeholder=placeholder)


_EVENT_OUT = (PortSpec("event", "触发", "event"),)
_EVENT_IN = (PortSpec("event", "触发", "event"),)
_CHANGE_OUT = (PortSpec("change", "参数变化", "change"),)
_ORDER = _field("order", "执行顺序（小的先执行）", "int", 0)
_QUEUE = _field("queue_index", "动作队列位置（≤0 保持原位置）", "int", 1)

COMPONENTS = {
    "ActionEvent": ComponentSpec("ActionEvent", "动作播放", "触发源", "#5B9EF5",
        (_field("events", "动作名称", default="touch_head", placeholder="多个名称用逗号或换行分隔，区分大小写"),), outputs=_EVENT_OUT),
    "TouchEvent": ComponentSpec("TouchEvent", "触区点击", "触发源", "#55BAA5",
        (_field("events", "触区名称", default="TouchDrag1", placeholder="例如 TouchDrag1, TouchDrag2；区分大小写"),), outputs=_EVENT_OUT),
    "IdleEvent": ComponentSpec("IdleEvent", "待机切换", "触发源", "#8B97EF",
        (_field("events", "待机编号", default="0", placeholder="例如 0, 1, 2"),), outputs=_EVENT_OUT),
    "AnyEvent": ComponentSpec("AnyEvent", "任一事件", "连接器", "#A187D7",
        inputs=(PortSpec("event", "同类事件", "event", multiple=True),), outputs=_EVENT_OUT),
    "AddValue": ComponentSpec("AddValue", "累加参数", "参数操作", "#DDA55B",
        (_field("value", "累加量（可为负数）", "float", 1.0), _QUEUE, _ORDER),
        inputs=_EVENT_IN, outputs=_CHANGE_OUT),
    "SetValue": ComponentSpec("SetValue", "设置参数", "参数操作", "#DDA55B",
        (_field("value", "设置为", "float", 0.0), _QUEUE, _ORDER),
        inputs=_EVENT_IN, outputs=_CHANGE_OUT),
    "ValueState": ComponentSpec("ValueState", "监听参数", "状态", "#CF829B",
        (_field("parameter", "Live2D 参数名", default="listener_value1"),
         _field("start_value", "初始值", "float", 0.0),
         _field("minimum", "参数下限", "float", 0.0),
         _field("maximum", "参数上限", "float", 1.0),
         _field("save_parameter", "保存参数值", "bool", 0)),
        inputs=(PortSpec("changes", "变化规则", "change", multiple=True),),
        outputs=(PortSpec("value", "参数值", "value"),)),
    "IdleRange": ComponentSpec("IdleRange", "区间切换待机", "结果", "#69BD82",
        (_field("minimum", "区间下限（包含）", "float", 0.0),
         _field("maximum", "区间上限（不包含）", "float", 1.0),
         _field("idle", "切换到待机编号", "int", 0), _ORDER),
        inputs=(PortSpec("value", "参数值", "value"),)),
}


def default_fields(kind: str) -> dict:
    return {field.key: copy.deepcopy(field.default) for field in COMPONENTS[kind].fields}


def new_listener_graph(parameter: str = "listener_value1"):
    from uuid import uuid4
    from .listener_graph import ListenerGraph, ListenerPart, ListenerWire

    parts = [ListenerPart(uuid4().hex, kind, default_fields(kind), {"x": x, "y": 80.0})
             for kind, x in (("ActionEvent", 0.0), ("AddValue", 340.0), ("ValueState", 680.0))]
    parts[-1].fields["parameter"] = parameter
    return ListenerGraph(nodes=parts, connections=[
        ListenerWire(parts[0].uuid, "event", parts[1].uuid, "event"),
        ListenerWire(parts[1].uuid, "change", parts[2].uuid, "changes"),
    ])
