"""Graphics scene and node canvas widgets."""

from __future__ import annotations

import math
from collections import defaultdict, deque
from typing import Any

from PyQt6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QLineF, QTimer, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QColor, QContextMenuEvent, QFont, QFontMetrics, QFontMetricsF, QKeyEvent, QMouseEvent, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsPathItem,
    QGraphicsProxyWidget,
    QGraphicsSceneHoverEvent,
    QGraphicsScene,
    QGraphicsView,
    QInputDialog,
    QDialog,
    QMenu,
)

from .logic import (
    DRAWFRAME_DEFAULT_SIZE,
    default_node_theme,
    display_value_for_field,
    function_node_types,
    node_title,
    parameter_table_colors,
    parameter_table_id,
    parameter_table_order,
    parameter_table_title,
    TABLE_BODY_COLOR_FIELD,
    TABLE_BORDER_COLOR_FIELD,
    TABLE_TEXT_COLOR_FIELD,
)
from .perf_tools import get_performance_recorder
from .schema import EditorSchema, field_visible
from .widgets import CommitComboBox, CommitLineEdit, NodeAppearanceDialog, NodeFormWidget, NumericLineEdit

performance_recorder = get_performance_recorder()


class GridScene(QGraphicsScene):
    GRID_TARGET_PIXEL_SPACING = 10.0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSceneRect(-1_000_000, -1_000_000, 2_000_000, 2_000_000)
        self.minor_grid = 20
        self.major_grid = 100
        self.top_right_hint = ""
        self.bottom_right_hint = "按住中键平移 / 滚轮缩放 / Delete 删除"

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        with performance_recorder.measure(
            "canvas.draw_background",
            "canvas",
            {"width": round(rect.width(), 1), "height": round(rect.height(), 1)},
        ):
            painter.fillRect(rect, QColor("#15181e"))
            view = self.views()[0] if self.views() else None
            scale = max(0.001, float(view.transform().m11())) if view else 1.0

            minor_step = self.minor_grid
            if minor_step * scale < self.GRID_TARGET_PIXEL_SPACING:
                stride = max(1, math.ceil(self.GRID_TARGET_PIXEL_SPACING / max(minor_step * scale, 0.001)))
                minor_step *= stride

            major_step = self.major_grid
            if major_step * scale < self.GRID_TARGET_PIXEL_SPACING:
                stride = max(1, math.ceil(self.GRID_TARGET_PIXEL_SPACING / max(major_step * scale, 0.001)))
                major_step *= stride

            left = int(rect.left()) - (int(rect.left()) % minor_step)
            top = int(rect.top()) - (int(rect.top()) % minor_step)
            right = int(rect.right())
            bottom = int(rect.bottom())

            minor_lines = []
            major_lines = []
            x = left
            while x < right:
                target = major_lines if x % major_step == 0 else minor_lines
                target.append(QLineF(x, rect.top(), x, rect.bottom()))
                x += minor_step
            y = top
            while y < bottom:
                target = major_lines if y % major_step == 0 else minor_lines
                target.append(QLineF(rect.left(), y, rect.right(), y))
                y += minor_step

            if minor_lines:
                painter.setPen(QPen(QColor("#20242c"), 1))
                painter.drawLines(minor_lines)
            painter.setPen(QPen(QColor("#2a3039"), 1))
            if major_lines:
                painter.drawLines(major_lines)

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        del rect
        view = self.views()[0] if self.views() else None
        if not view:
            return
        viewport_rect = view.viewport().rect()
        painter.save()
        painter.resetTransform()
        painter.setPen(QColor(239, 239, 241, 72))
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
    def __init__(self, view: "NodeCanvasView", from_uuid: str, to_uuid: str) -> None:
        super().__init__()
        self.view = view
        self.from_uuid = from_uuid
        self.to_uuid = to_uuid
        self.setZValue(-8)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        self.update_path()

    def update_path(self) -> None:
        start = self.view.connection_anchor_scene_pos(self.from_uuid, "output")
        end = self.view.connection_anchor_scene_pos(self.to_uuid, "input")
        if start is None or end is None:
            return
        delta = max(80.0, abs(end.x() - start.x()) * 0.5)
        path = QPainterPath(start)
        path.cubicTo(start.x() + delta, start.y(), end.x() - delta, end.y(), end.x(), end.y())
        self.setPath(path)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        motion_preview = self.view.should_use_motion_preview()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, not self.view.should_use_fast_rendering())
        color = QColor("#2b89ff") if self.isSelected() else QColor("#7d8aa0")
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(color, 1.4 if motion_preview else (2.8 if self.isSelected() else 2.0), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if motion_preview:
            path = self.path()
            if path.elementCount() >= 2:
                start = path.elementAt(0)
                end = path.elementAt(path.elementCount() - 1)
                painter.drawLine(QPointF(start.x, start.y), QPointF(end.x, end.y))
                return
        painter.drawPath(self.path())


class NodeItem(QGraphicsObject):
    geometryChanged = pyqtSignal(str)
    NORMAL_BASE_Z = 0.0
    NORMAL_SELECTED_Z = 50.0
    COMMENT_BASE_Z = -20.0
    COMMENT_SELECTED_Z = 40.0
    DRAWFRAME_BASE_Z = -30.0
    DRAWFRAME_SELECTED_Z = 30.0
    PIN_HIT_PADDING = 24.0
    VISUAL_PADDING = 8.0
    SIMPLE_WIDTH = 420
    ADVANCED_WIDTH = 460
    CARD_BASE_WIDTH = 560
    CARD_BASE_HEIGHT = 360
    RESIZE_MIN_WIDTH = 360.0
    DISPLAY_ROW_GAP = 28.0
    TITLE_BASE_POINT_SIZE = 23.6
    TITLE_MIN_POINT_SIZE = 18.8
    TITLE_MAX_POINT_SIZE = 160.0
    SUMMARY_BASE_POINT_SIZE = 9.8
    SUMMARY_MIN_POINT_SIZE = 7.8
    SUMMARY_MAX_POINT_SIZE = 56.0
    DRAWFRAME_TITLE_BASE_POINT_SIZE = 25.2
    DRAWFRAME_TITLE_MIN_POINT_SIZE = 21.2
    DRAWFRAME_TITLE_MAX_POINT_SIZE = 144.0
    CARD_NOTE_BASE_POINT_SIZE = 27.0
    CARD_NOTE_MIN_POINT_SIZE = 19.5
    CARD_NOTE_MAX_POINT_SIZE = 108.0
    CARD_TITLE_BASE_POINT_SIZE = 24.0
    CARD_TITLE_MIN_POINT_SIZE = 16.0
    CARD_TITLE_MAX_POINT_SIZE = 96.0
    CARD_ACTION_BASE_POINT_SIZE = 24.6
    CARD_ACTION_MIN_POINT_SIZE = 19.5
    CARD_ACTION_MAX_POINT_SIZE = 56.0
    CARD_TARGET_BASE_POINT_SIZE = 46.0
    CARD_TARGET_MIN_POINT_SIZE = 36.0
    CARD_TARGET_MAX_POINT_SIZE = 84.0
    CARD_PARAMETER_BASE_POINT_SIZE = 20.8
    CARD_PARAMETER_MIN_POINT_SIZE = 17.6
    CARD_PARAMETER_MAX_POINT_SIZE = 44.0
    INITIAL_TITLE_BASE_POINT_SIZE = 13.2
    INITIAL_TITLE_MIN_POINT_SIZE = 11.0
    INITIAL_TITLE_MAX_POINT_SIZE = 48.0
    TITLE_SCALE_FLOOR = 0.18
    SUMMARY_SCALE_FLOOR = 0.18
    DRAWFRAME_TITLE_SCALE_FLOOR = 0.2
    CARD_TEXT_SCALE_FLOOR = 0.18
    LAZY_FORM_NODE_COUNT_THRESHOLD = 50

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
        self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        self.proxy: QGraphicsProxyWidget | None = None
        self.form: NodeFormWidget | None = None
        self._recreate_form_proxy()
        self._card_editor_proxy: QGraphicsProxyWidget | None = None
        self._card_editor_key: str | None = None
        self._selection_drag_targets: set[str] = set()
        self.update_node(node)
        self.setPos(node.ui_position["x"], node.ui_position["y"])
        self.refresh_z_value()

    def boundingRect(self) -> QRectF:
        hit_padding = self._pin_radius + self.PIN_HIT_PADDING
        bounds = self._rect.adjusted(-hit_padding, -self.VISUAL_PADDING, hit_padding, self.VISUAL_PADDING + 4.0)
        if self._uses_compact_card() and "note" in self._card_layout:
            bounds = bounds.united(self._card_layout["note"].adjusted(-8.0, -self.VISUAL_PADDING, 8.0, self.VISUAL_PADDING))
        return bounds

    def _pin_anchor_rect(self) -> QRectF:
        if self._uses_compact_card():
            return self._card_layout.get("frame", self._rect)
        return self._rect

    def _pin_center(self, side: str) -> QPointF:
        anchor_rect = self._pin_anchor_rect()
        x = anchor_rect.left() if side == "input" else anchor_rect.right()
        y = anchor_rect.center().y() if self._uses_compact_card() else self._header_height + 18.0
        return QPointF(x, y)

    def _pin_rect(self, side: str, radius: float) -> QRectF:
        center = self._pin_center(side)
        return QRectF(center.x() - radius, center.y() - radius, radius * 2.0, radius * 2.0)

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
        size = 18 if self.node.type == "Comment" else 12
        return QRectF(self._rect.width() - size - 6, self._rect.height() - size - 6, size, size)

    def lock_rect(self) -> QRectF:
        return QRectF(self._rect.width() - 34.0, 10.0, 18.0, 18.0)

    def _canvas_view(self) -> "NodeCanvasView | None":
        if not self.scene():
            return None
        for view in self.scene().views():
            if isinstance(view, NodeCanvasView):
                return view
        return None

    def _is_function_node(self) -> bool:
        return self.node.type in function_node_types(self.schema)

    def _uses_compact_card(self) -> bool:
        return self._is_function_node() and self._display_mode == "card"

    def _is_draw_frame(self) -> bool:
        return self.node.type == "DrawFrame"

    def _supports_connections(self) -> bool:
        return self.node.type not in {"Comment", "DrawFrame"}

    def _proxy_content_rect(self) -> QRectF:
        return QRectF(
            self.proxy.pos().x(),
            self.proxy.pos().y(),
            self.proxy.size().width(),
            self.proxy.size().height(),
        )

    def _proxy_contains(self, local_pos: QPointF) -> bool:
        return self.proxy.isVisible() and self._proxy_content_rect().contains(local_pos)

    def _card_field_key_at(self, local_pos: QPointF) -> str | None:
        if not self._uses_compact_card():
            return None
        hit_targets = [
            ("note", "tips"),
            ("draw", "draw_able_name"),
            ("action", "action_trigger"),
            ("parameter", "parameter"),
        ]
        if self.node.type != "TouchDrag":
            hit_targets.insert(3, ("target_idle", "action_trigger_active"))
        for layout_key, field_key in hit_targets:
            rect = self._card_layout.get(layout_key)
            if rect and rect.contains(local_pos):
                return field_key
        return None

    def _card_rect_for_field(self, field_key: str) -> QRectF | None:
        return {
            "tips": self._card_layout.get("note"),
            "draw_able_name": self._card_layout.get("draw"),
            "action_trigger": self._card_layout.get("action"),
            "action_trigger_active": self._card_layout.get("target_idle"),
            "parameter": self._card_layout.get("parameter"),
        }.get(field_key)

    def _schema_field(self, key: str):
        definition = self.schema.nodes.get(self.node.type)
        if not definition:
            return None
        return next((field for field in definition.fields if field.key == key), None)

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
        previous_display_mode = self._display_mode
        self.node = node
        definition = self.schema.nodes[node.type]
        view = self._canvas_view()
        self._display_mode = view.node_display_mode(node.uuid) if view else ("card" if self._is_function_node() else "detail")
        if previous_display_mode != self._display_mode:
            self._recreate_form_proxy()
        if not self._uses_compact_card() and self._card_editor_proxy:
            self._discard_card_field_editor()
        compact_mode = False
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, not node.locked)
        form_mode = "advanced" if self._display_mode == "detail" else self.controller.preferences.global_mode
        build_inline_form = not self._uses_compact_card() or len(self.controller.document.nodes) <= self.LAZY_FORM_NODE_COUNT_THRESHOLD
        if build_inline_form:
            self.form.set_node(node, form_mode, self.controller.preferences.debug_json_field_names, compact_mode=compact_mode)
        base_content_width = self.SIMPLE_WIDTH if form_mode == "simple" else self.ADVANCED_WIDTH
        if self._uses_compact_card():
            base_content_width = self.CARD_BASE_WIDTH
        content_width = base_content_width
        if definition.resizable and node.ui_size:
            content_width = max(content_width, int(node.ui_size.get("width", content_width)) - self._margin * 2)
        persisted_width = float(node.ui_size.get("width", 0.0)) if definition.resizable and node.ui_size else 0.0
        base_width = max(content_width + self._margin * 2, persisted_width)
        self._font_scale_floor = self._text_scale_floor(base_width, compact_mode)
        width = base_width
        if self._uses_compact_card():
            height = self._recompute_compact_card_layout(width)
            self._rect = QRectF(0.0, 0.0, width, height)
            self.proxy.setVisible(False)
            self.proxy.setGeometry(QRectF(float(self._margin), float(self._header_height), 0.0, 0.0))
        elif self._is_draw_frame():
            width = max(self.RESIZE_MIN_WIDTH, persisted_width or DRAWFRAME_DEFAULT_SIZE["width"])
            height = max(180.0, float(node.ui_size.get("height", DRAWFRAME_DEFAULT_SIZE["height"])) if node.ui_size else DRAWFRAME_DEFAULT_SIZE["height"])
            self._header_height = 42.0
            self._content_top_gap = 0.0
            self._card_layout = {}
            self._rect = QRectF(0.0, 0.0, width, height)
            self.proxy.setVisible(False)
            self.proxy.setGeometry(QRectF(float(self._margin), float(self._header_height), 0.0, 0.0))
        else:
            self._recompute_header_layout(width)
        final_content_width = int(width - self._margin * 2)
        content_top_gap = 0.0 if compact_mode else self._content_top_gap
        if not self._uses_compact_card() and not self._is_draw_frame():
            self.form.setFixedWidth(final_content_width)
            self.form.ensurePolished()
            if self.form.layout() is not None:
                self.form.layout().activate()
            self.form.adjustSize()
            content_height = 0 if compact_mode else self.form.content_height_hint()
            self.form.setFixedHeight(content_height)
            self.form.updateGeometry()
            height = content_height + self._margin * 2 + self._header_height + content_top_gap
            if definition.resizable and node.ui_size and not compact_mode:
                height = max(height, float(node.ui_size.get("height", height)))
            min_height = 90.0 if compact_mode else 120.0
            self._rect = QRectF(0.0, 0.0, width, max(min_height, height))
            self._sync_form_proxy_geometry(
                final_content_width,
                content_height,
                self._header_height + content_top_gap,
            )
        self.setToolTip(self._full_title_text())
        self.update()
        self.geometryChanged.emit(self.node.uuid)

    def _recreate_form_proxy(self) -> None:
        previous_proxy = self.proxy
        if previous_proxy is not None:
            previous_widget = previous_proxy.widget()
            if previous_widget is not None:
                previous_proxy.setWidget(None)
            if self.scene() is not None:
                self.scene().removeItem(previous_proxy)
            previous_proxy.deleteLater()
        previous_form = self.form
        if previous_form is not None:
            previous_form.deleteLater()
        self.form = NodeFormWidget(self.schema, inline=True)
        self.form.fieldCommitted.connect(self._commit_field)
        self.form.fieldsCommitted.connect(self._commit_fields)
        self.proxy = QGraphicsProxyWidget(self)
        self.proxy.setWidget(self.form)

    def _sync_form_proxy_geometry(
        self,
        content_width: int,
        content_height: int,
        top: float,
    ) -> None:
        content_width = max(1, int(content_width))
        content_height = max(1, int(content_height))
        self.form.setFixedSize(content_width, content_height)
        self.form.resize(content_width, content_height)
        self.form.setGeometry(0, 0, content_width, content_height)
        if self.form.layout() is not None:
            self.form.layout().invalidate()
            self.form.layout().activate()
        self.form.updateGeometry()
        self.proxy.setMinimumSize(content_width, content_height)
        self.proxy.setPreferredSize(content_width, content_height)
        self.proxy.setMaximumSize(content_width, content_height)
        self.proxy.setGeometry(QRectF(float(self._margin), float(top), float(content_width), float(content_height)))
        self.proxy.setVisible(True)
        widget = self.proxy.widget()
        if widget is not None:
            widget.show()
            widget.updateGeometry()
            widget.update()
        self.proxy.updateGeometry()
        self.proxy.update()

    @staticmethod
    def _safe_color(value: str, fallback: str) -> QColor:
        color = QColor(str(value or "").strip())
        return color if color.isValid() else QColor(fallback)

    def _uses_legacy_theme_defaults(self) -> bool:
        legacy_by_type = {
            "Initial": ("#2b4139", "#6fc49e"),
            "TouchIdle": ("#27384d", "#78b1ff"),
            "TouchDrag": ("#332b4f", "#b5a1ff"),
            "ParameterTrigger": ("#071b2d", "#25b7ff"),
            "DrawFrame": ("#dfeada", "#69b070"),
            "Comment": ("#76808d", "#69b070"),
        }
        expected = legacy_by_type.get(self.node.type)
        if not expected:
            return False
        body_key = "note_box_color" if self.node.type == "Comment" else "theme_body_color"
        border_key = "theme_border_color"
        body_value = str(self.node.fields.get(body_key) or "").strip().lower()
        border_value = str(self.node.fields.get(border_key) or "").strip().lower()
        return body_value == expected[0] and border_value in {"", expected[1]}

    def _resolved_theme_colors(self) -> tuple[QColor, QColor, QColor]:
        defaults = default_node_theme(self.schema, self.node)
        theme_text_value = str(self.node.fields.get("theme_text_color") or "").strip()
        custom_text_value = theme_text_value
        if self.node.type == "Comment":
            comment_text_value = str(self.node.fields.get("note_text_color") or "").strip()
            custom_text_value = comment_text_value if QColor(comment_text_value).isValid() else theme_text_value
        if self._uses_legacy_theme_defaults():
            body_value = defaults["theme_body_color"]
            border_value = defaults["theme_border_color"]
            text_value = custom_text_value or defaults["theme_text_color"]
        else:
            body_value = str(self.node.fields.get("theme_body_color") or defaults["theme_body_color"])
            border_value = str(self.node.fields.get("theme_border_color") or defaults["theme_border_color"])
            text_value = custom_text_value or defaults["theme_text_color"]
        body = self._safe_color(body_value, defaults["theme_body_color"])
        border = self._safe_color(border_value, defaults["theme_border_color"])
        text = self._safe_color(text_value, defaults["theme_text_color"])
        return body, border, text

    def _card_field_text(self, key: str) -> str:
        value = display_value_for_field(self.schema, self.node, key, self.node.fields.get(key))
        return "" if value is None else str(value).strip()

    def _compact_note_layout_font(self) -> QFont:
        font = QFont(self.form.font())
        font.setBold(True)
        font.setPointSizeF(self.CARD_NOTE_BASE_POINT_SIZE)
        return font

    def _compact_card_font(self, point_size: float) -> QFont:
        font = QFont(self.form.font())
        font.setBold(True)
        font.setPointSizeF(point_size)
        return font

    def _compact_note_width_for_text(self, text: str, inner_width: float, font: QFont | None = None) -> float:
        resolved_text = str(text or "").strip() or "empty"
        note_metrics = QFontMetricsF(font or self._compact_note_layout_font())
        return min(max(150.0, float(note_metrics.horizontalAdvance(resolved_text) + 38)), inner_width)

    def _compact_note_height_for_font(self, font: QFont) -> float:
        return max(48.0, float(QFontMetricsF(font).height()) + 18.0)

    def _recompute_compact_card_layout(self, width: float, *, note_text: str | None = None) -> float:
        outer_margin = 18.0
        resolved_note_text = self._card_field_text("tips") if note_text is None else note_text
        note_font = self._compact_note_font_for_text(resolved_note_text, width - outer_margin * 2.0)
        title_height = self._compact_note_height_for_font(note_font)
        canvas_top = title_height + 16.0
        inner_width = width - outer_margin * 2.0
        note_width = self._compact_note_width_for_text(resolved_note_text, inner_width, note_font)
        frame_rect = QRectF(outer_margin, canvas_top, inner_width, self.CARD_BASE_HEIGHT - 26.0)
        top_box = QRectF(frame_rect.center().x() - 120.0, frame_rect.top() + 24.0, 240.0, 58.0)
        left_arrow = QRectF(frame_rect.left() + 26.0, frame_rect.top() + 116.0, 238.0, 118.0)
        right_capsule = QRectF(frame_rect.right() - 266.0, frame_rect.top() + 96.0, 246.0, 146.0)
        bottom_capsule = QRectF(frame_rect.center().x() - 118.0, frame_rect.bottom() - 74.0, 236.0, 54.0)
        if self.node.type == "TouchDrag":
            left_arrow = QRectF(frame_rect.center().x() - 150.0, frame_rect.top() + 116.0, 300.0, 118.0)
            right_capsule = QRectF(frame_rect.right() - 10.0, frame_rect.top() + 96.0, 0.0, 0.0)
        self._card_layout = {
            "note": QRectF(frame_rect.left(), 8.0, note_width, title_height),
            "frame": frame_rect,
            "draw": top_box,
            "action": left_arrow,
            "target_idle": right_capsule,
            "parameter": bottom_capsule,
        }
        self._header_height = 0.0
        self._content_top_gap = 0.0
        return frame_rect.bottom() + 18.0

    def _draw_frame_title_rect(self) -> QRectF:
        title_height = max(30.0, float(QFontMetrics(self._draw_frame_title_font()).height()) + 12.0)
        return QRectF(18.0, 12.0, max(120.0, self._rect.width() - 70.0), title_height)

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

    @staticmethod
    def _mix_colors(first: QColor, second: QColor, ratio: float) -> QColor:
        clamped = max(0.0, min(1.0, ratio))
        inverse = 1.0 - clamped
        return QColor(
            int(first.red() * inverse + second.red() * clamped),
            int(first.green() * inverse + second.green() * clamped),
            int(first.blue() * inverse + second.blue() * clamped),
            int(first.alpha() * inverse + second.alpha() * clamped),
        )

    def _node_shell_colors(self) -> tuple[QColor, QColor, QColor, QColor, QColor]:
        body_seed, border_seed, text_color = self._resolved_theme_colors()
        slate_base = QColor("#0f141b")
        panel_base = QColor("#151b24")
        accent = self._mix_colors(QColor(border_seed), QColor("#ffffff"), 0.1)
        accent.setAlpha(236)
        body_color = self._mix_colors(slate_base, QColor(body_seed), 0.48)
        body_color = self._mix_colors(body_color, panel_base, 0.58)
        body_color.setAlpha(246)
        header_color = self._mix_colors(body_color, accent, 0.24)
        header_color.setAlpha(250)
        border_color = self._mix_colors(accent, QColor("#f5f7fa"), 0.1)
        border_color.setAlpha(232)
        return body_color, header_color, border_color, accent, text_color

    def _comment_colors(self) -> tuple[QColor, QColor, QColor, QColor, QColor]:
        body_seed, border_seed, text_color = self._resolved_theme_colors()
        try:
            alpha_percent = max(0, min(100, int(self.node.fields.get("note_box_alpha", 62))))
        except (TypeError, ValueError):
            alpha_percent = 62
        opacity = max(0.34, min(0.9, alpha_percent / 100.0))
        paper_base = QColor("#17130d")
        accent_base = self._mix_colors(QColor(border_seed), QColor("#ffe3a8"), 0.18)
        body_color = self._mix_colors(paper_base, QColor(body_seed), 0.45)
        body_color.setAlphaF(opacity * 0.9)
        header_color = self._mix_colors(body_color, accent_base, 0.22)
        header_color.setAlphaF(min(1.0, opacity + 0.16))
        border_color = self._mix_colors(accent_base, QColor("#fff7de"), 0.12)
        border_color.setAlphaF(min(1.0, opacity + 0.08))
        accent_bar = QColor(accent_base)
        accent_bar.setAlphaF(min(1.0, opacity + 0.18))
        try:
            text_alpha = max(0, min(100, int(self.node.fields.get("note_text_alpha", 100))))
        except (TypeError, ValueError):
            text_alpha = 100
        text_color = QColor(text_color)
        text_color.setAlphaF(text_alpha / 100.0)
        return body_color, header_color, border_color, accent_bar, text_color

    def _compact_card_palette(self) -> dict[str, QColor]:
        body_color, header_color, border_color, accent, text_color = self._node_shell_colors()
        note_fill = self._mix_colors(QColor("#1e2c25"), accent, 0.42)
        note_fill.setAlpha(240)
        note_text = self._mix_colors(QColor("#eefbf4"), QColor(text_color), 0.14)
        draw_fill = self._mix_colors(header_color, accent, 0.56)
        draw_fill.setAlpha(244)
        draw_border = self._mix_colors(accent, QColor("#ffffff"), 0.12)
        action_border = self._mix_colors(accent, QColor("#ffffff"), 0.16)
        action_fill = self._mix_colors(QColor("#fbf7ff"), accent, 0.12)
        action_text = self._mix_colors(QColor("#1e1728"), QColor(text_color), 0.16)
        target_fill = self._mix_colors(QColor("#f5f7fa"), accent, 0.1)
        target_border = self._mix_colors(QColor("#ffffff"), accent, 0.24)
        parameter_fill = self._mix_colors(QColor("#fff3df"), accent, 0.12)
        parameter_border = self._mix_colors(QColor("#ffbc33"), accent, 0.3)
        parameter_text = self._mix_colors(QColor("#2d261d"), QColor(text_color), 0.12)
        frame_fill = self._mix_colors(body_color, QColor("#07080a"), 0.08)
        frame_fill.setAlpha(232)
        frame_inner_fill = self._mix_colors(header_color, QColor("#ffffff"), 0.06)
        frame_inner_fill.setAlpha(244)
        return {
            "frame_fill": frame_fill,
            "frame_inner_fill": frame_inner_fill,
            "note_fill": note_fill,
            "note_text": note_text,
            "draw_fill": draw_fill,
            "draw_border": draw_border,
            "draw_text": QColor("#ffffff"),
            "action_fill": action_fill,
            "action_border": action_border,
            "action_text": action_text,
            "target_fill": target_fill,
            "target_border": target_border,
            "target_text": QColor("#1f2530"),
            "parameter_fill": parameter_fill,
            "parameter_border": parameter_border,
            "parameter_text": parameter_text,
        }

    def _compact_text_layout(
        self,
        text: str,
        rect: QRectF,
        base_font: QFont,
        *,
        min_point_size: float,
        horizontal_padding: float = 0.0,
        vertical_padding: float = 0.0,
        shrink_to_fit: bool = True,
    ) -> tuple[QRectF, QFont, str]:
        draw_rect = rect.adjusted(horizontal_padding, vertical_padding, -horizontal_padding, -vertical_padding)
        draw_rect = QRectF(draw_rect)
        resolved_text = str(text or "").strip() or "empty"
        fitted_font = QFont(base_font)
        point_size = fitted_font.pointSizeF() if fitted_font.pointSizeF() > 0 else float(fitted_font.pointSize())
        point_size = max(min_point_size, point_size)
        min_point_size = max(6.0, min_point_size)

        while shrink_to_fit and point_size > min_point_size:
            fitted_font.setPointSizeF(point_size)
            metrics = QFontMetricsF(fitted_font)
            if metrics.height() <= draw_rect.height() + 0.5 and metrics.horizontalAdvance(resolved_text) <= draw_rect.width() + 0.5:
                break
            point_size = max(min_point_size, point_size - 0.5)
            if point_size == min_point_size:
                fitted_font.setPointSizeF(point_size)
                break
        if not shrink_to_fit:
            fitted_font.setPointSizeF(point_size)

        metrics = QFontMetrics(fitted_font)
        available_width = max(1, int(draw_rect.width()))
        display_text = metrics.elidedText(resolved_text, Qt.TextElideMode.ElideRight, available_width)
        return draw_rect, fitted_font, display_text

    def _paint_compact_text(
        self,
        painter: QPainter,
        rect: QRectF,
        text: str,
        base_font: QFont,
        color: QColor,
        alignment: Qt.AlignmentFlag,
        *,
        min_point_size: float,
        horizontal_padding: float = 0.0,
        vertical_padding: float = 0.0,
        shrink_to_fit: bool = True,
    ) -> None:
        draw_rect, fitted_font, display_text = self._compact_text_layout(
            text,
            rect,
            base_font,
            min_point_size=min_point_size,
            horizontal_padding=horizontal_padding,
            vertical_padding=vertical_padding,
            shrink_to_fit=shrink_to_fit,
        )
        painter.setFont(fitted_font)
        painter.setPen(color)
        painter.drawText(draw_rect, alignment, display_text)

    def _paint_lock_badge(self, painter: QPainter) -> None:
        lock_fill = QColor("#20242c" if self.node.locked else "#14181f")
        painter.setBrush(lock_fill)
        painter.setPen(QPen(QColor("#d4d9e6" if self.node.locked else "#758098"), 1.0))
        painter.drawRoundedRect(self.lock_rect(), 4, 4)
        shackle = QPainterPath()
        shackle.moveTo(self.lock_rect().left() + 5.5, self.lock_rect().top() + 8.0)
        shackle.arcTo(self.lock_rect().left() + 4.0, self.lock_rect().top() + 3.0, 10.0, 10.0, 180.0, -180.0)
        painter.drawPath(shackle)
        body_rect = QRectF(self.lock_rect().left() + 4.5, self.lock_rect().top() + 8.5, 9.0, 6.5)
        painter.drawRoundedRect(body_rect, 2, 2)

    def _paint_connection_pins(self, painter: QPainter, fill_color: QColor, border_color: QColor) -> None:
        if not self._supports_connections():
            return
        pin_stroke = QColor(border_color)
        pin_stroke.setAlpha(240)
        pin_fill = QColor(fill_color)
        pin_fill.setAlpha(250)
        painter.setPen(QPen(pin_stroke, 1.4))
        painter.setBrush(pin_fill)
        painter.drawEllipse(self.input_pin_rect())
        painter.drawEllipse(self.output_pin_rect())

    def paint(self, painter: QPainter, option, widget=None) -> None:
        del option, widget
        fast_render = self._fast_rendering()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, not fast_render)
        if self.node.type == "Comment":
            body_color, header_color, type_border, accent, text_color = self._comment_colors()
        else:
            body_color, header_color, type_border, accent, text_color = self._node_shell_colors()
        border = QColor(type_border)
        if self._warnings:
            border = QColor("#c85a5a")
        elif self._search_highlight:
            border = QColor("#ffcf25")
        elif self.isSelected():
            border = QColor(accent).lighter(112)

        shadow_color = QColor(0, 0, 0, 48 if self.isSelected() else 30)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(shadow_color)
        painter.drawRoundedRect(self._rect.adjusted(0, 4, 0, 4), 10, 10)

        if self._uses_compact_card():
            note_rect = self._card_layout["note"]
            frame_rect = self._card_layout["frame"]
            draw_rect = self._card_layout["draw"]
            action_rect = self._card_layout["action"]
            target_rect = self._card_layout["target_idle"]
            parameter_rect = self._card_layout["parameter"]

            palette = self._compact_card_palette()
            painter.setPen(QPen(border, 2.4))
            painter.setBrush(palette["frame_fill"])
            painter.drawRoundedRect(frame_rect, 6, 6)
            if self.isSelected():
                select_glow = QColor("#fff4a8")
                select_glow.setAlpha(210)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(select_glow, 4.0))
                painter.drawRoundedRect(frame_rect.adjusted(-3, -3, 3, 3), 9, 9)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(palette["frame_inner_fill"])
            painter.drawRoundedRect(frame_rect.adjusted(8, 8, -8, -8), 4, 4)

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(palette["note_fill"])
            painter.drawRoundedRect(note_rect, 6, 6)
            self._paint_compact_text(
                painter,
                note_rect,
                self._card_field_text("tips") or "empty",
                self._compact_note_font_for_text(self._card_field_text("tips") or "empty", note_rect.width()),
                palette["note_text"],
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                min_point_size=self.CARD_NOTE_MIN_POINT_SIZE,
                horizontal_padding=10.0,
                shrink_to_fit=False,
            )

            painter.setPen(QPen(palette["draw_border"], 2.0))
            painter.setBrush(palette["draw_fill"])
            painter.drawRoundedRect(draw_rect, 14, 14)
            block_font = self._compact_title_font()
            self._paint_compact_text(
                painter,
                draw_rect,
                self._card_field_text("draw_able_name") or "empty",
                block_font,
                palette["draw_text"],
                Qt.AlignmentFlag.AlignCenter,
                min_point_size=self.CARD_TITLE_MIN_POINT_SIZE,
                horizontal_padding=12.0,
                vertical_padding=4.0,
            )

            arrow_path = QPainterPath()
            arrow_path.moveTo(action_rect.left(), action_rect.top() + 16.0)
            arrow_path.lineTo(action_rect.left() + action_rect.width() - 62.0, action_rect.top() + 16.0)
            arrow_path.lineTo(action_rect.left() + action_rect.width() - 62.0, action_rect.top())
            arrow_path.lineTo(action_rect.right(), action_rect.center().y())
            arrow_path.lineTo(action_rect.left() + action_rect.width() - 62.0, action_rect.bottom())
            arrow_path.lineTo(action_rect.left() + action_rect.width() - 62.0, action_rect.bottom() - 16.0)
            arrow_path.lineTo(action_rect.left(), action_rect.bottom() - 16.0)
            arrow_path.closeSubpath()
            painter.setPen(QPen(palette["action_border"], 2.6))
            painter.setBrush(palette["action_fill"])
            painter.drawPath(arrow_path)
            self._paint_compact_text(
                painter,
                action_rect.adjusted(18, 0, -40, 0),
                self._card_field_text("action_trigger") or "empty",
                self._compact_action_font(),
                palette["action_text"],
                Qt.AlignmentFlag.AlignCenter,
                min_point_size=self.CARD_ACTION_MIN_POINT_SIZE,
                horizontal_padding=2.0,
                vertical_padding=10.0,
            )

            if target_rect.width() > 1.0 and target_rect.height() > 1.0:
                painter.setPen(QPen(palette["target_border"], 2.6))
                painter.setBrush(palette["target_fill"])
                painter.drawRoundedRect(target_rect, target_rect.height() / 2.0, target_rect.height() / 2.0)
                self._paint_compact_text(
                    painter,
                    target_rect,
                    self._card_field_text("action_trigger_active") or "empty",
                    self._compact_target_font(),
                    palette["target_text"],
                    Qt.AlignmentFlag.AlignCenter,
                    min_point_size=self.CARD_TARGET_MIN_POINT_SIZE,
                    horizontal_padding=18.0,
                    vertical_padding=8.0,
                )

            painter.setPen(QPen(palette["parameter_border"], 2.6))
            painter.setBrush(palette["parameter_fill"])
            painter.drawRoundedRect(parameter_rect, parameter_rect.height() / 2.0, parameter_rect.height() / 2.0)
            self._paint_compact_text(
                painter,
                parameter_rect,
                self._card_field_text("parameter") or "empty",
                self._compact_parameter_font(),
                palette["parameter_text"],
                Qt.AlignmentFlag.AlignCenter,
                min_point_size=self.CARD_PARAMETER_MIN_POINT_SIZE,
                horizontal_padding=12.0,
                vertical_padding=4.0,
            )

            self._paint_connection_pins(painter, accent, border)
            self._paint_lock_badge(painter)
            if self._attention_strength > 0.001:
                flash_fill = QColor(255, 255, 255, int(118 * self._attention_strength))
                painter.setBrush(flash_fill)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(self._rect, 10, 10)
            if self._warnings:
                painter.setBrush(QColor("#c85a5a"))
                painter.drawRoundedRect(QRectF(self._rect.width() - 44, 8, 30, 18), 6, 6)
                painter.setPen(QColor("#ffffff"))
                painter.drawText(QRectF(self._rect.width() - 44, 8, 30, 18), Qt.AlignmentFlag.AlignCenter, str(len(self._warnings)))
            return

        if self._is_draw_frame():
            frame_fill = QColor(body_color)
            frame_fill.setAlpha(28)
            painter.setPen(QPen(border, 2.5))
            painter.setBrush(frame_fill)
            painter.drawRoundedRect(self._rect, 8, 8)
            if self.isSelected():
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor("#fff4a8"), 4.0))
                painter.drawRoundedRect(self._rect.adjusted(-3, -3, 3, 3), 10, 10)
            title_rect = self._draw_frame_title_rect()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(border))
            painter.drawRoundedRect(title_rect.adjusted(0, 0, 0, 2), 6, 6)
            painter.setPen(text_color)
            title_font = self._draw_frame_title_font()
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
        if self.isSelected():
            select_glow = QColor("#fff4a8")
            select_glow.setAlpha(215)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(select_glow, 4.0))
            painter.drawRoundedRect(self._rect.adjusted(-3, -3, 3, 3), 12, 12)
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
            panel_fill = QColor("#0d0e12")
            panel_fill.setAlpha(228)
            panel_border = QColor("#c8d3e6")
            panel_border.setAlpha(28)
            painter.setBrush(panel_fill)
            painter.setPen(QPen(panel_border, 1.0))
            painter.drawRoundedRect(content_rect, 8, 8)
        title_color = QColor("#ff7d7d") if self._warnings else QColor(text_color)
        if self.node.fields.get("tips") and not self._warnings:
            title_color = QColor("#ffcf25")
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
        self._paint_connection_pins(painter, accent, border)
        if self._warnings:
            painter.setBrush(QColor("#c85a5a"))
            painter.drawRoundedRect(QRectF(self._rect.width() - 44, 8, 30, 18), 6, 6)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(QRectF(self._rect.width() - 44, 8, 30, 18), Qt.AlignmentFlag.AlignCenter, str(len(self._warnings)))
        if self.schema.nodes[self.node.type].resizable:
            painter.setBrush(QColor("#ffcf25"))
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
        if self._proxy_contains(event.pos()):
            super().mousePressEvent(event)
            return
        self._drag_start_pos = self.pos()
        self._drag_start_logical_pos = QPointF(float(self.node.ui_position["x"]), float(self.node.ui_position["y"]))
        if not self.node.locked:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            view = self._canvas_view()
            if view:
                view._set_interaction_busy("drag", True)
                selected_ids = [node_uuid for node_uuid in view.selected_node_uuids() if node_uuid != self.node.uuid]
                self._selection_drag_targets = {
                    node_uuid
                    for node_uuid in selected_ids
                    if node_uuid in view.node_items and not view.node_items[node_uuid].node.locked
                }
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
        logical_current = QPointF(current)
        new_pos = (logical_current.x(), logical_current.y())
        if self._group_drag_targets:
            positions = {self.node.uuid: new_pos}
            for node_uuid, origin in self._group_drag_targets.items():
                if not view:
                    continue
                item = view.node_items.get(node_uuid)
                if not item:
                    continue
                logical_position = QPointF(item.pos())
                positions[node_uuid] = (logical_position.x(), logical_position.y())
            self._group_drag_targets.clear()
            self._group_drag_active = False
            self._selection_drag_targets.clear()
            if len(positions) > 1 or positions.get(self.node.uuid) != old_pos:
                self.controller.move_nodes(positions, label="Move frame with nodes")
            return
        if view and self._selection_drag_targets:
            positions = {self.node.uuid: new_pos}
            for node_uuid in self._selection_drag_targets:
                item = view.node_items.get(node_uuid)
                if not item:
                    continue
                logical_position = QPointF(item.pos())
                positions[node_uuid] = (logical_position.x(), logical_position.y())
            self._selection_drag_targets.clear()
            if len(positions) > 1 or positions.get(self.node.uuid) != old_pos:
                self.controller.move_nodes(positions, label="Move selected nodes")
            return
        self._selection_drag_targets.clear()
        self.controller.move_node(self.node.uuid, old_pos, new_pos)

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value: Any):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.geometryChanged.emit(self.node.uuid)
        elif change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.refresh_z_value()
        return super().itemChange(change, value)

    def refresh_z_value(self) -> None:
        active = self.isSelected() or self.controller.selected_node_uuid == self.node.uuid
        if self.node.type == "DrawFrame":
            self.setZValue(self.DRAWFRAME_SELECTED_Z if active else self.DRAWFRAME_BASE_Z)
        elif self.node.type == "Comment":
            self.setZValue(self.COMMENT_SELECTED_Z if active else self.COMMENT_BASE_Z)
        else:
            self.setZValue(self.NORMAL_SELECTED_Z if active else self.NORMAL_BASE_Z)

    def hoverMoveEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        if self.lock_rect().contains(event.pos()):
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        elif self.schema.nodes[self.node.type].resizable and self.resize_handle_rect().contains(event.pos()):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif self._card_field_key_at(event.pos()):
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        elif self._proxy_contains(event.pos()):
            self.setCursor(Qt.CursorShape.ArrowCursor)
        elif self.pin_hit(event.scenePos()):
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif self.node.locked:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().hoverMoveEvent(event)

    def _commit_field(self, key: str, value: Any) -> None:
        self.controller.update_field(self.node.uuid, key, value, self.controller.preferences.global_mode)

    def _commit_fields(self, values: dict[str, Any]) -> None:
        self.controller.update_fields(self.node.uuid, values, self.controller.preferences.global_mode, label="应用外观方案")

    def _view_scale(self) -> float:
        if self.scene() and self.scene().views():
            return max(0.03, float(self.scene().views()[0].transform().m11()))
        return 1.0

    def refresh_view_scale(self) -> None:
        self.update()

    def _text_scale_floor(self, base_width: float, compact_mode: bool) -> float:
        del base_width
        return 0.2 if compact_mode else 0.24

    def _scale_compensated_point_size(
        self,
        base_size: float,
        min_size: float,
        max_size: float,
        *,
        scale_floor: float | None = None,
    ) -> float:
        compensation_scale = max(self._view_scale(), scale_floor if scale_floor is not None else self._font_scale_floor)
        return min(max_size, max(min_size, base_size / compensation_scale))

    def _fast_rendering(self) -> bool:
        view = self._canvas_view()
        return bool(view and view.should_use_fast_rendering())

    def _title_font(self) -> QFont:
        title_font = QFont(self.form.font())
        title_font.setBold(True)
        if self.node.type == "Initial":
            title_font.setPointSizeF(
                self._scale_compensated_point_size(
                    self.INITIAL_TITLE_BASE_POINT_SIZE,
                    self.INITIAL_TITLE_MIN_POINT_SIZE,
                    self.INITIAL_TITLE_MAX_POINT_SIZE,
                    scale_floor=self.TITLE_SCALE_FLOOR,
                )
            )
            return title_font
        title_font.setPointSizeF(
            self._scale_compensated_point_size(
                self.TITLE_BASE_POINT_SIZE,
                self.TITLE_MIN_POINT_SIZE,
                self.TITLE_MAX_POINT_SIZE,
                scale_floor=self.TITLE_SCALE_FLOOR,
            )
        )
        return title_font

    def _summary_font(self) -> QFont:
        summary_font = QFont(self.form.font())
        summary_font.setBold(True)
        summary_font.setPointSizeF(
            self._scale_compensated_point_size(
                self.SUMMARY_BASE_POINT_SIZE,
                self.SUMMARY_MIN_POINT_SIZE,
                self.SUMMARY_MAX_POINT_SIZE,
                scale_floor=self.SUMMARY_SCALE_FLOOR,
            )
        )
        return summary_font

    def _draw_frame_title_font(self) -> QFont:
        title_font = QFont(self.form.font())
        title_font.setBold(True)
        title_font.setPointSizeF(
            self._scale_compensated_point_size(
                self.DRAWFRAME_TITLE_BASE_POINT_SIZE,
                self.DRAWFRAME_TITLE_MIN_POINT_SIZE,
                self.DRAWFRAME_TITLE_MAX_POINT_SIZE,
                scale_floor=self.DRAWFRAME_TITLE_SCALE_FLOOR,
            )
        )
        return title_font

    def _compact_note_font(self) -> QFont:
        return self._compact_card_font(self.CARD_NOTE_BASE_POINT_SIZE)

    def _compact_note_font_for_text(self, text: str, max_width: float) -> QFont:
        font = self._compact_note_font()
        resolved_text = str(text or "").strip() or "empty"
        available_width = max(1.0, max_width - 20.0)
        metrics = QFontMetricsF(font)
        text_width = metrics.horizontalAdvance(resolved_text)
        if text_width <= available_width:
            return font
        current_size = font.pointSizeF() if font.pointSizeF() > 0 else float(font.pointSize())
        scaled_size = current_size * available_width / max(1.0, text_width)
        font.setPointSizeF(max(self.CARD_NOTE_MIN_POINT_SIZE, min(current_size, scaled_size)))
        return font

    def _compact_title_font(self) -> QFont:
        return self._compact_card_font(self.CARD_TITLE_BASE_POINT_SIZE)

    def _compact_action_font(self) -> QFont:
        return self._compact_card_font(self.CARD_ACTION_BASE_POINT_SIZE)

    def _compact_target_font(self) -> QFont:
        return self._compact_card_font(self.CARD_TARGET_BASE_POINT_SIZE)

    def _compact_parameter_font(self) -> QFont:
        return self._compact_card_font(self.CARD_PARAMETER_BASE_POINT_SIZE)

    def has_card_field_editor(self) -> bool:
        return self._card_editor_proxy is not None

    def _discard_card_field_editor(self) -> None:
        proxy = self._card_editor_proxy
        self._card_editor_proxy = None
        self._card_editor_key = None
        if not proxy:
            return
        widget = proxy.widget()
        if widget is not None:
            widget.blockSignals(True)
            proxy.setWidget(None)
            widget.deleteLater()
        if self.scene():
            self.scene().removeItem(proxy)
        proxy.deleteLater()

    def _begin_card_field_edit(self, field_key: str) -> bool:
        if self.node.locked or not self._uses_compact_card():
            return False
        field_rect = self._card_rect_for_field(field_key)
        if field_rect is None:
            return False
        self.setSelected(True)
        self.controller.set_selected_node(self.node.uuid)
        if self._card_editor_key == field_key and self._card_editor_proxy:
            widget = self._card_editor_proxy.widget()
            if widget is not None:
                widget.setFocus(Qt.FocusReason.MouseFocusReason)
                if hasattr(widget, "selectAll"):
                    widget.selectAll()
            return True
        self._discard_card_field_editor()
        editor = NumericLineEdit("nullable_int") if field_key == "action_trigger_active" else CommitLineEdit()
        editor_font = self._card_editor_font(field_key)
        editor.setFont(editor_font)
        schema_field = self._schema_field(field_key)
        if schema_field and schema_field.placeholder:
            editor.setPlaceholderText(schema_field.placeholder)
        editor.setText(self._card_field_text(field_key))
        editor.setAlignment(Qt.AlignmentFlag.AlignLeft if field_key == "tips" else Qt.AlignmentFlag.AlignCenter)
        self._apply_card_editor_style(editor)
        proxy = QGraphicsProxyWidget(self)
        proxy.setZValue(24)
        proxy.setWidget(editor)
        self._card_editor_proxy = proxy
        self._card_editor_key = field_key
        self._sync_card_editor_geometry(field_key, editor)
        editor.committed.connect(lambda value, key=field_key, target=editor: self._commit_card_field_edit(key, value, target))
        if field_key == "tips":
            editor.textChanged.connect(lambda _text, key=field_key, target=editor: self._sync_card_editor_geometry(key, target))
        editor.setFocus(Qt.FocusReason.MouseFocusReason)
        editor.selectAll()
        return True

    def _sync_card_editor_geometry(self, field_key: str, editor) -> None:
        if not self._card_editor_proxy or self._card_editor_proxy.widget() is not editor:
            return
        if field_key == "tips" and self._uses_compact_card():
            self.prepareGeometryChange()
            height = self._recompute_compact_card_layout(self._rect.width(), note_text=editor.text())
            frame_width = self._card_layout.get("frame", self._rect).width()
            editor_font = self._compact_note_font_for_text(editor.text(), frame_width)
            editor.setFont(editor_font)
            self._apply_card_editor_style(editor)
            editor_note_width = self._compact_note_width_for_text(editor.text(), frame_width, editor_font)
            current_note = self._card_layout.get("note")
            if current_note is not None:
                note_height = max(current_note.height(), self._compact_note_height_for_font(editor_font))
                self._card_layout["note"] = QRectF(current_note.left(), current_note.top(), editor_note_width, note_height)
            self._rect = QRectF(0.0, 0.0, self._rect.width(), height)
        field_rect = self._card_rect_for_field(field_key)
        if field_rect is None:
            return
        inset = 8.0 if field_key != "tips" else 4.0
        edit_rect = field_rect.adjusted(inset, 4.0, -inset, -4.0)
        width = max(120, int(math.ceil(edit_rect.width())))
        height = max(28, int(math.ceil(edit_rect.height())))
        self._card_editor_proxy.setPos(edit_rect.topLeft())
        self._card_editor_proxy.setMinimumSize(width, height)
        self._card_editor_proxy.setPreferredSize(width, height)
        self._card_editor_proxy.setMaximumSize(width, height)
        self._card_editor_proxy.setGeometry(QRectF(edit_rect.left(), edit_rect.top(), width, height))
        editor.setFixedSize(width, height)
        editor.resize(width, height)
        self._card_editor_proxy.updateGeometry()
        self._card_editor_proxy.update()
        self.update()

    def _apply_card_editor_style(self, editor) -> None:
        editor.setStyleSheet(
            "QLineEdit {"
            " background: rgba(9, 11, 16, 0.94);"
            " color: #f9f9f9;"
            " border: 1px solid rgba(255, 255, 255, 0.18);"
            " border-radius: 10px;"
            " padding: 4px 10px;"
            " font-weight: 600;"
            "}"
            "QLineEdit:focus {"
            " border: 2px solid #55b3ff;"
            "}"
        )

    def open_appearance_dialog(self, anchor_global_pos=None) -> bool:
        if self.node.locked:
            return False
        self.setSelected(True)
        self.controller.set_selected_node(self.node.uuid)
        if getattr(self.form, "node", None) is not self.node:
            dialog = NodeAppearanceDialog(self.node.fields, self._canvas_view() or self.form)
            if anchor_global_pos is not None:
                dialog.move(anchor_global_pos)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            updates = {key: value for key, value in dialog.values().items() if self.node.fields.get(key) != value}
            if updates:
                self.controller.update_fields(self.node.uuid, updates, self.controller.preferences.global_mode, label="应用外观方案")
            return True
        return self.form.open_appearance_dialog(anchor_global_pos)

    def _card_editor_font(self, field_key: str) -> QFont:
        if field_key == "tips":
            return self._compact_note_font()
        if field_key == "draw_able_name":
            return self._compact_title_font()
        if field_key == "action_trigger":
            return self._compact_action_font()
        if field_key == "action_trigger_active":
            return self._compact_target_font()
        return self._compact_parameter_font()

    def _commit_card_field_edit(self, field_key: str, value: Any, editor) -> None:
        if not self._card_editor_proxy or self._card_editor_proxy.widget() is not editor:
            return
        self._discard_card_field_editor()
        self.controller.update_field(self.node.uuid, field_key, value, "simple")

    @staticmethod
    def _summary_text_color(color: str) -> QColor:
        resolved = QColor(color)
        return resolved if resolved.isValid() else QColor("#dfe5ef")

    def _recompute_header_layout(self, width: float) -> None:
        title_font = self._title_font()
        metrics = QFontMetrics(title_font)
        summary_font = self._summary_font()
        summary_metrics = QFontMetrics(summary_font)
        available_width = max(140, int(width - 28.0))
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
        padding_top = 13.0
        title_bottom = padding_top + max(24.0, float(title_bounds.height()))
        summary_y = title_bottom + 6.0
        row_gap = 4.0
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
            row_height = max(16.0, float(row_bounds.height()))
            rect = QRectF(14.0, summary_y, width - 28.0, row_height)
            self._summary_layout_rows.append((rect, row_text, self._summary_text_color(color)))
            summary_y += row_height + row_gap
        if self._summary_layout_rows:
            summary_top = self._summary_layout_rows[-1][0].bottom()
        padding_bottom = 10.0
        self._title_rect = QRectF(
            14.0,
            padding_top,
            width - 28.0,
            max(24.0, float(title_bounds.height())),
        )
        self._header_height = max(
            self._base_header_height,
            max(self._title_rect.y() + self._title_rect.height(), summary_top) + padding_bottom,
        )
        self._content_top_gap = self._base_content_top_gap

    def _full_title_text(self) -> str:
        return node_title(self.schema, self.node)


