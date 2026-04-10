"""Graphics scene and node canvas widgets."""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QColor,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsPathItem,
    QGraphicsProxyWidget,
    QGraphicsScene,
    QGraphicsView,
    QMenu,
)

from .definitions import NODE_DEFINITIONS
from .widgets import NodeFormWidget


class ConnectionItem(QGraphicsPathItem):
    def __init__(self, from_item: "NodeItem", to_item: "NodeItem") -> None:
        super().__init__()
        self.from_item = from_item
        self.to_item = to_item
        self.from_uuid = from_item.node.uuid
        self.to_uuid = to_item.node.uuid
        self.setPen(QPen(QColor("#72809a"), 2.0))
        self.setZValue(-5)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.update_path()

    def update_path(self) -> None:
        start = self.from_item.output_pin_scene_pos()
        end = self.to_item.input_pin_scene_pos()
        path = QPainterPath(start)
        delta = max(60.0, abs(end.x() - start.x()) * 0.5)
        path.cubicTo(start.x() + delta, start.y(), end.x() - delta, end.y(), end.x(), end.y())
        self.setPath(path)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        pen = QPen(QColor("#00a3ff") if self.isSelected() else QColor("#72809a"), 2.4 if self.isSelected() else 2.0)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(pen)
        painter.drawPath(self.path())


