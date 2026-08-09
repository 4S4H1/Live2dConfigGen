"""Lightweight mind-map style plan view for the current formal graph."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QFont,
    QFontMetricsF,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
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

from .canvas import CanvasStrokeItem
from .plan import (
    PLAN_NEUTRAL_COLOR,
    PLAN_ROOT_COLOR,
    PLAN_TOUCHDRAG_COLOR,
    PLAN_TOUCHIDLE_COLOR,
    PLAN_UNCONNECTED_TITLE,
    PLAN_UNCONNECTED_UUID,
    plan_root_uuid,
    parse_touchidle_plan_title,
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


@dataclass(frozen=True)
class _PlanTopicCardSpec:
    """Measured, zoom-independent geometry for one plan topic card."""

    bounds: QRectF
    text_lines: tuple[str, ...] = ()
    semantic_draw_text: str = ""
    semantic_separator_text: str = ""
    semantic_action_text: str = ""
    note_lines: tuple[str, ...] = ()

    @property
    def semantic(self) -> bool:
        return bool(self.semantic_draw_text or self.semantic_action_text)


@dataclass(frozen=True)
class _TopicDropIntent:
    """The structural edit currently implied by a topic's drag position."""

    action: str
    new_parent_uuid: str | None = None
    index: int | None = None


