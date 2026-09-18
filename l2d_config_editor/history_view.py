"""Read-only, navigable graph comparisons for embedded and SVN history."""
from __future__ import annotations

import base64
import copy
import html
import json
from collections import defaultdict
from datetime import datetime

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDialog, QGraphicsItem, QGraphicsRectItem, QGraphicsScene,
    QGraphicsTextItem, QGraphicsView, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .document_history import history_is_anchored, history_snapshot, revision_snapshot, snapshot_payload
from .graph_diff import diff_documents
from .logic import export_document_dict, load_document_payload, node_title
from .plan import plan_formal_positions

CATEGORY_LABELS = {"nodes": "节点", "connections": "连线", "groups": "分组",
                   "plan_topics": "计划主题", "formal_strokes": "正式画笔",
                   "plan_strokes": "计划画笔", "images": "参考图片",
                   "metadata": "基础信息", "settings": "编辑设置"}
CHANGE_LABELS = {"added": "新增", "deleted": "删除", "modified": "修改"}
COLORS = {"added": "#36BE81", "deleted": "#F07178", "modified": "#F5C45E", "": "#627186"}


def display_value(value) -> str:
    if value is None:
        return "∅"
    return json.dumps(value, ensure_ascii=False, indent=2) if isinstance(value, (dict, list)) else str(value)