class NodeItem(QGraphicsObject):
    pinActivated = pyqtSignal(str, str)
    geometryChanged = pyqtSignal(str)

    def __init__(self, controller, node, inline_width: int = 270) -> None:
        super().__init__()
        self.controller = controller
        self.node = node
        self.inline_width = inline_width
        self._warnings: list[str] = []
        self._search_highlight = False
        self._margin = 14
        self._pin_radius = 6
        self._resizing = False
        self._resize_start = QPointF()
        self._resize_initial = (260.0, 160.0)
        self._drag_start_scene = QPointF()
        self._drag_start_pos = QPointF()
        self._rect = QRectF(0.0, 0.0, 300.0, 200.0)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        self.proxy = QGraphicsProxyWidget(self)
        self.form = NodeFormWidget(inline=True)
        self.form.fieldCommitted.connect(self._commit_field)
        self.form.modeRequested.connect(self._request_mode)
        self.proxy.setWidget(self.form)
        self.update_node(node)
        self.setPos(node.ui_position["x"], node.ui_position["y"])

    def boundingRect(self) -> QRectF:
        return self._rect

    def input_pin_scene_pos(self) -> QPointF:
        return self.mapToScene(QPointF(self._pin_radius + 2, self._rect.height() * 0.5))

    def output_pin_scene_pos(self) -> QPointF:
        return self.mapToScene(QPointF(self._rect.width() - self._pin_radius - 2, self._rect.height() * 0.5))

    def resize_handle_rect(self) -> QRectF:
        return QRectF(self._rect.width() - 14, self._rect.height() - 14, 12, 12)

    def input_pin_rect(self) -> QRectF:
        return QRectF(2, self._rect.height() * 0.5 - self._pin_radius, self._pin_radius * 2, self._pin_radius * 2)

    def output_pin_rect(self) -> QRectF:
        return QRectF(
            self._rect.width() - 2 - self._pin_radius * 2,
            self._rect.height() * 0.5 - self._pin_radius,
            self._pin_radius * 2,
            self._pin_radius * 2,
        )

    def update_node(self, node) -> None:
        self.prepareGeometryChange()
        self.node = node
        self.form.set_node(node)
        self.form.setSizePolicy(self.form.sizePolicy().horizontalPolicy(), self.form.sizePolicy().verticalPolicy())
        self.form.adjustSize()
        definition = NODE_DEFINITIONS[node.type]
        width = max(self.inline_width, self.form.sizeHint().width() + self._margin * 2)
        height = max(self.form.sizeHint().height() + self._margin * 2, 120)
        if definition.resizable and node.ui_size:
            width = max(width, float(node.ui_size.get("width", width)))
            height = max(height, float(node.ui_size.get("height", height)))
        self._rect = QRectF(0.0, 0.0, width, height)
        self.proxy.setPos(self._margin, self._margin)
        self.proxy.widget().setFixedSize(int(width - self._margin * 2), int(height - self._margin * 2))
        self.update()
        self.geometryChanged.emit(self.node.uuid)

    def set_warnings(self, warnings: list[str]) -> None:
        self._warnings = warnings
        self.update()

    def set_search_highlight(self, enabled: bool) -> None:
        self._search_highlight = enabled
        self.update()

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        background = QColor("#1d2330") if self.node.type in {"TouchIdle", "TouchDrag"} else QColor("#27303b")
        if self.node.type == "Initial":
            background = QColor("#233022")
        if self.node.type == "Comment":
            background = QColor(245, 220, 120, 120)
        border = QColor("#4f647d")
        if self._warnings:
            border = QColor("#d64545")
        elif self._search_highlight:
            border = QColor("#f3a536")
        elif self.isSelected():
            border = QColor("#00a3ff")
        painter.setBrush(background)
        painter.setPen(QPen(border, 2.0))
        painter.drawRoundedRect(self._rect, 12, 12)
        painter.setBrush(QColor("#8ea5bf"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(self.input_pin_rect())
        painter.drawEllipse(self.output_pin_rect())
        if self.node.type == "Comment":
            painter.setBrush(QColor("#2b2b2b"))
            painter.drawRect(self.resize_handle_rect())

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.input_pin_rect().contains(event.pos()):
            self.pinActivated.emit(self.node.uuid, "input")
            event.accept()
            return
        if self.output_pin_rect().contains(event.pos()):
            self.pinActivated.emit(self.node.uuid, "output")
            event.accept()
            return
        if self.node.type == "Comment" and self.resize_handle_rect().contains(event.pos()):
            self._resizing = True
            self._resize_start = event.scenePos()
            self._resize_initial = (self._rect.width(), self._rect.height())
            event.accept()
            return
        self._drag_start_scene = event.scenePos()
        self._drag_start_pos = self.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._resizing:
            delta = event.scenePos() - self._resize_start
            width = max(240.0, self._resize_initial[0] + delta.x())
            height = max(120.0, self._resize_initial[1] + delta.y())
            self.node.ui_size = {"width": width, "height": height}
            self.update_node(self.node)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._resizing:
            self._resizing = False
            self.node.ui_size = {"width": self._rect.width(), "height": self._rect.height()}
            self.controller.nodeUpdated.emit(self.node.uuid)
            event.accept()
            return
        super().mouseReleaseEvent(event)
        current = self.pos()
        old_pos = (self._drag_start_pos.x(), self._drag_start_pos.y())
        new_pos = (current.x(), current.y())
        self.controller.move_node(self.node.uuid, old_pos, new_pos)

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value: Any):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.geometryChanged.emit(self.node.uuid)
        return super().itemChange(change, value)

    def _commit_field(self, key: str, value: Any) -> None:
        self.controller.update_field(self.node.uuid, key, value)

    def _request_mode(self, mode: str) -> None:
        self.controller.set_mode(self.node.uuid, mode)


class NodeCanvasView(QGraphicsView):
    selectionSummaryChanged = pyqtSignal(object, object)

    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.scene_ref = QGraphicsScene(self)
        self.setScene(self.scene_ref)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setBackgroundBrush(QColor("#10151c"))
        self.node_items: dict[str, NodeItem] = {}
        self.connection_items: dict[tuple[str, str], ConnectionItem] = {}
        self._warnings_by_node: dict[str, list[str]] = {}
        self._connecting_from: str | None = None
        self._temp_path = QGraphicsPathItem()
        self._temp_path.setPen(QPen(QColor("#4c8bf5"), 2.0, Qt.PenStyle.DashLine))
        self._temp_path.setZValue(-4)
        self._temp_path.hide()
        self.scene_ref.addItem(self._temp_path)
        self._panning = False
        self._pan_start = QPoint()
        self.scene_ref.selectionChanged.connect(self._on_scene_selection_changed)
        controller.documentLoaded.connect(self.rebuild_scene)
        controller.nodeAdded.connect(self._add_or_update_node_item)
        controller.nodeRemoved.connect(self._remove_node_item)
        controller.nodeUpdated.connect(self._update_node_item)
        controller.connectionsChanged.connect(self._rebuild_connections)
        controller.validationChanged.connect(self._apply_validation)
        self.rebuild_scene()

    def rebuild_scene(self) -> None:
        for item in list(self.connection_items.values()):
            self.scene_ref.removeItem(item)
        for item in list(self.node_items.values()):
            self.scene_ref.removeItem(item)
        self.connection_items.clear()
        self.node_items.clear()
        for node in self.controller.document.nodes:
            self._create_item(node)
        self._rebuild_connections()
        self.resetTransform()
        scale = self.controller.document.canvas_view.scale
        if scale != 1.0:
            self.scale(scale, scale)
        self.centerOn(
            self.controller.document.canvas_view.offset_x,
            self.controller.document.canvas_view.offset_y,
        )

    def selected_node_uuids(self) -> list[str]:
        return [item.node.uuid for item in self.scene_ref.selectedItems() if isinstance(item, NodeItem)]

    def selected_connection_pairs(self) -> list[tuple[str, str]]:
        return [
            (item.from_uuid, item.to_uuid)
            for item in self.scene_ref.selectedItems()
            if isinstance(item, ConnectionItem)
        ]

    def center_on_node(self, node_uuid: str) -> None:
        item = self.node_items.get(node_uuid)
        if not item:
            return
        self.centerOn(item)

    def flash_node(self, node_uuid: str) -> None:
        item = self.node_items.get(node_uuid)
        if not item:
            return
        item.set_search_highlight(True)
        QTimer.singleShot(1500, lambda target=item: target.set_search_highlight(False))

    def paste_position(self) -> tuple[float, float]:
        point = self.mapToScene(self.viewport().rect().center())
        return point.x(), point.y()

    def wheelEvent(self, event) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)
        self._store_canvas_view()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_start = event.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if self._connecting_from and event.button() == Qt.MouseButton.LeftButton and not self.itemAt(event.pos()):
            self._cancel_connection_preview()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._panning:
            delta = event.pos() - self._pan_start
            self._pan_start = event.pos()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            self._store_canvas_view()
            event.accept()
            return
        if self._connecting_from:
            start_item = self.node_items.get(self._connecting_from)
            if start_item:
                start = start_item.output_pin_scene_pos()
                end = self.mapToScene(event.pos())
                path = QPainterPath(start)
                delta = max(60.0, abs(end.x() - start.x()) * 0.5)
                path.cubicTo(start.x() + delta, start.y(), end.x() - delta, end.y(), end.x(), end.y())
                self._temp_path.setPath(path)
                self._temp_path.show()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton and self._panning:
            self._panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)
        self._store_canvas_view()

    def contextMenuEvent(self, event) -> None:
        if self.itemAt(event.pos()):
            super().contextMenuEvent(event)
            return
        menu = QMenu(self)
        actions = {
            "TouchIdle": menu.addAction("创建 TouchIdle"),
            "TouchDrag": menu.addAction("创建 TouchDrag"),
            "Comment": menu.addAction("创建 Comment"),
        }
        selected = menu.exec(event.globalPos())
        if not selected:
            return
        scene_pos = self.mapToScene(event.pos())
        for node_type, action in actions.items():
            if selected == action:
                from .logic import create_node

                self.controller.add_node(create_node(node_type, (scene_pos.x(), scene_pos.y())))
                break

    def _create_item(self, node) -> None:
        item = NodeItem(self.controller, node)
        item.pinActivated.connect(self._handle_pin_activation)
        item.geometryChanged.connect(self._on_node_geometry_changed)
        self.scene_ref.addItem(item)
        self.node_items[node.uuid] = item
        warnings = self._warnings_by_node.get(node.uuid, [])
        item.set_warnings(warnings)

    def _add_or_update_node_item(self, node_uuid: str) -> None:
        node = self.controller.get_node(node_uuid)
        if not node:
            return
        if node_uuid not in self.node_items:
            self._create_item(node)
            return
        self._update_node_item(node_uuid)

    def _update_node_item(self, node_uuid: str) -> None:
        node = self.controller.get_node(node_uuid)
        item = self.node_items.get(node_uuid)
        if not node or not item:
            return
        if item.pos() != QPointF(node.ui_position["x"], node.ui_position["y"]):
            item.setPos(node.ui_position["x"], node.ui_position["y"])
        item.update_node(node)
        item.set_warnings(self._warnings_by_node.get(node_uuid, []))
        self._update_connections_for_node(node_uuid)

    def _remove_node_item(self, node_uuid: str) -> None:
        item = self.node_items.pop(node_uuid, None)
        if not item:
            return
        self.scene_ref.removeItem(item)
        for pair in list(self.connection_items.keys()):
            if node_uuid in pair:
                connection = self.connection_items.pop(pair)
                self.scene_ref.removeItem(connection)

    def _rebuild_connections(self) -> None:
        for item in list(self.connection_items.values()):
            self.scene_ref.removeItem(item)
        self.connection_items.clear()
        for connection in self.controller.document.connections:
            from_item = self.node_items.get(connection.from_uuid)
            to_item = self.node_items.get(connection.to_uuid)
            if not from_item or not to_item:
                continue
            item = ConnectionItem(from_item, to_item)
            self.connection_items[(connection.from_uuid, connection.to_uuid)] = item
            self.scene_ref.addItem(item)

    def _update_connections_for_node(self, node_uuid: str) -> None:
        for pair, item in self.connection_items.items():
            if node_uuid in pair:
                item.update_path()

    def _apply_validation(self, issues) -> None:
        warnings: dict[str, list[str]] = {}
        for issue in issues:
            warnings.setdefault(issue.node_uuid, []).append(issue.message)
        self._warnings_by_node = warnings
        for node_uuid, item in self.node_items.items():
            item.set_warnings(warnings.get(node_uuid, []))

    def _handle_pin_activation(self, node_uuid: str, pin_kind: str) -> None:
        if pin_kind == "output":
            self._connecting_from = node_uuid
            self._temp_path.show()
            return
        if pin_kind == "input" and self._connecting_from:
            self.controller.add_connection(self._connecting_from, node_uuid)
            self._cancel_connection_preview()

    def _cancel_connection_preview(self) -> None:
        self._connecting_from = None
        self._temp_path.hide()
        self._temp_path.setPath(QPainterPath())

    def _on_node_geometry_changed(self, node_uuid: str) -> None:
        self._update_connections_for_node(node_uuid)

    def _on_scene_selection_changed(self) -> None:
        node_selection = self.selected_node_uuids()
        connection_selection = self.selected_connection_pairs()
        self.selectionSummaryChanged.emit(node_selection, connection_selection)
        self.controller.set_selected_node(node_selection[0] if len(node_selection) == 1 else None)

    def _store_canvas_view(self) -> None:
        center = self.mapToScene(self.viewport().rect().center())
        self.controller.document.canvas_view.scale = self.transform().m11()
        self.controller.document.canvas_view.offset_x = center.x()
        self.controller.document.canvas_view.offset_y = center.y()