class PlanTopicItem(QGraphicsObject):
    """A measured plan card which becomes a text-free outline in overview."""

    MIN_CARD_WIDTH = 156.0
    MAX_CARD_WIDTH = 360.0
    MIN_SEMANTIC_CARD_WIDTH = 238.0
    MIN_CARD_HEIGHT = 52.0
    CARD_PADDING_X = 16.0
    CARD_PADDING_Y = 12.0
    CARD_LINE_GAP = 4.0
    CARD_CORNER_RADIUS = 8.0
    TITLE_POINT_SIZE = 13.5
    ROOT_TITLE_POINT_SIZE = 15.0
    # Text remains visible through ordinary manual zoom.  Overview is reserved
    # for a genuinely remote view, while focus restores a comfortably readable
    # scale rather than merely crossing the overview boundary.
    OVERVIEW_SCALE = 0.45
    READABLE_SCALE = 0.85

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
        sequence_locked: bool = False,
        semantic_title=None,
        card_spec: _PlanTopicCardSpec | None = None,
    ) -> None:
        super().__init__()
        self.view = view
        self.node_uuid = node_uuid
        self.title = title
        self.color = QColor(color)
        self.collapsed = bool(collapsed)
        self.virtual = bool(virtual)
        self.root = bool(root)
        self.sequence_locked = bool(sequence_locked)
        self.semantic_title = semantic_title
        self._card_spec = card_spec or self.measure_card(
            title,
            root=self.root,
            semantic_title=self.semantic_title,
            font=self._topic_font(),
        )
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
        semantic_hint = ""
        resolved_color = self.color.name().upper()
        if resolved_color == PLAN_TOUCHIDLE_COLOR:
            semantic_hint = "\n绿色：转换为 TouchIdle"
        elif resolved_color == PLAN_TOUCHDRAG_COLOR:
            semantic_hint = "\n紫色：转换为 TouchDrag"
        if self.sequence_locked:
            semantic_hint += "\n金色边框：序号已固定"
        self.setToolTip(f"{title}{semantic_hint}")

    def boundingRect(self) -> QRectF:
        return QRectF(self._card_spec.bounds)

    def connection_point(self, side: str) -> QPointF:
        bounds = self.boundingRect()
        x = bounds.left() if side == "left" else bounds.right()
        return self.mapToScene(QPointF(x, bounds.center().y()))

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value: Any):
        result = super().itemChange(change, value)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.view._on_topic_item_position_changed(self)
        return result

    @staticmethod
    def _elide_semantic_segment(
        metrics: QFontMetricsF,
        text: str,
        available_width: float,
    ) -> str:
        """Keep the numeric suffix visible for planned frame/action names."""

        return metrics.elidedText(
            str(text or ""),
            Qt.TextElideMode.ElideLeft,
            max(1, int(available_width)),
        )

    def _topic_font(self) -> QFont:
        """Return the stable scene font; semantic zoom controls readability."""

        font = QFont(self.view.font())
        font.setPointSizeF(
            self.ROOT_TITLE_POINT_SIZE if self.root else self.TITLE_POINT_SIZE
        )
        font.setBold(self.root)
        return font

    @staticmethod
    def _wrap_text_lines(
        metrics: QFontMetricsF,
        text: str,
        available_width: float,
        *,
        max_lines: int = 2,
    ) -> tuple[str, ...]:
        """Wrap CJK and Latin titles, eliding only a genuinely overlong tail."""

        remaining = str(text or "").strip() or "（无标题）"
        width = max(1, int(available_width))
        lines: list[str] = []
        while remaining and len(lines) < max_lines:
            if metrics.horizontalAdvance(remaining) <= width:
                lines.append(remaining)
                remaining = ""
                break
            cut = 1
            while (
                cut < len(remaining)
                and metrics.horizontalAdvance(remaining[: cut + 1]) <= width
            ):
                cut += 1
            whitespace = max(
                remaining.rfind(" ", 0, cut),
                remaining.rfind("\t", 0, cut),
            )
            if whitespace > 0:
                cut = whitespace + 1
            lines.append(remaining[:cut].rstrip())
            remaining = remaining[cut:].lstrip()
        if remaining:
            prefix = lines.pop() if lines else ""
            lines.append(
                metrics.elidedText(
                    f"{prefix}{remaining}",
                    Qt.TextElideMode.ElideRight,
                    width,
                )
            )
        return tuple(lines or ("（无标题）",))

    @classmethod
    def measure_card(
        cls,
        title: str,
        *,
        root: bool,
        semantic_title,
        font: QFont,
    ) -> _PlanTopicCardSpec:
        """Measure card content once per title/theme/font instead of per paint."""

        metrics = QFontMetricsF(font)
        line_height = max(1.0, float(metrics.lineSpacing()))
        if semantic_title is None:
            natural_width = metrics.horizontalAdvance(str(title or ""))
            width = min(
                cls.MAX_CARD_WIDTH,
                max(cls.MIN_CARD_WIDTH, natural_width + cls.CARD_PADDING_X * 2.0),
            )
            lines = cls._wrap_text_lines(
                metrics,
                title,
                width - cls.CARD_PADDING_X * 2.0,
            )
            height = max(
                cls.MIN_CARD_HEIGHT,
                cls.CARD_PADDING_Y * 2.0
                + line_height * len(lines)
                + cls.CARD_LINE_GAP * max(0, len(lines) - 1),
            )
            return _PlanTopicCardSpec(
                bounds=QRectF(0.0, 0.0, width, height),
                text_lines=lines,
            )

        separator_width = max(
            14.0,
            float(metrics.horizontalAdvance(semantic_title.separator_text)),
        )
        natural_width = (
            metrics.horizontalAdvance(semantic_title.draw_text)
            + metrics.horizontalAdvance(semantic_title.action_text)
            + separator_width
            + cls.CARD_PADDING_X * 2.0
            + 12.0
        )
        width = min(
            cls.MAX_CARD_WIDTH,
            max(cls.MIN_SEMANTIC_CARD_WIDTH, natural_width),
        )
        available = width - cls.CARD_PADDING_X * 2.0 - separator_width - 8.0
        part_width = max(24.0, available * 0.5)
        draw_text = cls._elide_semantic_segment(
            metrics, semantic_title.draw_text, part_width
        )
        action_text = cls._elide_semantic_segment(
            metrics, semantic_title.action_text, part_width
        )
        note_lines = (
            cls._wrap_text_lines(
                metrics,
                semantic_title.note_text,
                width - cls.CARD_PADDING_X * 2.0,
            )
            if semantic_title.note_text
            else ()
        )
        height = (
            cls.CARD_PADDING_Y * 2.0
            + line_height
            + (
                cls.CARD_LINE_GAP
                + len(note_lines) * line_height
                + cls.CARD_LINE_GAP * max(0, len(note_lines) - 1)
                if note_lines
                else 0.0
            )
        )
        return _PlanTopicCardSpec(
            bounds=QRectF(0.0, 0.0, width, max(cls.MIN_CARD_HEIGHT, height)),
            semantic_draw_text=draw_text,
            semantic_separator_text=semantic_title.separator_text,
            semantic_action_text=action_text,
            note_lines=note_lines,
        )

    @staticmethod
    def _sequence_lock_pen() -> QPen:
        # Keep the fixed outline proportional to the plan card.  A cosmetic
        # pen stays several pixels wide while the card shrinks and therefore
        # looks progressively heavier at overview zoom levels.
        return QPen(QColor("#F2C14E"), 3.4)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        del option, widget
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        palette = self.view.theme_palette
        text_color = QColor(palette.editor_text)
        branch_color = QColor(PLAN_NEUTRAL_COLOR) if self.virtual else QColor(self.color)
        bounds = self.boundingRect().adjusted(0.75, 0.75, -0.75, -0.75)
        fill = QColor(branch_color)
        fill.setAlpha(56 if self.isSelected() else 22)
        line_pen = QPen(branch_color, 2.0 if self.root else 1.35)
        line_pen.setCosmetic(True)
        painter.setPen(line_pen)
        painter.setBrush(fill)
        painter.drawRoundedRect(bounds, self.CARD_CORNER_RADIUS, self.CARD_CORNER_RADIUS)
        if self.sequence_locked:
            painter.setPen(self._sequence_lock_pen())
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                bounds.adjusted(2.5, 2.5, -2.5, -2.5),
                self.CARD_CORNER_RADIUS - 1.0,
                self.CARD_CORNER_RADIUS - 1.0,
            )
        if self.view.is_overview_mode():
            return

        font = self._topic_font()
        metrics = QFontMetricsF(font)
        painter.setFont(font)
        line_height = max(1.0, float(metrics.lineSpacing()))
        x = self.CARD_PADDING_X
        y = self.CARD_PADDING_Y
        available = self.boundingRect().width() - self.CARD_PADDING_X * 2.0
        if not self._card_spec.semantic:
            painter.setPen(text_color)
            for line in self._card_spec.text_lines:
                painter.drawText(
                    QRectF(x, y, available, line_height),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    line,
                )
                y += line_height + self.CARD_LINE_GAP
        else:
            blue = QColor(
                "#78B1FF"
                if self.view.theme_mode is ThemeMode.DARK
                else "#1D4ED8"
            )
            orange = QColor(
                "#F6C85F"
                if self.view.theme_mode is ThemeMode.DARK
                else "#9A3412"
            )
            separator_width = max(
                14.0,
                float(metrics.horizontalAdvance(self._card_spec.semantic_separator_text)),
            )
            part_width = max(24.0, (available - separator_width - 8.0) * 0.5)
            segments = [
                (
                    self._card_spec.semantic_draw_text,
                    part_width,
                    blue,
                    Qt.AlignmentFlag.AlignRight,
                ),
                (
                    self._card_spec.semantic_separator_text,
                    separator_width + 8.0,
                    text_color,
                    Qt.AlignmentFlag.AlignCenter,
                ),
                (
                    self._card_spec.semantic_action_text,
                    part_width,
                    orange,
                    Qt.AlignmentFlag.AlignRight,
                ),
            ]
            for segment, width, color, alignment in segments:
                painter.setPen(color)
                painter.drawText(
                    QRectF(x, y, width, line_height),
                    alignment | Qt.AlignmentFlag.AlignVCenter,
                    segment,
                )
                x += width
            y += line_height
            if self._card_spec.note_lines:
                y += self.CARD_LINE_GAP
                painter.setPen(text_color)
                for line in self._card_spec.note_lines:
                    painter.drawText(
                        QRectF(
                            self.CARD_PADDING_X,
                            y,
                            available,
                            line_height,
                        ),
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                        line,
                    )
                    y += line_height + self.CARD_LINE_GAP
        if self.collapsed:
            painter.setPen(branch_color)
            painter.drawText(
                QRectF(
                    self.boundingRect().right() - 22.0,
                    self.CARD_PADDING_Y,
                    18.0,
                    line_height,
                ),
                Qt.AlignmentFlag.AlignCenter,
                "›",
            )

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        self._drag_start = QPointF(self.pos())
        if not self.root and not self.virtual:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.view._set_busy("topic_drag", True)
            self.view._begin_topic_drag(self.node_uuid, self._drag_start)
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
            self.view._end_topic_drag()
            if moved > 4.0:
                self.view._handle_topic_drop(
                    self.node_uuid,
                    QPointF(self.pos()),
                    QPointF(self._drag_start),
                )
            elif self.view.is_overview_mode() and not event.modifiers():
                QTimer.singleShot(
                    0,
                    lambda node_uuid=self.node_uuid: self.view.focus_on_node(
                        node_uuid,
                        target_scale=self.READABLE_SCALE,
                    ),
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
    COLUMN_GAP = 96.0
    SIBLING_GAP = 28.0
    REORDER_HORIZONTAL_SLOP = 72.0
    REPARENT_DISTANCE = 360.0

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
        self._curve_bindings: list[
            tuple[QGraphicsPathItem, PlanTopicItem, PlanTopicItem]
        ] = []
        self._dragged_topic_uuid: str | None = None
        self._drag_original_position = QPointF()
        self._drop_preview_item = QGraphicsPathItem()
        self._drop_preview_item.setZValue(-2.0)
        self._drop_preview_item.hide()
        self.scene_ref.addItem(self._drop_preview_item)
        self.stroke_items: dict[str, CanvasStrokeItem] = {}
        self.pen_color = "#2F80ED"
        self.pen_width = 4.0
        self._drawing_stroke = False
        self._drawing_points: list[QPointF] = []
        self._pending_ctrl_stroke = False
        self._ctrl_stroke_start_view = QPointF()
        self._ctrl_stroke_start_scene = QPointF()
        self._ctrl_stroke_topic_uuid: str | None = None
        self._ctrl_stroke_threshold_px = 4.0
        self._erasing_strokes = False
        self._erased_stroke_uuids: set[str] = set()
        self._eraser_last_scene = QPointF()
        self._temporary_stroke = QGraphicsPathItem()
        self._temporary_stroke.setZValue(92.0)
        self._temporary_stroke.hide()
        self.scene_ref.addItem(self._temporary_stroke)
        self._busy_flags: set[str] = set()
        self._panning = False
        self._pan_start = QPointF()
        self._selection_guard = False
        self._rebuild_queued = False
        self._restore_view_on_rebuild = True
        self._temporarily_expanded: set[str] = set()
        self._card_spec_cache: dict[tuple[Any, ...], _PlanTopicCardSpec] = {}
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
        controller.planCanvasStrokesChanged.connect(self._sync_canvas_strokes)
        self.set_ui_theme(self.theme_mode)
        self.rebuild_scene(restore_view=True)

    def _on_document_loaded(self) -> None:
        self.cancel_stroke_preview()
        self.cancel_erase_gesture()
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
        self._card_spec_cache.clear()
        self.scene_ref.setBackgroundBrush(QColor(self.theme_palette.canvas_background))
        self._schedule_rebuild()

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

    def is_overview_mode(self) -> bool:
        """Overview deliberately omits labels instead of rendering tiny ellipses."""

        return float(self.transform().m11()) < PlanTopicItem.OVERVIEW_SCALE

    def shows_topic_text(self) -> bool:
        """A small testable seam for the overview rendering contract."""

        return not self.is_overview_mode()

    def set_pen_style(self, color: str, width: float) -> None:
        resolved = QColor(str(color))
        self.pen_color = resolved.name() if resolved.isValid() else "#2f80ed"
        self.pen_width = (
            2.0 if float(width) <= 2.0 else (4.0 if float(width) <= 4.0 else 8.0)
        )
        if self._drawing_stroke:
            self._temporary_stroke.setPen(
                QPen(
                    QColor(self.pen_color),
                    self.pen_width,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
            )

    def _card_spec_for(
        self,
        title: str,
        *,
        root: bool,
        virtual: bool,
        semantic_title,
    ) -> _PlanTopicCardSpec:
        """Cache content measurements; scrolling and zooming never remeasure."""

        semantic_key = (
            (
                semantic_title.draw_text,
                semantic_title.separator_text,
                semantic_title.action_text,
                semantic_title.note_text,
                semantic_title.note_separator_text,
            )
            if semantic_title is not None
            else None
        )
        base_font = QFont(self.font())
        base_font.setPointSizeF(
            PlanTopicItem.ROOT_TITLE_POINT_SIZE
            if root
            else PlanTopicItem.TITLE_POINT_SIZE
        )
        base_font.setBold(root)
        key = (
            str(title),
            bool(root),
            bool(virtual),
            semantic_key,
            self.theme_mode.value,
            base_font.family(),
            base_font.pointSizeF(),
        )
        spec = self._card_spec_cache.get(key)
        if spec is None:
            spec = PlanTopicItem.measure_card(
                title,
                root=root,
                semantic_title=semantic_title,
                font=base_font,
            )
            self._card_spec_cache[key] = spec
        return spec

    def _visible_layout(
        self,
        topics: dict[str, Any],
        children: dict[str | None, list[str]],
        root_uuid: str | None,
        card_specs: dict[str, _PlanTopicCardSpec],
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

        depth_by_uuid: dict[str, int] = {}
        visible_by_parent: dict[str, list[str]] = {}
        positions: dict[str, QPointF] = {}
        edges: list[tuple[str, str]] = []
        next_leaf_y = 0.0
        # Explicit pre/post traversal keeps imported deep plans stack-safe and
        # centers a parent between the first and last visible child card.
        stack: list[tuple[str, int, bool]] = [(root_uuid, 0, False)]
        entered: set[str] = set()
        while stack:
            node_uuid, depth, postorder = stack.pop()
            if not postorder:
                if node_uuid in entered:
                    continue
                entered.add(node_uuid)
                child_ids = [
                    child_uuid
                    for child_uuid in visible_children(node_uuid)
                    if child_uuid in card_specs
                ]
                visible_by_parent[node_uuid] = child_ids
                depth_by_uuid[node_uuid] = depth
                edges.extend((node_uuid, child_uuid) for child_uuid in child_ids)
                stack.append((node_uuid, depth, True))
                for child_uuid in reversed(child_ids):
                    stack.append((child_uuid, depth + 1, False))
                continue

            spec = card_specs.get(node_uuid)
            if spec is None:
                continue
            child_ids = [
                child_uuid
                for child_uuid in visible_by_parent.get(node_uuid, ())
                if child_uuid in positions
            ]
            if child_ids:
                first = card_specs[child_ids[0]].bounds
                last = card_specs[child_ids[-1]].bounds
                first_center = positions[child_ids[0]].y() + first.height() * 0.5
                last_center = positions[child_ids[-1]].y() + last.height() * 0.5
                y = (first_center + last_center) * 0.5 - spec.bounds.height() * 0.5
            else:
                y = next_leaf_y
                next_leaf_y += spec.bounds.height() + self.SIBLING_GAP
            positions[node_uuid] = QPointF(0.0, y)

        width_by_depth: dict[int, float] = defaultdict(float)
        for node_uuid, depth in depth_by_uuid.items():
            spec = card_specs.get(node_uuid)
            if spec is not None:
                width_by_depth[depth] = max(width_by_depth[depth], spec.bounds.width())
        x_by_depth: dict[int, float] = {}
        next_x = 0.0
        for depth in sorted(width_by_depth):
            x_by_depth[depth] = next_x
            next_x += width_by_depth[depth] + self.COLUMN_GAP
        for node_uuid, position in positions.items():
            position.setX(x_by_depth.get(depth_by_uuid.get(node_uuid, 0), 0.0))
        return positions, edges

    def rebuild_scene(self, *, restore_view: bool = False) -> None:
        selected = set(self.selected_node_uuids())
        self._end_topic_drag()
        self._curve_bindings.clear()
        self._selection_guard = True
        try:
            for item in list(self.topic_items.values()):
                self.scene_ref.removeItem(item)
            for item in [*self.reference_items, *self.primary_items]:
                self.scene_ref.removeItem(item)
            self.topic_items.clear()
            self.reference_items.clear()
            self.primary_items.clear()
            self._sync_canvas_strokes()
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
            title_by_uuid: dict[str, str] = {}
            card_specs: dict[str, _PlanTopicCardSpec] = {}
            for node_uuid, topic in topics.items():
                node = node_by_uuid.get(node_uuid)
                if node is None:
                    continue
                topic_title = plan_topic_title(
                    self.schema,
                    self.controller.document,
                    node,
                    topic,
                )
                semantic_title = (
                    parse_touchidle_plan_title(topic_title)
                    if topic.formalization_state != "formal"
                    else None
                )
                title_by_uuid[node_uuid] = topic_title
                card_specs[node_uuid] = self._card_spec_for(
                    topic_title,
                    root=node_uuid == root_uuid,
                    virtual=False,
                    semantic_title=semantic_title,
                )
            orphan_roots = list(children.get(None, ()))
            if orphan_roots:
                title_by_uuid[PLAN_UNCONNECTED_UUID] = PLAN_UNCONNECTED_TITLE
                card_specs[PLAN_UNCONNECTED_UUID] = self._card_spec_for(
                    PLAN_UNCONNECTED_TITLE,
                    root=False,
                    virtual=True,
                    semantic_title=None,
                )
            positions, primary_edges = self._visible_layout(
                topics,
                children,
                root_uuid,
                card_specs,
            )
            for node_uuid, position in positions.items():
                if node_uuid == PLAN_UNCONNECTED_UUID:
                    item = PlanTopicItem(
                        self,
                        node_uuid,
                        PLAN_UNCONNECTED_TITLE,
                        PLAN_NEUTRAL_COLOR,
                        virtual=True,
                        card_spec=card_specs[node_uuid],
                    )
                else:
                    node = node_by_uuid.get(node_uuid)
                    topic = topics.get(node_uuid)
                    if node is None or topic is None:
                        continue
                    topic_title = title_by_uuid[node_uuid]
                    semantic_title = (
                        parse_touchidle_plan_title(topic_title)
                        if topic.formalization_state != "formal"
                        else None
                    )
                    item = PlanTopicItem(
                        self,
                        node_uuid,
                        topic_title,
                        topic.branch_color or PLAN_ROOT_COLOR,
                        collapsed=topic.collapsed,
                        root=node_uuid == root_uuid,
                        sequence_locked=node.sequence_locked,
                        semantic_title=(
                            semantic_title
                        ),
                        card_spec=card_specs[node_uuid],
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
                    QColor(PLAN_NEUTRAL_COLOR)
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
        path = self._curve_path(source, target)
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
        self._curve_bindings.append((item, source, target))
        return item

    @staticmethod
    def _curve_path(
        source: PlanTopicItem,
        target: PlanTopicItem,
    ) -> QPainterPath:
        start = source.connection_point("right")
        end = target.connection_point("left")
        span = max(48.0, abs(end.x() - start.x()) * 0.52)
        path = QPainterPath(start)
        path.cubicTo(
            QPointF(start.x() + span, start.y()),
            QPointF(end.x() - span, end.y()),
            end,
        )
        return path

    def _on_topic_item_position_changed(self, moved: PlanTopicItem) -> None:
        """Keep every visible edge attached while a topic is being dragged."""

        for path_item, source, target in self._curve_bindings:
            if moved is source or moved is target:
                path_item.setPath(self._curve_path(source, target))
        if self._dragged_topic_uuid == moved.node_uuid:
            self._update_topic_drop_preview(moved)

    def _begin_topic_drag(
        self,
        node_uuid: str,
        original_position: QPointF,
    ) -> None:
        self._dragged_topic_uuid = node_uuid
        self._drag_original_position = QPointF(original_position)
        self._drop_preview_item.hide()
        self._drop_preview_item.setPath(QPainterPath())

    def _end_topic_drag(self) -> None:
        self._dragged_topic_uuid = None
        self._drop_preview_item.hide()
        self._drop_preview_item.setPath(QPainterPath())

    def _update_topic_drop_preview(self, moved: PlanTopicItem) -> None:
        intent = self._topic_drop_intent(
            moved.node_uuid,
            QPointF(moved.pos()),
            QPointF(self._drag_original_position),
        )
        if intent is None or intent.action != "reparent":
            self._drop_preview_item.hide()
            self._drop_preview_item.setPath(QPainterPath())
            return
        parent_item_uuid = (
            PLAN_UNCONNECTED_UUID
            if intent.new_parent_uuid is None
            else intent.new_parent_uuid
        )
        parent = self.topic_items.get(parent_item_uuid)
        if parent is None:
            self._drop_preview_item.hide()
            return
        color = QColor(moved.color)
        color.setAlpha(210)
        pen = QPen(color, 2.0, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        self._drop_preview_item.setPen(pen)
        self._drop_preview_item.setPath(self._curve_path(parent, moved))
        self._drop_preview_item.show()

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
        if len(requested) == 1 and self.is_overview_mode():
            self.focus_on_node(
                requested[0], target_scale=PlanTopicItem.READABLE_SCALE
            )

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
        readable_target = max(
            PlanTopicItem.READABLE_SCALE,
            float(target_scale)
            if target_scale is not None
            else PlanTopicItem.READABLE_SCALE,
        )
        if self.transform().m11() < readable_target:
            current = max(0.001, self.transform().m11())
            factor = min(8.0, readable_target) / current
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
                if (current < PlanTopicItem.OVERVIEW_SCALE) != (
                    target < PlanTopicItem.OVERVIEW_SCALE
                ):
                    # Geometry is independent of zoom.  A repaint is enough
                    # to exchange full card content for clean overview cards.
                    for item in self.topic_items.values():
                        item.update()
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

    def _sync_canvas_strokes(self) -> None:
        records = {
            str(record.uuid): record
            for record in self.controller.document.plan_canvas_strokes
        }
        for stroke_uuid in list(self.stroke_items):
            if stroke_uuid in records:
                continue
            item = self.stroke_items.pop(stroke_uuid)
            self.scene_ref.removeItem(item)
        for stroke_uuid, record in records.items():
            item = self.stroke_items.get(stroke_uuid)
            if item is None:
                item = CanvasStrokeItem(self, record)
                self.scene_ref.addItem(item)
                self.stroke_items[stroke_uuid] = item
            elif item.record != record:
                item.prepareGeometryChange()
                item.record = record
                item.setPath(CanvasStrokeItem.path_for_points(record.points))
                item.update()
            item.show()

    def _topic_item_at_view_point(self, point: QPointF) -> PlanTopicItem | None:
        item = self.itemAt(point.toPoint() if hasattr(point, "toPoint") else point)
        return item if isinstance(item, PlanTopicItem) and not item.virtual else None

    def _append_stroke_point(
        self, scene_point: QPointF, *, force: bool = False
    ) -> None:
        if self._drawing_points and not force:
            previous_view = QPointF(self.mapFromScene(self._drawing_points[-1]))
            current_view = QPointF(self.mapFromScene(scene_point))
            if (
                math.hypot(
                    current_view.x() - previous_view.x(),
                    current_view.y() - previous_view.y(),
                )
                < 2.0
            ):
                return
        self._drawing_points.append(QPointF(scene_point))
        self._temporary_stroke.setPath(
            CanvasStrokeItem.path_for_points(self._drawing_points)
        )

    def _start_stroke_preview(self, scene_point: QPointF) -> None:
        self.cancel_stroke_preview()
        self._drawing_stroke = True
        self._drawing_points = []
        self._temporary_stroke.setPen(
            QPen(
                QColor(self.pen_color),
                self.pen_width,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        self._append_stroke_point(scene_point, force=True)
        self._temporary_stroke.show()
        self.viewport().setCursor(Qt.CursorShape.CrossCursor)
        self._set_busy("stroke", True)

    def cancel_stroke_preview(self) -> None:
        if self._drawing_stroke:
            self._set_busy("stroke", False)
        self._drawing_stroke = False
        self._drawing_points = []
        self._temporary_stroke.setPath(QPainterPath())
        self._temporary_stroke.hide()
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)

    def cancel_pen_gestures(self) -> None:
        self.cancel_stroke_preview()
        self.cancel_erase_gesture()
        self._pending_ctrl_stroke = False
        self._ctrl_stroke_topic_uuid = None

    def _finish_stroke_preview(self, scene_point: QPointF) -> None:
        if not self._drawing_stroke:
            return
        self._append_stroke_point(scene_point, force=True)
        points = [(point.x(), point.y()) for point in self._drawing_points]
        self.cancel_stroke_preview()
        if len(points) == 1:
            points.append(points[0])
        self.controller.add_canvas_stroke(
            points,
            self.pen_color,
            self.pen_width,
            "plan",
        )

    def _stroke_uuids_in_eraser_segment(
        self,
        start_scene: QPointF,
        end_scene: QPointF,
    ) -> list[str]:
        scale = max(0.001, abs(float(self.transform().m11())))
        radius = 12.0 / scale
        segment = QPainterPath(QPointF(start_scene))
        if QLineF(start_scene, end_scene).length() <= 0.001:
            end_scene = QPointF(start_scene.x() + 0.01, start_scene.y() + 0.01)
        segment.lineTo(end_scene)
        stroker = QPainterPathStroker()
        stroker.setWidth(radius * 2.0)
        stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
        stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        corridor = stroker.createStroke(segment)
        matches: list[str] = []
        for candidate in self.scene_ref.items(corridor.boundingRect()):
            if not isinstance(candidate, CanvasStrokeItem) or not candidate.isVisible():
                continue
            if corridor.intersects(candidate.mapToScene(candidate.shape())):
                matches.append(str(candidate.record.uuid))
        return matches

    def _erase_stroke_segment(
        self,
        start_scene: QPointF,
        end_scene: QPointF,
    ) -> None:
        for stroke_uuid in self._stroke_uuids_in_eraser_segment(
            start_scene, end_scene
        ):
            if stroke_uuid in self._erased_stroke_uuids:
                continue
            item = self.stroke_items.get(stroke_uuid)
            if item is None:
                continue
            item.hide()
            self._erased_stroke_uuids.add(stroke_uuid)

    def _start_erase_gesture(self, scene_point: QPointF) -> None:
        self.cancel_erase_gesture()
        self._erasing_strokes = True
        self._erased_stroke_uuids.clear()
        self._eraser_last_scene = QPointF(scene_point)
        self._set_busy("erase", True)
        self.viewport().setCursor(Qt.CursorShape.CrossCursor)
        self._erase_stroke_segment(scene_point, scene_point)

    def cancel_erase_gesture(self) -> None:
        if self._erasing_strokes:
            self._set_busy("erase", False)
        for stroke_uuid in self._erased_stroke_uuids:
            item = self.stroke_items.get(stroke_uuid)
            if item is not None:
                item.show()
        self._erasing_strokes = False
        self._erased_stroke_uuids.clear()
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)

    def _finish_erase_gesture(self, scene_point: QPointF) -> None:
        if not self._erasing_strokes:
            return
        self._erase_stroke_segment(self._eraser_last_scene, scene_point)
        erased = list(self._erased_stroke_uuids)
        self._erasing_strokes = False
        self._erased_stroke_uuids.clear()
        self._set_busy("erase", False)
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        if erased:
            self.controller.remove_canvas_strokes(erased, "plan")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.button() == Qt.MouseButton.LeftButton:
                scene_point = self.mapToScene(event.position().toPoint())
                topic_item = self._topic_item_at_view_point(event.position())
                if topic_item is None:
                    self._start_stroke_preview(scene_point)
                else:
                    self._pending_ctrl_stroke = True
                    self._ctrl_stroke_start_view = QPointF(event.position())
                    self._ctrl_stroke_start_scene = QPointF(scene_point)
                    self._ctrl_stroke_topic_uuid = topic_item.node_uuid
                event.accept()
                return
            if event.button() == Qt.MouseButton.RightButton:
                self._start_erase_gesture(
                    self.mapToScene(event.position().toPoint())
                )
                event.accept()
                return
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_start = QPointF(event.position())
            self._set_busy("pan", True)
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._erasing_strokes:
            current = self.mapToScene(event.position().toPoint())
            self._erase_stroke_segment(self._eraser_last_scene, current)
            self._eraser_last_scene = QPointF(current)
            event.accept()
            return
        if self._pending_ctrl_stroke:
            delta = event.position() - self._ctrl_stroke_start_view
            if math.hypot(delta.x(), delta.y()) > self._ctrl_stroke_threshold_px:
                start = QPointF(self._ctrl_stroke_start_scene)
                self._pending_ctrl_stroke = False
                self._ctrl_stroke_topic_uuid = None
                self._start_stroke_preview(start)
                self._append_stroke_point(
                    self.mapToScene(event.position().toPoint()),
                    force=True,
                )
            event.accept()
            return
        if self._drawing_stroke:
            self._append_stroke_point(
                self.mapToScene(event.position().toPoint())
            )
            event.accept()
            return
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
        if event.button() == Qt.MouseButton.RightButton and self._erasing_strokes:
            self._finish_erase_gesture(
                self.mapToScene(event.position().toPoint())
            )
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._pending_ctrl_stroke:
            node_uuid = self._ctrl_stroke_topic_uuid
            self._pending_ctrl_stroke = False
            self._ctrl_stroke_topic_uuid = None
            item = self.topic_items.get(node_uuid or "")
            if item is not None:
                item.setSelected(not item.isSelected())
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._drawing_stroke:
            self._finish_stroke_preview(
                self.mapToScene(event.position().toPoint())
            )
            event.accept()
            return
        if event.button() == Qt.MouseButton.MiddleButton and self._panning:
            self._panning = False
            self._set_busy("pan", False)
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            self._store_view_state()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _topic_drop_intent(
        self,
        node_uuid: str,
        dropped_position: QPointF,
        original_position: QPointF,
    ) -> _TopicDropIntent | None:
        topic = self.controller.plan_topic(node_uuid)
        if topic is None:
            return None
        horizontal_delta = dropped_position.x() - original_position.x()
        if abs(horizontal_delta) < self.REORDER_HORIZONTAL_SLOP:
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
                    and self.topic_items[sibling_uuid].sceneBoundingRect().center().y()
                    < drop_y
                )
            )
            try:
                current_index = siblings.index(node_uuid)
            except ValueError:
                return None
            if index == current_index:
                return None
            return _TopicDropIntent(
                action="reorder",
                new_parent_uuid=topic.parent_uuid,
                index=index,
            )

        source = self.topic_items.get(node_uuid)
        drop_center = dropped_position + (
            source.boundingRect().center() if source is not None else QPointF()
        )
        topics = plan_topic_map(self.controller.document)
        candidates: list[tuple[float, PlanTopicItem, str | None]] = []
        for candidate in self.topic_items.values():
            if candidate.node_uuid == node_uuid:
                continue
            center = candidate.sceneBoundingRect().center()
            if center.x() > drop_center.x() + 30.0:
                continue
            distance = math.hypot(
                center.x() - drop_center.x(), center.y() - drop_center.y()
            )
            new_parent = None if candidate.virtual else candidate.node_uuid
            current = new_parent
            valid = True
            seen: set[str] = set()
            while current is not None:
                if current == node_uuid or current in seen:
                    valid = False
                    break
                seen.add(current)
                parent_topic = topics.get(current)
                current = parent_topic.parent_uuid if parent_topic is not None else None
            if valid:
                candidates.append((distance, candidate, new_parent))
        candidates.sort(key=lambda item: item[0])
        if candidates and candidates[0][0] <= self.REPARENT_DISTANCE:
            new_parent = candidates[0][2]
            if new_parent != topic.parent_uuid:
                return _TopicDropIntent(
                    action="reparent",
                    new_parent_uuid=new_parent,
                )
        return None

    def _handle_topic_drop(
        self,
        node_uuid: str,
        dropped_position: QPointF,
        original_position: QPointF,
    ) -> None:
        # A plan drag has structural meaning only.  Formal positions belong to
        # the formal canvas, so never call controller.move_node() here.
        intent = self._topic_drop_intent(
            node_uuid,
            dropped_position,
            original_position,
        )
        if intent is not None and intent.action == "reorder":
            if not self.controller.reorder_plan_topic(
                node_uuid,
                int(intent.index or 0),
            ):
                self.rebuild_scene()
            return
        if intent is not None and intent.action == "reparent":
            if not self.controller.reparent_plan_topic(
                node_uuid,
                intent.new_parent_uuid,
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
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            event.accept()
            return
        item = self.itemAt(event.pos())
        topic_item = item if isinstance(item, PlanTopicItem) else None
        menu = QMenu(self)
        sibling_action = menu.addAction("新增同级主题")
        child_action = menu.addAction("新增子主题")
        edit_action = menu.addAction("编辑标题")
        type_menu = menu.addMenu("设置转换类型")
        touchidle_action = type_menu.addAction("绿色 · TouchIdle")
        touchdrag_action = type_menu.addAction("紫色 · TouchDrag")
        collapse_action = menu.addAction("折叠/展开")
        promote_action = menu.addAction("提升层级")
        delete_action = menu.addAction("删除子树")
        if topic_item is None or topic_item.virtual:
            edit_action.setEnabled(False)
            type_menu.setEnabled(False)
            collapse_action.setEnabled(False)
            promote_action.setEnabled(False)
            delete_action.setEnabled(False)
        elif topic_item.root:
            sibling_action.setEnabled(False)
            type_menu.setEnabled(False)
            promote_action.setEnabled(False)
            delete_action.setEnabled(False)
        selected = menu.exec(event.globalPos())
        if selected == sibling_action:
            self.create_sibling_topic()
        elif selected == child_action:
            self.create_child_topic()
        elif selected == edit_action and topic_item is not None:
            self.edit_topic_title(topic_item.node_uuid)
        elif selected == touchidle_action and topic_item is not None:
            self.controller.set_plan_topic_color(
                topic_item.node_uuid,
                PLAN_TOUCHIDLE_COLOR,
            )
        elif selected == touchdrag_action and topic_item is not None:
            self.controller.set_plan_topic_color(
                topic_item.node_uuid,
                PLAN_TOUCHDRAG_COLOR,
            )
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
        if event.key() == Qt.Key.Key_Escape and self._erasing_strokes:
            self.cancel_erase_gesture()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape and self._drawing_stroke:
            self.cancel_stroke_preview()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape and self._pending_ctrl_stroke:
            self._pending_ctrl_stroke = False
            self._ctrl_stroke_topic_uuid = None
            event.accept()
            return
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
