"""Graphics scene and node canvas widgets."""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QPointF, QRectF, Qt, QLineF, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QContextMenuEvent, QKeyEvent, QMouseEvent, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsPathItem,
    QGraphicsProxyWidget,
    QGraphicsScene,
    QGraphicsView,
    QMenu,
)

from .schema import EditorSchema
from .widgets import NodeFormWidget


class GridScene(QGraphicsScene):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSceneRect(-6000, -6000, 12000, 12000)
        self.minor_grid = 20
        self.major_grid = 100

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        painter.fillRect(rect, QColor("#222c3b"))
        left = int(rect.left()) - (int(rect.left()) % self.minor_grid)
        top = int(rect.top()) - (int(rect.top()) % self.minor_grid)

        minor_lines = []
        major_lines = []
        x = left
        while x < int(rect.right()):
            target = major_lines if x % self.major_grid == 0 else minor_lines
            target.append(QLineF(x, rect.top(), x, rect.bottom()))
            x += self.minor_grid
        y = top
        while y < int(rect.bottom()):
            target = major_lines if y % self.major_grid == 0 else minor_lines
            target.append(QLineF(rect.left(), y, rect.right(), y))
            y += self.minor_grid

        painter.setPen(QPen(QColor("#314056"), 1))
        painter.drawLines(minor_lines)
        painter.setPen(QPen(QColor("#425572"), 1))
        painter.drawLines(major_lines)