class GroupItem(QGraphicsObject):
    BASE_Z = -140.0
    SELECTED_Z = -130.0
    OUTER_PADDING = 28.0

    def __init__(self, controller, view: "NodeCanvasView", group) -> None:
        super().__init__()
        self.controller = controller
        self.view = view
        self.group = group
        self._rect = QRectF(0.0, 0.0, 120.0, 80.0)
        self._title_rect = QRectF(0.0, 0.0, 120.0, 34.0)
        self._dragging = False
        self._drag_start_scene = QPointF()
        self._drag_start_pos = QPointF()
        self._member_origins: dict[str, QPointF] = {}
        self._table_origins: dict[str, QPointF] = {}
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        self.setZValue(self.BASE_Z)

    def boundingRect(self) -> QRectF:
        return self._rect.adjusted(-8.0, -8.0, 8.0, 8.0)

    def update_group(self, group, member_rects: list[QRectF]) -> None:
        self.prepareGeometryChange()
        self.group = group
        if member_rects:
            bounds = member_rects[0]
            for rect in member_rects[1:]:
                bounds = bounds.united(rect)
            left = bounds.left() - self.OUTER_PADDING
            top = bounds.top() - self.OUTER_PADDING
            width = max(180.0, bounds.width() + self.OUTER_PADDING * 2.0)
            height = max(110.0, bounds.height() + self.OUTER_PADDING * 2.0)
        else:
            left = 0.0
            top = 0.0
            width = 180.0
            height = 110.0
        self.setPos(left, top)
        self._rect = QRectF(0.0, 0.0, width, height)
        title_height = max(32.0, float(QFontMetrics(self._title_font()).height()) + 12.0)
        self._title_rect = QRectF(16.0, 12.0, max(120.0, width - 32.0), title_height)
        self.update()

    def group_title(self) -> str:
        title = str(getattr(self.group, "title", "") or "").strip()
        return title or "分组"

    def _title_font(self) -> QFont:
        font = QFont()
        font.setBold(True)
        font.setPointSizeF(11.5)
        return font

    def paint(self, painter: QPainter, option, widget=None) -> None:
        del option, widget
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, not self.view.should_use_fast_rendering())
        body = QColor(str(getattr(self.group, "theme_body_color", "#dfeada") or "#dfeada"))
        border = QColor(str(getattr(self.group, "theme_border_color", "#69b070") or "#69b070"))
        text = QColor(str(getattr(self.group, "theme_text_color", "#ffffff") or "#ffffff"))
        frame_fill = QColor(body)
        frame_fill.setAlpha(22)
        title_fill = QColor(border)
        title_fill.setAlpha(242)
        stroke = QColor(border.lighter(114) if self.isSelected() else border)
        painter.setPen(QPen(stroke, 2.2))
        painter.setBrush(frame_fill)
        painter.drawRoundedRect(self._rect, 8, 8)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(title_fill)
        painter.drawRoundedRect(self._title_rect, 6, 6)
        painter.setPen(text)
        painter.setFont(self._title_font())
        painter.drawText(self._title_rect.adjusted(10.0, 0.0, -10.0, 0.0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.group_title())

    def title_contains(self, scene_pos: QPointF) -> bool:
        return self._title_rect.contains(self.mapFromScene(scene_pos))

    def focus_rect(self) -> QRectF:
        return self.mapRectToScene(self._rect)

    def _movable_member_positions(self) -> dict[str, QPointF]:
        result: dict[str, QPointF] = {}
        for node_uuid in self.controller.group_node_uuids(self.group.uuid):
            node = self.controller.get_node(node_uuid)
            if not node or node.locked:
                continue
            result[node_uuid] = QPointF(float(node.ui_position["x"]), float(node.ui_position["y"]))
        return result

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._title_rect.contains(event.pos()):
            self._member_origins = self._movable_member_positions()
            self._table_origins = {}
            for node_uuid in self._member_origins:
                table_item = self.view.table_row_to_item.get(node_uuid)
                if table_item is not None:
                    self._table_origins.setdefault(table_item.table_id, QPointF(table_item.pos()))
            if self._member_origins:
                self.setSelected(True)
                self._dragging = True
                self._drag_start_scene = event.scenePos()
                self._drag_start_pos = QPointF(self.pos())
                self.view._set_interaction_busy("drag", True)
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging:
            delta = event.scenePos() - self._drag_start_scene
            self.setPos(self._drag_start_pos + delta)
            for node_uuid, origin in self._member_origins.items():
                node_item = self.view.node_items.get(node_uuid)
                if node_item is not None:
                    node_item.setPos(origin + delta)
            for table_id, table_origin in self._table_origins.items():
                table_item = self.view.table_items.get(table_id)
                if table_item is not None:
                    table_item.setPos(table_origin + delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._dragging:
            delta = event.scenePos() - self._drag_start_scene
            self._dragging = False
            self.view._set_interaction_busy("drag", False)
            self.setCursor(Qt.CursorShape.OpenHandCursor if self._title_rect.contains(event.pos()) else Qt.CursorShape.ArrowCursor)
            positions = {
                node_uuid: (origin.x() + delta.x(), origin.y() + delta.y())
                for node_uuid, origin in self._member_origins.items()
            }
            self._member_origins.clear()
            self._table_origins.clear()
            if positions:
                self.controller.move_nodes(positions, label="Move group with nodes")
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def hoverMoveEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        if self._title_rect.contains(event.pos()):
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().hoverMoveEvent(event)

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value: Any):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.setZValue(self.SELECTED_Z if bool(value) else self.BASE_Z)
        return super().itemChange(change, value)


class ParameterTableItem(QGraphicsObject):
    BASE_Z = 0.0
    SELECTED_Z = 48.0
    HEADER_HEIGHT = 62.0
    ROW_HEIGHT = 54.0
    ROW_GAP = 8.0
    ROW_TOP_MARGIN = 12.0
    ROW_BOTTOM_MARGIN = 16.0
    LEFT_GUTTER = 12.0
    RIGHT_GUTTER = 12.0

    def __init__(self, schema: EditorSchema, controller, view: "NodeCanvasView", table_id: str) -> None:
        super().__init__()
        self.schema = schema
        self.controller = controller
        self.view = view
        self.table_id = table_id
        self._rows: list[Any] = []
        self._row_rects: dict[str, QRectF] = {}
        self._cell_rects: dict[tuple[str, str], QRectF] = {}
        self._selected_row_uuid: str | None = None
        self._selected_row_uuids: set[str] = set()
        self._rect = QRectF(0.0, 0.0, 520.0, 120.0)
        self._header_rect = QRectF(0.0, 0.0, 520.0, self.HEADER_HEIGHT)
        self._add_button_rect = QRectF()
        self._remove_button_rect = QRectF()
        self._dragging = False
        self._drag_start_scene = QPointF()
        self._drag_start_pos = QPointF()
        self._row_origins: dict[str, QPointF] = {}
        self._editor_proxy: QGraphicsProxyWidget | None = None
        self._editor_target: tuple[str, str] | None = None
        self._columns = self._build_columns()
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        self.setZValue(self.BASE_Z)

    def _build_columns(self) -> list[tuple[str, str, float]]:
        columns: list[tuple[str, str, float]] = []
        skip_keys = {
            "id",
            "target_idle",
            "action_trigger_active",
            "action_trigger_kind_ui",
            "action_trigger_reserved_ui",
            "action_trigger_active_kind_ui",
            "action_trigger_active_reserved_ui",
            "theme_body_color",
            "theme_border_color",
            "theme_text_color",
        }
        for field in self.schema.nodes["ParameterTrigger"].fields:
            if "simple" not in field.show_in_modes or field.key in skip_keys:
                continue
            width = 112.0
            if field.key in {"tips"}:
                width = 160.0
            elif field.key in {"draw_able_name", "parameter", "action_trigger"}:
                width = 156.0
            elif field.editor in {"range", "text"}:
                width = 132.0
            columns.append((field.key, field.label, width))
        return columns

    def selected_row_uuid(self) -> str | None:
        return self._selected_row_uuid

    def selected_row_uuids(self) -> list[str]:
        ordered = [row.uuid for row in self._rows if row.uuid in self._selected_row_uuids]
        if ordered:
            return ordered
        return [self._selected_row_uuid] if self._selected_row_uuid else []

    def row_node_uuids(self) -> list[str]:
        return [row.uuid for row in self._rows]

    def select_row(self, node_uuid: str | None) -> None:
        if node_uuid is not None and node_uuid not in self._row_rects:
            return
        self._selected_row_uuid = node_uuid
        self._selected_row_uuids = {node_uuid} if node_uuid else set()
        self.setSelected(True)
        if node_uuid:
            self.controller.set_selected_node(node_uuid)
        self.update()

    def update_rows(self, rows: list[Any]) -> None:
        self.prepareGeometryChange()
        self._rows = list(rows)
        self._rows.sort(key=lambda node: (parameter_table_order(node), node.ui_position["y"], node.ui_position["x"], node.uuid))
        if self._selected_row_uuid not in {row.uuid for row in self._rows}:
            self._selected_row_uuid = self._rows[0].uuid if self._rows else None
        self._selected_row_uuids.intersection_update({row.uuid for row in self._rows})
        if self._selected_row_uuid and not self._selected_row_uuids:
            self._selected_row_uuids.add(self._selected_row_uuid)
        if not self._rows:
            self._row_rects = {}
            self._cell_rects = {}
            self._rect = QRectF(0.0, 0.0, 520.0, 120.0)
            self._header_rect = QRectF(0.0, 0.0, 520.0, self.HEADER_HEIGHT)
            self._add_button_rect = QRectF(460.0, 7.0, 24.0, 24.0)
            self._remove_button_rect = QRectF(488.0, 7.0, 24.0, 24.0)
            self.setPos(0.0, 0.0)
            self.update()
            return
        anchor_x = min(float(row.ui_position["x"]) for row in self._rows)
        anchor_y = min(float(row.ui_position["y"]) for row in self._rows)
        table_width = self.LEFT_GUTTER + self.RIGHT_GUTTER + sum(width for _key, _label, width in self._columns)
        row_bottom = self.HEADER_HEIGHT + self.ROW_TOP_MARGIN
        self._row_rects = {}
        self._cell_rects = {}
        for index, row in enumerate(self._rows):
            row_y = self.HEADER_HEIGHT + self.ROW_TOP_MARGIN + index * (self.ROW_HEIGHT + self.ROW_GAP)
            rect = QRectF(self.LEFT_GUTTER, row_y, table_width - self.LEFT_GUTTER - self.RIGHT_GUTTER, self.ROW_HEIGHT)
            self._row_rects[row.uuid] = rect
            cursor_x = rect.left()
            for field_key, _label, width in self._columns:
                self._cell_rects[(row.uuid, field_key)] = QRectF(cursor_x, rect.top(), width, rect.height())
                cursor_x += width
            row_bottom = max(row_bottom, rect.bottom())
        self.setPos(anchor_x, anchor_y)
        self._rect = QRectF(0.0, 0.0, table_width, row_bottom + self.ROW_BOTTOM_MARGIN)
        self._header_rect = QRectF(0.0, 0.0, table_width, self.HEADER_HEIGHT)
        self._remove_button_rect = QRectF(table_width - 62.0, 7.0, 24.0, 24.0)
        self._add_button_rect = QRectF(table_width - 32.0, 7.0, 24.0, 24.0)
        self.update()

    def boundingRect(self) -> QRectF:
        return self._rect.adjusted(-10.0, -10.0, 10.0, 10.0)

    def _palette(self) -> tuple[QColor, QColor, QColor]:
        anchor = self._rows[0] if self._rows else None
        if anchor is None:
            return QColor("#071b2d"), QColor("#25b7ff"), QColor("#e8f8ff")
        colors = parameter_table_colors(anchor)
        return QColor(colors["_table_body_color"]), QColor(colors["_table_border_color"]), QColor(colors["_table_text_color"])

    def table_title(self) -> str:
        anchor = self._rows[0] if self._rows else None
        return parameter_table_title(anchor) if anchor is not None else "参数表"

    def row_scene_rect(self, node_uuid: str) -> QRectF | None:
        rect = self._row_rects.get(node_uuid)
        if rect is None:
            return None
        return self.mapRectToScene(rect)

    def pin_scene_pos(self, node_uuid: str, side: str) -> QPointF | None:
        del node_uuid, side
        return None

    def pin_hit(self, scene_pos: QPointF) -> tuple[str, str] | None:
        del scene_pos
        return None

    def row_uuid_at(self, local_pos: QPointF) -> str | None:
        for row_uuid, rect in self._row_rects.items():
            if rect.contains(local_pos):
                return row_uuid
        return None

    def cell_at(self, local_pos: QPointF) -> tuple[str, str] | None:
        for key, rect in self._cell_rects.items():
            if rect.contains(local_pos):
                return key
        return None

    def _schema_field(self, field_key: str):
        return next((field for field in self.schema.nodes["ParameterTrigger"].fields if field.key == field_key), None)

    def _row_by_uuid(self, node_uuid: str):
        return next((row for row in self._rows if row.uuid == node_uuid), None)

    def _discard_cell_editor(self) -> None:
        proxy = self._editor_proxy
        self._editor_proxy = None
        self._editor_target = None
        if not proxy:
            return
        widget = proxy.widget()
        if widget is not None:
            widget.blockSignals(True)
            proxy.setWidget(None)
            widget.deleteLater()
        if self.scene():
            self.scene().removeItem(proxy)
        proxy.deleteLater()

    def begin_cell_edit(self, row_uuid: str, field_key: str) -> bool:
        row = self._row_by_uuid(row_uuid)
        field = self._schema_field(field_key)
        cell_rect = self._cell_rects.get((row_uuid, field_key))
        if row is None or field is None or cell_rect is None or row.locked or field.read_only:
            return False
        self.select_row(row_uuid)
        self._discard_cell_editor()
        raw_value = row.fields.get(field_key, field.default)
        display_value = display_value_for_field(self.schema, row, field_key, raw_value)
        if field.editor == "combo":
            editor = CommitComboBox()
            for option in field.options:
                editor.addItem(option.label, option.value)
            index = editor.findData(raw_value)
            if index < 0:
                index = editor.findData(display_value)
            if index >= 0:
                editor.setCurrentIndex(index)
            editor.setStyleSheet(
                "QComboBox { background: #0b1420; color: #f8fafc; border: 2px solid #55b3ff; "
                "border-radius: 6px; padding: 4px 7px; }"
            )
        elif field.editor == "bool":
            editor = CommitComboBox()
            editor.addItem("否", 0)
            editor.addItem("是", 1)
            try:
                checked = bool(int(raw_value or 0))
            except (TypeError, ValueError):
                checked = str(raw_value).strip().lower() in {"true", "yes", "on", "是"}
            editor.setCurrentIndex(1 if checked else 0)
            editor.setStyleSheet(
                "QComboBox { background: #0b1420; color: #f8fafc; border: 2px solid #55b3ff; "
                "border-radius: 6px; padding: 4px 7px; }"
            )
        else:
            editor = NumericLineEdit(field.editor) if field.editor in {"int", "float", "nullable_int"} else CommitLineEdit()
            editor.setText(str(display_value or ""))
            editor.setPlaceholderText(field.placeholder)
            editor.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            editor.setStyleSheet(
                "QLineEdit { background: #0b1420; color: #f8fafc; border: 2px solid #55b3ff; "
                "border-radius: 6px; padding: 4px 7px; }"
            )
        proxy = QGraphicsProxyWidget(self)
        proxy.setZValue(32)
        proxy.setWidget(editor)
        edit_rect = cell_rect.adjusted(4.0, 7.0, -4.0, -7.0)
        proxy.setPos(edit_rect.topLeft())
        editor.setFixedSize(max(40, int(edit_rect.width())), max(24, int(edit_rect.height())))
        editor.committed.connect(lambda value, target=editor: self._commit_cell_edit(value, target))
        self._editor_proxy = proxy
        self._editor_target = (row_uuid, field_key)
        editor.setFocus(Qt.FocusReason.MouseFocusReason)
        if hasattr(editor, "selectAll"):
            editor.selectAll()
        return True

    def _commit_cell_edit(self, value: Any, editor) -> None:
        if not self._editor_proxy or self._editor_proxy.widget() is not editor or not self._editor_target:
            return
        row_uuid, field_key = self._editor_target
        self._discard_cell_editor()
        self.controller.update_field(row_uuid, field_key, value, self.controller.preferences.global_mode)

    def focus_rect(self) -> QRectF:
        if self._selected_row_uuid:
            rect = self.row_scene_rect(self._selected_row_uuid)
            if rect is not None:
                return rect
        return self.mapRectToScene(self._rect)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        del option, widget
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, not self.view.should_use_fast_rendering())
        body_color, border_color, text_color = self._palette()
        fill = QColor(body_color)
        fill.setAlpha(235)
        header_fill = QColor(border_color)
        header_fill.setAlpha(248)
        stroke = QColor(border_color.lighter(115) if self.isSelected() else border_color)
        painter.setPen(QPen(stroke, 2.0))
        painter.setBrush(fill)
        painter.drawRoundedRect(self._rect, 9, 9)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(header_fill)
        painter.drawRoundedRect(self._header_rect, 9, 9)
        painter.setBrush(header_fill)
        painter.drawRect(QRectF(0.0, self._header_rect.height() - 9.0, self._header_rect.width(), 9.0))

        title_font = QFont()
        title_font.setBold(True)
        title_font.setPointSizeF(11.4)
        painter.setFont(title_font)
        painter.setPen(QColor(text_color))
        title_rect = QRectF(14.0, 4.0, self._header_rect.width() - 82.0, 28.0)
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, f"{self.table_title()}  ({len(self._rows)} 行)")

        button_font = QFont()
        button_font.setBold(True)
        button_font.setPointSizeF(13.0)
        painter.setFont(button_font)
        for rect, label, enabled in (
            (self._remove_button_rect, "-", len(self._rows) > 1 and bool(self.selected_row_uuids())),
            (self._add_button_rect, "+", True),
        ):
            button_fill = QColor("#10151f")
            button_fill.setAlpha(230 if enabled else 90)
            painter.setPen(QPen(QColor(255, 255, 255, 160 if enabled else 70), 1.2))
            painter.setBrush(button_fill)
            painter.drawRoundedRect(rect, 5, 5)
            painter.setPen(QColor(255, 255, 255, 230 if enabled else 100))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)

        header_font = QFont()
        header_font.setBold(True)
        header_font.setPointSizeF(9.6)
        painter.setFont(header_font)
        x = self.LEFT_GUTTER
        for _field_key, label, width in self._columns:
            header_cell = QRectF(x, self.HEADER_HEIGHT - 26.0, width, 20.0)
            painter.setPen(QColor(255, 255, 255, 222))
            painter.drawText(header_cell.adjusted(4.0, 0.0, -4.0, 0.0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
            x += width

        cell_font = QFont()
        cell_font.setPointSizeF(9.3)
        painter.setFont(cell_font)
        for row in self._rows:
            rect = self._row_rects[row.uuid]
            row_fill = QColor(body_color.darker(126))
            is_row_selected = row.uuid in self._selected_row_uuids
            row_fill.setAlpha(252 if is_row_selected else 228)
            painter.setPen(QPen(QColor(border_color.darker(108)), 1.2))
            painter.setBrush(row_fill)
            painter.drawRoundedRect(rect, 7, 7)
            if is_row_selected:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor("#fff4a8"), 2.4))
                painter.drawRoundedRect(rect.adjusted(1.5, 1.5, -1.5, -1.5), 7, 7)
            cursor_x = rect.left()
            for field_key, _label, width in self._columns:
                cell_rect = QRectF(cursor_x, rect.top(), width, rect.height())
                painter.setPen(QColor(text_color))
                text = str(display_value_for_field(self.schema, row, field_key, row.fields.get(field_key)) or "").strip()
                painter.drawText(cell_rect.adjusted(8.0, 0.0, -8.0, 0.0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text or "-")
                painter.setPen(QColor(255, 255, 255, 22))
                painter.drawLine(QPointF(cell_rect.right(), rect.top() + 8.0), QPointF(cell_rect.right(), rect.bottom() - 8.0))
                cursor_x += width

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._discard_cell_editor()
            if self._add_button_rect.contains(event.pos()):
                self.controller.add_parameter_table_row(self.table_id, self._selected_row_uuid)
                event.accept()
                return
            if self._remove_button_rect.contains(event.pos()) and len(self._rows) > 1:
                for row_uuid in self.selected_row_uuids():
                    self.controller.remove_parameter_table_row(row_uuid)
                event.accept()
                return
            row_uuid = self.row_uuid_at(event.pos())
            if row_uuid:
                modifiers = event.modifiers()
                if modifiers & Qt.KeyboardModifier.ControlModifier:
                    if row_uuid in self._selected_row_uuids:
                        self._selected_row_uuids.remove(row_uuid)
                    else:
                        self._selected_row_uuids.add(row_uuid)
                elif modifiers & Qt.KeyboardModifier.ShiftModifier and self._selected_row_uuid:
                    row_ids = self.row_node_uuids()
                    start = row_ids.index(self._selected_row_uuid)
                    end = row_ids.index(row_uuid)
                    lower, upper = sorted((start, end))
                    self._selected_row_uuids = set(row_ids[lower : upper + 1])
                else:
                    self._selected_row_uuids = {row_uuid}
                self._selected_row_uuid = row_uuid
                self.controller.set_selected_node(row_uuid)
            else:
                self._selected_row_uuid = None
                self._selected_row_uuids.clear()
            self.setSelected(True)
            self._row_origins = {
                row.uuid: QPointF(float(row.ui_position["x"]), float(row.ui_position["y"]))
                for row in self._rows
                if not row.locked and (not self._selected_row_uuids or row.uuid in self._selected_row_uuids)
            }
            if self._row_origins and not (event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)):
                self._dragging = True
                self._drag_start_scene = event.scenePos()
                self._drag_start_pos = QPointF(self.pos())
                self.view._set_interaction_busy("drag", True)
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
                return
            self.update()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            cell = self.cell_at(event.pos())
            if cell and self.begin_cell_edit(*cell):
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging:
            delta = event.scenePos() - self._drag_start_scene
            self.setPos(self._drag_start_pos + delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._dragging:
            delta = event.scenePos() - self._drag_start_scene
            self._dragging = False
            self.view._set_interaction_busy("drag", False)
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            positions = {
                node_uuid: (origin.x() + delta.x(), origin.y() + delta.y())
                for node_uuid, origin in self._row_origins.items()
            }
            self._row_origins.clear()
            if positions:
                self.controller.move_nodes(positions, label="Move parameter table")
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def hoverMoveEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        if self._add_button_rect.contains(event.pos()) or self._remove_button_rect.contains(event.pos()) or self.cell_at(event.pos()):
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().hoverMoveEvent(event)

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value: Any):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.setZValue(self.SELECTED_Z if bool(value) else self.BASE_Z)
        return super().itemChange(change, value)