class HistoryCanvas(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#151B24"))

    def wheelEvent(self, event):
        factor = 1.18 if event.angleDelta().y() > 0 else 1 / 1.18
        if .04 <= self.transform().m11() * factor <= 4:
            self.scale(factor, factor)
        event.accept()


class GraphComparisonWidget(QWidget):
    """Detached scene: no editable proxies, controllers, save or undo callbacks."""
    def __init__(self, schema, parent=None):
        super().__init__(parent)
        self.schema = schema
        self.before = self.after = None
        self.items_by_key = {}
        self.entries_by_key = {}
        self._selecting = False
        self._decorations = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.summary = QLabel("选择版本以查看变化")
        row.addWidget(self.summary, 1)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["正式图", "计划图"])
        self.mode_combo.currentIndexChanged.connect(self._render_graph)
        row.addWidget(self.mode_combo)
        fit = QPushButton("显示全图")
        fit.clicked.connect(self.fit_graph)
        row.addWidget(fit)
        layout.addLayout(row)
        layout.addWidget(QLabel("绿色 新增  ·  红色虚线 删除  ·  金色 修改  |  滚轮缩放，拖动平移，点击修改项定位。只读预览。"))
        splitter = QSplitter()
        self.canvas = HistoryCanvas()
        self.canvas.scene().selectionChanged.connect(self._scene_selected)
        splitter.addWidget(self.canvas)
        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        self.changes = QTreeWidget()
        self.changes.setHeaderLabels(["变化 / 对象", "字段"])
        self.changes.setUniformRowHeights(True)
        self.changes.currentItemChanged.connect(self._change_selected)
        side_layout.addWidget(self.changes, 3)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setPlaceholderText("选择修改项，查看完整的修改前 / 修改后值")
        side_layout.addWidget(self.detail, 2)
        splitter.addWidget(side)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([920, 360])
        layout.addWidget(splitter, 1)

    def set_documents(self, before, after):
        self.before, self.after = before, after
        self.diff = diff_documents(before, after)
        counts = self.diff.counts()
        self.summary.setText(f"新增 {counts['added']} · 删除 {counts['deleted']} · 修改 {counts['modified']} 项")
        self.entries_by_key = defaultdict(list)
        self.changes.clear()
        self.detail.clear()
        nodes = {node.uuid: node for node in [*before.nodes, *after.nodes]}
        self._nodes = nodes
        for entry in self.diff.entries:
            key = (entry.category, entry.identity)
            self.entries_by_key[key].append(entry)
            node = nodes.get(entry.identity) if entry.category in {"nodes", "plan_topics"} else None
            label = node_title(self.schema, node) if node else self._object_label(entry)
            row = QTreeWidgetItem([f"{CHANGE_LABELS[entry.change]} · {CATEGORY_LABELS.get(entry.category, entry.category)} · {label}", self._field_label(entry)])
            row.setData(0, Qt.ItemDataRole.UserRole, entry)
            row.setForeground(0, QColor(COLORS[entry.change]))
            self.changes.addTopLevelItem(row)
        self.changes.setColumnWidth(0, 250)
        self._render_graph()

    def _object_label(self, entry):
        if entry.category == "connections":
            names = [node_title(self.schema, self._nodes[uuid]) if uuid in self._nodes else uuid
                     for uuid in entry.identity.split("->")]
            return " → ".join(names)
        if entry.category == "metadata":
            return "配置底座"
        if entry.category == "settings":
            return "图表设置"
        return entry.identity

    def _field_label(self, entry):
        node = self._nodes.get(entry.identity)
        if node and entry.field_path.startswith("fields."):
            key = entry.field_path.removeprefix("fields.")
            schema = self.schema.nodes.get(node.type)
            if schema:
                field = next((field for field in schema.fields if field.key == key), None)
                if field:
                    return field.label
        labels = {"position": "位置", "size": "尺寸", "title": "标题", "parent_uuid": "父主题",
                  "order": "同级顺序", "branch_color": "主题颜色", "formalization_state": "转换状态",
                  "content_sha256": "图片内容", "points_sha256": "笔迹点集", "memo": "备注",
                  "CharName": "角色名称", "author": "作者", "locked": "锁定", "sequence_locked": "固定序号"}
        parts = entry.field_path.split(".")
        return ".".join([labels.get(parts[0], parts[0]), *parts[1:]])

    def _status(self, key):
        entries = self.entries_by_key.get(key, [])
        if any(entry.change == "deleted" for entry in entries):
            return "deleted"
        if any(entry.change == "added" for entry in entries):
            return "added"
        return "modified" if entries else ""

    def _pen(self, status, width=2):
        pen = QPen(QColor(COLORS[status]), width)
        pen.setCosmetic(True)
        if status == "deleted":
            pen.setStyle(Qt.PenStyle.DashLine)
        return pen

    def _register(self, item, key):
        self.items_by_key[key] = item
        item.setData(0, key)
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)

    def _render_graph(self, *_args):
        if self.after is None:
            return
        self._selecting = True
        scene = self.canvas.scene()
        scene.clear()
        self._decorations.clear()
        self.items_by_key = {}
        plan_mode = self.mode_combo.currentIndex() == 1
        old_nodes = {node.uuid: node for node in self.before.nodes}
        new_nodes = {node.uuid: node for node in self.after.nodes}
        nodes = {**old_nodes, **new_nodes}
        topics = {topic.node_uuid: topic for topic in [*self.before.plan_layout.topics, *self.after.plan_layout.topics]}
        positions = {}
        if plan_mode:
            positions = {**plan_formal_positions(copy.deepcopy(self.before)),
                         **plan_formal_positions(copy.deepcopy(self.after))}
        for uuid, node in nodes.items():
            if node.type == "DrawFrame":
                continue
            key = ("nodes", uuid)
            status = self._status(key) or self._status(("plan_topics", uuid))
            entries = [*self.entries_by_key.get(key, []), *self.entries_by_key.get(("plan_topics", uuid), [])]
            title = topics[uuid].plan_title if plan_mode and uuid in topics else node_title(self.schema, node)
            lines = [f"<b>{html.escape(title or node.type)}</b>", f"<span style='color:{COLORS[status]}'>{CHANGE_LABELS.get(status, '未修改')} · {html.escape(node.type)}</span>"]
            for entry in entries[:4]:
                if entry.field_path:
                    before = display_value(entry.before).replace("\n", " ")[:75]
                    after = display_value(entry.after).replace("\n", " ")[:75]
                    lines.append(html.escape(f"{self._field_label(entry)}: {before} → {after}"))
            if len(entries) > 4:
                lines.append(f"另有 {len(entries) - 4} 项变化，点击查看")
            card = QGraphicsRectItem()
            text = QGraphicsTextItem(card)
            text.setTextWidth(270)
            text.setDefaultTextColor(QColor("#E5ECF5"))
            text.setHtml("<div style='font-size:12px;'>" + "<br>".join(lines) + "</div>")
            text.setPos(8, 6)
            text.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self._decorations.append(text)
            card.setRect(0, 0, 286, max(86, text.boundingRect().height() + 12))
            card.setBrush(QColor("#222D3D"))
            card.setPen(self._pen(status, 3 if status else 1))
            x, y = positions.get(uuid, (node.ui_position.get("x", 0), node.ui_position.get("y", 0)))
            card.setPos(x, y)
            card.setZValue(5)
            scene.addItem(card)
            self._register(card, key)
            self.items_by_key[("plan_topics", uuid)] = card
        pairs = {(edge.from_uuid, edge.to_uuid) for doc in (self.before, self.after) for edge in doc.connections}
        old_plan, new_plan = set(), set()
        if plan_mode:
            old_plan, new_plan = ({(topic.parent_uuid, topic.node_uuid) for topic in doc.plan_layout.topics if topic.parent_uuid}
                                  for doc in (self.before, self.after))
            pairs |= old_plan | new_plan
        for source, target in pairs:
            first = self.items_by_key.get(("nodes", source))
            second = self.items_by_key.get(("nodes", target))
            if first is None or second is None:
                continue
            key = ("connections", f"{source}->{target}")
            status = self._status(key)
            if plan_mode and (source, target) in old_plan | new_plan:
                status = "added" if (source, target) not in old_plan else "deleted" if (source, target) not in new_plan else status
                key = ("plan_edges", f"{source}->{target}")
            first_rect, second_rect = first.sceneBoundingRect(), second.sceneBoundingRect()
            start = QPointF(first_rect.right(), first_rect.center().y())
            end = QPointF(second_rect.left(), second_rect.center().y())
            curve = QPainterPath(start)
            middle = (start.x() + end.x()) / 2
            curve.cubicTo(QPointF(middle, start.y()), QPointF(middle, end.y()), end)
            curve.moveTo(end + QPointF(-10, -5))
            curve.lineTo(end)
            curve.lineTo(end + QPointF(-10, 5))
            item = scene.addPath(curve, self._pen(status, 2))
            self._register(item, key)
        self._render_assets(scene, plan_mode)
        scene.setSceneRect(scene.itemsBoundingRect().adjusted(-80, -80, 80, 80))
        self._selecting = False
        self.fit_graph()

    def _render_assets(self, scene, plan_mode):
        category = "plan_strokes" if plan_mode else "formal_strokes"
        strokes = {stroke.uuid: stroke for doc in (self.before, self.after)
                   for stroke in (doc.plan_canvas_strokes if plan_mode else doc.canvas_strokes)}
        for uuid, stroke in strokes.items():
            if not stroke.points:
                continue
            path = QPainterPath(QPointF(*stroke.points[0]))
            for point in stroke.points[1:]:
                path.lineTo(*point)
            key = (category, uuid)
            pen = self._pen(self._status(key), max(2, stroke.width))
            if not self._status(key):
                pen.setColor(QColor(stroke.color))
            item = scene.addPath(path, pen)
            item.setZValue(8)
            self._register(item, key)
        if plan_mode:
            return
        for category, attr in (("groups", "groups"), ("images", "canvas_images")):
            records = {record.uuid: record for doc in (self.before, self.after) for record in getattr(doc, attr)}
            for uuid, record in records.items():
                position = record.ui_position or {}
                size = record.ui_size or {}
                key = (category, uuid)
                item = scene.addRect(0, 0, size.get("width", 330), size.get("height", 180), self._pen(self._status(key)))
                item.setPos(position.get("x", 0), position.get("y", 0))
                item.setZValue(-3)
                label = QGraphicsTextItem(record.title if category == "groups" else record.name, item)
                label.setDefaultTextColor(QColor("#B3C2D8"))
                label.setPos(4, -28)
                label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                self._decorations.append(label)
                if category == "images":
                    pixmap = QPixmap()
                    pixmap.loadFromData(base64.b64decode(record.data_base64))
                    if not pixmap.isNull():
                        image = scene.addPixmap(pixmap)
                        image.setParentItem(item)
                        image.setScale(min(item.rect().width() / pixmap.width(), item.rect().height() / pixmap.height()))
                        image.setOpacity(min(.7, record.opacity))
                        image.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                        self._decorations.append(image)
                self._register(item, key)

    def fit_graph(self):
        bounds = self.canvas.scene().itemsBoundingRect()
        if not bounds.isEmpty():
            self.canvas.fitInView(bounds.adjusted(-40, -40, 40, 40), Qt.AspectRatioMode.KeepAspectRatio)
            if self.canvas.transform().m11() > 1:
                self.canvas.resetTransform()

    def _change_selected(self, row, _previous):
        if row is None:
            return
        entry = row.data(0, Qt.ItemDataRole.UserRole)
        self.detail.setPlainText(f"{CATEGORY_LABELS.get(entry.category, entry.category)} · {self._object_label(entry)}\n{self._field_label(entry)}\n\n修改前\n{display_value(entry.before)}\n\n修改后\n{display_value(entry.after)}")
        key = (entry.category, entry.identity)
        if entry.category == "plan_strokes" and self.mode_combo.currentIndex() != 1:
            self.mode_combo.setCurrentIndex(1)
        elif entry.category in {"formal_strokes", "groups", "images"} and self.mode_combo.currentIndex() != 0:
            self.mode_combo.setCurrentIndex(0)
        item = self.items_by_key.get(key)
        if item is not None:
            self._selecting = True
            self.canvas.scene().clearSelection()
            item.setSelected(True)
            self.canvas.resetTransform()
            self.canvas.centerOn(item)
            self._selecting = False

    def _scene_selected(self):
        if self._selecting:
            return
        items = self.canvas.scene().selectedItems()
        if not items:
            return
        key = items[0].data(0)
        entries = self.entries_by_key.get(key, [])
        if key and key[0] == "nodes":
            entries = [*entries, *self.entries_by_key.get(("plan_topics", key[1]), [])]
        self.detail.setPlainText("\n\n".join(
            f"{self._field_label(entry) or CHANGE_LABELS[entry.change]}\n修改前：{display_value(entry.before)}\n修改后：{display_value(entry.after)}"
            for entry in entries) or "此对象在两个版本间没有变化。")


