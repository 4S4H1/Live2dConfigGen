"""Graphics scene and node canvas widgets."""

from __future__ import annotations

import math
from collections import defaultdict, deque
from typing import Any

from PyQt6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QLineF, QTimer, QVariantAnimation, pyqtSignal
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

from .logic import DRAWFRAME_DEFAULT_SIZE, default_node_theme, display_value_for_field, function_node_types, node_title
from .schema import EditorSchema
from .styles import normalize_theme_mode, theme_palette
from .widgets import NodeFormWidget


class GridScene(QGraphicsScene):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSceneRect(-1_000_000, -1_000_000, 2_000_000, 2_000_000)
        self.minor_grid = 20
        self.major_grid = 100
        self.top_right_hint = ""
        self.bottom_right_hint = "按住中键平移 / 滚轮缩放 / Delete 删除"
        self._theme_mode = "light"
        self._palette = theme_palette("light")

    def set_theme(self, mode: str) -> None:
        self._theme_mode = normalize_theme_mode(mode)
        self._palette = theme_palette(self._theme_mode)
        self.update()

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        painter.fillRect(rect, QColor(str(self._palette["canvas_bg"])))
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

        painter.setPen(QPen(QColor(str(self._palette["canvas_minor_grid"])), 1))
        painter.drawLines(minor_lines)
        painter.setPen(QPen(QColor(str(self._palette["canvas_major_grid"])), 1))
        painter.drawLines(major_lines)

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        del rect
        view = self.views()[0] if self.views() else None
        if not view:
            return
        viewport_rect = view.viewport().rect()
        painter.save()
        painter.resetTransform()
        painter.setPen(QColor(str(self._palette["canvas_hint"])))
        font = painter.font()
        font.setBold(True)
        font.setPointSize(15)
        painter.setFont(font)
        margin = 20
        if self.top_right_hint:
            top_rect = QRectF(viewport_rect.width() * 0.45, margin, viewport_rect.width() * 0.5, 32)
            painter.drawText(top_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, self.top_right_hint)
        if self.bottom_right_hint:
            bottom_rect = QRectF(viewport_rect.width() * 0.35, viewport_rect.height() - 44, viewport_rect.width() * 0.6, 28)
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
        palette = self.from_item._theme_palette()
        color = QColor(str(palette["connection_selected"] if self.isSelected() else palette["connection"]))
        painter.setPen(QPen(color, 2.8 if self.isSelected() else 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawPath(self.path())


class NodeItem(QGraphicsObject):
    geometryChanged = pyqtSignal(str)
    PIN_HIT_PADDING = 9.0
    SIMPLE_WIDTH = 420
    ADVANCED_WIDTH = 460
    CARD_BASE_WIDTH = 560
    CARD_BASE_HEIGHT = 360
    RESIZE_MIN_WIDTH = 360.0
    MIN_VISIBLE_WIDTH = 340.0
    MIN_VISIBLE_WIDTH_COMPACT = 300.0
    MIN_VISIBLE_HEIGHT = 200.0
    MIN_VISIBLE_HEIGHT_COMPACT = 180.0
    MAX_ADAPTIVE_SCALE_FACTOR = 2.6
    DISPLAY_ROW_GAP = 28.0
    TITLE_BASE_POINT_SIZE = 10.4
    TITLE_MIN_POINT_SIZE = 7.8
    SUMMARY_BASE_POINT_SIZE = 8.8
    SUMMARY_MIN_POINT_SIZE = 6.8
    DRAWFRAME_TITLE_BASE_POINT_SIZE = 11.5
    DRAWFRAME_TITLE_MAX_POINT_SIZE = 28.0

    def __init__(self, schema: EditorSchema, controller, node, inline_width: int = 340) -> None:
        super().__init__()
        self.schema = schema
        self.controller = controller
        self.node = node
        self.inline_width = inline_width
        self._warnings: list[str] = []
        self._search_highlight = False
        self._attention_strength = 0.0
        self._attention_animation = QVariantAnimation(self)
        self._attention_animation.setStartValue(0.0)
        self._attention_animation.setEasingCurve(QEasingCurve.Type.Linear)
        self._attention_animation.valueChanged.connect(self._advance_attention_flash)
        self._attention_animation.finished.connect(self._clear_attention_flash)
        self._margin = 12
        self._base_header_height = 32.0
        self._base_content_top_gap = 12.0
        self._header_height = self._base_header_height
        self._content_top_gap = self._base_content_top_gap
        self._font_scale_floor = 0.03
        self._display_scale = 1.0
        self._title_rect = QRectF(14.0, 8.0, 180.0, 20.0)
        self._summary_layout_rows: list[tuple[QRectF, str, QColor]] = []
        self._pin_radius = 8
        self._resizing = False
        self._resize_start = QPointF()
        self._resize_initial = (360.0, 180.0)
        self._drag_start_pos = QPointF()
        self._drag_start_logical_pos = QPointF()
        self._display_mode = "detail"
        self._card_layout: dict[str, QRectF] = {}
        self._group_drag_targets: dict[str, QPointF] = {}
        self._group_drag_origin = QPointF()
        self._group_drag_active = False
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
        hit_padding = self._scaled(self._pin_radius + self.PIN_HIT_PADDING)
        return self._rect.adjusted(-hit_padding, -2.0, hit_padding, 2.0)

    def _pin_center(self, side: str) -> QPointF:
        if self._uses_compact_card() and self._card_layout:
            frame_rect = self._card_layout["frame"]
            x = frame_rect.left() if side == "input" else frame_rect.right()
            return QPointF(x, frame_rect.center().y())
        x = 0.0 if side == "input" else self._rect.width()
        return QPointF(x, self._header_height + self._scaled(18.0))

    def _pin_rect(self, side: str, radius: float) -> QRectF:
        center = self._pin_center(side)
        effective_radius = self._scaled(radius)
        return QRectF(center.x() - effective_radius, center.y() - effective_radius, effective_radius * 2.0, effective_radius * 2.0)

    def input_pin_scene_pos(self) -> QPointF:
        if not self._supports_connections():
            return self.sceneBoundingRect().center()
        return self.mapToScene(self._pin_center("input"))

    def output_pin_scene_pos(self) -> QPointF:
        if not self._supports_connections():
            return self.sceneBoundingRect().center()
        return self.mapToScene(self._pin_center("output"))

    def input_pin_rect(self) -> QRectF:
        return self._pin_rect("input", self._pin_radius)

    def output_pin_rect(self) -> QRectF:
        return self._pin_rect("output", self._pin_radius)

    def resize_handle_rect(self) -> QRectF:
        size = self._scaled(18 if self.node.type == "Comment" else 12)
        inset = self._scaled(6.0)
        return QRectF(self._rect.width() - size - inset, self._rect.height() - size - inset, size, size)

    def lock_rect(self) -> QRectF:
        return QRectF(self._rect.width() - self._scaled(34.0), self._scaled(10.0), self._scaled(18.0), self._scaled(18.0))

    def _canvas_view(self) -> "NodeCanvasView | None":
        if not self.scene():
            return None
        for view in self.scene().views():
            if isinstance(view, NodeCanvasView):
                return view
        return None

    def _theme_palette(self) -> dict[str, object]:
        view = self._canvas_view()
        if view is not None:
            return view.theme_palette
        return theme_palette("light")

    def _scaled(self, value: float) -> float:
        return float(value) * self._display_scale

    def _screen_display_scale(self) -> float:
        return max(0.06, self._view_scale() * self._display_scale)

    def _is_function_node(self) -> bool:
        return self.node.type in function_node_types(self.schema)

    def _uses_compact_card(self) -> bool:
        return self._is_function_node() and self._display_mode == "card"

    def _is_draw_frame(self) -> bool:
        return self.node.type == "DrawFrame"

    def _supports_connections(self) -> bool:
        return self.node.type not in {"Comment", "DrawFrame"}

    def pin_hit(self, scene_pos: QPointF) -> str | None:
        if not self._supports_connections():
            return None
        local = self.mapFromScene(scene_pos)
        input_hit = self._pin_rect("input", self._pin_radius + self.PIN_HIT_PADDING)
        output_hit = self._pin_rect("output", self._pin_radius + self.PIN_HIT_PADDING)
        if output_hit.contains(local):
            return "output"
        if input_hit.contains(local):
            return "input"
        return None

    def update_node(self, node) -> None:
        self.prepareGeometryChange()
        self.node = node
        definition = self.schema.nodes[node.type]
        view = self._canvas_view()
        self._display_mode = view.node_display_mode(node.uuid) if view else ("card" if self._is_function_node() else "detail")
        compact_mode = view.should_render_thumbnail_nodes() if view else False
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, not node.locked)
        form_mode = "advanced" if self._display_mode == "detail" else self.controller.preferences.global_mode
        self.form.set_node(node, form_mode, self.controller.preferences.debug_json_field_names, compact_mode=compact_mode)
        base_content_width = self.SIMPLE_WIDTH if form_mode == "simple" else self.ADVANCED_WIDTH
        if self._uses_compact_card():
            base_content_width = self.CARD_BASE_WIDTH
        content_width = base_content_width
        if definition.resizable and node.ui_size:
            content_width = max(content_width, int(node.ui_size.get("width", content_width)) - self._margin * 2)
        persisted_width = float(node.ui_size.get("width", 0.0)) if definition.resizable and node.ui_size else 0.0
        base_width = max(content_width + self._margin * 2, persisted_width)
        base_height_hint = self.CARD_BASE_HEIGHT + 52.0 if self._uses_compact_card() else float(node.ui_size.get("height", 180.0)) if definition.resizable and node.ui_size else 180.0
        self._display_scale = self._adaptive_scale(base_width, base_height_hint, compact_mode)
        self._font_scale_floor = self._text_scale_floor(base_width, compact_mode)
        width = base_width * self._display_scale
        if self._uses_compact_card():
            height = self._recompute_compact_card_layout(width)
            self._rect = QRectF(0.0, 0.0, width, height)
            self.proxy.setVisible(False)
        elif self._is_draw_frame():
            raw_width = max(self.RESIZE_MIN_WIDTH, persisted_width or DRAWFRAME_DEFAULT_SIZE["width"])
            raw_height = max(180.0, float(node.ui_size.get("height", DRAWFRAME_DEFAULT_SIZE["height"])) if node.ui_size else DRAWFRAME_DEFAULT_SIZE["height"])
            self._display_scale = self._adaptive_scale(raw_width, raw_height, compact_mode)
            width = raw_width * self._display_scale
            height = raw_height * self._display_scale
            self._header_height = self._scaled(42.0)
            self._content_top_gap = 0.0
            self._card_layout = {}
            self._rect = QRectF(0.0, 0.0, width, height)
            self.proxy.setVisible(False)
        else:
            self._recompute_header_layout(width)
        margin = self._scaled(self._margin)
        final_content_width = int(max(0.0, width - margin * 2))
        content_top_gap = 0.0 if compact_mode else self._content_top_gap
        if not self._uses_compact_card() and not self._is_draw_frame():
            content_height = 0 if compact_mode else self.form.content_height_hint()
            provisional_height = content_height + self._margin * 2 + self._base_header_height + (0.0 if compact_mode else self._base_content_top_gap)
            self._display_scale = self._adaptive_scale(base_width, provisional_height, compact_mode)
            self._font_scale_floor = self._text_scale_floor(base_width, compact_mode)
            width = base_width * self._display_scale
            margin = self._scaled(self._margin)
            final_content_width = int(max(0.0, width - margin * 2))
            self._recompute_header_layout(width)
            content_top_gap = 0.0 if compact_mode else self._content_top_gap
            self.form.setFixedWidth(final_content_width)
            self.form.ensurePolished()
            self.form.setFixedHeight(content_height)
            self.form.updateGeometry()
            height = content_height + margin * 2 + self._header_height + content_top_gap
            if definition.resizable and node.ui_size and not compact_mode:
                height = max(height, float(node.ui_size.get("height", height)))
            min_height = self._scaled(90.0 if compact_mode else 120.0)
            self._rect = QRectF(0.0, 0.0, width, max(min_height, height))
            self.proxy.setVisible(not compact_mode)
            self.proxy.setPos(margin, self._header_height + content_top_gap)
            self.proxy.resize(final_content_width, content_height)
        self.setToolTip(self._full_title_text())
        self.update()
        self.geometryChanged.emit(self.node.uuid)

    @staticmethod
    def _safe_color(value: str, fallback: str) -> QColor:
        color = QColor(str(value or "").strip())
        return color if color.isValid() else QColor(fallback)

    def _resolved_theme_colors(self) -> tuple[QColor, QColor, QColor]:
        defaults = default_node_theme(self.schema, self.node)
        body = self._safe_color(str(self.node.fields.get("theme_body_color") or defaults["theme_body_color"]), defaults["theme_body_color"])
        border = self._safe_color(str(self.node.fields.get("theme_border_color") or defaults["theme_border_color"]), defaults["theme_border_color"])
        text = self._safe_color(str(self.node.fields.get("theme_text_color") or defaults["theme_text_color"]), defaults["theme_text_color"])
        return body, border, text

    def _card_field_text(self, key: str) -> str:
        value = display_value_for_field(self.schema, self.node, key, self.node.fields.get(key))
        return "" if value is None else str(value).strip()

    def _recompute_compact_card_layout(self, width: float) -> float:
        outer_margin = self._scaled(18.0)
        title_height = self._scaled(34.0)
        canvas_top = title_height + self._scaled(16.0)
        inner_width = width - outer_margin * 2.0
        note_text = self._card_field_text("tips") or "empty"
        note_font = QFont(self.form.font())
        note_font.setBold(True)
        note_font.setPointSizeF(self._scaled(11.0))
        note_metrics = QFontMetrics(note_font)
        note_width = min(max(self._scaled(132.0), float(note_metrics.horizontalAdvance(note_text) + self._scaled(26.0))), inner_width * 0.58)
        frame_rect = QRectF(outer_margin, canvas_top, inner_width, self._scaled(self.CARD_BASE_HEIGHT - 26.0))
        top_box = QRectF(frame_rect.center().x() - self._scaled(120.0), frame_rect.top() + self._scaled(24.0), self._scaled(240.0), self._scaled(58.0))
        left_arrow = QRectF(frame_rect.left() + self._scaled(26.0), frame_rect.top() + self._scaled(116.0), self._scaled(238.0), self._scaled(118.0))
        right_capsule = QRectF(frame_rect.right() - self._scaled(266.0), frame_rect.top() + self._scaled(96.0), self._scaled(246.0), self._scaled(146.0))
        bottom_capsule = QRectF(frame_rect.center().x() - self._scaled(118.0), frame_rect.bottom() - self._scaled(74.0), self._scaled(236.0), self._scaled(54.0))
        self._card_layout = {
            "note": QRectF(frame_rect.left(), self._scaled(8.0), note_width, title_height),
            "frame": frame_rect,
            "draw": top_box,
            "action": left_arrow,
            "target_idle": right_capsule,
            "parameter": bottom_capsule,
        }
        self._header_height = 0.0
        self._content_top_gap = 0.0
        return frame_rect.bottom() + self._scaled(18.0)

    def _draw_frame_title_rect(self) -> QRectF:
        return QRectF(self._scaled(18.0), self._scaled(12.0), max(self._scaled(120.0), self._rect.width() - self._scaled(70.0)), self._scaled(28.0))

    def set_warnings(self, warnings: list[str]) -> None:
        self._warnings = warnings
        self.update()

    def set_search_highlight(self, enabled: bool) -> None:
        self._search_highlight = enabled
        self.update()

    def start_attention_flash(self, pulses: int = 2) -> None:
        pulse_count = max(1, pulses)
        if pulse_count <= 0:
            return
        self._attention_animation.stop()
        self._attention_animation.setDuration(280 * pulse_count)
        self._attention_animation.setEndValue(float(pulse_count))
        self._attention_animation.start()

    def _advance_attention_flash(self, value: Any) -> None:
        phase = float(value)
        self._attention_strength = max(0.0, math.sin(math.pi * phase)) ** 2
        self.update()

    def _clear_attention_flash(self) -> None:
        self._attention_strength = 0.0
        self.update()

    def _node_shell_colors(self) -> tuple[QColor, QColor, QColor, QColor, QColor]:
        body_seed, border_seed, text_color = self._resolved_theme_colors()
        accent = QColor(border_seed)
        accent.setAlpha(230)
        body_color = QColor(body_seed)
        body_color = body_color.darker(118)
        body_color.setAlpha(244)
        header_color = QColor(body_seed)
        header_color = header_color.lighter(118)
        header_color.setAlpha(248)
        border_color = QColor(border_seed)
        border_color = border_color.darker(132)
        border_color.setAlpha(225)
        return body_color, header_color, border_color, accent, text_color

    def _comment_colors(self) -> tuple[QColor, QColor, QColor, QColor, QColor]:
        body_seed, border_seed, text_color = self._resolved_theme_colors()
        try:
            alpha_percent = max(0, min(100, int(self.node.fields.get("note_box_alpha", 62))))
        except (TypeError, ValueError):
            alpha_percent = 62
        opacity = max(0.34, min(0.9, alpha_percent / 100.0))
        body_color = QColor(body_seed)
        body_color = body_color.darker(255)
        body_color.setAlphaF(opacity * 0.9)
        header_color = QColor(body_seed)
        header_color = header_color.darker(185)
        header_color.setAlphaF(min(1.0, opacity + 0.16))
        border_color = QColor(border_seed)
        border_color = border_color.darker(125)
        border_color.setAlphaF(min(1.0, opacity + 0.08))
        accent_bar = QColor(border_seed)
        accent_bar.setAlphaF(min(1.0, opacity + 0.18))
        try:
            text_alpha = max(0, min(100, int(self.node.fields.get("note_text_alpha", 100))))
        except (TypeError, ValueError):
            text_alpha = 100
        text_color = QColor(text_color)
        text_color.setAlphaF(text_alpha / 100.0)
        return body_color, header_color, border_color, accent_bar, text_color

    def _paint_lock_badge(self, painter: QPainter) -> None:
        palette = self._theme_palette()
        lock_fill = QColor(str(palette["lock_fill"]))
        painter.setBrush(lock_fill)
        painter.setPen(QPen(QColor(str(palette["lock_border"])), 1.0))
        painter.drawRoundedRect(self.lock_rect(), self._scaled(4.0), self._scaled(4.0))
        shackle = QPainterPath()
        shackle.moveTo(self.lock_rect().left() + self._scaled(5.5), self.lock_rect().top() + self._scaled(8.0))
        shackle.arcTo(self.lock_rect().left() + self._scaled(4.0), self.lock_rect().top() + self._scaled(3.0), self._scaled(10.0), self._scaled(10.0), 180.0, -180.0)
        painter.drawPath(shackle)
        body_rect = QRectF(self.lock_rect().left() + self._scaled(4.5), self.lock_rect().top() + self._scaled(8.5), self._scaled(9.0), self._scaled(6.5))
        painter.drawRoundedRect(body_rect, self._scaled(2.0), self._scaled(2.0))

    def paint(self, painter: QPainter, option, widget=None) -> None:
        del option, widget
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        palette = self._theme_palette()
        if self.node.type == "Comment":
            body_color, header_color, type_border, accent, text_color = self._comment_colors()
        else:
            body_color, header_color, type_border, accent, text_color = self._node_shell_colors()
        border = QColor(type_border)
        if self._warnings:
            border = QColor(str(palette["warning_border"]))
        elif self._search_highlight:
            border = QColor(str(palette["search_border"]))
        elif self.isSelected():
            border = QColor(accent).lighter(112)

        shadow_color = QColor(str(palette["shadow"]))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(shadow_color)
        painter.drawRoundedRect(self._rect.adjusted(0, self._scaled(4.0), 0, self._scaled(4.0)), self._scaled(10.0), self._scaled(10.0))

        if self._uses_compact_card():
            note_rect = self._card_layout["note"]
            frame_rect = self._card_layout["frame"]
            draw_rect = self._card_layout["draw"]
            action_rect = self._card_layout["action"]
            target_rect = self._card_layout["target_idle"]
            parameter_rect = self._card_layout["parameter"]

            frame_fill = QColor(str(palette["card_shell"]))
            frame_inner_fill = QColor(str(palette["card_inner"]))
            painter.setPen(QPen(border, self._scaled(3.0)))
            painter.setBrush(frame_fill)
            painter.drawRoundedRect(frame_rect, self._scaled(8.0), self._scaled(8.0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(frame_inner_fill)
            painter.drawRoundedRect(frame_rect.adjusted(self._scaled(8.0), self._scaled(8.0), -self._scaled(8.0), -self._scaled(8.0)), self._scaled(6.0), self._scaled(6.0))

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(str(palette["card_note_fill"])))
            painter.drawRoundedRect(note_rect, self._scaled(6.0), self._scaled(6.0))
            painter.setPen(QColor(str(palette["card_note_text"])))
            note_font = QFont(self.form.font())
            note_font.setBold(True)
            note_font.setPointSizeF(self._scaled(11.0))
            painter.setFont(note_font)
            painter.drawText(note_rect.adjusted(self._scaled(10.0), 0, -self._scaled(10.0), 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._card_field_text("tips") or "empty")

            painter.setPen(QPen(QColor(str(palette["card_draw_border"])), self._scaled(2.0)))
            painter.setBrush(QColor(str(palette["card_draw_fill"])))
            painter.drawRoundedRect(draw_rect, self._scaled(14.0), self._scaled(14.0))
            block_font = QFont(self.form.font())
            block_font.setPointSizeF(self._scaled(16.0))
            painter.setFont(block_font)
            painter.setPen(QColor(str(palette["card_draw_text"])))
            painter.drawText(draw_rect.adjusted(self._scaled(12.0), 0, -self._scaled(12.0), 0), Qt.AlignmentFlag.AlignCenter, self._card_field_text("draw_able_name") or "empty")

            arrow_path = QPainterPath()
            arrow_path.moveTo(action_rect.left(), action_rect.top() + self._scaled(16.0))
            arrow_path.lineTo(action_rect.left() + action_rect.width() - self._scaled(62.0), action_rect.top() + self._scaled(16.0))
            arrow_path.lineTo(action_rect.left() + action_rect.width() - self._scaled(62.0), action_rect.top())
            arrow_path.lineTo(action_rect.right(), action_rect.center().y())
            arrow_path.lineTo(action_rect.left() + action_rect.width() - self._scaled(62.0), action_rect.bottom())
            arrow_path.lineTo(action_rect.left() + action_rect.width() - self._scaled(62.0), action_rect.bottom() - self._scaled(16.0))
            arrow_path.lineTo(action_rect.left(), action_rect.bottom() - self._scaled(16.0))
            arrow_path.closeSubpath()
            painter.setPen(QPen(QColor(str(palette["card_action_border"])), self._scaled(3.0)))
            painter.setBrush(QColor(str(palette["card_action_fill"])))
            painter.drawPath(arrow_path)
            painter.setPen(QColor(str(palette["card_action_text"])))
            painter.drawText(action_rect.adjusted(self._scaled(18.0), 0, -self._scaled(40.0), 0), Qt.AlignmentFlag.AlignCenter, self._card_field_text("action_trigger") or "empty")

            painter.setPen(QPen(QColor(str(palette["card_target_border"])), self._scaled(3.0)))
            painter.setBrush(QColor(str(palette["card_target_fill"])))
            painter.drawRoundedRect(target_rect, target_rect.height() / 2.0, target_rect.height() / 2.0)
            target_font = QFont(self.form.font())
            target_font.setPointSizeF(self._scaled(22.0))
            painter.setFont(target_font)
            painter.setPen(QColor(str(palette["card_target_text"])))
            painter.drawText(target_rect, Qt.AlignmentFlag.AlignCenter, self._card_field_text("action_trigger_active") or "empty")

            painter.setPen(QPen(QColor(str(palette["card_parameter_border"])), self._scaled(3.0)))
            painter.setBrush(QColor(str(palette["card_parameter_fill"])))
            painter.drawRoundedRect(parameter_rect, parameter_rect.height() / 2.0, parameter_rect.height() / 2.0)
            painter.setFont(block_font)
            painter.setPen(QColor(str(palette["card_parameter_text"])))
            painter.drawText(parameter_rect, Qt.AlignmentFlag.AlignCenter, self._card_field_text("parameter") or "empty")

            if self._supports_connections():
                painter.setBrush(QColor(str(palette["pin_selected"] if self.isSelected() else palette["pin_fill"])))
                painter.setPen(QPen(QColor(str(palette["pin_outline"])), self._scaled(2.0)))
                painter.drawEllipse(self.input_pin_rect())
                painter.drawEllipse(self.output_pin_rect())

            self._paint_lock_badge(painter)
            if self._attention_strength > 0.001:
                flash_fill = QColor(255, 255, 255, int(118 * self._attention_strength))
                painter.setBrush(flash_fill)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(self._rect, 10, 10)
            if self._warnings:
                painter.setBrush(QColor(str(palette["warning_border"])))
                painter.drawRoundedRect(QRectF(self._rect.width() - self._scaled(44.0), self._scaled(8.0), self._scaled(30.0), self._scaled(18.0)), self._scaled(6.0), self._scaled(6.0))
                painter.setPen(QColor(str(palette["text_inverse"])))
                painter.drawText(QRectF(self._rect.width() - self._scaled(44.0), self._scaled(8.0), self._scaled(30.0), self._scaled(18.0)), Qt.AlignmentFlag.AlignCenter, str(len(self._warnings)))
            return

        if self._is_draw_frame():
            frame_fill = QColor(str(palette["frame_fill"]))
            painter.setPen(QPen(QColor(str(palette["frame_border"])), self._scaled(2.5)))
            painter.setBrush(frame_fill)
            painter.drawRoundedRect(self._rect, self._scaled(8.0), self._scaled(8.0))
            title_rect = self._draw_frame_title_rect()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(str(palette["frame_title_fill"])))
            painter.drawRoundedRect(title_rect.adjusted(0, 0, 0, self._scaled(2.0)), self._scaled(6.0), self._scaled(6.0))
            painter.setPen(QColor(str(palette["frame_title_text"])))
            title_font = QFont(self.form.font())
            title_font.setBold(True)
            title_font.setPointSizeF(self._draw_frame_title_point_size())
            painter.setFont(title_font)
            painter.drawText(title_rect.adjusted(10, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, str(self.node.fields.get("title") or "画框"))
            self._paint_lock_badge(painter)
            if self.schema.nodes[self.node.type].resizable:
                painter.setBrush(QColor("#ffcf25"))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRect(self.resize_handle_rect())
            return

        border_width = 4.0 if self._warnings else 1.6
        painter.setPen(QPen(border, border_width))
        painter.setBrush(body_color)
        painter.drawRoundedRect(self._rect, 10, 10)
        painter.setBrush(header_color)
        painter.drawRoundedRect(QRectF(0, 0, self._rect.width(), self._header_height + 8), 10, 10)
        painter.fillRect(QRectF(0, self._header_height, self._rect.width(), 12), header_color)
        accent_bar = QColor(accent)
        accent_bar.setAlpha(min(255, accent_bar.alpha() + 12))
        painter.setBrush(accent_bar)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(12, 8, max(52.0, self._rect.width() - 24), 4), 2, 2)
        self._paint_lock_badge(painter)
        content_rect = QRectF(
            self.proxy.pos().x() - 2,
            self.proxy.pos().y() - 2,
            self.proxy.size().width() + 4,
            self.proxy.size().height() + 4,
        )
        if self.proxy.isVisible() and content_rect.width() > 0 and content_rect.height() > 0:
            panel_fill = QColor(str(palette["input_bg"]))
            panel_border = QColor(str(palette["input_border"]))
            painter.setBrush(panel_fill)
            painter.setPen(QPen(panel_border, 1.0))
            painter.drawRoundedRect(content_rect, self._scaled(8.0), self._scaled(8.0))
        title_color = QColor(str(palette["warning_border"])) if self._warnings else QColor(text_color)
        if self.node.fields.get("tips") and not self._warnings:
            title_color = QColor(str(palette["search_border"]))
        painter.setPen(title_color)
        title_font = self._title_font()
        painter.setFont(title_font)
        painter.drawText(
            self._title_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
            self._full_title_text(),
        )
        summary_font = self._summary_font()
        painter.setFont(summary_font)
        for rect, text, color in self._summary_layout_rows:
            painter.setPen(color)
            painter.drawText(
                rect,
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
                text,
            )
        if self._attention_strength > 0.001:
            flash_fill = QColor(255, 255, 255, int(118 * self._attention_strength))
            painter.setBrush(flash_fill)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(self._rect, 10, 10)
        if self._supports_connections():
            painter.setBrush(QColor(str(palette["pin_selected"] if self.isSelected() else palette["pin_fill"])))
            painter.setPen(QPen(QColor(str(palette["pin_outline"])), self._scaled(2.0)))
            painter.drawEllipse(self.input_pin_rect())
            painter.drawEllipse(self.output_pin_rect())
        if self._warnings:
            painter.setBrush(QColor(str(palette["warning_border"])))
            painter.drawRoundedRect(QRectF(self._rect.width() - self._scaled(44.0), self._scaled(8.0), self._scaled(30.0), self._scaled(18.0)), self._scaled(6.0), self._scaled(6.0))
            painter.setPen(QColor(str(palette["text_inverse"])))
            painter.drawText(QRectF(self._rect.width() - self._scaled(44.0), self._scaled(8.0), self._scaled(30.0), self._scaled(18.0)), Qt.AlignmentFlag.AlignCenter, str(len(self._warnings)))
        if self.schema.nodes[self.node.type].resizable:
            painter.setBrush(QColor(str(palette["search_border"])))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(self.resize_handle_rect())

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.lock_rect().contains(event.pos()):
            self.controller.set_node_locked(self.node.uuid, not self.node.locked)
            event.accept()
            return
        if self.schema.nodes[self.node.type].resizable and self.resize_handle_rect().contains(event.pos()):
            if self.node.locked:
                event.accept()
                return
            self._resizing = True
            self._resize_start = event.scenePos()
            self._resize_initial = (self._rect.width(), self._rect.height())
            view = self._canvas_view()
            if view:
                view._set_interaction_busy("resize", True)
            event.accept()
            return
        self._drag_start_pos = self.pos()
        self._drag_start_logical_pos = QPointF(float(self.node.ui_position["x"]), float(self.node.ui_position["y"]))
        if not self.node.locked:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            view = self._canvas_view()
            if view:
                view._set_interaction_busy("drag", True)
                if self._is_draw_frame():
                    self._group_drag_targets = view.frame_drag_members(self.node.uuid)
                    self._group_drag_origin = QPointF(self._drag_start_pos)
                    self._group_drag_active = bool(self._group_drag_targets)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._resizing:
            delta = event.scenePos() - self._resize_start
            width = max(self.RESIZE_MIN_WIDTH, self._resize_initial[0] + delta.x())
            height = max(140.0, self._resize_initial[1] + delta.y())
            self.node.ui_size = {"width": width, "height": height}
            self.update_node(self.node)
            event.accept()
            return
        super().mouseMoveEvent(event)
        if self._group_drag_targets:
            delta = self.pos() - self._drag_start_pos
            view = self._canvas_view()
            if view:
                for node_uuid, origin in self._group_drag_targets.items():
                    item = view.node_items.get(node_uuid)
                    if item:
                        item.setPos(origin + delta)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._resizing:
            self._resizing = False
            view = self._canvas_view()
            if view:
                view._set_interaction_busy("resize", False)
            self.controller.nodeUpdated.emit(self.node.uuid)
            event.accept()
            return
        super().mouseReleaseEvent(event)
        view = self._canvas_view()
        if not self.node.locked:
            if view:
                view._set_interaction_busy("drag", False)
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        current = self.pos()
        old_pos = (self._drag_start_logical_pos.x(), self._drag_start_logical_pos.y())
        if view:
            logical_current = view.logical_position_from_display(self.node.uuid, current)
        else:
            logical_current = QPointF(current)
        new_pos = (logical_current.x(), logical_current.y())
        if self._group_drag_targets:
            positions = {self.node.uuid: new_pos}
            old_positions = {self.node.uuid: old_pos}
            for node_uuid, origin in self._group_drag_targets.items():
                if not view:
                    continue
                item = view.node_items.get(node_uuid)
                if not item:
                    continue
                logical_origin = view.logical_position_for_node(node_uuid)
                logical_position = view.logical_position_from_display(node_uuid, item.pos())
                old_positions[node_uuid] = (logical_origin.x(), logical_origin.y())
                positions[node_uuid] = (logical_position.x(), logical_position.y())
            self._group_drag_targets.clear()
            self._group_drag_active = False
            if old_positions != positions:
                self.controller.move_nodes(positions, label="移动画框")
            return
        self.controller.move_node(self.node.uuid, old_pos, new_pos)

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value: Any):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.geometryChanged.emit(self.node.uuid)
        return super().itemChange(change, value)

    def hoverMoveEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        if self.lock_rect().contains(event.pos()):
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        elif self.schema.nodes[self.node.type].resizable and self.resize_handle_rect().contains(event.pos()):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif self.pin_hit(event.scenePos()):
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif self.node.locked:
            self.setCursor(Qt.CursorShape.ArrowCursor)
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

    def _text_scale_floor(self, base_width: float, compact_mode: bool) -> float:
        min_visible_width = self.MIN_VISIBLE_WIDTH_COMPACT if compact_mode else self.MIN_VISIBLE_WIDTH
        max_adaptive_width = max(base_width, base_width * self.MAX_ADAPTIVE_SCALE_FACTOR)
        return min(1.0, max(0.03, min_visible_width / max(1.0, max_adaptive_width)))

    def _adaptive_scale(self, base_width: float, base_height: float, compact_mode: bool) -> float:
        scale = self._view_scale()
        min_visible_width = self.MIN_VISIBLE_WIDTH_COMPACT if compact_mode else self.MIN_VISIBLE_WIDTH
        min_visible_height = self.MIN_VISIBLE_HEIGHT_COMPACT if compact_mode else self.MIN_VISIBLE_HEIGHT
        width_scale = min_visible_width / max(1.0, base_width * max(0.12, scale))
        height_scale = min_visible_height / max(1.0, base_height * max(0.12, scale))
        return min(self.MAX_ADAPTIVE_SCALE_FACTOR, max(1.0, width_scale, height_scale))

    def _scale_compensated_point_size(self, base_size: float, min_size: float) -> float:
        compensation_scale = max(self._screen_display_scale(), self._font_scale_floor)
        return max(min_size, base_size / compensation_scale)

    def _title_font(self) -> QFont:
        title_font = QFont(self.form.font())
        title_font.setBold(True)
        title_font.setPointSizeF(self._scale_compensated_point_size(self.TITLE_BASE_POINT_SIZE, self.TITLE_MIN_POINT_SIZE))
        return title_font

    def _summary_font(self) -> QFont:
        summary_font = QFont(self.form.font())
        summary_font.setBold(True)
        summary_font.setPointSizeF(
            self._scale_compensated_point_size(self.SUMMARY_BASE_POINT_SIZE, self.SUMMARY_MIN_POINT_SIZE)
        )
        return summary_font

    def _draw_frame_title_point_size(self) -> float:
        return min(
            self.DRAWFRAME_TITLE_MAX_POINT_SIZE,
            max(
                self.DRAWFRAME_TITLE_BASE_POINT_SIZE,
                self.DRAWFRAME_TITLE_BASE_POINT_SIZE / max(0.42, self._screen_display_scale()),
            ),
        )

    @staticmethod
    def _summary_text_color(color: str) -> QColor:
        resolved = QColor(color)
        return resolved if resolved.isValid() else QColor("#dfe5ef")

    def _recompute_header_layout(self, width: float) -> None:
        title_font = self._title_font()
        metrics = QFontMetrics(title_font)
        summary_font = self._summary_font()
        summary_metrics = QFontMetrics(summary_font)
        scale = self._screen_display_scale()
        available_width = max(int(self._scaled(140.0)), int(width - self._scaled(60.0)))
        title_text = self._full_title_text()
        title_bounds = metrics.boundingRect(
            0,
            0,
            available_width,
            4096,
            int(Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap),
            title_text,
        )
        summary_top = 0.0
        self._summary_layout_rows = []
        accent_bar_bottom = self._scaled(12.0)
        zoom_in_title_offset = max(0.0, (scale - 1.0) * self._scaled(5.0))
        padding_top = max(self._scaled(13.0), accent_bar_bottom + self._scaled(1.5), self._scaled(10.0) / scale + zoom_in_title_offset)
        title_bottom = padding_top + max(self._scaled(24.0), float(title_bounds.height()))
        summary_y = title_bottom + max(self._scaled(6.0), self._scaled(8.0) / scale)
        row_gap = max(self._scaled(2.0), self._scaled(4.0) / scale)
        for icon, label, value, color in self.form.summary_display_rows():
            row_text = f"{icon} {label}: {value}"
            row_bounds = summary_metrics.boundingRect(
                0,
                0,
                available_width,
                4096,
                int(Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap),
                row_text,
            )
            row_height = max(self._scaled(16.0), float(row_bounds.height()))
            rect = QRectF(self._scaled(14.0), summary_y, width - self._scaled(60.0), row_height)
            self._summary_layout_rows.append((rect, row_text, self._summary_text_color(color)))
            summary_y += row_height + row_gap
        if self._summary_layout_rows:
            summary_top = self._summary_layout_rows[-1][0].bottom()
        padding_bottom = max(self._scaled(10.0), self._scaled(12.0) / scale)
        self._title_rect = QRectF(
            self._scaled(14.0),
            padding_top,
            width - self._scaled(60.0),
            max(self._scaled(24.0), float(title_bounds.height())),
        )
        self._header_height = max(
            self._scaled(self._base_header_height),
            max(self._title_rect.y() + self._title_rect.height(), summary_top) + padding_bottom,
        )
        self._content_top_gap = max(self._scaled(self._base_content_top_gap), self._scaled(16.0) / max(scale, 0.12))

    def _full_title_text(self) -> str:
        return node_title(self.schema, self.node)


class NodeCanvasView(QGraphicsView):
    selectionSummaryChanged = pyqtSignal(object, object)
    interactionBusyChanged = pyqtSignal(bool)
    THUMBNAIL_SCALE_THRESHOLD = 0.45
    DISPLAY_RELAYOUT_THRESHOLD = 0.45
    DISPLAY_RELAYOUT_ROW_BAND = 150.0
    DISPLAY_RELAYOUT_GAP_PIXELS = 44.0

    def __init__(self, schema: EditorSchema, controller, parent=None) -> None:
        super().__init__(parent)
        self.schema = schema
        self.controller = controller
        self.scene_ref = GridScene(self)
        self._theme_mode = "light"
        self.theme_palette = theme_palette("light")
        self.scene_ref.set_theme(self._theme_mode)
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
        self.zoom_wheel_modifier = "ctrl"
        self.horizontal_wheel_modifier = "alt_shift"
        self.expanded_node_uuids: set[str] = set()
        self._display_position_overrides: dict[str, QPointF] = {}
        self._warnings_by_node: dict[str, list[str]] = {}
        self._connecting_from: str | None = None
        self._connecting_moved = False
        self._connecting_start_scene = QPointF()
        self._temp_path = QGraphicsPathItem()
        self._temp_path.setPen(QPen(QColor(str(self.theme_palette["canvas_temp_connection"])), 2.0, Qt.PenStyle.DashLine))
        self._temp_path.setZValue(90)
        self._temp_path.hide()
        self.scene_ref.addItem(self._temp_path)
        self._panning = False
        self._pan_start = QPointF()
        self._focus_start_center = QPointF()
        self._focus_end_center = QPointF()
        self._focus_start_scale = 1.0
        self._focus_end_scale = 1.0
        self._focus_target_uuid: str | None = None
        self._focus_emphasize = False
        self._focus_animation = QVariantAnimation(self)
        self._focus_animation.setStartValue(0.0)
        self._focus_animation.setEndValue(1.0)
        self._focus_animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._focus_animation.valueChanged.connect(self._advance_focus_animation)
        self._focus_animation.finished.connect(self._finish_focus_animation)
        self._interaction_flags: set[str] = set()
        self._wheel_busy_timer = QTimer(self)
        self._wheel_busy_timer.setSingleShot(True)
        self._wheel_busy_timer.timeout.connect(lambda: self._set_interaction_busy("wheel", False))
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

    def apply_theme(self, mode: str) -> None:
        self._theme_mode = normalize_theme_mode(mode)
        self.theme_palette = theme_palette(self._theme_mode)
        self.scene_ref.set_theme(self._theme_mode)
        self._temp_path.setPen(QPen(QColor(str(self.theme_palette["canvas_temp_connection"])), 2.0, Qt.PenStyle.DashLine))
        for item in self.node_items.values():
            item.update()
        for connection in self.connection_items.values():
            connection.update()
        self.viewport().update()

    def rebuild_scene(self) -> None:
        self.expanded_node_uuids.clear()
        self._display_position_overrides.clear()
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

    def _set_interaction_busy(self, flag: str, busy: bool) -> None:
        before = bool(self._interaction_flags)
        if busy:
            self._interaction_flags.add(flag)
        else:
            self._interaction_flags.discard(flag)
        after = bool(self._interaction_flags)
        if before != after:
            self.interactionBusyChanged.emit(after)

    def is_busy(self) -> bool:
        return bool(self._interaction_flags)

    @staticmethod
    def _matches_wheel_modifier(config: str, modifiers: Qt.KeyboardModifier) -> bool:
        if config == "none":
            return modifiers == Qt.KeyboardModifier.NoModifier
        if config == "ctrl":
            return bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        if config == "alt":
            return bool(modifiers & Qt.KeyboardModifier.AltModifier)
        if config == "shift":
            return bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        if config == "alt_shift":
            return bool(modifiers & (Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ShiftModifier))
        return False

    def selected_node_uuids(self) -> list[str]:
        return [item.node.uuid for item in self.scene_ref.selectedItems() if isinstance(item, NodeItem)]

    def selected_connection_pairs(self) -> list[tuple[str, str]]:
        return [(item.from_uuid, item.to_uuid) for item in self.scene_ref.selectedItems() if isinstance(item, ConnectionItem)]

    def node_display_mode(self, node_uuid: str) -> str:
        node = self.controller.get_node(node_uuid)
        if not node:
            return "detail"
        if node.type in function_node_types(self.schema):
            return "detail" if node_uuid in self.expanded_node_uuids else "card"
        return "detail"

    def should_render_thumbnail_nodes(self) -> bool:
        return float(self.transform().m11()) <= self.THUMBNAIL_SCALE_THRESHOLD

    def logical_position_for_node(self, node_uuid: str) -> QPointF:
        node = self.controller.get_node(node_uuid)
        if not node:
            return QPointF()
        return QPointF(float(node.ui_position["x"]), float(node.ui_position["y"]))

    def display_position_for_node(self, node_uuid: str) -> QPointF:
        return QPointF(self._display_position_overrides.get(node_uuid, self.logical_position_for_node(node_uuid)))

    def logical_position_from_display(self, node_uuid: str, display_pos: QPointF) -> QPointF:
        logical_pos = self.logical_position_for_node(node_uuid)
        display_origin = self.display_position_for_node(node_uuid)
        return QPointF(
            display_pos.x() - (display_origin.x() - logical_pos.x()),
            display_pos.y() - (display_origin.y() - logical_pos.y()),
        )

    def toggle_node_display_mode(self, node_uuid: str) -> None:
        node = self.controller.get_node(node_uuid)
        if not node or node.type not in function_node_types(self.schema):
            return
        if node_uuid in self.expanded_node_uuids:
            self.expanded_node_uuids.discard(node_uuid)
        else:
            self.expanded_node_uuids.add(node_uuid)
        self._update_node_item(node_uuid)

    def frame_drag_members(self, node_uuid: str) -> dict[str, QPointF]:
        members: dict[str, QPointF] = {}
        frame_item = self.node_items.get(node_uuid)
        if not frame_item:
            return members
        frame_rect = frame_item.mapRectToScene(frame_item.boundingRect())
        for other_uuid, item in self.node_items.items():
            if other_uuid == node_uuid:
                continue
            node = self.controller.get_node(other_uuid)
            if not node or node.locked or node.type == "DrawFrame":
                continue
            if frame_rect.intersects(item.mapRectToScene(item.boundingRect())):
                members[other_uuid] = QPointF(item.pos())
        return members

    def center_on_node(self, node_uuid: str) -> None:
        item = self.node_items.get(node_uuid)
        if item:
            target_center = self._focus_target_center(node_uuid)
            if target_center is not None:
                self.centerOn(target_center)
            else:
                self.centerOn(item)
            self._store_canvas_view()

    def flash_node(self, node_uuid: str, *, emphasize: bool = False) -> None:
        item = self.node_items.get(node_uuid)
        if not item:
            return
        item.set_search_highlight(True)
        if emphasize:
            item.start_attention_flash(pulses=2)
        QTimer.singleShot(1500, lambda target=item: target.set_search_highlight(False))

    def focus_on_node(self, node_uuid: str, *, target_scale: float | None = None, emphasize: bool = False) -> None:
        item = self.node_items.get(node_uuid)
        if not item:
            return
        self._focus_animation.stop()
        self._set_interaction_busy("focus", True)
        current_scale = max(0.001, float(self.transform().m11()))
        self._focus_start_scale = current_scale
        self._focus_end_scale = max(current_scale, float(target_scale)) if target_scale is not None else current_scale
        self._focus_start_center = self.mapToScene(self.viewport().rect().center())
        self._focus_end_center = self._focus_target_center(node_uuid) or item.mapRectToScene(item.boundingRect()).center()
        self._focus_target_uuid = node_uuid
        self._focus_emphasize = emphasize
        self._focus_animation.setDuration(420 if emphasize else 320)
        self._focus_animation.start()

    def paste_position(self) -> tuple[float, float]:
        point = self.mapToScene(self.viewport().rect().center())
        return point.x(), point.y()

    def reset_view_layout(self) -> None:
        self._focus_animation.stop()
        self._set_interaction_busy("focus", False)
        self.resetTransform()
        target_rect: QRectF | None = None
        for item in self.node_items.values():
            item_rect = item.mapRectToScene(item.boundingRect())
            target_rect = item_rect if target_rect is None else target_rect.united(item_rect)
        if target_rect is not None and target_rect.isValid():
            self.scale(1.0, 1.0)
            self.centerOn(target_rect.center())
        else:
            self.centerOn(0.0, 0.0)
        self._refresh_scale_sensitive_nodes()
        self._store_canvas_view()

    def wheelEvent(self, event: QMouseEvent) -> None:
        delta = event.angleDelta().y()
        if not delta:
            event.accept()
            return
        self._set_interaction_busy("wheel", True)
        self._wheel_busy_timer.start(180)
        modifiers = event.modifiers()
        if self._matches_wheel_modifier(self.zoom_wheel_modifier, modifiers):
            factor = 1.1 if delta > 0 else 1 / 1.1
            current_scale = max(0.001, float(self.transform().m11()))
            target_scale = current_scale * factor
            if 0.03 <= target_scale <= 8.0:
                self.scale(factor, factor)
                self._refresh_scale_sensitive_nodes()
                self._store_canvas_view()
        else:
            step = 60 if delta > 0 else -60
            if self._matches_wheel_modifier(self.horizontal_wheel_modifier, modifiers):
                self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() - step))
            else:
                self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() - step))
            self._store_canvas_view()
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_start = event.position()
            self._set_interaction_busy("pan", True)
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

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            node_item = self._node_item_at_view_point(event.position())
            if node_item:
                self.toggle_node_display_mode(node_item.node.uuid)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

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
            self._set_interaction_busy("pan", False)
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
            self.controller._emit_meta_blocked(reason)
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
        elif node.type == "DrawFrame":
            item.setZValue(-30)
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
            self._recompute_display_position_overrides()
            self._apply_display_position_overrides()
            self._update_connections_for_node(node_uuid)
            return
        self._update_node_item(node_uuid)

    def _update_node_item(self, node_uuid: str) -> None:
        node = self.controller.get_node(node_uuid)
        item = self.node_items.get(node_uuid)
        if not node or not item:
            return
        item.update_node(node)
        item.set_warnings(self._warnings_by_node.get(node_uuid, []))
        self._recompute_display_position_overrides()
        self._apply_display_position_overrides()

    def _remove_node_item(self, node_uuid: str) -> None:
        self.expanded_node_uuids.discard(node_uuid)
        self._display_position_overrides.pop(node_uuid, None)
        item = self.node_items.pop(node_uuid, None)
        if not item:
            return
        self.scene_ref.removeItem(item)
        for pair in list(self.connection_items.keys()):
            if node_uuid in pair:
                connection = self.connection_items.pop(pair)
                self.scene_ref.removeItem(connection)
        self._recompute_display_position_overrides()
        self._apply_display_position_overrides()

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

    def _apply_view_state(self, scale: float, center: QPointF) -> None:
        clamped_scale = max(0.03, min(8.0, float(scale)))
        self.resetTransform()
        if clamped_scale != 1.0:
            self.scale(clamped_scale, clamped_scale)
        self._refresh_scale_sensitive_nodes()
        self.centerOn(center)

    def _advance_focus_animation(self, value: Any) -> None:
        progress = float(value)
        center = QPointF(
            self._focus_start_center.x() + (self._focus_end_center.x() - self._focus_start_center.x()) * progress,
            self._focus_start_center.y() + (self._focus_end_center.y() - self._focus_start_center.y()) * progress,
        )
        scale = self._focus_start_scale + (self._focus_end_scale - self._focus_start_scale) * progress
        self._apply_view_state(scale, center)

    def _finish_focus_animation(self) -> None:
        self._apply_view_state(self._focus_end_scale, self._focus_end_center)
        if self._focus_target_uuid:
            target_item = self.node_items.get(self._focus_target_uuid)
            if target_item is not None:
                self.centerOn(target_item)
        self._set_interaction_busy("focus", False)
        self._store_canvas_view()
        if self._focus_target_uuid:
            self.flash_node(self._focus_target_uuid, emphasize=self._focus_emphasize)

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
            self.controller._emit_meta_blocked(reason)
            return
        self._focus_animation.stop()
        self._set_interaction_busy("focus", False)
        self._connecting_from = node_item.node.uuid
        self._connecting_moved = False
        self._connecting_start_scene = node_item.output_pin_scene_pos()
        self._set_interaction_busy("connect", True)
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
        self._set_interaction_busy("connect", False)
        self._temp_path.hide()
        self._temp_path.setPath(QPainterPath())

    def _show_quick_create_menu(self, global_pos, scene_pos: QPointF) -> None:
        if not self._connecting_from:
            return
        allowed, reason = self.controller.can_create_graph_content()
        if not allowed:
            self.controller._emit_meta_blocked(reason)
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
            if node.type in function_node_types(self.schema) and not node.locked
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

        comment_attachments = self._build_comment_attachments(components)
        attached_comment_ids = set(comment_attachments)
        occupied_rects = self._static_obstacle_rects(set(movable_nodes) | attached_comment_ids)
        final_positions: dict[str, tuple[float, float]] = {}
        final_comment_positions: dict[str, tuple[float, float]] = {}
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
            comment_positions = self._layout_attached_comments(component, component_positions, comment_attachments, occupied_rects)
            for node_uuid, position in comment_positions.items():
                final_comment_positions[node_uuid] = position
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
        for node_uuid, position in final_comment_positions.items():
            comment_node = self.controller.get_node(node_uuid)
            if not comment_node:
                continue
            if (
                abs(comment_node.ui_position["x"] - position[0]) > 0.5
                or abs(comment_node.ui_position["y"] - position[1]) > 0.5
            ):
                changed_positions[node_uuid] = position
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

    def _build_comment_attachments(self, components: list[list[str]]) -> dict[str, tuple[str, float, float]]:
        attachments: dict[str, tuple[str, float, float]] = {}
        component_bounds = [self._component_bounds(component) for component in components]
        for node in self.controller.document.nodes:
            if node.type != "Comment" or node.uuid not in self.node_items:
                continue
            comment_rect = self._expanded_rect_for(node.uuid, (node.ui_position["x"], node.ui_position["y"]))
            comment_center = comment_rect.center()
            best_match: tuple[float, str] | None = None
            for index, component in enumerate(components):
                bounds = component_bounds[index]
                if bounds is None:
                    continue
                proximity_bounds = bounds.adjusted(-180.0, -140.0, 180.0, 140.0)
                distance = self._distance_to_rect(comment_center, proximity_bounds)
                if not comment_rect.intersects(proximity_bounds) and distance > 220.0:
                    continue
                anchor_uuid = min(
                    component,
                    key=lambda node_uuid: self._distance_between_rect_centers(
                        comment_rect,
                        self._expanded_rect_for(
                            node_uuid,
                            (
                                self.controller.get_node(node_uuid).ui_position["x"],
                                self.controller.get_node(node_uuid).ui_position["y"],
                            ),
                        ),
                    ),
                )
                if best_match is None or distance < best_match[0]:
                    best_match = (distance, anchor_uuid)
            if best_match is None:
                continue
            anchor_uuid = best_match[1]
            anchor_node = self.controller.get_node(anchor_uuid)
            if not anchor_node:
                continue
            attachments[node.uuid] = (
                anchor_uuid,
                float(node.ui_position["x"] - anchor_node.ui_position["x"]),
                float(node.ui_position["y"] - anchor_node.ui_position["y"]),
            )
        return attachments

    def _layout_attached_comments(
        self,
        component: list[str],
        component_positions: dict[str, tuple[float, float]],
        comment_attachments: dict[str, tuple[str, float, float]],
        occupied_rects: list[QRectF],
    ) -> dict[str, tuple[float, float]]:
        component_set = set(component)
        attached_comment_ids = sorted(
            [
                comment_uuid
                for comment_uuid, (anchor_uuid, _offset_x, _offset_y) in comment_attachments.items()
                if anchor_uuid in component_set and anchor_uuid in component_positions
            ],
            key=lambda node_uuid: (
                self.controller.get_node(node_uuid).ui_position["y"],
                self.controller.get_node(node_uuid).ui_position["x"],
            ),
        )
        positions: dict[str, tuple[float, float]] = {}
        for comment_uuid in attached_comment_ids:
            anchor_uuid, offset_x, offset_y = comment_attachments[comment_uuid]
            anchor_position = component_positions[anchor_uuid]
            desired_x = anchor_position[0] + offset_x
            desired_y = anchor_position[1] + offset_y
            resolved_y = self._resolve_non_overlapping_y(comment_uuid, desired_x, desired_y, occupied_rects)
            positions[comment_uuid] = (desired_x, resolved_y)
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

    def _component_bounds(self, node_ids: list[str]) -> QRectF | None:
        bounds: QRectF | None = None
        for node_uuid in node_ids:
            node = self.controller.get_node(node_uuid)
            if not node or node_uuid not in self.node_items:
                continue
            rect = self._expanded_rect_for(node_uuid, (node.ui_position["x"], node.ui_position["y"]))
            bounds = rect if bounds is None else bounds.united(rect)
        return bounds

    @staticmethod
    def _distance_to_rect(point: QPointF, rect: QRectF) -> float:
        if rect.contains(point):
            return 0.0
        dx = 0.0
        if point.x() < rect.left():
            dx = rect.left() - point.x()
        elif point.x() > rect.right():
            dx = point.x() - rect.right()
        dy = 0.0
        if point.y() < rect.top():
            dy = rect.top() - point.y()
        elif point.y() > rect.bottom():
            dy = point.y() - rect.bottom()
        return math.hypot(dx, dy)

    @staticmethod
    def _distance_between_rect_centers(first: QRectF, second: QRectF) -> float:
        return math.hypot(first.center().x() - second.center().x(), first.center().y() - second.center().y())

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
        self.scene_ref.top_right_hint = "" if state.is_meta_ready else "!!请先填写初始节点里的配置!!"
        self._refresh_hint_overlay()

    def _refresh_hint_overlay(self, *_args) -> None:
        self.scene_ref.top_right_hint = "" if self.controller.document.state.is_meta_ready else "!!请先填写初始节点里的配置!!"
        tips = str(self.controller.document.meta.tips or "").strip()
        self.scene_ref.bottom_right_hint = tips or "按住中键平移 / 滚轮缩放 / 右键创建 / DEL键删除"
        self.scene_ref.update()

    def _focus_target_center(self, node_uuid: str) -> QPointF | None:
        item = self.node_items.get(node_uuid)
        if not item:
            return None
        return item.mapRectToScene(item.boundingRect()).center()

    def _recompute_display_position_overrides(self) -> None:
        self._display_position_overrides = {}
        scale = max(0.06, float(self.transform().m11()))
        if scale > self.DISPLAY_RELAYOUT_THRESHOLD:
            return

        ordered_nodes: list[tuple[str, float, float]] = []
        for node in self.controller.document.nodes:
            if node.uuid not in self.node_items:
                continue
            if node.type not in function_node_types(self.schema) and node.type != "Comment":
                continue
            ordered_nodes.append((node.uuid, float(node.ui_position["x"]), float(node.ui_position["y"])))
        if len(ordered_nodes) < 2:
            return

        rows: list[list[tuple[str, float, float]]] = []
        row_centers: list[float] = []
        for node_uuid, x, y in sorted(ordered_nodes, key=lambda entry: (entry[2], entry[1], entry[0])):
            match_index: int | None = None
            best_distance: float | None = None
            for index, center_y in enumerate(row_centers):
                distance = abs(y - center_y)
                if distance > self.DISPLAY_RELAYOUT_ROW_BAND:
                    continue
                if best_distance is None or distance < best_distance:
                    best_distance = distance
                    match_index = index
            if match_index is None:
                rows.append([(node_uuid, x, y)])
                row_centers.append(y)
                continue
            rows[match_index].append((node_uuid, x, y))
            member_count = len(rows[match_index])
            row_centers[match_index] = ((row_centers[match_index] * (member_count - 1)) + y) / member_count

        display_gap = self.DISPLAY_RELAYOUT_GAP_PIXELS / scale
        for row in rows:
            cursor_right: float | None = None
            for node_uuid, logical_x, logical_y in sorted(row, key=lambda entry: (entry[1], entry[0])):
                candidate_x = logical_x
                candidate_rect = self._expanded_rect_for(node_uuid, (candidate_x, logical_y))
                if cursor_right is not None and candidate_rect.left() < cursor_right + display_gap:
                    candidate_x += cursor_right + display_gap - candidate_rect.left()
                    candidate_rect = self._expanded_rect_for(node_uuid, (candidate_x, logical_y))
                cursor_right = candidate_rect.right()
                if abs(candidate_x - logical_x) >= 0.5:
                    self._display_position_overrides[node_uuid] = QPointF(candidate_x, logical_y)

    def _apply_display_position_overrides(self) -> None:
        moved_nodes: list[str] = []
        for node_uuid, item in self.node_items.items():
            target_pos = self.display_position_for_node(node_uuid)
            if item.pos() == target_pos:
                continue
            item.setPos(target_pos)
            moved_nodes.append(node_uuid)
        for node_uuid in moved_nodes:
            self._update_connections_for_node(node_uuid)

    def _refresh_scale_sensitive_nodes(self) -> None:
        for item in self.node_items.values():
            item.refresh_view_scale()
        self._recompute_display_position_overrides()
        self._apply_display_position_overrides()
