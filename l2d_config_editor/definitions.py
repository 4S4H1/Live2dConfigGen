"""Node and field definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


VisibilityRule = Callable[[dict[str, Any], str], bool]


@dataclass(frozen=True)
class Option:
    label: str
    value: Any


@dataclass(frozen=True)
class FieldDefinition:
    key: str
    label: str
    editor: str
    default: Any = ""
    modes: tuple[str, ...] = ("simple", "advanced")
    options: tuple[Option, ...] = ()
    read_only: bool = False
    multiline: bool = False
    visible_if: VisibilityRule | None = None
    placeholder: str = ""


@dataclass(frozen=True)
class NodeDefinition:
    type_name: str
    title: str
    fields: tuple[FieldDefinition, ...]
    resizable: bool = False
    copyable: bool = True


CLICK_DRAG_OPTIONS = (
    Option("点击", "click"),
    Option("拖拽", "drag"),
)

DRAG_DIRECTION_OPTIONS = (
    Option("无", ""),
    Option("上", "up"),
    Option("下", "down"),
    Option("左", "left"),
    Option("右", "right"),
)

TRANSITION_OPTIONS = (
    Option("动画", "animated"),
    Option("硬切", "hard"),
)

MODE_OPTIONS = (
    Option("覆盖", 1),
    Option("加法", 2),
    Option("乘法", 3),
)

DRAG_DIRECT_OPTIONS = (
    Option("0 不适用", 0),
    Option("1 正向", 1),
    Option("2 负向", 2),
)

RESULT_OPTIONS = (
    Option("播放动画", "action"),
    Option("数值变化", "value"),
)


def when_drag(fields: dict[str, Any], _: str) -> bool:
    return fields.get("control_type") == "drag"


def when_animated(fields: dict[str, Any], mode: str) -> bool:
    if mode == "advanced":
        return True
    return fields.get("result_type", "action") == "action"


def when_value_result(fields: dict[str, Any], mode: str) -> bool:
    return mode == "advanced" or fields.get("result_type") == "value"


def always(_: dict[str, Any], __: str) -> bool:
    return True


NODE_DEFINITIONS = {
    "Initial": NodeDefinition(
        type_name="Initial",
        title="Initial",
        copyable=False,
        fields=(
            FieldDefinition("tips", "备注", "text", ""),
            FieldDefinition("version", "版本", "date", "2099-09-09"),
            FieldDefinition("defaultState", "默认状态", "text", "idle0"),
            FieldDefinition("CharName", "角色名", "text", ""),
            FieldDefinition("memo", "角色资源名", "text", ""),
            FieldDefinition("ship_skin_id", "角色ID", "int", 0),
            FieldDefinition("react_condition", "允许目光拖拽的待机", "int", 0),
            FieldDefinition("author", "作者", "text", ""),
        ),
    ),
    "TouchIdle": NodeDefinition(
        type_name="TouchIdle",
        title="TouchIdle",
        fields=(
            FieldDefinition("tips", "备注", "text", "", modes=("simple",)),
            FieldDefinition("draw_able_name", "框", "text", "", modes=("simple", "advanced")),
            FieldDefinition("parameter", "参数", "text", "Paramtouch_idle1", modes=("simple", "advanced")),
            FieldDefinition("range", "参数范围", "range", "{0,1}", modes=("simple", "advanced")),
            FieldDefinition("control_type", "交互类型", "combo", "click", modes=("simple",), options=CLICK_DRAG_OPTIONS),
            FieldDefinition(
                "drag_ui_direction",
                "拖拽方向",
                "combo",
                "",
                modes=("simple",),
                options=DRAG_DIRECTION_OPTIONS,
                visible_if=when_drag,
            ),
            FieldDefinition(
                "transition_type",
                "过渡形式",
                "combo",
                "animated",
                modes=("simple",),
                options=TRANSITION_OPTIONS,
            ),
            FieldDefinition("target_idle", "目标idle", "int", 1, modes=("simple",)),
            FieldDefinition("auto_preview", "自动生成区", "readonly", "", modes=("simple",), read_only=True),
            FieldDefinition("id", "ID", "int", 0, modes=("advanced",), read_only=True),
            FieldDefinition("mode", "数值模式", "combo", 1, modes=("advanced",), options=MODE_OPTIONS),
            FieldDefinition("start_value", "初始值", "int", 0, modes=("advanced",)),
            FieldDefinition("parts_data", "数值吸附范围", "text", "", modes=("advanced",)),
            FieldDefinition("ignore_react", "屏蔽目光跟随", "bool", 1, modes=("advanced",)),
            FieldDefinition("ignore_action", "非待机状态不可拖动", "bool", 1, modes=("advanced",)),
            FieldDefinition("range_abs", "使用绝对值", "bool", 1, modes=("advanced",)),
            FieldDefinition("drag_direct", "方向响应", "combo", 0, modes=("advanced",), options=DRAG_DIRECT_OPTIONS),
            FieldDefinition("react_pos_x", "眼神跟随X", "nullable_int", None, modes=("advanced",)),
            FieldDefinition("react_pos_y", "眼神跟随Y", "nullable_int", None, modes=("advanced",)),
            FieldDefinition("offset_x", "左右移动影响拖拽", "int", 0, modes=("advanced",)),
            FieldDefinition("offset_y", "上下移动影响拖拽", "int", 0, modes=("advanced",)),
            FieldDefinition("smooth", "开始拖拽响应系数", "int", 100, modes=("advanced",)),
            FieldDefinition("revert_smooth", "回弹拖拽响应系数", "int", 100, modes=("advanced",)),
            FieldDefinition("revert", "回弹延迟", "int", -1, modes=("advanced",)),
            FieldDefinition("gyro", "陀螺仪启用", "bool", 0, modes=("advanced",)),
            FieldDefinition("gyro_x", "陀螺仪X轴", "int", 0, modes=("advanced",)),
            FieldDefinition("gyro_y", "陀螺仪Y轴", "int", 0, modes=("advanced",)),
            FieldDefinition("gyro_z", "陀螺仪Z轴", "int", 0, modes=("advanced",)),
            FieldDefinition("limit_time", "触发间隔", "int", 1, modes=("advanced",)),
            FieldDefinition("action_trigger", "动作触发器", "text", "", modes=("advanced",)),
            FieldDefinition("action_trigger_active", "激活后操作", "text", "", modes=("advanced",)),
            FieldDefinition("shop_action", "商店动作", "bool", 0, modes=("advanced",)),
        ),
    ),
    "TouchDrag": NodeDefinition(
        type_name="TouchDrag",
        title="TouchDrag",
        fields=(
            FieldDefinition("tips", "备注", "text", "", modes=("simple",)),
            FieldDefinition("draw_able_name", "框", "text", "drag", modes=("simple", "advanced")),
            FieldDefinition("parameter", "参数", "text", "Touch_drag1", modes=("simple", "advanced")),
            FieldDefinition("range", "参数范围", "range", "{0,1}", modes=("simple", "advanced")),
            FieldDefinition("control_type", "交互类型", "combo", "click", modes=("simple",), options=CLICK_DRAG_OPTIONS),
            FieldDefinition(
                "drag_ui_direction",
                "拖拽方向",
                "combo",
                "",
                modes=("simple",),
                options=DRAG_DIRECTION_OPTIONS,
                visible_if=when_drag,
            ),
            FieldDefinition("result_type", "交互结果", "combo", "action", modes=("simple",), options=RESULT_OPTIONS),
            FieldDefinition("target_idle", "目标idle", "int", 1, modes=("simple",)),
            FieldDefinition("action_trigger", "动作触发器", "text", "", modes=("simple", "advanced"), visible_if=when_animated),
            FieldDefinition(
                "action_trigger_active",
                "激活后操作",
                "text",
                "",
                modes=("simple", "advanced"),
                visible_if=when_animated,
            ),
            FieldDefinition("target_value", "数值变化到", "float", 1.0, modes=("simple",), visible_if=when_value_result),
            FieldDefinition("revert_enabled", "数值是否回弹", "bool", 0, modes=("simple",), visible_if=when_value_result),
            FieldDefinition("parts_data", "数值吸附", "text", "", modes=("simple", "advanced"), visible_if=always),
            FieldDefinition("id", "ID", "int", 0, modes=("advanced",), read_only=True),
            FieldDefinition("mode", "数值模式", "combo", 1, modes=("advanced",), options=MODE_OPTIONS),
            FieldDefinition("start_value", "初始值", "int", 0, modes=("advanced",)),
            FieldDefinition("ignore_react", "屏蔽目光跟随", "bool", 1, modes=("advanced",)),
            FieldDefinition("ignore_action", "非待机状态不可拖动", "bool", 1, modes=("advanced",)),
            FieldDefinition("range_abs", "使用绝对值", "bool", 1, modes=("advanced",)),
            FieldDefinition("drag_direct", "方向响应", "combo", 0, modes=("advanced",), options=DRAG_DIRECT_OPTIONS),
            FieldDefinition("react_pos_x", "眼神跟随X", "nullable_int", None, modes=("advanced",)),
            FieldDefinition("react_pos_y", "眼神跟随Y", "nullable_int", None, modes=("advanced",)),
            FieldDefinition("offset_x", "左右移动影响拖拽", "int", 0, modes=("advanced",)),
            FieldDefinition("offset_y", "上下移动影响拖拽", "int", 0, modes=("advanced",)),
            FieldDefinition("smooth", "开始拖拽响应系数", "int", 100, modes=("advanced",)),
            FieldDefinition("revert_smooth", "回弹拖拽响应系数", "int", 100, modes=("advanced",)),
            FieldDefinition("revert", "回弹延迟", "int", -1, modes=("advanced",)),
            FieldDefinition("gyro", "陀螺仪启用", "bool", 0, modes=("advanced",)),
            FieldDefinition("gyro_x", "陀螺仪X轴", "int", 0, modes=("advanced",)),
            FieldDefinition("gyro_y", "陀螺仪Y轴", "int", 0, modes=("advanced",)),
            FieldDefinition("gyro_z", "陀螺仪Z轴", "int", 0, modes=("advanced",)),
            FieldDefinition("limit_time", "触发间隔", "int", 1, modes=("advanced",)),
            FieldDefinition("shop_action", "商店动作", "bool", 0, modes=("advanced",)),
        ),
    ),
    "Comment": NodeDefinition(
        type_name="Comment",
        title="Comment",
        resizable=True,
        fields=(
            FieldDefinition("content", "内容", "text", "", multiline=True),
        ),
    ),
}
