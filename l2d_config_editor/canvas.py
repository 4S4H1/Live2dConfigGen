"""Graphics scene and node canvas widgets."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from PyQt6.QtCore import QPointF, QRectF, Qt, QLineF, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QContextMenuEvent, QFont, QFontMetrics, QKeyEvent, QMouseEvent, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsPathItem,
    QGraphicsProxyWidget,
    QGraphicsSceneHoverEvent,
    QGraphicsScene,
    QGraphicsView,
    QMenu,
)

from .logic import node_title
from .schema import EditorSchema
from .widgets import NodeFormWidget


class GridScene(QGraphicsScene):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSceneRect(-1_000_000, -1_000_000, 2_000_000, 2_000_000)
        self.minor_grid = 20
        self.major_grid = 100
        self.top_right_hint = ""
        self.bottom_right_hint = "按住中键平移 / 滚轮缩放 / Delete 删除"

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

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        del rect
        view = self.views()[0] if self.views() else None
        if not view:
            return
        viewport_rect = view.viewport().rect()
        painter.save()
        painter.resetTransform()
        painter.setPen(QColor(255, 255, 255, 62))
        font = painter.font()
        font.setBold(True)
        font.setPointSize(22)
        painter.setFont(font)
        margin = 20
        if self.top_right_hint:
            top_rect = QRectF(viewport_rect.width() * 0.45, margin, viewport_rect.width() * 0.5, 42)
            painter.drawText(top_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, self.top_right_hint)
        if self.bottom_right_hint:
            bottom_rect = QRectF(viewport_rect.width() * 0.35, viewport_rect.height() - 60, viewport_rect.width() * 0.6, 42)
            painter.drawText(bottom_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, self.bottom_right_hint)
        painter.restore()


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
        self._base_header_height = 34.0
        self._base_content_top_gap = 14.0
        self._header_height = self._base_header_height
        self._content_top_gap = self._base_content_top_gap
        self._title_rect = QRectF(14.0, 8.0, 180.0, 20.0)
        self._pin_radius = 8
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
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.proxy = QGraphicsProxyWidget(self)
        self.form = NodeFormWidget(schema, inline=True)
        self.form.fieldCommitted.connect(self._commit_field)
        self.proxy.setWidget(self.form)
        self.update_node(node)
        self.setPos(node.ui_position["x"], node.ui_position["y"])

    def boundingRect(self) -> QRectF:
        return self._rect

    def _pin_center(self, side: str) -> QPointF:
        x = 0.0 if side == "input" else self._rect.width()
        return QPointF(x, self._header_height + 18.0)

    def _pin_rect(self, side: str, radius: float) -> QRectF:
        center = self._pin_center(side)
        return QRectF(center.x() - radius, center.y() - radius, radius * 2.0, radius * 2.0)

    def input_pin_scene_pos(self) -> QPointF:
        return self.mapToScene(self._pin_center("input"))

    def output_pin_scene_pos(self) -> QPointF:
        return self.mapToScene(self._pin_center("output"))

    def input_pin_rect(self) -> QRectF:
        return self._pin_rect("input", self._pin_radius)

    def output_pin_rect(self) -> QRectF:
        return self._pin_rect("output", self._pin_radius)

    def resize_handle_rect(self) -> QRectF:
        size = 18 if self.node.type == "Comment" else 12
        return QRectF(self._rect.width() - size - 6, self._rect.height() - size - 6, size, size)

    def pin_hit(self, scene_pos: QPointF) -> str | None:
        local = self.mapFromScene(scene_pos)
        input_hit = self._pin_rect("input", self._pin_radius + 7.0)
        output_hit = self._pin_rect("output", self._pin_radius + 7.0)
        if output_hit.contains(local):
            return "output"
        if input_hit.contains(local):
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
        width = content_width + self._margin * 2
        if definition.resizable and node.ui_size:
            width = max(width, float(node.ui_size.get("width", width)))
        self._recompute_header_layout(width)
        final_content_width = int(width - self._margin * 2)
        self.form.setFixedWidth(final_content_width)
        self.form.ensurePolished()
        content_height = self.form.content_height_hint()
        self.form.setFixedHeight(content_height)
        self.form.updateGeometry()
        height = content_height + self._margin * 2 + self._header_height + self._content_top_gap
        if definition.resizable and node.ui_size:
            height = max(height, float(node.ui_size.get("height", height)))
        self._rect = QRectF(0.0, 0.0, width, max(120.0, height))
        self.proxy.setPos(self._margin, self._header_height + self._content_top_gap)
        self.proxy.resize(final_content_width, content_height)
        if definition.resizable:
            node.ui_size = {"width": self._rect.width(), "height": self._rect.height()}
        self.setToolTip(self._full_title_text())
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
        if self.node.type == "Comment":
            body_color = QColor(198, 215, 238, 95)
            header_color = QColor(210, 226, 246, 128)
            accent = QColor(129, 164, 204, 220)
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
        content_rect = QRectF(
            self.proxy.pos().x() - 2,
            self.proxy.pos().y() - 2,
            self.proxy.size().width() + 4,
            self.proxy.size().height() + 4,
        )
        if content_rect.width() > 0 and content_rect.height() > 0:
            panel_fill = QColor("#111827")
            panel_fill.setAlpha(72)
            panel_border = QColor("#dbe7ff")
            panel_border.setAlpha(26)
            painter.setBrush(panel_fill)
            painter.setPen(QPen(panel_border, 1.0))
            painter.drawRoundedRect(content_rect, 12, 12)
        base_title = self.schema.nodes[self.node.type].title
        painter.setPen(QColor("#f6d365" if self.node.fields.get("tips") else "#e8eef8"))
        title_font = self._title_font()
        painter.setFont(title_font)
        painter.drawText(
            self._title_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
            self._full_title_text(),
        )
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
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
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
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        current = self.pos()
        old_pos = (self._drag_start_pos.x(), self._drag_start_pos.y())
        new_pos = (current.x(), current.y())
        self.controller.move_node(self.node.uuid, old_pos, new_pos)

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value: Any):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.geometryChanged.emit(self.node.uuid)
        return super().itemChange(change, value)

    def hoverMoveEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        if self.schema.nodes[self.node.type].resizable and self.resize_handle_rect().contains(event.pos()):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif self.pin_hit(event.scenePos()):
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().hoverMoveEvent(event)

    def _commit_field(self, key: str, value: Any) -> None:
        self.controller.update_field(self.node.uuid, key, value, self.controller.preferences.global_mode)

    def _view_scale(self) -> float:
        if self.scene() and self.scene().views():
            return max(0.06, float(self.scene().views()[0].transform().m11()))
        return 1.0

    def refresh_view_scale(self) -> None:
        self.update_node(self.node)

    def _title_font(self) -> QFont:
        title_font = QFont(self.form.font())
        title_font.setBold(True)
        title_font.setPointSizeF(max(7.5, 11.0 / self._view_scale()))
        return title_font

    def _recompute_header_layout(self, width: float) -> None:
        title_font = self._title_font()
        metrics = QFontMetrics(title_font)
        available_width = max(140, int(width - 60.0))
        title_text = self._full_title_text()
        title_bounds = metrics.boundingRect(
            0,
            0,
            available_width,
            4096,
            int(Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap),
            title_text,
        )
        padding_top = max(8.0, 10.0 / self._view_scale())
        padding_bottom = max(10.0, 12.0 / self._view_scale())
        self._title_rect = QRectF(
            14.0,
            padding_top,
            width - 60.0,
            max(24.0, float(title_bounds.height())),
        )
        self._header_height = max(
            self._base_header_height,
            self._title_rect.y() + self._title_rect.height() + padding_bottom,
        )
        self._content_top_gap = max(self._base_content_top_gap, 16.0 / self._view_scale())

    def _full_title_text(self) -> str:
        return node_title(self.schema, self.node)


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
        controller.documentStateChanged.connect(self._handle_document_state_changed)
        controller.nodeUpdated.connect(self._refresh_hint_overlay)
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
        self._refresh_scale_sensitive_nodes()
        self.centerOn(self.controller.document.canvas_view.offset_x, self.controller.document.canvas_view.offset_y)
        self._refresh_hint_overlay()

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
        current_scale = max(0.001, float(self.transform().m11()))
        target_scale = current_scale * factor
        if target_scale < 0.03 or target_scale > 8.0:
            event.accept()
            return
        self.scale(factor, factor)
        self._refresh_scale_sensitive_nodes()
        self._store_canvas_view()
        event.accept()

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
        if node.type == "Comment":
            item.setZValue(-20)
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
        self._refresh_scale_sensitive_nodes()

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

    def optimize_connection_layout(self) -> bool:
        movable_nodes = {
            node.uuid: node
            for node in self.controller.document.nodes
            if node.type != "Comment"
        }
        edge_pairs = [
            (connection.from_uuid, connection.to_uuid)
            for connection in self.controller.document.connections
            if connection.from_uuid in movable_nodes and connection.to_uuid in movable_nodes
        ]
        connected_ids = {node_uuid for pair in edge_pairs for node_uuid in pair}
        movable_nodes = {
            node_uuid: node
            for node_uuid, node in movable_nodes.items()
            if node_uuid in connected_ids
        }
        if not movable_nodes:
            self.controller.statusMessage.emit("当前没有可优化的连线布局")
            return False

        adjacency: dict[str, set[str]] = defaultdict(set)
        outgoing: dict[str, list[str]] = defaultdict(list)
        incoming: dict[str, list[str]] = defaultdict(list)
        for from_uuid, to_uuid in edge_pairs:
            if from_uuid not in movable_nodes or to_uuid not in movable_nodes:
                continue
            adjacency[from_uuid].add(to_uuid)
            adjacency[to_uuid].add(from_uuid)
            outgoing[from_uuid].append(to_uuid)
            incoming[to_uuid].append(from_uuid)

        for node_uuid in movable_nodes:
            adjacency.setdefault(node_uuid, set())
            outgoing.setdefault(node_uuid, [])
            incoming.setdefault(node_uuid, [])

        components: list[list[str]] = []
        remaining = set(movable_nodes)
        while remaining:
            start = min(remaining, key=lambda node_uuid: (movable_nodes[node_uuid].ui_position["x"], movable_nodes[node_uuid].ui_position["y"]))
            queue = deque([start])
            component: list[str] = []
            remaining.remove(start)
            while queue:
                current = queue.popleft()
                component.append(current)
                for neighbor in sorted(adjacency[current], key=lambda node_uuid: (movable_nodes[node_uuid].ui_position["x"], movable_nodes[node_uuid].ui_position["y"])):
                    if neighbor in remaining:
                        remaining.remove(neighbor)
                        queue.append(neighbor)
            components.append(component)

        occupied_rects = self._static_obstacle_rects(set(movable_nodes))
        final_positions: dict[str, tuple[float, float]] = {}
        for component in sorted(
            components,
            key=lambda comp: (
                min(movable_nodes[node_uuid].ui_position["x"] for node_uuid in comp),
                min(movable_nodes[node_uuid].ui_position["y"] for node_uuid in comp),
            ),
        ):
            component_positions = self._layout_component(component, outgoing, incoming, occupied_rects)
            if not component_positions:
                continue
            for node_uuid, position in component_positions.items():
                final_positions[node_uuid] = position
                occupied_rects.append(self._expanded_rect_for(node_uuid, position))

        changed_positions = {
            node_uuid: position
            for node_uuid, position in final_positions.items()
            if node_uuid in movable_nodes
            and (
                abs(movable_nodes[node_uuid].ui_position["x"] - position[0]) > 0.5
                or abs(movable_nodes[node_uuid].ui_position["y"] - position[1]) > 0.5
            )
        }
        if not changed_positions:
            self.controller.statusMessage.emit("当前布局已经比较规整")
            return False
        self.controller.move_nodes(changed_positions, label="优化连线布局")
        return True

    def _layout_component(
        self,
        component: list[str],
        outgoing: dict[str, list[str]],
        incoming: dict[str, list[str]],
        occupied_rects: list[QRectF],
    ) -> dict[str, tuple[float, float]]:
        if not component:
            return {}
        component_set = set(component)
        original_x = {node_uuid: float(self.controller.get_node(node_uuid).ui_position["x"]) for node_uuid in component}
        original_y = {node_uuid: float(self.controller.get_node(node_uuid).ui_position["y"]) for node_uuid in component}
        indegree = {node_uuid: sum(1 for source in incoming[node_uuid] if source in component_set) for node_uuid in component}
        roots = sorted(
            [node_uuid for node_uuid in component if indegree[node_uuid] == 0],
            key=lambda node_uuid: (original_x[node_uuid], original_y[node_uuid]),
        )
        if not roots:
            roots = [min(component, key=lambda node_uuid: (original_x[node_uuid], original_y[node_uuid]))]

        levels: dict[str, int] = {node_uuid: 0 for node_uuid in roots}
        queue = deque(roots)
        while queue:
            current = queue.popleft()
            for target in sorted(
                (value for value in outgoing[current] if value in component_set),
                key=lambda node_uuid: (original_y[node_uuid], original_x[node_uuid]),
            ):
                next_level = levels[current] + 1
                if next_level > levels.get(target, -1):
                    levels[target] = next_level
                    queue.append(target)
        for node_uuid in component:
            levels.setdefault(node_uuid, 0)

        max_width = max(self.node_items[node_uuid].boundingRect().width() for node_uuid in component if node_uuid in self.node_items)
        column_gap = max(180.0, max_width * 0.55)
        row_gap = 96.0
        anchor_x = min(original_x.values())
        positions: dict[str, tuple[float, float]] = {}
        child_anchor_cache: dict[str, float] = {}

        for column in sorted(set(levels.values())):
            column_nodes = [node_uuid for node_uuid in component if levels[node_uuid] == column]
            column_nodes.sort(
                key=lambda node_uuid: (
                    self._desired_y_for_layout(
                        node_uuid,
                        positions,
                        outgoing,
                        incoming,
                        original_y,
                        child_anchor_cache,
                        component_set,
                        row_gap,
                    ),
                    original_y[node_uuid],
                    original_x[node_uuid],
                )
            )
            for node_uuid in column_nodes:
                desired_y = self._desired_y_for_layout(
                    node_uuid,
                    positions,
                    outgoing,
                    incoming,
                    original_y,
                    child_anchor_cache,
                    component_set,
                    row_gap,
                )
                x = anchor_x + column * (max_width + column_gap)
                y = self._resolve_non_overlapping_y(node_uuid, x, desired_y, occupied_rects)
                positions[node_uuid] = (x, y)
                occupied_rects.append(self._expanded_rect_for(node_uuid, (x, y)))

                parent_ids = [source for source in incoming[node_uuid] if source in component_set and source in positions]
                if len(parent_ids) == 1:
                    parent_id = parent_ids[0]
                    siblings = [child for child in outgoing[parent_id] if child in component_set]
                    siblings.sort(key=lambda child_uuid: (original_y[child_uuid], original_x[child_uuid]))
                    if len(siblings) > 1 and siblings[0] == node_uuid:
                        child_anchor_cache[parent_id] = y
        return positions

    def _desired_y_for_layout(
        self,
        node_uuid: str,
        positions: dict[str, tuple[float, float]],
        outgoing: dict[str, list[str]],
        incoming: dict[str, list[str]],
        original_y: dict[str, float],
        child_anchor_cache: dict[str, float],
        component_set: set[str],
        row_gap: float,
    ) -> float:
        parent_ids = [source for source in incoming[node_uuid] if source in component_set and source in positions]
        if not parent_ids:
            return original_y.get(node_uuid, 0.0)
        if len(parent_ids) > 1:
            return sum(positions[parent_uuid][1] for parent_uuid in parent_ids) / len(parent_ids)
        parent_id = parent_ids[0]
        siblings = [child for child in outgoing[parent_id] if child in component_set]
        siblings.sort(key=lambda child_uuid: (original_y.get(child_uuid, 0.0), child_uuid))
        parent_y = positions[parent_id][1]
        if len(siblings) <= 1:
            return parent_y
        base_y = child_anchor_cache.get(parent_id, parent_y)
        return base_y + siblings.index(node_uuid) * row_gap

    def _static_obstacle_rects(self, excluded_node_ids: set[str]) -> list[QRectF]:
        rects: list[QRectF] = []
        for node_uuid, item in self.node_items.items():
            if node_uuid in excluded_node_ids:
                continue
            rects.append(self._expanded_rect_for(node_uuid, (item.pos().x(), item.pos().y())))
        return rects

    def _expanded_rect_for(self, node_uuid: str, position: tuple[float, float]) -> QRectF:
        item = self.node_items[node_uuid]
        margin = 26.0
        width = item.boundingRect().width()
        height = item.boundingRect().height()
        return QRectF(position[0] - margin, position[1] - margin, width + margin * 2.0, height + margin * 2.0)

    def _resolve_non_overlapping_y(
        self,
        node_uuid: str,
        x: float,
        desired_y: float,
        occupied_rects: list[QRectF],
    ) -> float:
        candidate_y = desired_y
        for _ in range(240):
            candidate_rect = self._expanded_rect_for(node_uuid, (x, candidate_y))
            if not any(candidate_rect.intersects(other) for other in occupied_rects):
                return candidate_y
            candidate_y += 36.0
        return candidate_y

    def _handle_document_state_changed(self, state) -> None:
        self.scene_ref.top_right_hint = "" if state.is_meta_ready else "请先完成初始节点里的内容"
        self._refresh_hint_overlay()

    def _refresh_hint_overlay(self, *_args) -> None:
        self.scene_ref.top_right_hint = "" if self.controller.document.state.is_meta_ready else "请先完成初始节点里的内容"
        tips = str(self.controller.document.meta.tips or "").strip()
        self.scene_ref.bottom_right_hint = tips or "按住中键平移 / 滚轮缩放 / Delete 删除"
        self.scene_ref.update()

    def _refresh_scale_sensitive_nodes(self) -> None:
        for item in self.node_items.values():
            item.refresh_view_scale()
