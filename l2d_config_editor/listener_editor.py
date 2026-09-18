"""Interactive, command-backed editor for one listener's component graph.

The scene is a projection of the owner's graph.  Every edit is submitted as a
cloned graph through the controller, so the main document owns undo and saves.
"""
from __future__ import annotations

import copy
import json
import math
import uuid
from dataclasses import dataclass, field as dataclass_field

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QKeySequence, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFormLayout, QGraphicsEllipseItem,
    QGraphicsItem, QGraphicsPathItem, QGraphicsRectItem, QGraphicsScene,
    QGraphicsSimpleTextItem, QGraphicsView, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMenu, QPlainTextEdit, QPushButton, QScrollArea,
    QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)
from shiboken6 import isValid

from .listener_graph import ListenerGraph, ListenerPart, ListenerWire


@dataclass(frozen=True)
class _Field:
    key: str
    label: str
    kind: str = "text"
    default: object = ""
    description: str = ""
    choices: tuple = ()


@dataclass(frozen=True)
class _Component:
    kind: str
    title: str
    category: str
    description: str
    fields: tuple[_Field, ...]
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    port_labels: dict = dataclass_field(default_factory=dict)
    color: str = "#6785A5"


def _component_catalog() -> dict[str, _Component]:
    """Keep UI presentation adaptation separate from the pure runtime catalog."""
    from .listener_catalog import COMPONENTS
    descriptions = {
        "ActionEvent": "监听指定动作播放。多个动作名称用逗号或换行分隔。",
        "TouchEvent": "监听指定触区点击。多个触区名称用逗号或换行分隔。",
        "IdleEvent": "监听待机状态切换。填写一个或多个待机编号。",
        "AnyEvent": "任一输入事件触发后继续；输入事件必须属于同一类型。",
        "AddValue": "事件触发后为监听参数累加指定数值；负数表示递减。",
        "SetValue": "事件触发后将监听参数设置为指定数值。",
        "ValueState": "汇集参数变化规则，定义 Live2D 参数、初始值、上下限和保存方式。",
        "IdleRange": "参数落在左闭右开区间时切换到指定待机。",
    }
    return {kind: _Component(
        kind, spec.title, spec.category, descriptions.get(kind, ""),
        tuple(_Field(item.key, item.label, "multiline" if item.multiline or item.key == "events" else item.editor,
                     copy.deepcopy(item.default), item.placeholder,
                     tuple((option.label, option.value) for option in item.options)) for item in spec.fields),
        tuple(port.key for port in spec.inputs), tuple(port.key for port in spec.outputs),
        {(False, port.key): port.label for port in spec.inputs}
        | {(True, port.key): port.label for port in spec.outputs}, spec.color,
    ) for kind, spec in COMPONENTS.items()}


def _compile(graph, owner):
    from .listener_compiler import compile_listener_graph
    return compile_listener_graph(graph)