class DocumentHistoryDialog(QDialog):
    def __init__(self, schema, document, parent=None):
        super().__init__(parent)
        self.setWindowTitle("版本历史 · 图表对比")
        self.resize(1360, 860)
        self.schema = schema
        self.document = copy.deepcopy(document)
        self.file_path = document.path
        self.current = history_snapshot(export_document_dict(schema, self.document))
        self.saved = document.history_snapshot or self.current
        self.history = copy.deepcopy(document.history)
        layout = QVBoxLayout(self)
        self.notice = QLabel("每次有内容变化的保存形成一个版本；保留最近 50 版，历史区最多 512 KiB。此窗口显示打开时的内容。")
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        selectors = QHBoxLayout()
        self.left_combo, self.right_combo = QComboBox(), QComboBox()
        selectors.addWidget(QLabel("从"))
        selectors.addWidget(self.left_combo, 1)
        selectors.addWidget(QLabel("到"))
        selectors.addWidget(self.right_combo, 1)
        layout.addLayout(selectors)
        self.comparison = GraphComparisonWidget(schema)
        layout.addWidget(self.comparison, 1)
        for combo in (self.left_combo, self.right_combo):
            combo.addItem("当前编辑内容（打开时）", "current")
        if history_is_anchored(self.history, self.saved):
            for index in range(len(self.history["revisions"]) - 1, -1, -1):
                entry = self.history["revisions"][index]
                try:
                    stamp = datetime.fromisoformat(entry["saved_at"]).astimezone().strftime("%Y-%m-%d %H:%M:%S")
                except ValueError:
                    stamp = entry["saved_at"]
                label = f"{stamp} · {entry['author'] or '未知作者'} · {entry.get('label', '保存')} · {entry['id'][:6]}"
                for combo in (self.left_combo, self.right_combo):
                    combo.addItem(label, index)
            if self.current == self.saved and self.left_combo.count() > 2:
                self.left_combo.setCurrentIndex(2)
                self.right_combo.setCurrentIndex(1)
            else:
                self.left_combo.setCurrentIndex(1)
        else:
            self.notice.setText("此文件尚无可还原的内嵌历史，或历史与文件内容不匹配。后续保存会建立历史；较早的提交可从 SVN 历史查看。")
            if self.history and self.history.get("version") != 1:
                self.notice.setText("此文件的历史由更新版本的编辑器生成，当前版本会原样保留历史扩展，仅预览当前图表。")
            if document.history_snapshot is not None:
                self.left_combo.addItem("打开文件时的基线", "baseline")
                self.left_combo.setCurrentIndex(1)
        self.left_combo.currentIndexChanged.connect(self._compare)
        self.right_combo.currentIndexChanged.connect(self._compare)
        self._compare()

    def _document_for(self, value):
        if value == "current":
            return self.document
        snapshot = self.saved if value == "baseline" else revision_snapshot(self.history, self.saved, int(value))
        return load_document_payload(self.schema, snapshot_payload(snapshot))

    def _compare(self, *_args):
        try:
            self.comparison.set_documents(self._document_for(self.left_combo.currentData()),
                                          self._document_for(self.right_combo.currentData()))
        except (ValueError, TypeError, KeyError) as exc:
            self.notice.setText(f"此历史无法还原：{exc}。当前图表未被修改。")