class ConnectionItem(QGraphicsPathItem):
    def __init__(self, from_item: "NodeItem", to_item: "NodeItem") -> None:
        super().__init__()
        self.from_item = from_item
        self.to_item = to_item
        self.from_uuid = from_item.node.uuid
        self.to_uuid = to_item.node.uuid
        self.setZValue(-8)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.update_path()

    def update_path(self) -> None:
        start = self.from_item.output_pin_scene_pos()
        end = self.to_item.input_pin_scene_pos()
        delta = max(80.0, abs(end.x() - start.x()) * 0.5)
        path = QPainterPath(start)
        path.cubicTo(start.x() + delta, start.y(), end.x() - delta, end.y(), end.x(), end.y())
        self.setPath(path)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        color = QColor("#4c94ff") if self.isSelected() else QColor("#7e95b8")
        painter.setPen(QPen(color, 3.0 if self.isSelected() else 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawPath(self.path())


class NodeItem(QGraphicsObject):
    geometryChanged = pyqtSignal(str)

    def __init__(self, schema: EditorSchema, controller, node, inline_width: int = 340) -> None:
        super().__init__()
        self.schema = schema
        self.controller = controller
        self.node = node
        self.inline_width = inline_width
        self._warnings: list[str] = []
        self._search_highlight = False
        self._margin = 14
        self._header_height = 34
        self._pin_radius = 6
        self._resizing = False
        self._resize_start = QPointF()
        self._resize_initial = (360.0, 180.0)
        self._drag_start_pos = QPointF()
        self._rect = QRectF(0.0, 0.0, 380.0, 180.0)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.proxy = QGraphicsProxyWidget(self)
        self.form = NodeFormWidget(schema, inline=True)
        self.form.fieldCommitted.connect(self._commit_field)
        self.proxy.setWidget(self.form)
        self.update_node(node)
        self.setPos(node.ui_position["x"], node.ui_position["y"])

    def boundingRect(self) -> QRectF:
        return self._rect

    def input_pin_scene_pos(self) -> QPointF:
        return self.mapToScene(QPointF(self._pin_radius + 3, self._header_height + 18))

    def output_pin_scene_pos(self) -> QPointF:
        return self.mapToScene(QPointF(self._rect.width() - self._pin_radius - 3, self._header_height + 18))

    def input_pin_rect(self) -> QRectF:
        return QRectF(4, self._header_height + 18 - self._pin_radius, self._pin_radius * 2, self._pin_radius * 2)

    def output_pin_rect(self) -> QRectF:
        return QRectF(self._rect.width() - 4 - self._pin_radius * 2, self._header_height + 18 - self._pin_radius, self._pin_radius * 2, self._pin_radius * 2)

    def resize_handle_rect(self) -> QRectF:
        return QRectF(self._rect.width() - 16, self._rect.height() - 16, 12, 12)

    def pin_hit(self, scene_pos: QPointF) -> str | None:
        local = self.mapFromScene(scene_pos)
        if self.output_pin_rect().contains(local):
            return "output"
        if self.input_pin_rect().contains(local):
            return "input"
        return None

    def update_node(self, node) -> None:
        self.prepareGeometryChange()
        self.node = node
        definition = self.schema.nodes[node.type]
        self.form.set_node(node, self.controller.preferences.global_mode)
        base_width = 340 if self.controller.preferences.global_mode == "simple" else 380
        content_width = base_width
        if definition.resizable and node.ui_size:
            content_width = max(content_width, int(node.ui_size.get("width", content_width)) - self._margin * 2)
        self.proxy.widget().setFixedWidth(content_width)
        self.proxy.widget().adjustSize()
        self.proxy.widget().updateGeometry()
        content_width = max(base_width, self.proxy.widget().sizeHint().width())
        self.proxy.widget().setFixedWidth(content_width)
        self.proxy.widget().adjustSize()
        content_height = self.proxy.widget().sizeHint().height()
        width = content_width + self._margin * 2
        height = content_height + self._margin * 2 + self._header_height
        if definition.resizable and node.ui_size:
            width = max(width, float(node.ui_size.get("width", width)))
            height = max(height, float(node.ui_size.get("height", height)))
        self._rect = QRectF(0.0, 0.0, width, max(120.0, height))
        self.proxy.setPos(self._margin, self._header_height)
        self.proxy.widget().setFixedWidth(int(self._rect.width() - self._margin * 2))
        if definition.resizable:
            node.ui_size = {"width": self._rect.width(), "height": self._rect.height()}
        self.update()
        self.geometryChanged.emit(self.node.uuid)

    def set_warnings(self, warnings: list[str]) -> None:
        self._warnings = warnings
        self.update()

    def set_search_highlight(self, enabled: bool) -> None:
        self._search_highlight = enabled
        self.update()

    def paint(self, painter: QPainter, option, widget=None) -> None:
        del option, widget
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        definition = self.schema.nodes[self.node.type]
        body_color = QColor(definition.body_color)
        header_color = QColor(definition.header_color)
        accent = QColor(definition.accent_color)
        border = QColor("#50627f")
        if self._warnings:
            border = QColor("#d95c5c")
        elif self._search_highlight:
            border = QColor("#f2b84a")
        elif self.isSelected():
            border = accent
        painter.setPen(QPen(border, 2.0))
        painter.setBrush(body_color)
        painter.drawRoundedRect(self._rect, 12, 12)
        painter.setBrush(header_color)
        painter.drawRoundedRect(QRectF(0, 0, self._rect.width(), self._header_height + 10), 12, 12)
        painter.fillRect(QRectF(0, self._header_height, self._rect.width(), 14), header_color)
        painter.setPen(QColor("#e8eef8"))
        painter.setFont(self.form.font())
        painter.drawText(QRectF(14, 8, self._rect.width() - 80, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.schema.nodes[self.node.type].title)
        painter.setBrush(accent)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(self.input_pin_rect())
        painter.drawEllipse(self.output_pin_rect())
        if self._warnings:
            painter.setBrush(QColor("#d95c5c"))
            painter.drawRoundedRect(QRectF(self._rect.width() - 44, 8, 30, 18), 8, 8)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(QRectF(self._rect.width() - 44, 8, 30, 18), Qt.AlignmentFlag.AlignCenter, str(len(self._warnings)))
        if self.schema.nodes[self.node.type].resizable:
            painter.setBrush(QColor("#d8c48e"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(self.resize_handle_rect())

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.schema.nodes[self.node.type].resizable and self.resize_handle_rect().contains(event.pos()):
            self._resizing = True
            self._resize_start = event.scenePos()
            self._resize_initial = (self._rect.width(), self._rect.height())
            event.accept()
            return
        self._drag_start_pos = self.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._resizing:
            delta = event.scenePos() - self._resize_start
            width = max(280.0, self._resize_initial[0] + delta.x())
            height = max(140.0, self._resize_initial[1] + delta.y())
            self.node.ui_size = {"width": width, "height": height}
            self.update_node(self.node)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._resizing:
            self._resizing = False
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
        self.controller.update_field(self.node.uuid, key, value, self.controller.preferences.global_mode)


class NodeCanvasView(QGraphicsView):
    selectionSummaryChanged = pyqtSignal(object, object)

    def __init__(self, schema: EditorSchema, controller, parent=None) -> None:
        super().__init__(parent)
        self.schema = schema
        self.controller = controller
        self.scene_ref = GridScene(self)
        self.setScene(self.scene_ref)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.node_items: dict[str, NodeItem] = {}
        self.connection_items: dict[tuple[str, str], ConnectionItem] = {}
        self._warnings_by_node: dict[str, list[str]] = {}
        self._connecting_from: str | None = None
        self._connecting_moved = False
        self._connecting_start_scene = QPointF()
        self._temp_path = QGraphicsPathItem()
        self._temp_path.setPen(QPen(QColor("#6fb6ff"), 2.0, Qt.PenStyle.DashLine))
        self._temp_path.setZValue(90)
        self._temp_path.hide()
        self.scene_ref.addItem(self._temp_path)
        self._panning = False
        self._pan_start = QPointF()
        self.scene_ref.selectionChanged.connect(self._on_scene_selection_changed)
        controller.documentLoaded.connect(self.rebuild_scene)
        controller.nodeAdded.connect(self._add_or_update_node_item)
        controller.nodeRemoved.connect(self._remove_node_item)
        controller.nodeUpdated.connect(self._update_node_item)
        controller.connectionsChanged.connect(self._rebuild_connections)
        controller.validationChanged.connect(self._apply_validation)
        controller.globalModeChanged.connect(self._handle_mode_changed)
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
        self.centerOn(self.controller.document.canvas_view.offset_x, self.controller.document.canvas_view.offset_y)

    def selected_node_uuids(self) -> list[str]:
        return [item.node.uuid for item in self.scene_ref.selectedItems() if isinstance(item, NodeItem)]

    def selected_connection_pairs(self) -> list[tuple[str, str]]:
        return [(item.from_uuid, item.to_uuid) for item in self.scene_ref.selectedItems() if isinstance(item, ConnectionItem)]

    def center_on_node(self, node_uuid: str) -> None:
        item = self.node_items.get(node_uuid)
        if item:
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

    def wheelEvent(self, event: QMouseEvent) -> None:
        factor = 1.1 if event.angleDelta().y() > 0 else 1 / 1.1
        self.scale(factor, factor)
        self._store_canvas_view()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_start = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton and self._connecting_from:
            self.cancel_connection_preview()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            node_item = self._node_item_at_view_point(event.position())
            if node_item:
                pin = node_item.pin_hit(self.mapToScene(event.position().toPoint()))
                if pin == "output":
                    self._start_connection(node_item)
                    event.accept()
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._panning:
            delta = event.position() - self._pan_start
            self._pan_start = event.position()
            self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() - delta.x()))
            self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() - delta.y()))
            self._store_canvas_view()
            event.accept()
            return
        if self._connecting_from:
            end = self.mapToScene(event.position().toPoint())
            self._connecting_moved = self._connecting_moved or (abs(end.x() - self._connecting_start_scene.x()) > 12 or abs(end.y() - self._connecting_start_scene.y()) > 12)
            self._update_temp_connection(end)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton and self._panning:
            self._panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._connecting_from:
            release_scene = self.mapToScene(event.position().toPoint())
            target_item = self._node_item_at_view_point(event.position())
            if target_item and target_item.pin_hit(release_scene) == "input" and target_item.node.uuid != self._connecting_from:
                self.controller.add_connection(self._connecting_from, target_item.node.uuid)
                self.cancel_connection_preview()
                event.accept()
                return
            if target_item:
                self.cancel_connection_preview()
                event.accept()
                return
            if self._connecting_moved:
                self._show_quick_create_menu(event.globalPosition().toPoint(), release_scene)
                self.cancel_connection_preview()
                event.accept()
                return
            self.cancel_connection_preview()
            event.accept()
            return
        super().mouseReleaseEvent(event)
        self._store_canvas_view()

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        if self._connecting_from:
            self.cancel_connection_preview()
            event.accept()
            return
        if self.itemAt(event.pos()):
            super().contextMenuEvent(event)
            return
        allowed, reason = self.controller.can_create_graph_content()
        if not allowed:
            self.controller.statusMessage.emit(reason)
            return
        menu = QMenu(self)
        actions = {}
        for type_name, definition in self.schema.nodes.items():
            if type_name == "Initial" or not definition.quick_create:
                continue
            actions[type_name] = menu.addAction(f"添加 {definition.title}")
        selected = menu.exec(event.globalPos())
        if not selected:
            return
        scene_pos = self.mapToScene(event.pos())
        for node_type, action in actions.items():
            if selected == action:
                self.controller.create_node(node_type, (scene_pos.x(), scene_pos.y()))
                break

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape and self._connecting_from:
            self.cancel_connection_preview()
            event.accept()
            return
        super().keyPressEvent(event)

    def _create_item(self, node) -> None:
        item = NodeItem(self.schema, self.controller, node)
        item.geometryChanged.connect(self._on_node_geometry_changed)
        self.scene_ref.addItem(item)
        self.node_items[node.uuid] = item
        item.set_warnings(self._warnings_by_node.get(node.uuid, []))

    def _add_or_update_node_item(self, node_uuid: str) -> None:
        node = self.controller.get_node(node_uuid)
        if not node:
            return
        if node_uuid not in self.node_items:
            self._create_item(node)
        else:
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

    def _handle_mode_changed(self, _: str) -> None:
        for node_uuid in list(self.node_items):
            self._update_node_item(node_uuid)

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

    def _node_item_at_view_point(self, point) -> NodeItem | None:
        for item in self.items(point.toPoint() if hasattr(point, "toPoint") else point):
            current = item
            while current:
                if isinstance(current, NodeItem):
                    return current
                current = current.parentItem()
        return None

    def _start_connection(self, node_item: NodeItem) -> None:
        allowed, reason = self.controller.can_create_graph_content()
        if not allowed:
            self.controller.statusMessage.emit(reason)
            return
        self._connecting_from = node_item.node.uuid
        self._connecting_moved = False
        self._connecting_start_scene = node_item.output_pin_scene_pos()
        self._update_temp_connection(self._connecting_start_scene)
        self._temp_path.show()

    def _update_temp_connection(self, end_scene_pos: QPointF) -> None:
        if not self._connecting_from:
            return
        start_item = self.node_items.get(self._connecting_from)
        if not start_item:
            return
        start = start_item.output_pin_scene_pos()
        delta = max(80.0, abs(end_scene_pos.x() - start.x()) * 0.5)
        path = QPainterPath(start)
        path.cubicTo(start.x() + delta, start.y(), end_scene_pos.x() - delta, end_scene_pos.y(), end_scene_pos.x(), end_scene_pos.y())
        self._temp_path.setPath(path)

    def cancel_connection_preview(self) -> None:
        self._connecting_from = None
        self._connecting_moved = False
        self._temp_path.hide()
        self._temp_path.setPath(QPainterPath())

    def _show_quick_create_menu(self, global_pos, scene_pos: QPointF) -> None:
        if not self._connecting_from:
            return
        allowed, reason = self.controller.can_create_graph_content()
        if not allowed:
            self.controller.statusMessage.emit(reason)
            return
        menu = QMenu(self)
        actions = {}
        for type_name, definition in self.schema.nodes.items():
            if type_name == "Initial" or not definition.quick_create:
                continue
            actions[type_name] = menu.addAction(f"创建并连接 {definition.title}")
        selected = menu.exec(global_pos)
        if not selected:
            return
        for node_type, action in actions.items():
            if selected == action:
                self.controller.create_node_with_connection(self._connecting_from, node_type, (scene_pos.x(), scene_pos.y()))
                break
