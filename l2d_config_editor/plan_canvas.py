"""Lightweight mind-map style plan view for the current formal graph."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QFont,
    QFontMetricsF,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsSceneMouseEvent,
    QGraphicsView,
    QInputDialog,
    QMenu,
    QMessageBox,
    QWidget,
)

from .plan import (
    PLAN_ROOT_COLOR,
    PLAN_UNCONNECTED_TITLE,
    PLAN_UNCONNECTED_UUID,
    plan_root_uuid,
    plan_topic_map,
    plan_topic_title,
)
from .schema import EditorSchema
from .styles import ThemeMode, normalize_theme_mode, palette_for_theme


def _restore_item_opacity(item: QGraphicsItem, opacity: float) -> None:
    """Ignore a delayed emphasis callback after its scene item was rebuilt."""

    try:
        if item.scene() is not None:
            item.setOpacity(opacity)
    except RuntimeError:
        # PySide can keep a Python wrapper briefly after Qt deleted the C++
        # graphics item during a queued scene rebuild.
        return


class PlanTopicItem(QGraphicsObject):
    """One underlined mind-map topic backed by a real node (or virtual branch)."""

    WIDTH = 248.0
    ROOT_WIDTH = 276.0
    HEIGHT = 46.0
    BASELINE_Y = 36.0

    def __init__(
        self,
        view: "PlanCanvasView",
        node_uuid: str,
        title: str,
        color: str,
        *,
        collapsed: bool = False,
        virtual: bool = False,
        root: bool = False,
    ) -> None:
        super().__init__()
        self.view = view
        self.node_uuid = node_uuid
        self.title = title
        self.color = QColor(color)
        self.collapsed = bool(collapsed)
        self.virtual = bool(virtual)
        self.root = bool(root)
        self._drag_start = QPointF()
        flags = QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
        if not self.root and not self.virtual:
            flags |= (
                QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
            )
        self.setFlags(flags)
        self.setAcceptHoverEvents(True)
        self.setCursor(
            Qt.CursorShape.ArrowCursor
            if self.root or self.virtual
            else Qt.CursorShape.OpenHandCursor
        )
        self.setZValue(10.0)
        self.setToolTip(title)

    @property
    def width(self) -> float:
        return self.ROOT_WIDTH if self.root else self.WIDTH

    def boundingRect(self) -> QRectF:
        return QRectF(0.0, 0.0, self.width, self.HEIGHT)

    def connection_point(self, side: str) -> QPointF:
        x = 8.0 if side == "left" else self.width - 8.0
        return self.mapToScene(QPointF(x, self.BASELINE_Y))

    def paint(self, painter: QPainter, option, widget=None) -> None:
        del option, widget
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        palette = self.view.theme_palette
        text_color = QColor(palette.editor_text)
        branch_color = QColor("#8B95A5") if self.virtual else QColor(self.color)
        if self.isSelected():
            selection = QColor(branch_color)
            selection.setAlpha(42)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(selection)
            painter.drawRoundedRect(
                self.boundingRect().adjusted(0.0, 1.0, 0.0, -1.0),
                7.0,
                7.0,
            )

        line_pen = QPen(branch_color, 2.0 if self.root else 1.6)
        line_pen.setCosmetic(True)
        painter.setPen(line_pen)
        painter.setBrush(QColor(palette.canvas_background))
        painter.drawEllipse(QPointF(8.0, self.BASELINE_Y), 5.2, 5.2)
        painter.drawLine(
            QPointF(13.0, self.BASELINE_Y),
            QPointF(self.width - 7.0, self.BASELINE_Y),
        )

        font = QFont(self.view.font())
        font.setPointSizeF(13.0 if self.root else 11.5)
        font.setBold(self.root)
        metrics = QFontMetricsF(font)
        available = max(20.0, self.width - 36.0)
        display = metrics.elidedText(
            self.title,
            Qt.TextElideMode.ElideRight,
            int(available),
        )
        painter.setFont(font)
        painter.setPen(text_color)
        painter.drawText(
            QRectF(18.0, 3.0, available, 29.0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            display,
        )
        if self.collapsed:
            painter.setPen(branch_color)
            painter.drawText(
                QRectF(self.width - 24.0, 3.0, 18.0, 28.0),
                Qt.AlignmentFlag.AlignCenter,
                "›",
            )

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        self._drag_start = QPointF(self.pos())
        if not self.root and not self.virtual:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.view._set_busy("topic_drag", True)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        moved = math.hypot(
            self.pos().x() - self._drag_start.x(),
            self.pos().y() - self._drag_start.y(),
        )
        super().mouseReleaseEvent(event)
        if not self.root and not self.virtual:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.view._set_busy("topic_drag", False)
            if moved > 4.0:
                self.view._handle_topic_drop(
                    self.node_uuid,
                    QPointF(self.pos()),
                    QPointF(self._drag_start),
                )

    def mouseDoubleClickEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if not self.virtual:
            self.view.edit_topic_title(self.node_uuid)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class PlanCanvasView(QGraphicsView):
    """Left-root/right-expanding plan canvas over the formal graph."""

    selectionSummaryChanged = Signal(object, object)
    interactionBusyChanged = Signal(bool)
    HORIZONTAL_GAP = 292.0
    VERTICAL_GAP = 64.0

    def __init__(self, schema: EditorSchema, controller, parent=None) -> None:
        super().__init__(parent)
        self.schema = schema
        self.controller = controller
        self.theme_mode = ThemeMode.DARK
        self.theme_palette = palette_for_theme(self.theme_mode)
        self.scene_ref = QGraphicsScene(self)
        self.setScene(self.scene_ref)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
        )
        self.setViewportUpdateMode(
            QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate
        )
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.AnchorUnderMouse
        )
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.topic_items: dict[str, PlanTopicItem] = {}
        self.reference_items: list[QGraphicsPathItem] = []
        self.primary_items: list[QGraphicsPathItem] = []
        self._busy_flags: set[str] = set()
        self._panning = False
        self._pan_start = QPointF()
        self._selection_guard = False
        self._rebuild_queued = False
        self._restore_view_on_rebuild = True
        self._temporarily_expanded: set[str] = set()
        self.zoom_wheel_modifier = "ctrl"
        self.horizontal_wheel_modifier = "alt_shift"
        self.scene_ref.selectionChanged.connect(self._on_scene_selection_changed)

        controller.documentLoaded.connect(self._on_document_loaded)
        controller.nodeAdded.connect(self._schedule_rebuild)
        controller.nodeRemoved.connect(self._schedule_rebuild)
        controller.nodeUpdated.connect(self._schedule_rebuild)
        controller.connectionsChanged.connect(self._schedule_rebuild)
        controller.planLayoutChanged.connect(self._on_plan_layout_changed)
        controller.planViewChanged.connect(self._apply_stored_view_state)
        controller.selectionChanged.connect(self._handle_controller_selection)
        self.set_ui_theme(self.theme_mode)
        self.rebuild_scene(restore_view=True)

    def _on_document_loaded(self) -> None:
        self._temporarily_expanded.clear()
        self._restore_view_on_rebuild = True
        self._schedule_rebuild()

    def _on_plan_layout_changed(self) -> None:
        self._temporarily_expanded.clear()
        self._schedule_rebuild()

    def _schedule_rebuild(self, *_args: Any) -> None:
        if self._rebuild_queued:
            return
        self._rebuild_queued = True
        QTimer.singleShot(0, self._run_scheduled_rebuild)

    def _run_scheduled_rebuild(self) -> None:
        self._rebuild_queued = False
        restore = self._restore_view_on_rebuild
        self._restore_view_on_rebuild = False
        self.rebuild_scene(restore_view=restore)

    def set_ui_theme(self, mode: ThemeMode | str) -> None:
        self.theme_mode = normalize_theme_mode(mode)
        self.theme_palette = palette_for_theme(self.theme_mode)
        self.scene_ref.setBackgroundBrush(QColor(self.theme_palette.canvas_background))
        for item in self.scene_ref.items():
            item.update()
        self.viewport().update()

    def _set_busy(self, flag: str, busy: bool) -> None:
        before = bool(self._busy_flags)
        if busy:
            self._busy_flags.add(flag)
        else:
            self._busy_flags.discard(flag)
        after = bool(self._busy_flags)
        if before != after:
            self.interactionBusyChanged.emit(after)

    def is_busy(self) -> bool:
        return bool(self._busy_flags)

    def _visible_layout(
        self,
        topics: dict[str, Any],
        children: dict[str | None, list[str]],
        root_uuid: str | None,
    ) -> tuple[dict[str, QPointF], list[tuple[str, str]]]:
        if root_uuid is None:
            return {}, []
        orphan_roots = list(children.get(None, ()))

        def visible_children(node_uuid: str) -> list[str]:
            if node_uuid == PLAN_UNCONNECTED_UUID:
                return orphan_roots
            topic = topics.get(node_uuid)
            if (
                topic is not None
                and topic.collapsed
                and node_uuid not in self._temporarily_expanded
            ):
                return []
            result = list(children.get(node_uuid, ()))
            if node_uuid == root_uuid and orphan_roots:
                result.append(PLAN_UNCONNECTED_UUID)
            return result

        positions: dict[str, QPointF] = {}
        edges: list[tuple[str, str]] = []
        next_leaf_y = 0.0
        visible_by_parent: dict[str, list[str]] = {}
        # Explicit pre/post stack avoids Python recursion limits for imported
        # graphs with thousands of levels.
        stack: list[tuple[str, int, bool]] = [(root_uuid, 0, False)]
        while stack:
            node_uuid, depth, postorder = stack.pop()
            if not postorder:
                child_ids = visible_children(node_uuid)
                visible_by_parent[node_uuid] = child_ids
                stack.append((node_uuid, depth, True))
                edges.extend((node_uuid, child_uuid) for child_uuid in child_ids)
                for child_uuid in reversed(child_ids):
                    stack.append((child_uuid, depth + 1, False))
                continue
            child_ids = visible_by_parent.get(node_uuid, ())
            if child_ids:
                y = (
                    positions[child_ids[0]].y()
                    + positions[child_ids[-1]].y()
                ) * 0.5
            else:
                y = next_leaf_y
                next_leaf_y += self.VERTICAL_GAP
            positions[node_uuid] = QPointF(depth * self.HORIZONTAL_GAP, y)
        return positions, edges

    def rebuild_scene(self, *, restore_view: bool = False) -> None:
        selected = set(self.selected_node_uuids())
        self._selection_guard = True
        try:
            self.scene_ref.clear()
            self.topic_items.clear()
            self.reference_items.clear()
            self.primary_items.clear()
            layout = self.controller.ensure_plan_layout()
            topics = {
                topic.node_uuid: topic
                for topic in layout.topics
            }
            children: dict[str | None, list[str]] = defaultdict(list)
            root_uuid = plan_root_uuid(self.controller.document)
            for topic in topics.values():
                if topic.node_uuid != root_uuid:
                    children[topic.parent_uuid].append(topic.node_uuid)
            for child_ids in children.values():
                child_ids.sort(
                    key=lambda node_uuid: (
                        topics[node_uuid].order,
                        node_uuid,
                    )
                )
            node_by_uuid = {
                node.uuid: node for node in self.controller.document.nodes
            }
            positions, primary_edges = self._visible_layout(
                topics,
                children,
                root_uuid,
            )
            title_by_uuid: dict[str, str] = {}
            for node_uuid, position in positions.items():
                if node_uuid == PLAN_UNCONNECTED_UUID:
                    item = PlanTopicItem(
                        self,
                        node_uuid,
                        PLAN_UNCONNECTED_TITLE,
                        "#8B95A5",
                        virtual=True,
                    )
                else:
                    node = node_by_uuid.get(node_uuid)
                    topic = topics.get(node_uuid)
                    if node is None or topic is None:
                        continue
                    topic_title = plan_topic_title(
                        self.schema,
                        self.controller.document,
                        node,
                        topic,
                    )
                    title_by_uuid[node_uuid] = topic_title
                    item = PlanTopicItem(
                        self,
                        node_uuid,
                        topic_title,
                        topic.branch_color or PLAN_ROOT_COLOR,
                        collapsed=topic.collapsed,
                        root=node_uuid == root_uuid,
                    )
                item.setPos(position)
                self.scene_ref.addItem(item)
                self.topic_items[node_uuid] = item

            for from_uuid, to_uuid in primary_edges:
                source = self.topic_items.get(from_uuid)
                target = self.topic_items.get(to_uuid)
                if source is None or target is None:
                    continue
                color = (
                    QColor("#8B95A5")
                    if PLAN_UNCONNECTED_UUID in {from_uuid, to_uuid}
                    else QColor(target.color)
                )
                path_item = self._make_curve(source, target, color, dashed=False)
                self.primary_items.append(path_item)

            primary_pairs = {
                (topic.parent_uuid, topic.node_uuid)
                for topic in topics.values()
                if topic.parent_uuid is not None
            }
            for connection in self.controller.document.connections:
                if (connection.from_uuid, connection.to_uuid) in primary_pairs:
                    continue
                source = self.topic_items.get(connection.from_uuid)
                target = self.topic_items.get(connection.to_uuid)
                if source is None or target is None:
                    continue
                path_item = self._make_curve(
                    source,
                    target,
                    QColor(self.theme_palette.connection_normal),
                    dashed=True,
                )
                path_item.setToolTip(
                    f"引用：{title_by_uuid.get(connection.from_uuid, connection.from_uuid)} → "
                    f"{title_by_uuid.get(connection.to_uuid, connection.to_uuid)}"
                )
                self.reference_items.append(path_item)

            for node_uuid in selected:
                item = self.topic_items.get(node_uuid)
                if item is not None:
                    item.setSelected(True)
            bounds = self.scene_ref.itemsBoundingRect()
            if bounds.isValid():
                self.scene_ref.setSceneRect(bounds.adjusted(-100.0, -100.0, 140.0, 100.0))
        finally:
            self._selection_guard = False

        if restore_view:
            self._apply_stored_view_state()

    def _make_curve(
        self,
        source: PlanTopicItem,
        target: PlanTopicItem,
        color: QColor,
        *,
        dashed: bool,
    ) -> QGraphicsPathItem:
        start = source.connection_point("right")
        end = target.connection_point("left")
        span = max(48.0, abs(end.x() - start.x()) * 0.52)
        path = QPainterPath(start)
        path.cubicTo(
            QPointF(start.x() + span, start.y()),
            QPointF(end.x() - span, end.y()),
            end,
        )
        item = QGraphicsPathItem(path)
        pen = QPen(color, 1.4 if dashed else 1.8)
        pen.setCosmetic(True)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        if dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
            color.setAlpha(170)
            pen.setColor(color)
            item.setZValue(-4.0)
        else:
            item.setZValue(-6.0)
        item.setPen(pen)
        self.scene_ref.addItem(item)
        return item

    def selected_node_uuids(self) -> list[str]:
        return [
            item.node_uuid
            for item in self.scene_ref.selectedItems()
            if isinstance(item, PlanTopicItem) and not item.virtual
        ]

    def selected_connection_pairs(self) -> list[tuple[str, str]]:
        return []

    def select_node_uuids(self, node_uuids: list[str]) -> None:
        requested = [
            node_uuid
            for node_uuid in dict.fromkeys(node_uuids)
            if self.controller.get_node(node_uuid) is not None
        ]
        missing = [
            node_uuid
            for node_uuid in requested
            if node_uuid not in self.topic_items
        ]
        if missing:
            topics = plan_topic_map(self.controller.document)
            reveal: set[str] = set()
            for node_uuid in missing:
                current = topics.get(node_uuid)
                seen: set[str] = set()
                while current is not None and current.parent_uuid is not None:
                    if current.parent_uuid in seen:
                        break
                    seen.add(current.parent_uuid)
                    reveal.add(current.parent_uuid)
                    current = topics.get(current.parent_uuid)
            self._temporarily_expanded.update(reveal)
            self.rebuild_scene()
        self._selection_guard = True
        try:
            self.scene_ref.clearSelection()
            for node_uuid in requested:
                item = self.topic_items.get(node_uuid)
                if item is not None and not item.virtual:
                    item.setSelected(True)
            selected = self.selected_node_uuids()
            self.selectionSummaryChanged.emit(selected, [])
            self.controller.set_selected_node(
                selected[0] if len(selected) == 1 else None
            )
        finally:
            self._selection_guard = False

    def _on_scene_selection_changed(self) -> None:
        if self._selection_guard:
            return
        selected = self.selected_node_uuids()
        self.selectionSummaryChanged.emit(selected, [])
        self._selection_guard = True
        try:
            self.controller.set_selected_node(
                selected[0] if len(selected) == 1 else None
            )
        finally:
            self._selection_guard = False

    def _handle_controller_selection(self, node_uuid: str | None) -> None:
        if self._selection_guard:
            return
        self._selection_guard = True
        try:
            self.scene_ref.clearSelection()
            item = self.topic_items.get(node_uuid or "")
            if item is not None:
                item.setSelected(True)
        finally:
            self._selection_guard = False

    def focus_on_node(
        self,
        node_uuid: str,
        target_scale: float | None = None,
        emphasize: bool = False,
    ) -> None:
        item = self.topic_items.get(node_uuid)
        if item is None and self.controller.get_node(node_uuid) is not None:
            topics = plan_topic_map(self.controller.document)
            current = topics.get(node_uuid)
            reveal: set[str] = set()
            seen: set[str] = set()
            while current is not None and current.parent_uuid is not None:
                if current.parent_uuid in seen:
                    break
                seen.add(current.parent_uuid)
                reveal.add(current.parent_uuid)
                current = topics.get(current.parent_uuid)
            self._temporarily_expanded.update(reveal)
            self.rebuild_scene()
            item = self.topic_items.get(node_uuid)
        if item is None:
            return
        if target_scale is not None and self.transform().m11() < float(target_scale):
            current = max(0.001, self.transform().m11())
            factor = min(8.0, float(target_scale)) / current
            self.scale(factor, factor)
        self.centerOn(item.sceneBoundingRect().center())
        self._selection_guard = True
        try:
            self.scene_ref.clearSelection()
            item.setSelected(True)
        finally:
            self._selection_guard = False
        self.controller.set_selected_node(node_uuid)
        if emphasize:
            original = item.opacity()
            item.setOpacity(0.35)
            QTimer.singleShot(
                120,
                lambda target=item, opacity=original: _restore_item_opacity(
                    target,
                    opacity,
                ),
            )
        self._store_view_state()

    def reset_view_layout(self) -> None:
        self.resetTransform()
        bounds = self.scene_ref.itemsBoundingRect()
        if bounds.isValid() and not bounds.isEmpty():
            self.fitInView(
                bounds.adjusted(-40.0, -40.0, 40.0, 40.0),
                Qt.AspectRatioMode.KeepAspectRatio,
            )
            if self.transform().m11() > 1.15:
                self.resetTransform()
                self.scale(1.15, 1.15)
                self.centerOn(bounds.center())
        else:
            self.centerOn(0.0, 0.0)
        self._store_view_state()

    def _store_view_state(self) -> None:
        center = self.mapToScene(self.viewport().rect().center())
        self.controller.set_plan_view_state(
            max(0.03, min(8.0, float(self.transform().m11()))),
            float(center.x()),
            float(center.y()),
        )

    def _apply_stored_view_state(self, *_args: Any) -> None:
        state = self.controller.document.plan_layout.view
        self.resetTransform()
        if state.scale != 1.0:
            self.scale(state.scale, state.scale)
        self.centerOn(state.offset_x, state.offset_y)

    @staticmethod
    def _matches_wheel_modifier(
        config: str,
        modifiers: Qt.KeyboardModifier,
    ) -> bool:
        if config == "none":
            return modifiers == Qt.KeyboardModifier.NoModifier
        if config == "ctrl":
            return bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        if config == "alt":
            return bool(modifiers & Qt.KeyboardModifier.AltModifier)
        if config == "shift":
            return bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        if config == "alt_shift":
            return bool(
                modifiers
                & (
                    Qt.KeyboardModifier.AltModifier
                    | Qt.KeyboardModifier.ShiftModifier
                )
            )
        return False

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if not delta:
            event.accept()
            return
        modifiers = event.modifiers()
        if self._matches_wheel_modifier(self.zoom_wheel_modifier, modifiers):
            factor = 1.22 ** (delta / 120.0)
            current = max(0.001, float(self.transform().m11()))
            target = current * factor
            if 0.03 <= target <= 8.0:
                self.scale(factor, factor)
        else:
            step = 60 if delta > 0 else -60
            if self._matches_wheel_modifier(
                self.horizontal_wheel_modifier,
                modifiers,
            ):
                self.horizontalScrollBar().setValue(
                    self.horizontalScrollBar().value() - step
                )
            else:
                self.verticalScrollBar().setValue(
                    self.verticalScrollBar().value() - step
                )
        event.accept()
        self._store_view_state()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_start = QPointF(event.position())
            self._set_busy("pan", True)
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._panning:
            delta = event.position() - self._pan_start
            self._pan_start = QPointF(event.position())
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - int(delta.x())
            )
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - int(delta.y())
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton and self._panning:
            self._panning = False
            self._set_busy("pan", False)
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            self._store_view_state()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _handle_topic_drop(
        self,
        node_uuid: str,
        dropped_position: QPointF,
        original_position: QPointF,
    ) -> None:
        topic = self.controller.plan_topic(node_uuid)
        if topic is None:
            self.rebuild_scene()
            return
        horizontal_delta = dropped_position.x() - original_position.x()
        if abs(horizontal_delta) < self.HORIZONTAL_GAP * 0.32:
            siblings = self.controller.plan_children(topic.parent_uuid)
            ordered = [
                sibling_uuid
                for sibling_uuid in siblings
                if sibling_uuid != node_uuid
            ]
            drop_y = dropped_position.y()
            index = sum(
                1
                for sibling_uuid in ordered
                if (
                    self.topic_items.get(sibling_uuid) is not None
                    and self.topic_items[sibling_uuid].pos().y() < drop_y
                )
            )
            if not self.controller.reorder_plan_topic(node_uuid, index):
                self.rebuild_scene()
            return

        drop_center = dropped_position + QPointF(
            PlanTopicItem.WIDTH * 0.5,
            PlanTopicItem.HEIGHT * 0.5,
        )
        candidates: list[tuple[float, PlanTopicItem]] = []
        for candidate in self.topic_items.values():
            if candidate.node_uuid == node_uuid:
                continue
            center = candidate.sceneBoundingRect().center()
            if center.x() > drop_center.x() + 30.0:
                continue
            distance = math.hypot(center.x() - drop_center.x(), center.y() - drop_center.y())
            candidates.append((distance, candidate))
        candidates.sort(key=lambda item: item[0])
        if candidates and candidates[0][0] <= self.HORIZONTAL_GAP * 0.75:
            target = candidates[0][1]
            new_parent = None if target.virtual else target.node_uuid
            if not self.controller.reparent_plan_topic(
                node_uuid,
                new_parent,
            ):
                self.rebuild_scene()
            return
        self.rebuild_scene()

    def edit_topic_title(self, node_uuid: str) -> bool:
        if node_uuid == PLAN_UNCONNECTED_UUID or self.controller.get_node(node_uuid) is None:
            return False
        current = self.controller.plan_title(node_uuid)
        title, accepted = QInputDialog.getText(
            self,
            "编辑计划主题",
            "主题：",
            text=current,
        )
        if not accepted:
            return False
        return self.controller.set_plan_title(node_uuid, title)

    def create_sibling_topic(self, title: str = "新主题") -> str | None:
        selected = self.selected_node_uuids()
        if not selected:
            root_uuid = plan_root_uuid(self.controller.document)
            return self.controller.create_plan_topic(root_uuid, title)
        root_uuid = plan_root_uuid(self.controller.document)
        if selected[0] == root_uuid:
            return self.controller.create_plan_topic(root_uuid, title)
        topic = self.controller.plan_topic(selected[0])
        if topic is None:
            return None
        return self.controller.create_plan_topic(
            topic.parent_uuid,
            title,
            after_uuid=topic.node_uuid,
        )

    def create_child_topic(self, title: str = "新主题") -> str | None:
        selected = self.selected_node_uuids()
        parent_uuid = selected[0] if selected else plan_root_uuid(self.controller.document)
        if parent_uuid is None:
            return None
        return self.controller.create_plan_topic(parent_uuid, title)

    def duplicate_selected_as_sibling(self) -> str | None:
        selected = self.selected_node_uuids()
        if not selected:
            return None
        topic = self.controller.plan_topic(selected[0])
        if topic is None or selected[0] == plan_root_uuid(self.controller.document):
            return None
        payload = self.controller.serialize_selection([selected[0]])
        source = self.controller.get_node(selected[0])
        if payload is None or source is None:
            return None
        pasted = self.controller.paste_payload(
            payload,
            (
                float(source.ui_position.get("x", 0.0)) + 40.0,
                float(source.ui_position.get("y", 0.0)) + 40.0,
            ),
            override_plan_parent=True,
            plan_parent_uuid=topic.parent_uuid,
            plan_after_uuid=selected[0],
        )
        return pasted[0] if pasted else None

    def delete_selected_subtrees(
        self,
        parent_widget: QWidget | None = None,
    ) -> bool:
        selected = self.selected_node_uuids()
        root_uuid = plan_root_uuid(self.controller.document)
        roots = [node_uuid for node_uuid in selected if node_uuid != root_uuid]
        node_ids: set[str] = set()
        for node_uuid in roots:
            node_ids.update(self.controller.plan_subtree_uuids(node_uuid))
        if not node_ids:
            return False
        answer = QMessageBox.question(
            parent_widget or self,
            "删除计划子树",
            f"将删除 {len(node_ids)} 个计划主题及其正式节点和连线。\n"
            "此操作可一次撤销，是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        return self.controller.delete_plan_subtrees(roots)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        item = self.itemAt(event.pos())
        topic_item = item if isinstance(item, PlanTopicItem) else None
        menu = QMenu(self)
        sibling_action = menu.addAction("新增同级主题")
        child_action = menu.addAction("新增子主题")
        edit_action = menu.addAction("编辑标题")
        collapse_action = menu.addAction("折叠/展开")
        promote_action = menu.addAction("提升层级")
        delete_action = menu.addAction("删除子树")
        if topic_item is None or topic_item.virtual:
            edit_action.setEnabled(False)
            collapse_action.setEnabled(False)
            promote_action.setEnabled(False)
            delete_action.setEnabled(False)
        elif topic_item.root:
            sibling_action.setEnabled(False)
            promote_action.setEnabled(False)
            delete_action.setEnabled(False)
        selected = menu.exec(event.globalPos())
        if selected == sibling_action:
            self.create_sibling_topic()
        elif selected == child_action:
            self.create_child_topic()
        elif selected == edit_action and topic_item is not None:
            self.edit_topic_title(topic_item.node_uuid)
        elif selected == collapse_action and topic_item is not None:
            self.controller.toggle_plan_collapsed(topic_item.node_uuid)
        elif selected == promote_action and topic_item is not None:
            self.controller.promote_plan_topic(topic_item.node_uuid)
        elif selected == delete_action:
            self.delete_selected_subtrees()

    def focusNextPrevChild(self, next: bool) -> bool:
        del next
        return False

    def keyPressEvent(self, event: QKeyEvent) -> None:
        selected = self.selected_node_uuids()
        node_uuid = selected[0] if selected else None
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            self.create_sibling_topic()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Tab and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.create_child_topic()
            event.accept()
            return
        if event.key() in {Qt.Key.Key_Backtab, Qt.Key.Key_Tab} and (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            if node_uuid:
                self.controller.promote_plan_topic(node_uuid)
            event.accept()
            return
        if event.key() == Qt.Key.Key_F2 and node_uuid:
            self.edit_topic_title(node_uuid)
            event.accept()
            return
        if event.key() == Qt.Key.Key_Space and node_uuid:
            self.controller.toggle_plan_collapsed(node_uuid)
            event.accept()
            return
        if event.key() == Qt.Key.Key_Delete:
            self.delete_selected_subtrees()
            event.accept()
            return
        if node_uuid and event.key() in {
            Qt.Key.Key_Left,
            Qt.Key.Key_Right,
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
        }:
            target_uuid = self._navigation_target(node_uuid, event.key())
            if target_uuid:
                self.focus_on_node(target_uuid)
            event.accept()
            return
        super().keyPressEvent(event)

    def _navigation_target(self, node_uuid: str, key: Qt.Key) -> str | None:
        topic = self.controller.plan_topic(node_uuid)
        if topic is None:
            return None
        if key == Qt.Key.Key_Left:
            return topic.parent_uuid
        if key == Qt.Key.Key_Right:
            children = self.controller.plan_children(node_uuid)
            return children[0] if children else None
        visible = [
            item
            for item in self.topic_items.values()
            if not item.virtual and item.node_uuid != node_uuid
        ]
        current = self.topic_items.get(node_uuid)
        if current is None:
            return None
        current_y = current.sceneBoundingRect().center().y()
        if key == Qt.Key.Key_Up:
            candidates = [
                item for item in visible
                if item.sceneBoundingRect().center().y() < current_y
            ]
            if not candidates:
                return None
            return max(
                candidates,
                key=lambda item: item.sceneBoundingRect().center().y(),
            ).node_uuid
        candidates = [
            item for item in visible
            if item.sceneBoundingRect().center().y() > current_y
        ]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda item: item.sceneBoundingRect().center().y(),
        ).node_uuid