class _DraftLineEdit(QLineEdit):
    """Do not rebuild or submit an editor while its input method has preedit."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.composing = False

    def inputMethodEvent(self, event):
        self.composing = bool(event.preeditString())
        super().inputMethodEvent(event)


class _DraftTextEdit(QPlainTextEdit):
    committed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.composing = False

    def inputMethodEvent(self, event):
        self.composing = bool(event.preeditString())
        super().inputMethodEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.committed.emit()


class ListenerPortItem(QGraphicsEllipseItem):
    def __init__(self, node, port: str, output: bool, index: int):
        super().__init__(-7, -7, 14, 14, node)
        self.part_uuid = node.part_uuid
        self.port = port
        self.output = output
        self.setPos(node.WIDTH if output else 0, 58 + index * 28)
        self.setBrush(QColor("#83D6B2" if output else "#82BFFA"))
        self.setPen(QPen(QColor("#131E2C"), 2))
        self.setZValue(5)
        self.setCursor(Qt.CursorShape.CrossCursor)
        label = node.spec.port_labels.get((output, port), port)
        self.setToolTip(f"{'输出' if output else '输入'}：{label}\n拖动到另一组件的端口以连线")


class ListenerPartItem(QGraphicsRectItem):
    WIDTH = 232

    def __init__(self, part, spec, dialog):
        self.dialog = dialog
        self.part_uuid = part.uuid
        self.spec = spec
        self.ports = {}
        self.summary = None
        height = 82 + 28 * max(len(spec.inputs), len(spec.outputs), 1)
        super().__init__(0, 0, self.WIDTH, height)
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                      | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        if not dialog.read_only:
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setBrush(QColor("#223144"))
        self.setPen(QPen(QColor(spec.color), 1.5))
        self.setZValue(1)
        self.setToolTip(spec.description)
        title = QGraphicsSimpleTextItem(spec.title, self)
        title.setBrush(QColor("#F1F6FC"))
        font = QFont()
        font.setBold(True)
        font.setPointSizeF(11)
        title.setFont(font)
        title.setPos(12, 12)
        for output, names in ((False, spec.inputs), (True, spec.outputs)):
            for index, name in enumerate(names):
                port = ListenerPortItem(self, name, output, index)
                self.ports[(output, name)] = port
                label = QGraphicsSimpleTextItem(spec.port_labels.get((output, name), name), self)
                port_font = QFont()
                port_font.setPointSizeF(9)
                label.setFont(port_font)
                label.setBrush(QColor("#BED0E4"))
                x = self.WIDTH - label.boundingRect().width() - 14 if output else 14
                label.setPos(x, 49 + index * 28)
        self.summary = QGraphicsSimpleTextItem(self)
        self.summary.setBrush(QColor("#A9BED4"))
        self.summary.setPos(12, height - 27)
        self.update_part(part)

    def update_part(self, part):
        summary = " · ".join(f"{field.label}: {part.fields.get(field.key, field.default)}"
                             for field in self.spec.fields[:2])
        self.summary.setText(QFontMetricsF(self.summary.font()).elidedText(
            summary, Qt.TextElideMode.ElideRight, self.WIDTH - 24))
        self.summary.setToolTip(summary)
        self.setPos(float((part.ui_position or {}).get("x", 0)),
                    float((part.ui_position or {}).get("y", 0)))

    def itemChange(self, change, value):
        result = super().itemChange(change, value)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.dialog._update_wire_paths()
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.setPen(QPen(QColor("#F0C875" if value else self.spec.color), 2.5 if value else 1.5))
        return result


class ListenerWireItem(QGraphicsPathItem):
    def __init__(self, wire, dialog):
        super().__init__()
        self.wire = wire
        self.dialog = dialog
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setZValue(0)
        self.setPen(QPen(QColor("#84B6D9"), 2.5))
        self.setToolTip(f"{wire.from_port} → {wire.to_port}\n选中后按 Delete 删除连线")
        self.update_path()

    def update_path(self):
        source = self.dialog.node_items.get(self.wire.from_uuid)
        target = self.dialog.node_items.get(self.wire.to_uuid)
        source_port = source.ports.get((True, self.wire.from_port)) if source else None
        target_port = target.ports.get((False, self.wire.to_port)) if target else None
        if source_port and target_port:
            self.setPath(_wire_path(source_port.scenePos(), target_port.scenePos()))
            self.setVisible(True)
        else:
            self.setVisible(False)

    def shape(self):
        # Widen the hit area without making the displayed cable distracting.
        from PySide6.QtGui import QPainterPathStroker
        stroker = QPainterPathStroker()
        stroker.setWidth(14)
        return stroker.createStroke(self.path())

    def itemChange(self, change, value):
        result = super().itemChange(change, value)
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.setPen(QPen(QColor("#F0C875" if value else "#84B6D9"), 3 if value else 2.5))
        return result


def _wire_path(start: QPointF, end: QPointF):
    distance = max(60, abs(end.x() - start.x()) * .45)
    path = QPainterPath(start)
    path.cubicTo(start + QPointF(distance, 0), end - QPointF(distance, 0), end)
    return path


class ListenerCanvas(QGraphicsView):
    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#17202C"))
        self.setSceneRect(-10000, -10000, 20000, 20000)
        self._source_port = None
        self._draft_wire = None
        self._pan_position = None
        self.dragging = False

    def drawBackground(self, painter, rect):
        super().drawBackground(painter, rect)
        if self.transform().m11() < .3:
            return
        painter.setPen(QPen(QColor("#263547"), 0))
        step = 28
        left = math.floor(rect.left() / step) * step
        top = math.floor(rect.top() / step) * step
        for x in range(left, math.ceil(rect.right()), step):
            for y in range(top, math.ceil(rect.bottom()), step):
                painter.drawPoint(QPointF(x, y))

    def wheelEvent(self, event):
        factor = 1.16 if event.angleDelta().y() > 0 else 1 / 1.16
        if .12 <= self.transform().m11() * factor <= 3:
            self.scale(factor, factor)
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_position = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        item = self.itemAt(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and isinstance(item, ListenerPortItem):
            if not self.dialog.read_only and self.dialog.commit_pending_edits():
                self._source_port = item
                self._draft_wire = QGraphicsPathItem()
                self._draft_wire.setPen(QPen(QColor("#F0C875"), 2, Qt.PenStyle.DashLine))
                self._draft_wire.setZValue(4)
                self.scene().addItem(self._draft_wire)
            event.accept()
            return
        self.dragging = event.button() == Qt.MouseButton.LeftButton
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._pan_position is not None:
            delta = event.position() - self._pan_position
            self._pan_position = event.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(delta.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(delta.y()))
            event.accept()
            return
        if self._source_port is not None:
            origin, destination = self._source_port.scenePos(), self.mapToScene(event.position().toPoint())
            if not self._source_port.output:
                origin, destination = destination, origin
            self._draft_wire.setPath(_wire_path(origin, destination))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._pan_position is not None:
            self._pan_position = None
            self.unsetCursor()
            event.accept()
            return
        if self._source_port is not None:
            source = self._source_port
            target = next((item for item in self.items(event.position().toPoint())
                           if isinstance(item, ListenerPortItem)), None)
            self.cancel_connection()
            if target and source.output != target.output:
                if not source.output:
                    source, target = target, source
                self.dialog.connect_parts(source.part_uuid, source.port, target.part_uuid, target.port)
            event.accept()
            return
        super().mouseReleaseEvent(event)
        was_dragging = self.dragging
        self.dragging = False
        if was_dragging:
            self.dialog._commit_positions()

    def cancel_connection(self):
        if self._draft_wire is not None:
            self.scene().removeItem(self._draft_wire)
        self._draft_wire = self._source_port = None

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self._source_port is not None:
            self.cancel_connection()
            event.accept()
        elif event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.dialog.delete_selected()
            event.accept()
        elif event.matches(QKeySequence.StandardKey.Undo):
            self.dialog.undo()
            event.accept()
        elif event.matches(QKeySequence.StandardKey.Redo):
            self.dialog.redo()
            event.accept()
        else:
            super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        if not self.dialog.read_only:
            add_menu = menu.addMenu("添加组件")
            position = self.mapToScene(event.pos())
            for spec in self.dialog.catalog.values():
                action = add_menu.addAction(f"{spec.category} · {spec.title}")
                action.triggered.connect(lambda _checked=False, kind=spec.kind: self.dialog.add_part(kind, position))
            delete = menu.addAction("删除所选组件 / 连线")
            delete.setEnabled(bool(self.scene().selectedItems()))
            delete.triggered.connect(self.dialog.delete_selected)
        menu.addAction("显示全图", self.dialog.fit_graph)
        menu.exec(event.globalPos())


class ListenerGraphDialog(QDialog):
    """Edit one owner's embedded graph using the document's shared undo stack."""
    saveRequested = Signal()

    def __init__(self, controller, owner_uuid: str, parent=None, *, read_only=False):
        super().__init__(parent)
        self.controller = controller
        self.owner_uuid = owner_uuid
        self._explicit_read_only = read_only
        owner = controller.get_node(owner_uuid)
        self.read_only = read_only or bool(owner and owner.locked)
        self._requires_graph = bool(owner and owner.listener_graph is not None)
        self.catalog = _component_catalog()
        self.node_items = {}
        self.wire_items = []
        self.field_editors = {}
        self._field_baselines = {}
        self._selected_uuid = None
        self._updating = False
        self._committing = False
        self._refresh_pending = False
        self._document = controller.document
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("监听器子蓝图")
        self.resize(1250, 790)
        self._build_ui()
        controller.nodeUpdated.connect(self._node_updated)
        controller.nodeRemoved.connect(self._node_removed)
        controller.documentLoaded.connect(self._document_loaded)
        self._refresh()
        QTimer.singleShot(0, self.fit_graph)

    def _build_ui(self):
        root = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self.heading = QLabel("监听器子蓝图")
        toolbar.addWidget(self.heading, 1)
        self.save_button = QPushButton("保存 JSON")
        self.save_button.setToolTip("Ctrl+S · 应用属性并保存完整文档")
        self.save_button.setEnabled(not self._explicit_read_only)
        self.save_button.clicked.connect(self._request_save)
        toolbar.addWidget(self.save_button)
        self.undo_button = QPushButton("撤销")
        self.redo_button = QPushButton("重做")
        self.undo_button.setToolTip("Ctrl+Z · 与主图共享撤销历史")
        self.redo_button.setToolTip("Ctrl+Y · 与主图共享撤销历史")
        self.undo_button.clicked.connect(self.undo)
        self.redo_button.clicked.connect(self.redo)
        toolbar.addWidget(self.undo_button)
        toolbar.addWidget(self.redo_button)
        fit_button = QPushButton("显示全图")
        fit_button.clicked.connect(self.fit_graph)
        toolbar.addWidget(fit_button)
        self.delete_button = QPushButton("删除所选")
        self.delete_button.clicked.connect(self.delete_selected)
        toolbar.addWidget(self.delete_button)
        root.addLayout(toolbar)
        hint = QLabel("组合组件构建监听逻辑；拖动端口连线，选中组件编辑属性。滚轮缩放，中键平移，Delete 删除。")
        hint.setWordWrap(True)
        root.addWidget(hint)
        splitter = QSplitter()
        root.addWidget(splitter, 1)
        palette_panel = QWidget()
        palette_layout = QVBoxLayout(palette_panel)
        palette_layout.setContentsMargins(0, 0, 0, 0)
        self.palette_search = QLineEdit()
        self.palette_search.setPlaceholderText("筛选组件…")
        self.palette_search.textChanged.connect(self._filter_palette)
        palette_layout.addWidget(self.palette_search)
        self.palette = QTreeWidget()
        self.palette.setHeaderHidden(True)
        groups = {}
        for spec in self.catalog.values():
            group = groups.get(spec.category)
            if group is None:
                group = QTreeWidgetItem([spec.category])
                group.setFlags(group.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                self.palette.addTopLevelItem(group)
                groups[spec.category] = group
            item = QTreeWidgetItem(group, [spec.title])
            item.setData(0, Qt.ItemDataRole.UserRole, spec.kind)
            item.setToolTip(0, spec.description)
        self.palette.expandAll()
        self.palette.itemDoubleClicked.connect(self._palette_activated)
        palette_layout.addWidget(self.palette, 1)
        self.add_button = QPushButton("添加选中组件")
        self.add_button.clicked.connect(lambda: self._palette_activated(self.palette.currentItem()))
        palette_layout.addWidget(self.add_button)
        splitter.addWidget(palette_panel)
        self.canvas = ListenerCanvas(self)
        self.canvas.scene().selectionChanged.connect(self._selection_changed)
        splitter.addWidget(self.canvas)
        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        self.part_heading = QLabel("选择组件以编辑属性")
        self.part_heading.setWordWrap(True)
        side_layout.addWidget(self.part_heading)
        self.form_scroll = QScrollArea()
        self.form_scroll.setWidgetResizable(True)
        self.form_widget = QWidget()
        self.form_layout = QFormLayout(self.form_widget)
        self.form_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.form_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.form_scroll.setWidget(self.form_widget)
        side_layout.addWidget(self.form_scroll, 3)
        self.apply_button = QPushButton("应用属性")
        self.apply_button.clicked.connect(self.commit_pending_edits)
        side_layout.addWidget(self.apply_button)
        self.status = QLabel()
        self.status.setWordWrap(True)
        side_layout.addWidget(self.status)
        side_layout.addWidget(QLabel("校验结果（点击定位组件）"))
        self.issues = QListWidget()
        self.issues.itemClicked.connect(self._issue_selected)
        side_layout.addWidget(self.issues, 1)
        side_layout.addWidget(QLabel("生成配置预览"))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        side_layout.addWidget(self.preview, 2)
        splitter.addWidget(side)
        splitter.setSizes([185, 705, 330])
        splitter.setStretchFactor(1, 1)
        for button in (self.add_button, self.apply_button, self.delete_button):
            button.setEnabled(not self.read_only)
        self.controller.undo_stack.canUndoChanged.connect(self._sync_undo_buttons)
        self.controller.undo_stack.canRedoChanged.connect(self._sync_undo_buttons)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        self._sync_undo_buttons()

    def _sync_undo_buttons(self, *_args):
        if not isValid(self.controller.undo_stack) or not isValid(self.undo_button) or not isValid(self.redo_button):
            return
        self.undo_button.setEnabled(not self.read_only and self.controller.undo_stack.canUndo())
        self.redo_button.setEnabled(not self.read_only and self.controller.undo_stack.canRedo())

    def _owner(self):
        return self.controller.get_node(self.owner_uuid)

    def graph(self):
        owner = self._owner()
        return owner.listener_graph if owner and owner.listener_graph is not None else ListenerGraph()

    def _submit(self, graph, label):
        if self.read_only or self._owner() is None:
            return False
        try:
            return self.controller.set_listener_graph(self.owner_uuid, graph, label)
        except ValueError as error:
            self.status.setText(str(error))
            return False

    def _filter_palette(self, value):
        value = value.casefold().strip()
        for index in range(self.palette.topLevelItemCount()):
            group = self.palette.topLevelItem(index)
            visible = False
            for child_index in range(group.childCount()):
                item = group.child(child_index)
                spec = self.catalog[item.data(0, Qt.ItemDataRole.UserRole)]
                match = not value or value in f"{spec.title} {spec.kind} {spec.description}".casefold()
                item.setHidden(not match)
                visible = visible or match
            group.setHidden(not visible)

    def _palette_activated(self, item, _column=0):
        if item is not None:
            kind = item.data(0, Qt.ItemDataRole.UserRole)
            if kind in self.catalog:
                self.add_part(kind)

    def add_part(self, kind, position=None):
        if self.read_only or kind not in self.catalog or not self.commit_pending_edits():
            return None
        graph = self.graph().clone()
        position = position or self.canvas.mapToScene(self.canvas.viewport().rect().center())
        # Repeated palette additions occupy a free spot instead of obscuring
        # the previous component at the centre of the viewport.
        while any(abs(position.x() - part.ui_position.get("x", 0)) < 240
                  and abs(position.y() - part.ui_position.get("y", 0)) < 140 for part in graph.nodes):
            position = position + QPointF(36, 150)
        spec = self.catalog[kind]
        part = ListenerPart(uuid=str(uuid.uuid4()), kind=kind,
                            fields={field.key: copy.deepcopy(field.default) for field in spec.fields},
                            ui_position={"x": position.x(), "y": position.y()})
        graph.nodes.append(part)
        if self._submit(graph, f"添加监听组件：{spec.title}"):
            self._refresh()
            self.select_part(part.uuid)
            return part.uuid
        return None

    def connect_parts(self, from_uuid, from_port, to_uuid, to_port):
        if self.read_only or not self.commit_pending_edits():
            return False
        graph = self.graph().clone()
        if from_uuid == to_uuid:
            self.status.setText("不能连接组件自身。")
            return False
        nodes = {part.uuid: part for part in graph.nodes}
        source = self.catalog.get(nodes[from_uuid].kind) if from_uuid in nodes else None
        target = self.catalog.get(nodes[to_uuid].kind) if to_uuid in nodes else None
        if source is None or target is None or from_port not in source.outputs or to_port not in target.inputs:
            self.status.setText("请从输出端口连接到输入端口。")
            return False
        from .listener_catalog import COMPONENTS
        source_port = next(port for port in COMPONENTS[nodes[from_uuid].kind].outputs if port.key == from_port)
        target_port = next(port for port in COMPONENTS[nodes[to_uuid].kind].inputs if port.key == to_port)
        if source_port.data_type != target_port.data_type:
            self.status.setText("端口类型不同：请连接相同类型的事件、参数变化或参数值。")
            return False
        wire = ListenerWire(from_uuid=from_uuid, from_port=from_port, to_uuid=to_uuid, to_port=to_port)
        if any(_wire_key(existing) == _wire_key(wire) for existing in graph.connections):
            self.status.setText("这两个端口已经连接。")
            return False
        if not target_port.multiple and any(existing.to_uuid == to_uuid and existing.to_port == to_port
                                            for existing in graph.connections):
            self.status.setText("此输入端口已有连线，请先删除原连线。多个事件可通过“任一事件”汇合。")
            return False
        graph.connections.append(wire)
        return self._submit(graph, "连接监听组件")

    def delete_selected(self):
        if self.read_only or not self.commit_pending_edits():
            return False
        selected = self.canvas.scene().selectedItems()
        parts = {item.part_uuid for item in selected if isinstance(item, ListenerPartItem)}
        wires = {_wire_key(item.wire) for item in selected if isinstance(item, ListenerWireItem)}
        if not parts and not wires:
            return False
        graph = self.graph().clone()
        graph.nodes = [part for part in graph.nodes if part.uuid not in parts]
        graph.connections = [wire for wire in graph.connections if wire.from_uuid not in parts
                             and wire.to_uuid not in parts and _wire_key(wire) not in wires]
        return self._submit(graph, "删除监听组件 / 连线")

    def _commit_positions(self):
        if self.read_only:
            return
        graph = self.graph().clone()
        changed = False
        for part in graph.nodes:
            item = self.node_items.get(part.uuid)
            if item is None:
                continue
            position = {"x": item.pos().x(), "y": item.pos().y()}
            if part.ui_position != position:
                part.ui_position = position
                changed = True
        if changed:
            if not self._submit(graph, "移动监听组件"):
                self._refresh()

    def _update_wire_paths(self):
        for item in self.wire_items:
            item.update_path()

    def _node_updated(self, node_uuid):
        if node_uuid == self.owner_uuid:
            self._queue_refresh()

    def _node_removed(self, node_uuid):
        if node_uuid == self.owner_uuid:
            self.reject()

    def _document_loaded(self):
        # Closing on a document switch avoids writing an old inspector draft into
        # a different document that happens to reuse this node's UUID.
        self.reject()

    def _queue_refresh(self):
        if not self._refresh_pending:
            self._refresh_pending = True
            QTimer.singleShot(0, self._refresh)

    def _refresh(self):
        self._refresh_pending = False
        owner = self._owner()
        if owner is None or self.controller.document is not self._document:
            return
        if owner.listener_graph is None and self._requires_graph:
            self.reject()
            return
        if self.canvas.dragging or self.canvas._source_port is not None:
            self._queue_refresh_later()
            return
        graph = self.graph()
        self._updating = True
        try:
            self.read_only = self._explicit_read_only or bool(owner.locked)
            for button in (self.add_button, self.apply_button, self.delete_button):
                button.setEnabled(not self.read_only)
            for _, editor in self.field_editors.values():
                editor.setEnabled(not self.read_only)
            self._sync_undo_buttons()
            self.heading.setText(f"监听器子蓝图 · {owner.fields.get('tips') or owner.fields.get('id', '')}"
                                 f"  |  {len(graph.nodes)} 个组件 / {len(graph.connections)} 条连线")
            ids = {part.uuid for part in graph.nodes}
            for node_uuid in list(self.node_items):
                if node_uuid not in ids:
                    self.canvas.scene().removeItem(self.node_items.pop(node_uuid))
            for part in graph.nodes:
                spec = self.catalog.get(part.kind)
                if spec is None:
                    inputs = tuple(dict.fromkeys(w.to_port for w in graph.connections if w.to_uuid == part.uuid))
                    outputs = tuple(dict.fromkeys(w.from_port for w in graph.connections if w.from_uuid == part.uuid))
                    spec = _Component(part.kind, f"未知组件：{part.kind}", "未知", "原始数据会被保留", (), inputs, outputs)
                item = self.node_items.get(part.uuid)
                if item is None:
                    item = ListenerPartItem(part, spec, self)
                    self.node_items[part.uuid] = item
                    self.canvas.scene().addItem(item)
                else:
                    item.update_part(part)
                item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, not self.read_only)
            selected_wires = {_wire_key(item.wire) for item in self.wire_items if item.isSelected()}
            for item in self.wire_items:
                self.canvas.scene().removeItem(item)
            self.wire_items = []
            for wire in graph.connections:
                item = ListenerWireItem(wire, self)
                self.canvas.scene().addItem(item)
                item.setSelected(_wire_key(wire) in selected_wires)
                self.wire_items.append(item)
            self._update_wire_paths()
            part = next((part for part in graph.nodes if part.uuid == self._selected_uuid), None)
            if part is not None:
                self._update_inspector_values(part)
            elif self._selected_uuid is not None:
                self._build_inspector(None)
            self._refresh_validation(graph, owner)
        finally:
            self._updating = False

    def _queue_refresh_later(self):
        if not self._refresh_pending:
            self._refresh_pending = True
            QTimer.singleShot(120, self._refresh)

    def _selection_changed(self):
        if self._updating:
            return
        part_ids = [item.part_uuid for item in self.canvas.scene().selectedItems()
                    if isinstance(item, ListenerPartItem)]
        selected_uuid = part_ids[0] if len(part_ids) == 1 else None
        if selected_uuid == self._selected_uuid:
            return
        if not self.commit_pending_edits():
            self.select_part(self._selected_uuid)
            return
        part = next((part for part in self.graph().nodes if part.uuid == selected_uuid), None)
        self._build_inspector(part)

    def select_part(self, part_uuid):
        item = self.node_items.get(part_uuid)
        if item is None:
            return
        self._updating = True
        self.canvas.scene().clearSelection()
        item.setSelected(True)
        self._updating = False
        if part_uuid != self._selected_uuid:
            self._build_inspector(next((part for part in self.graph().nodes if part.uuid == part_uuid), None))
        self.canvas.ensureVisible(item, 35, 35)

    def _build_inspector(self, part):
        self._selected_uuid = part.uuid if part else None
        self.field_editors = {}
        self._field_baselines = {}
        while self.form_layout.rowCount():
            self.form_layout.removeRow(0)
        if part is None:
            self.part_heading.setText("选择一个组件以编辑属性")
            return
        spec = self.catalog.get(part.kind)
        if spec is None:
            self.part_heading.setText(f"未知组件：{part.kind}；保留原始数据")
            return
        self.part_heading.setText(f"{spec.title}\n{spec.description}")
        for field in spec.fields:
            if field.kind in ("bool", "boolean"):
                editor = QCheckBox()
                editor.toggled.connect(self._schedule_commit)
            elif field.choices:
                editor = QComboBox()
                for choice in field.choices:
                    if isinstance(choice, (tuple, list)):
                        editor.addItem(str(choice[0]), choice[1])
                    else:
                        editor.addItem(str(choice), choice)
                editor.activated.connect(self._schedule_commit)
            elif field.kind in ("json", "list", "object", "multiline"):
                editor = _DraftTextEdit()
                editor.setMinimumHeight(74)
                editor.setMaximumHeight(135)
                editor.committed.connect(self._schedule_commit)
            else:
                editor = _DraftLineEdit()
                editor.editingFinished.connect(self._schedule_commit)
                if field.kind in ("int", "float", "number"):
                    editor.setPlaceholderText("整数" if field.kind == "int" else "数值")
            editor.setToolTip(field.description or field.key)
            editor.setEnabled(not self.read_only)
            self.field_editors[field.key] = (field, editor)
            value = copy.deepcopy(part.fields.get(field.key, field.default))
            self._set_editor_value(field, editor, value)
            self._field_baselines[field.key] = value
            self.form_layout.addRow(field.label, editor)
        if not spec.fields:
            self.form_layout.addRow(QLabel("此组件没有需要填写的属性。"))

    def _schedule_commit(self, *_args):
        # Committing after Qt's focus transition avoids deleting an editor in
        # its own focusOutEvent and keeps input-method composition intact.
        if not self._updating and not self._committing:
            QTimer.singleShot(0, self.commit_pending_edits)

    @staticmethod
    def _set_editor_value(field, editor, value):
        editor.blockSignals(True)
        try:
            if isinstance(editor, QCheckBox):
                editor.setChecked(bool(value))
            elif isinstance(editor, QComboBox):
                index = editor.findData(value)
                if index < 0:
                    editor.addItem(str(value), value)
                    index = editor.count() - 1
                editor.setCurrentIndex(index)
            elif isinstance(editor, QPlainTextEdit):
                editor.setPlainText(value if field.kind == "multiline" else json.dumps(value, ensure_ascii=False, indent=2))
            else:
                editor.setText("" if value is None else str(value))
        finally:
            editor.blockSignals(False)

    @staticmethod
    def _editor_value(field, editor):
        if isinstance(editor, QCheckBox):
            return editor.isChecked()
        if isinstance(editor, QComboBox):
            return editor.currentData()
        text = editor.toPlainText() if isinstance(editor, QPlainTextEdit) else editor.text()
        if field.kind in ("json", "list", "object"):
            value = json.loads(text)
            if field.kind == "list" and not isinstance(value, list):
                raise ValueError("请输入 JSON 数组，例如 [0, 1]")
            if field.kind == "object" and not isinstance(value, dict):
                raise ValueError("请输入 JSON 对象")
            return value
        if field.kind == "int":
            return int(text)
        if field.kind in ("float", "number"):
            value = float(text)
            if not math.isfinite(value):
                raise ValueError("请输入有限数值")
            return value
        return text

    def _update_inspector_values(self, part):
        for key, (field, editor) in self.field_editors.items():
            model_value = copy.deepcopy(part.fields.get(key, field.default))
            baseline = self._field_baselines[key]
            if model_value == baseline or getattr(editor, "composing", False):
                continue
            try:
                draft = self._editor_value(field, editor)
            except (ValueError, TypeError):
                continue
            # An unrelated command must never replace a locally edited draft.
            if draft == baseline:
                self._set_editor_value(field, editor, model_value)
                self._field_baselines[key] = model_value

    def commit_pending_edits(self):
        if self._committing or self.read_only or not self.field_editors:
            return True
        if self._owner() is None or self.controller.document is not self._document:
            return True
        self._committing = True
        try:
            QApplication.inputMethod().commit()
            if any(getattr(editor, "composing", False) for _, editor in self.field_editors.values()):
                return False
            changes = {}
            for key, (field, editor) in self.field_editors.items():
                try:
                    value = self._editor_value(field, editor)
                except (ValueError, TypeError) as error:
                    self.status.setText(f"{field.label}：{error}")
                    editor.setStyleSheet("border: 1px solid #e78282;")
                    return False
                editor.setStyleSheet("")
                if value != self._field_baselines[key]:
                    changes[key] = value
            if not changes:
                return True
            graph = self.graph().clone()
            part = next((part for part in graph.nodes if part.uuid == self._selected_uuid), None)
            if part is None:
                return True
            part.fields.update(changes)
            if not self._submit(graph, "修改监听组件属性"):
                self.status.setText("属性尚未应用；请检查节点是否被锁定。")
                return False
            for key, value in changes.items():
                self._field_baselines[key] = copy.deepcopy(value)
            self.status.setText("")
            return True
        finally:
            self._committing = False

    def has_active_editor(self):
        focus = QApplication.focusWidget()
        return any(getattr(editor, "composing", False)
                   or (focus is not None and (focus is editor or editor.isAncestorOf(focus)))
                   for _, editor in self.field_editors.values())

    def is_busy(self):
        if self.has_active_editor() or self.canvas.dragging or self.canvas._source_port is not None:
            return True
        # An invalid draft remains pending even after focus leaves the field.
        # Background saving / external reload must not treat that as idle.
        for key, (field, editor) in self.field_editors.items():
            try:
                if self._editor_value(field, editor) != self._field_baselines[key]:
                    return True
            except (ValueError, TypeError):
                return True
        return False

    def _refresh_validation(self, graph, owner):
        result = _compile(graph, owner)
        self.issues.clear()
        for issue in result.issues:
            prefix = "提醒" if issue.severity == "warning" else "错误"
            item = QListWidgetItem(f"{prefix}：{issue.message}")
            item.setData(Qt.ItemDataRole.UserRole, getattr(issue, "part_uuid", None))
            item.setToolTip(str(issue.message))
            item.setForeground(QColor("#D49F43" if issue.severity == "warning" else "#DB6E75"))
            self.issues.addItem(item)
        if not result.issues:
            self.issues.addItem("✓ 配置有效")
        preview_fields = result.fields
        if result.valid and owner.type != "Listener":
            # An attached graph owns its listener state, while the host keeps
            # its original action, direction, smoothing and other behavior.
            owned = {"listener_data", "parameter", "range", "start_value", "save_parameter"}
            preview_fields = {key: copy.deepcopy(value) for key, value in owner.fields.items()
                              if key in self.controller.schema.csv_columns}
            preview_fields.update({key: value for key, value in result.fields.items() if key in owned})
        self.preview.setPlainText(json.dumps(preview_fields, ensure_ascii=False, indent=2)
                                  if result.valid else "请先完成连线并修正上方问题，然后生成运行配置。")

    def _issue_selected(self, item):
        part_uuid = item.data(Qt.ItemDataRole.UserRole)
        if part_uuid and self.commit_pending_edits():
            self.select_part(part_uuid)

    def fit_graph(self):
        if not self.node_items:
            self.canvas.centerOn(0, 0)
            return
        bounds = QRectF()
        for item in self.node_items.values():
            bounds = bounds.united(item.sceneBoundingRect())
        self.canvas.fitInView(bounds.adjusted(-70, -70, 70, 70), Qt.AspectRatioMode.KeepAspectRatio)
        if self.canvas.transform().m11() > 1.2:
            self.canvas.resetTransform()
            self.canvas.scale(1.2, 1.2)
            self.canvas.centerOn(bounds.center())

    def undo(self):
        if not self.read_only and self.commit_pending_edits():
            self.controller.undo_stack.undo()
            self._refresh()

    def redo(self):
        if not self.read_only and self.commit_pending_edits():
            self.controller.undo_stack.redo()
            self._refresh()

    def _request_save(self):
        if not self._explicit_read_only and self.commit_pending_edits():
            self.saveRequested.emit()

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Save):
            self._request_save()
            event.accept()
        elif event.matches(QKeySequence.StandardKey.Undo):
            self.undo()
            event.accept()
        elif event.matches(QKeySequence.StandardKey.Redo):
            self.redo()
            event.accept()
        else:
            super().keyPressEvent(event)

    def reject(self):
        if self._owner() is None or self.controller.document is not self._document or self.commit_pending_edits():
            super().reject()

    def closeEvent(self, event):
        if self.commit_pending_edits():
            super().closeEvent(event)
        else:
            event.ignore()


def _wire_key(wire):
    return wire.from_uuid, wire.from_port, wire.to_uuid, wire.to_port