class NodeCanvasView(QGraphicsView):
    selectionSummaryChanged = pyqtSignal(object, object)
    interactionBusyChanged = pyqtSignal(bool)
    THUMBNAIL_SCALE_THRESHOLD = 0.45

    def __init__(self, schema: EditorSchema, controller, parent=None) -> None:
        super().__init__(parent)
        self.schema = schema
        self.controller = controller
        self.scene_ref = GridScene(self)
        self.setScene(self.scene_ref)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)
        self.setOptimizationFlag(QGraphicsView.OptimizationFlag.DontAdjustForAntialiasing, True)
        self.setOptimizationFlag(QGraphicsView.OptimizationFlag.DontSavePainterState, True)
        self.setCacheMode(QGraphicsView.CacheModeFlag.CacheBackground)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.node_items: dict[str, NodeItem] = {}
        self.connection_items: dict[tuple[str, str], ConnectionItem] = {}
        self.group_items: dict[str, GroupItem] = {}
        self.table_items: dict[str, ParameterTableItem] = {}
        self.table_row_to_item: dict[str, ParameterTableItem] = {}
        self.zoom_wheel_modifier = "ctrl"
        self.horizontal_wheel_modifier = "alt_shift"
        self.expanded_node_uuids: set[str] = set()
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
        self._deferred_connection_update_uuids: set[str] = set()
        self._last_scale_sensitive_scale: float | None = None
        self._pending_display_toggle_uuid: str | None = None
        self._display_toggle_click_timer = QTimer(self)
        self._display_toggle_click_timer.setSingleShot(True)
        self._display_toggle_click_timer.timeout.connect(self._clear_pending_display_toggle)
        self._wheel_busy_timer = QTimer(self)
        self._wheel_busy_timer.setSingleShot(True)
        self._wheel_busy_timer.timeout.connect(lambda: self._set_interaction_busy("wheel", False))
        self.scene_ref.selectionChanged.connect(self._on_scene_selection_changed)
        controller.documentLoaded.connect(self.rebuild_scene)
        controller.nodeAdded.connect(self._add_or_update_node_item)
        controller.nodeRemoved.connect(self._remove_node_item)
        controller.nodeUpdated.connect(self._update_node_item)
        controller.nodeMoved.connect(self._move_node_item)
        controller.connectionsChanged.connect(self._rebuild_connections)
        controller.validationChanged.connect(self._apply_validation)
        controller.selectionChanged.connect(lambda _uuid: self._refresh_node_z_values())
        controller.globalModeChanged.connect(self._handle_mode_changed)
        controller.documentStateChanged.connect(self._handle_document_state_changed)
        controller.nodeUpdated.connect(self._refresh_hint_overlay)
        controller.groupsChanged.connect(self._rebuild_groups)
        self.rebuild_scene()

    def rebuild_scene(self) -> None:
        with performance_recorder.measure(
            "canvas.rebuild_scene",
            "canvas",
            {"node_count": len(self.controller.document.nodes), "connection_count": len(self.controller.document.connections)},
        ):
            self.expanded_node_uuids.clear()
            for item in list(self.connection_items.values()):
                self.scene_ref.removeItem(item)
            for item in list(self.group_items.values()):
                self.scene_ref.removeItem(item)
            for item in list(self.table_items.values()):
                self.scene_ref.removeItem(item)
            for item in list(self.node_items.values()):
                self.scene_ref.removeItem(item)
            self.connection_items.clear()
            self.group_items.clear()
            self.table_items.clear()
            self.table_row_to_item.clear()
            self.node_items.clear()
            with performance_recorder.measure("canvas.create_node_items", "canvas", {"node_count": len(self.controller.document.nodes)}):
                for node in self.controller.document.nodes:
                    self._create_item(node)
            self._rebuild_parameter_tables()
            self._rebuild_groups()
            self._rebuild_connections()
            self.resetTransform()
            scale = self.controller.document.canvas_view.scale
            if scale != 1.0:
                self.scale(scale, scale)
            self._refresh_scale_sensitive_nodes(force=True)
            self.centerOn(self.controller.document.canvas_view.offset_x, self.controller.document.canvas_view.offset_y)
            self._refresh_hint_overlay()

    def _set_interaction_busy(self, flag: str, busy: bool) -> None:
        before = bool(self._interaction_flags)
        was_motion_preview = self.should_use_motion_preview()
        if busy:
            self._interaction_flags.add(flag)
        else:
            self._interaction_flags.discard(flag)
            if flag in {"drag", "resize"}:
                self._flush_deferred_connection_updates()
        after = bool(self._interaction_flags)
        if before != after:
            self.interactionBusyChanged.emit(after)
            if was_motion_preview and not self.should_use_motion_preview():
                self.viewport().update()

    def is_busy(self) -> bool:
        return bool(self._interaction_flags)

    def should_use_fast_rendering(self) -> bool:
        item_count = len(self.node_items) + len(self.table_items)
        if item_count <= 50:
            return False
        if self._interaction_flags:
            return True
        return float(self.transform().m11()) < 0.42

    def should_use_motion_preview(self) -> bool:
        item_count = len(self.node_items) + len(self.table_items)
        if item_count <= 50:
            return False
        if not (self._interaction_flags & {"pan", "wheel", "focus", "resize"}):
            return False
        return float(self.transform().m11()) < 0.5

    def _flush_deferred_connection_updates(self) -> None:
        if not self._deferred_connection_update_uuids:
            return
        pending = list(self._deferred_connection_update_uuids)
        self._deferred_connection_update_uuids.clear()
        for node_uuid in pending:
            self._update_connections_for_node(node_uuid)

    def _clear_pending_display_toggle(self) -> None:
        self._pending_display_toggle_uuid = None

    def _queue_display_toggle(self, node_uuid: str) -> None:
        self._pending_display_toggle_uuid = node_uuid
        app = QApplication.instance()
        interval = app.styleHints().mouseDoubleClickInterval() if app is not None else 400
        self._display_toggle_click_timer.start(max(250, interval))

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
        selected: list[str] = []
        seen: set[str] = set()
        for item in self.scene_ref.selectedItems():
            if isinstance(item, NodeItem):
                if item.node.uuid not in seen:
                    seen.add(item.node.uuid)
                    selected.append(item.node.uuid)
            elif isinstance(item, ParameterTableItem):
                row_ids = item.selected_row_uuids() or item.row_node_uuids()
                for node_uuid in row_ids:
                    if node_uuid and node_uuid not in seen:
                        seen.add(node_uuid)
                        selected.append(node_uuid)
        return selected

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
        return False

    def toggle_node_display_mode(self, node_uuid: str) -> None:
        node = self.controller.get_node(node_uuid)
        if not node or node.type not in function_node_types(self.schema):
            return
        if node_uuid in self.expanded_node_uuids:
            self.expanded_node_uuids.discard(node_uuid)
        else:
            self.expanded_node_uuids.add(node_uuid)
        self._update_node_item(node_uuid)

    def focus_node_field(self, node_uuid: str, field_key: str) -> bool:
        item = self.node_items.get(node_uuid)
        if not item:
            return False
        if item._uses_compact_card():
            self.expanded_node_uuids.add(node_uuid)
            self._update_node_item(node_uuid)
            item = self.node_items.get(node_uuid)
            if not item:
                return False
        item.setSelected(True)
        self.controller.set_selected_node(node_uuid)
        return item.form.focus_field(field_key)

    def _refresh_node_z_values(self) -> None:
        for item in self.node_items.values():
            item.refresh_z_value()

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
        target_center = self._focus_target_center(node_uuid)
        if target_center is not None:
            self.centerOn(target_center)
            self._store_canvas_view()

    def flash_node(self, node_uuid: str, *, emphasize: bool = False) -> None:
        item = self.node_items.get(node_uuid)
        if item:
            item.set_search_highlight(True)
            if emphasize:
                item.start_attention_flash(pulses=2)
            QTimer.singleShot(1500, lambda target=item: target.set_search_highlight(False))
            return
        table_item = self.table_row_to_item.get(node_uuid)
        if table_item:
            table_item.select_row(node_uuid)

    def focus_on_node(self, node_uuid: str, *, target_scale: float | None = None, emphasize: bool = False) -> None:
        if self._focus_target_center(node_uuid) is None:
            return
        table_item = self.table_row_to_item.get(node_uuid)
        if table_item:
            table_item.select_row(node_uuid)
        self._focus_animation.stop()
        self._set_interaction_busy("focus", True)
        current_scale = max(0.001, float(self.transform().m11()))
        self._focus_start_scale = current_scale
        self._focus_end_scale = max(current_scale, float(target_scale)) if target_scale is not None else current_scale
        self._focus_start_center = self.mapToScene(self.viewport().rect().center())
        self._focus_end_center = self._focus_target_center(node_uuid) or self.mapToScene(self.viewport().rect().center())
        self._focus_target_uuid = node_uuid
        self._focus_emphasize = emphasize
        self._focus_animation.setDuration(420 if emphasize else 320)
        self._focus_animation.start()

    def focus_on_group(self, group_uuid: str) -> None:
        item = self.group_items.get(group_uuid)
        if not item:
            return
        self.centerOn(item.focus_rect().center())
        item.setSelected(True)
        self._store_canvas_view()

    def focus_on_parameter_table(self, table_id: str) -> None:
        item = self.table_items.get(table_id)
        if not item:
            return
        item.select_row(None)
        self.centerOn(item.focus_rect().center())
        self._store_canvas_view()

    def paste_position(self) -> tuple[float, float]:
        point = self.mapToScene(self.viewport().rect().center())
        return point.x(), point.y()

    def _pan_viewport_by(self, delta) -> None:
        horizontal = self.horizontalScrollBar()
        vertical = self.verticalScrollBar()
        next_x = int(horizontal.value() - delta.x())
        next_y = int(vertical.value() - delta.y())
        if next_x != horizontal.value():
            horizontal.setValue(next_x)
        if next_y != vertical.value():
            vertical.setValue(next_y)

    def reset_view_layout(self) -> None:
        self._focus_animation.stop()
        self._set_interaction_busy("focus", False)
        self.resetTransform()
        target_rect: QRectF | None = None
        for item in self.node_items.values():
            item_rect = item.mapRectToScene(item.boundingRect())
            target_rect = item_rect if target_rect is None else target_rect.united(item_rect)
        for item in self.table_items.values():
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
        mode = "zoom" if self._matches_wheel_modifier(self.zoom_wheel_modifier, event.modifiers()) else "scroll"
        with performance_recorder.measure("canvas.wheel_event", "canvas", {"mode": mode, "delta": delta}):
            self._set_interaction_busy("wheel", True)
            self._wheel_busy_timer.start(180)
            modifiers = event.modifiers()
            if self._matches_wheel_modifier(self.zoom_wheel_modifier, modifiers):
                factor = 1.22 ** (delta / 120.0)
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
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    node_item.setSelected(not node_item.isSelected())
                    self._clear_pending_display_toggle()
                    event.accept()
                    return
                local_pos = node_item.mapFromScene(self.mapToScene(event.position().toPoint()))
                if self._pending_display_toggle_uuid == node_item.node.uuid and not node_item._proxy_contains(local_pos):
                    self._clear_pending_display_toggle()
                    if node_item.has_card_field_editor():
                        node_item._discard_card_field_editor()
                    self.toggle_node_display_mode(node_item.node.uuid)
                    event.accept()
                    return
            elif self._pending_display_toggle_uuid:
                self._clear_pending_display_toggle()
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
                local_pos = node_item.mapFromScene(self.mapToScene(event.position().toPoint()))
                if node_item._proxy_contains(local_pos):
                    super().mouseDoubleClickEvent(event)
                    return
                if node_item._uses_compact_card():
                    field_key = node_item._card_field_key_at(local_pos)
                    if field_key:
                        self._clear_pending_display_toggle()
                        node_item._begin_card_field_edit(field_key)
                        event.accept()
                        return
                if node_item.has_card_field_editor():
                    node_item._discard_card_field_editor()
                if node_item.node.type in function_node_types(self.schema):
                    self._queue_display_toggle(node_item.node.uuid)
                    event.accept()
                    return
                self._clear_pending_display_toggle()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._panning:
            delta = event.position() - self._pan_start
            self._pan_start = event.position()
            self._pan_viewport_by(delta)
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
            self._store_canvas_view()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._connecting_from:
            release_scene = self.mapToScene(event.position().toPoint())
            target_uuid = self._input_target_uuid_at_scene(release_scene, exclude_uuid=self._connecting_from)
            if target_uuid and target_uuid != self._connecting_from:
                self.controller.add_connection(self._connecting_from, target_uuid)
                self.cancel_connection_preview()
                event.accept()
                return
            if self._hover_item_at_point(event.position()):
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
        node_item = self._node_item_at_view_point(event.pos())
        if node_item:
            self._clear_pending_display_toggle()
            if node_item.has_card_field_editor():
                node_item._discard_card_field_editor()
            if node_item.isSelected() and len(self.selected_node_uuids()) > 1:
                self._show_selection_menu(event.globalPos())
                event.accept()
                return
            node_item.open_appearance_dialog(event.globalPos())
            event.accept()
            return
        table_item = self._table_item_at_view_point(event.pos())
        if table_item:
            self._show_parameter_table_menu(table_item, event.globalPos())
            event.accept()
            return
        group_item = self._group_item_at_view_point(event.pos())
        if group_item:
            self._show_group_menu(group_item, event.globalPos())
            event.accept()
            return
        if len(self.selected_node_uuids()) > 1:
            self._show_selection_menu(event.globalPos())
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
            if type_name in {"Initial", "DrawFrame"} or not definition.quick_create:
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
        if node.type in {"ParameterTrigger", "DrawFrame"}:
            return
        item = NodeItem(self.schema, self.controller, node)
        item.refresh_z_value()
        item.geometryChanged.connect(self._on_node_geometry_changed)
        self.scene_ref.addItem(item)
        self.node_items[node.uuid] = item
        item.set_warnings(self._warnings_by_node.get(node.uuid, []))

    def _add_or_update_node_item(self, node_uuid: str) -> None:
        node = self.controller.get_node(node_uuid)
        if not node:
            return
        if node.type in {"ParameterTrigger", "DrawFrame"}:
            self._rebuild_parameter_tables()
            self._rebuild_groups()
            self._update_connections_for_node(node_uuid)
            return
        if node_uuid not in self.node_items:
            self._create_item(node)
            self._rebuild_groups()
            self._update_connections_for_node(node_uuid)
            return
        self._update_node_item(node_uuid)

    def _update_node_item(self, node_uuid: str) -> None:
        node = self.controller.get_node(node_uuid)
        item = self.node_items.get(node_uuid)
        if node and node.type in {"ParameterTrigger", "DrawFrame"}:
            self._rebuild_parameter_tables()
            self._rebuild_groups()
            self._update_connections_for_node(node_uuid)
            return
        if not node or not item:
            return
        with performance_recorder.measure("canvas.update_node_item", "canvas", {"node_type": node.type}):
            item.update_node(node)
            item.set_warnings(self._warnings_by_node.get(node_uuid, []))
            target_pos = QPointF(float(node.ui_position["x"]), float(node.ui_position["y"]))
            if item.pos() != target_pos:
                item.setPos(target_pos)
        self._rebuild_groups()

    def _move_node_item(self, node_uuid: str) -> None:
        node = self.controller.get_node(node_uuid)
        item = self.node_items.get(node_uuid)
        if node and node.type in {"ParameterTrigger", "DrawFrame"}:
            self._rebuild_parameter_tables()
            self._rebuild_groups()
            self._update_connections_for_node(node_uuid)
            return
        if not node or not item:
            return
        target_pos = QPointF(float(node.ui_position["x"]), float(node.ui_position["y"]))
        if item.pos() != target_pos:
            item.setPos(target_pos)
        self._update_connections_for_node(node_uuid)
        self._rebuild_groups()

    def _remove_node_item(self, node_uuid: str) -> None:
        self.expanded_node_uuids.discard(node_uuid)
        item = self.node_items.pop(node_uuid, None)
        if item:
            self.scene_ref.removeItem(item)
        for pair in list(self.connection_items.keys()):
            if node_uuid in pair:
                connection = self.connection_items.pop(pair)
                self.scene_ref.removeItem(connection)
        self._rebuild_parameter_tables()
        self._rebuild_groups()

    def _rebuild_connections(self) -> None:
        with performance_recorder.measure(
            "canvas.rebuild_connections",
            "canvas",
            {"connection_count": len(self.controller.document.connections)},
        ):
            for item in list(self.connection_items.values()):
                self.scene_ref.removeItem(item)
            self.connection_items.clear()
            for connection in self.controller.document.connections:
                if not self.connection_anchor_scene_pos(connection.from_uuid, "output"):
                    continue
                if not self.connection_anchor_scene_pos(connection.to_uuid, "input"):
                    continue
                item = ConnectionItem(self, connection.from_uuid, connection.to_uuid)
                self.connection_items[(connection.from_uuid, connection.to_uuid)] = item
                self.scene_ref.addItem(item)

    def _rebuild_parameter_tables(self) -> None:
        existing_ids = {table["table_id"] for table in self.controller.parameter_tables()}
        for table_id in list(self.table_items):
            if table_id in existing_ids:
                continue
            item = self.table_items.pop(table_id)
            self.scene_ref.removeItem(item)
        self.table_row_to_item.clear()
        for table in self.controller.parameter_tables():
            table_id = table["table_id"]
            rows = self.controller.parameter_table_rows(table_id)
            if not rows:
                continue
            item = self.table_items.get(table_id)
            if item is None:
                item = ParameterTableItem(self.schema, self.controller, self, table_id)
                self.table_items[table_id] = item
                self.scene_ref.addItem(item)
            item.update_rows(rows)
            for row in rows:
                self.table_row_to_item[row.uuid] = item

    def _rebuild_groups(self) -> None:
        groups = self.controller.group_records()
        existing_ids = {group.uuid for group in groups}
        for group_uuid in list(self.group_items):
            if group_uuid in existing_ids:
                continue
            item = self.group_items.pop(group_uuid)
            self.scene_ref.removeItem(item)
        for group in groups:
            member_rects: list[QRectF] = []
            processed_tables: set[str] = set()
            for node_uuid in group.node_uuids:
                table_item = self.table_row_to_item.get(node_uuid)
                if table_item is not None:
                    if table_item.table_id in processed_tables:
                        continue
                    processed_tables.add(table_item.table_id)
                    member_rects.append(table_item.mapRectToScene(table_item.boundingRect()))
                    continue
                rect = self.node_visual_rect(node_uuid)
                if rect is not None:
                    member_rects.append(rect)
            if not member_rects:
                continue
            item = self.group_items.get(group.uuid)
            if item is None:
                item = GroupItem(self.controller, self, group)
                self.group_items[group.uuid] = item
                self.scene_ref.addItem(item)
            item.update_group(group, member_rects)

    def connection_anchor_scene_pos(self, node_uuid: str, side: str) -> QPointF | None:
        table_item = self.table_row_to_item.get(node_uuid)
        if table_item is not None:
            return table_item.pin_scene_pos(node_uuid, side)
        item = self.node_items.get(node_uuid)
        if item is None:
            return None
        return item.input_pin_scene_pos() if side == "input" else item.output_pin_scene_pos()

    def node_visual_rect(self, node_uuid: str) -> QRectF | None:
        table_item = self.table_row_to_item.get(node_uuid)
        if table_item is not None:
            return table_item.row_scene_rect(node_uuid)
        item = self.node_items.get(node_uuid)
        if item is None:
            return None
        return item.mapRectToScene(item.boundingRect())

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
        self._refresh_scale_sensitive_nodes(force=True)

    def _on_node_geometry_changed(self, node_uuid: str) -> None:
        if self._interaction_flags & {"drag", "resize"} and self.should_use_motion_preview():
            self._deferred_connection_update_uuids.add(node_uuid)
            return
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
            target_center = self._focus_target_center(self._focus_target_uuid)
            if target_center is not None:
                self.centerOn(target_center)
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

    def _table_item_at_view_point(self, point) -> ParameterTableItem | None:
        for item in self.items(point.toPoint() if hasattr(point, "toPoint") else point):
            current = item
            while current:
                if isinstance(current, ParameterTableItem):
                    return current
                current = current.parentItem()
        return None

    def _group_item_at_view_point(self, point) -> GroupItem | None:
        for item in self.items(point.toPoint() if hasattr(point, "toPoint") else point):
            current = item
            while current:
                if isinstance(current, GroupItem):
                    return current
                current = current.parentItem()
        return None

    def _hover_item_at_point(self, point) -> QGraphicsItem | None:
        items = self.items(point.toPoint() if hasattr(point, "toPoint") else point)
        for item in items:
            if item is self._temp_path:
                continue
            return item
        return None

    def _input_target_uuid_at_scene(self, scene_pos: QPointF, *, exclude_uuid: str | None = None) -> str | None:
        best_match: tuple[float, str] | None = None
        for node_uuid, node_item in self.node_items.items():
            if node_uuid == exclude_uuid or not node_item._supports_connections():
                continue
            pin = node_item.pin_hit(scene_pos)
            if pin != "input":
                continue
            center = node_item.input_pin_scene_pos()
            distance = math.hypot(center.x() - scene_pos.x(), center.y() - scene_pos.y())
            if best_match is None or distance < best_match[0]:
                best_match = (distance, node_uuid)
        for node_uuid, table_item in self.table_row_to_item.items():
            if node_uuid == exclude_uuid:
                continue
            pin = table_item.pin_hit(scene_pos)
            if not pin or pin[0] != node_uuid or pin[1] != "input":
                continue
            center = table_item.pin_scene_pos(node_uuid, "input")
            if center is None:
                continue
            distance = math.hypot(center.x() - scene_pos.x(), center.y() - scene_pos.y())
            if best_match is None or distance < best_match[0]:
                best_match = (distance, node_uuid)
        return best_match[1] if best_match else None

    def _start_connection(self, node_item: NodeItem) -> None:
        self._start_connection_from_uuid(node_item.node.uuid)

    def _start_connection_from_uuid(self, node_uuid: str) -> None:
        allowed, reason = self.controller.can_create_graph_content()
        if not allowed:
            self.controller._emit_meta_blocked(reason)
            return
        self._focus_animation.stop()
        self._set_interaction_busy("focus", False)
        self._connecting_from = node_uuid
        self._connecting_moved = False
        start_point = self.connection_anchor_scene_pos(node_uuid, "output")
        if start_point is None:
            return
        self._connecting_start_scene = start_point
        self._set_interaction_busy("connect", True)
        self._update_temp_connection(self._connecting_start_scene)
        self._temp_path.show()

    def _update_temp_connection(self, end_scene_pos: QPointF) -> None:
        if not self._connecting_from:
            return
        start = self.connection_anchor_scene_pos(self._connecting_from, "output")
        if start is None:
            return
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
            if type_name in {"Initial", "DrawFrame"} or not definition.quick_create:
                continue
            actions[type_name] = menu.addAction(f"创建并连接 {definition.title}")
        selected = menu.exec(global_pos)
        if not selected:
            return
        for node_type, action in actions.items():
            if selected == action:
                self.controller.create_node_with_connection(self._connecting_from, node_type, (scene_pos.x(), scene_pos.y()))
                break

    def _show_group_menu(self, group_item: GroupItem, global_pos) -> None:
        menu = QMenu(self)
        rename_action = menu.addAction("重命名分组")
        remove_action = menu.addAction("取消分组")
        selected = menu.exec(global_pos)
        if selected == rename_action:
            text, accepted = QInputDialog.getText(self, "重命名分组", "组名：", text=group_item.group_title())
            if accepted:
                self.controller.rename_group(group_item.group.uuid, text)
        elif selected == remove_action:
            self.controller.remove_group(group_item.group.uuid)

    def _show_selection_menu(self, global_pos) -> None:
        menu = QMenu(self)
        color_action = menu.addAction("设置所选节点颜色")
        selected = menu.exec(global_pos)
        if selected == color_action:
            self._edit_selected_node_appearance(global_pos)

    def _edit_selected_node_appearance(self, global_pos=None) -> bool:
        node_uuids = self.selected_node_uuids()
        nodes = [self.controller.get_node(node_uuid) for node_uuid in node_uuids]
        editable_nodes = [node for node in nodes if node is not None and not node.locked]
        if not editable_nodes:
            return False
        dialog = NodeAppearanceDialog(editable_nodes[0].fields, self)
        if global_pos is not None:
            dialog.move(global_pos)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        self.controller.update_fields_for_nodes(
            [node.uuid for node in editable_nodes],
            dialog.values(),
            self.controller.preferences.global_mode,
            label="批量设置节点颜色",
        )
        return True

    def _show_parameter_table_menu(self, table_item: ParameterTableItem, global_pos) -> None:
        menu = QMenu(self)
        add_row_action = menu.addAction("新增一行")
        remove_row_action = menu.addAction("删除当前行")
        color_action = menu.addAction("设置参数表颜色")
        if not table_item.selected_row_uuids() or len(table_item.row_node_uuids()) <= 1:
            remove_row_action.setEnabled(False)
        selected = menu.exec(global_pos)
        if selected == add_row_action:
            self.controller.add_parameter_table_row(table_item.table_id, table_item.selected_row_uuid())
        elif selected == remove_row_action:
            for row_uuid in table_item.selected_row_uuids():
                self.controller.remove_parameter_table_row(row_uuid)
        elif selected == color_action:
            self._edit_parameter_table_appearance(table_item, global_pos)

    def _edit_parameter_table_appearance(self, table_item: ParameterTableItem, global_pos=None) -> bool:
        rows = [self.controller.get_node(node_uuid) for node_uuid in table_item.row_node_uuids()]
        rows = [row for row in rows if row is not None and not row.locked]
        if not rows:
            return False
        anchor = rows[0]
        values = {
            "theme_body_color": anchor.fields.get(TABLE_BODY_COLOR_FIELD, "#071b2d"),
            "theme_border_color": anchor.fields.get(TABLE_BORDER_COLOR_FIELD, "#25b7ff"),
            "theme_text_color": anchor.fields.get(TABLE_TEXT_COLOR_FIELD, "#e8f8ff"),
        }
        dialog = NodeAppearanceDialog(values, self)
        if global_pos is not None:
            dialog.move(global_pos)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        colors = dialog.values()
        self.controller.update_fields_for_nodes(
            [row.uuid for row in rows],
            {
                TABLE_BODY_COLOR_FIELD: colors["theme_body_color"],
                TABLE_BORDER_COLOR_FIELD: colors["theme_border_color"],
                TABLE_TEXT_COLOR_FIELD: colors["theme_text_color"],
            },
            self.controller.preferences.global_mode,
            label="设置参数表颜色",
        )
        return True

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

        self._normalize_parameter_table_positions(final_positions)

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

    def _normalize_parameter_table_positions(self, positions: dict[str, tuple[float, float]]) -> None:
        row_stride = ParameterTableItem.ROW_HEIGHT + ParameterTableItem.ROW_GAP
        for table in self.controller.parameter_tables():
            row_ids = [node_uuid for node_uuid in table["node_uuids"] if node_uuid in positions]
            if len(row_ids) < 2:
                continue
            ordered_rows = self.controller.parameter_table_rows(table["table_id"])
            ordered_ids = [row.uuid for row in ordered_rows if row.uuid in positions]
            if len(ordered_ids) < 2:
                continue
            anchor_x = min(positions[row_uuid][0] for row_uuid in ordered_ids)
            anchor_y = min(positions[row_uuid][1] for row_uuid in ordered_ids)
            for index, row_uuid in enumerate(ordered_ids):
                positions[row_uuid] = (anchor_x, anchor_y + index * row_stride)

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

        layout_roots, layout_children = self._build_layout_tree(component, incoming, levels, original_x, original_y)
        row_spans: dict[str, int] = {}
        for root_uuid in layout_roots:
            self._subtree_row_span(root_uuid, layout_children, row_spans)
        row_indices: dict[str, int] = {}
        next_row = 0
        for root_uuid in layout_roots:
            self._assign_subtree_rows(root_uuid, next_row, layout_children, row_spans, row_indices)
            next_row += row_spans[root_uuid]
        for node_uuid in component:
            if node_uuid not in row_indices:
                row_indices[node_uuid] = next_row
                next_row += 1

        node_sizes = [self._layout_node_size(node_uuid) for node_uuid in component]
        max_width = max(width for width, _height in node_sizes)
        max_height = max(height for _width, height in node_sizes)
        column_gap = max(90.0, max_width * 0.275)
        row_stride = max(132.0, max_height + 56.0)
        anchor_x = min(original_x.values())
        anchor_y = min(original_y.values())
        positions: dict[str, tuple[float, float]] = {}
        row_y_positions: dict[int, float] = {}
        for node_uuid in sorted(
            component,
            key=lambda value: (
                row_indices.get(value, 0),
                levels.get(value, 0),
                original_y[value],
                original_x[value],
            ),
        ):
            row_index = row_indices.get(node_uuid, 0)
            desired_y = row_y_positions.get(row_index)
            if desired_y is None:
                previous_rows = [row for row in row_y_positions if row < row_index]
                previous_y = max((row_y_positions[row] for row in previous_rows), default=anchor_y - row_stride)
                desired_y = max(anchor_y + row_index * row_stride, previous_y + row_stride)
            x = anchor_x + levels.get(node_uuid, 0) * (max_width + column_gap)
            y = self._resolve_non_overlapping_y(node_uuid, x, desired_y, occupied_rects)
            row_y_positions.setdefault(row_index, y)
            positions[node_uuid] = (x, row_y_positions[row_index])
            occupied_rects.append(self._expanded_rect_for(node_uuid, positions[node_uuid]))
        return positions

    def _build_layout_tree(
        self,
        component: list[str],
        incoming: dict[str, list[str]],
        levels: dict[str, int],
        original_x: dict[str, float],
        original_y: dict[str, float],
    ) -> tuple[list[str], dict[str, list[str]]]:
        component_set = set(component)
        primary_parent: dict[str, str] = {}
        for node_uuid in component:
            parent_candidates = [source for source in incoming[node_uuid] if source in component_set]
            if not parent_candidates:
                continue
            primary_parent[node_uuid] = min(
                parent_candidates,
                key=lambda source_uuid: (
                    -levels.get(source_uuid, 0),
                    original_y[source_uuid],
                    original_x[source_uuid],
                    source_uuid,
                ),
            )
        roots = [node_uuid for node_uuid in component if node_uuid not in primary_parent]
        if not roots:
            root_uuid = min(component, key=lambda node_uuid: (original_x[node_uuid], original_y[node_uuid], node_uuid))
            primary_parent.pop(root_uuid, None)
            roots = [root_uuid]
        roots.sort(key=lambda node_uuid: (original_x[node_uuid], original_y[node_uuid], node_uuid))
        layout_children: dict[str, list[str]] = defaultdict(list)
        for node_uuid, parent_uuid in primary_parent.items():
            layout_children[parent_uuid].append(node_uuid)
        for parent_uuid in layout_children:
            layout_children[parent_uuid].sort(key=lambda node_uuid: (original_y[node_uuid], original_x[node_uuid], node_uuid))
        return roots, layout_children

    def _subtree_row_span(
        self,
        node_uuid: str,
        layout_children: dict[str, list[str]],
        row_spans: dict[str, int],
    ) -> int:
        if node_uuid in row_spans:
            return row_spans[node_uuid]
        child_ids = layout_children.get(node_uuid, [])
        if not child_ids:
            row_spans[node_uuid] = 1
            return 1
        if len(child_ids) == 1:
            span = self._subtree_row_span(child_ids[0], layout_children, row_spans)
            row_spans[node_uuid] = span
            return span
        span = sum(self._subtree_row_span(child_uuid, layout_children, row_spans) for child_uuid in child_ids)
        row_spans[node_uuid] = max(1, span)
        return row_spans[node_uuid]

    def _assign_subtree_rows(
        self,
        node_uuid: str,
        start_row: int,
        layout_children: dict[str, list[str]],
        row_spans: dict[str, int],
        row_indices: dict[str, int],
    ) -> None:
        row_indices[node_uuid] = start_row
        child_ids = layout_children.get(node_uuid, [])
        if not child_ids:
            return
        if len(child_ids) == 1:
            self._assign_subtree_rows(child_ids[0], start_row, layout_children, row_spans, row_indices)
            return
        current_row = start_row
        for child_uuid in child_ids:
            self._assign_subtree_rows(child_uuid, current_row, layout_children, row_spans, row_indices)
            current_row += row_spans[child_uuid]

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

    def _static_obstacle_rects(self, excluded_node_ids: set[str]) -> list[QRectF]:
        rects: list[QRectF] = []
        for node in self.controller.document.nodes:
            if node.uuid in excluded_node_ids:
                continue
            rects.append(self._expanded_rect_for(node.uuid, (node.ui_position["x"], node.ui_position["y"])))
        return rects

    def _layout_node_size(self, node_uuid: str) -> tuple[float, float]:
        item = self.node_items.get(node_uuid)
        if item is not None:
            rect = item.boundingRect()
            return rect.width(), rect.height()
        table_item = self.table_row_to_item.get(node_uuid)
        if table_item is not None and node_uuid in table_item._row_rects:
            row_rect = table_item._row_rects[node_uuid]
            return row_rect.width(), row_rect.height()
        return 380.0, ParameterTableItem.ROW_HEIGHT

    def _component_bounds(self, node_ids: list[str]) -> QRectF | None:
        bounds: QRectF | None = None
        for node_uuid in node_ids:
            node = self.controller.get_node(node_uuid)
            if not node:
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
        margin = 26.0
        item = self.node_items.get(node_uuid)
        if item is not None:
            width = item.boundingRect().width()
            height = item.boundingRect().height()
        else:
            table_item = self.table_row_to_item.get(node_uuid)
            if table_item is not None and node_uuid in table_item._row_rects:
                row_rect = table_item._row_rects[node_uuid]
                width = row_rect.width()
                height = row_rect.height()
            else:
                width = 760.0
                height = ParameterTableItem.ROW_HEIGHT
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
        if item:
            return item.mapRectToScene(item.boundingRect()).center()
        table_item = self.table_row_to_item.get(node_uuid)
        if table_item:
            rect = table_item.row_scene_rect(node_uuid)
            if rect is not None:
                return rect.center()
        return None

    def _refresh_scale_sensitive_nodes(self, force: bool = False) -> None:
        with performance_recorder.measure(
            "canvas.refresh_scale_sensitive_nodes",
            "canvas",
            {"node_count": len(self.node_items), "scale": round(float(self.transform().m11()), 3)},
        ):
            current_scale = round(float(self.transform().m11()), 6)
            if not force and self._last_scale_sensitive_scale == current_scale:
                return
            for item in self.node_items.values():
                item.refresh_view_scale()
            self._last_scale_sensitive_scale = current_scale
