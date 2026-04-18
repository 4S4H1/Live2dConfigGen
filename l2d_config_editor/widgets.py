"""Reusable PyQt widgets for node editing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from PyQt6.QtCore import QDate, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFocusEvent, QFont, QKeyEvent, QKeySequence, QRegularExpressionValidator
from PyQt6.QtCore import QRegularExpression
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QColorDialog,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .logic import display_value_for_field
from .schema import EditorSchema, FieldSchema, field_visible


class CommitLineEdit(QLineEdit):
    committed = pyqtSignal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.editingFinished.connect(self._emit_commit)

    def _emit_commit(self) -> None:
        self.committed.emit(self.text())


class NumericLineEdit(CommitLineEdit):
    def __init__(self, mode: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.mode = mode
        pattern = r"-?\d*" if mode == "int" else r"-?\d*(?:\.\d{0,3})?"
        self.setValidator(QRegularExpressionValidator(QRegularExpression(pattern), self))
        self.setAlignment(Qt.AlignmentFlag.AlignRight)

    def _emit_commit(self) -> None:
        text = self.text().strip()
        if self.mode == "nullable_int":
            if not text:
                self.committed.emit(None)
                return
            try:
                self.committed.emit(int(text))
                return
            except ValueError:
                self.committed.emit(None)
                return
        if self.mode == "int":
            self.committed.emit(int(text or "0"))
            return
        self.committed.emit(float(text or "0"))


class CommitPlainTextEdit(QPlainTextEdit):
    committed = pyqtSignal(object)

    def focusOutEvent(self, event: QFocusEvent) -> None:
        super().focusOutEvent(event)
        self.committed.emit(self.toPlainText())


class CopyFriendlyPlainTextEdit(QPlainTextEdit):
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.matches(QKeySequence.StandardKey.Copy):
            if not self.textCursor().hasSelection():
                self.selectAll()
            self.copy()
            event.accept()
            return
        super().keyPressEvent(event)


class CommitComboBox(QComboBox):
    committed = pyqtSignal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.activated.connect(self._queue_commit)

    def _queue_commit(self, _index: int) -> None:
        QTimer.singleShot(0, self._emit_commit)

    def _emit_commit(self) -> None:
        self.committed.emit(self.currentData())


class ColorFieldWidget(QWidget):
    committed = pyqtSignal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.edit = CommitLineEdit()
        self.edit.committed.connect(self._emit_text)
        self.button = QPushButton("选择")
        self.button.setObjectName("colorPickButton")
        self.button.clicked.connect(self._pick_color)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.button, 0)

    def set_placeholder_text(self, text: str) -> None:
        self.edit.setPlaceholderText(text)

    def set_read_only(self, read_only: bool) -> None:
        self.edit.setReadOnly(read_only)
        self.button.setEnabled(not read_only)

    def _emit_text(self, value: str) -> None:
        self.committed.emit(value.strip())

    def _pick_color(self) -> None:
        current = QColor(self.edit.text().strip() or "#ffffff")
        color = QColorDialog.getColor(current, self, "选择颜色")
        if not color.isValid():
            return
        self.edit.setText(color.name())
        self.committed.emit(color.name())


class ColorChoiceButton(QPushButton):
    colorChanged = pyqtSignal(str)

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(title, parent)
        self._color = QColor("#ffffff")
        self.clicked.connect(self._pick_color)
        self._sync_style()

    def color_name(self) -> str:
        return self._color.name()

    def set_color(self, value: str) -> None:
        color = QColor(str(value or "").strip())
        if not color.isValid():
            return
        self._color = color
        self._sync_style()

    def _pick_color(self) -> None:
        dialog = QColorDialog(self._color, self)
        dialog.setOption(QColorDialog.ColorDialogOption.ShowAlphaChannel, False)
        dialog.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
        dialog.setWindowTitle("选择颜色")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        color = dialog.currentColor()
        if not color.isValid():
            return
        self._color = color
        self._sync_style()
        self.colorChanged.emit(self._color.name())

    def _sync_style(self) -> None:
        fg = "#10151f" if self._color.lightness() > 130 else "#f3f6fb"
        self.setText(self._color.name())
        self.setStyleSheet(
            f"QPushButton {{ background-color: {self._color.name()}; color: {fg}; font-weight: 700; }}"
        )


class CommentAppearanceDialog(QDialog):
    def __init__(self, values: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("备注外观")
        self.resize(420, 250)
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.box_color_button = ColorChoiceButton("选择框体颜色", self)
        self.box_color_button.set_color(str(values.get("note_box_color") or "#76808d"))
        form.addRow("框体颜色", self.box_color_button)

        self.box_alpha_spin = QSpinBox(self)
        self.box_alpha_spin.setRange(0, 100)
        self.box_alpha_spin.setSuffix("%")
        self.box_alpha_spin.setValue(int(values.get("note_box_alpha", 62) or 62))
        form.addRow("框体透明度", self.box_alpha_spin)

        self.text_color_button = ColorChoiceButton("选择字体颜色", self)
        self.text_color_button.set_color(str(values.get("note_text_color") or "#f3f5f8"))
        form.addRow("字体颜色", self.text_color_button)

        self.text_alpha_spin = QSpinBox(self)
        self.text_alpha_spin.setRange(0, 100)
        self.text_alpha_spin.setSuffix("%")
        self.text_alpha_spin.setValue(int(values.get("note_text_alpha", 96) or 96))
        form.addRow("字体透明度", self.text_alpha_spin)

        self.font_size_spin = QSpinBox(self)
        self.font_size_spin.setRange(11, 28)
        self.font_size_spin.setValue(int(values.get("note_font_size", 15) or 15))
        form.addRow("字体大小", self.font_size_spin)

        layout.addLayout(form)
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def values(self) -> dict[str, Any]:
        return {
            "note_box_color": self.box_color_button.color_name(),
            "note_box_alpha": int(self.box_alpha_spin.value()),
            "note_text_color": self.text_color_button.color_name(),
            "note_text_alpha": int(self.text_alpha_spin.value()),
            "note_font_size": int(self.font_size_spin.value()),
        }


class NodeAppearanceDialog(QDialog):
    def __init__(self, values: dict[str, Any], parent: QWidget | None = None, *, include_font_size: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle("节点外观")
        self.resize(420, 220 if not include_font_size else 250)
        self._include_font_size = include_font_size
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.body_color_button = ColorChoiceButton("选择主体颜色", self)
        self.body_color_button.set_color(str(values.get("theme_body_color") or values.get("note_box_color") or "#76808d"))
        form.addRow("主体颜色", self.body_color_button)

        self.border_color_button = ColorChoiceButton("选择边框颜色", self)
        self.border_color_button.set_color(str(values.get("theme_border_color") or values.get("note_box_color") or "#69b070"))
        form.addRow("边框颜色", self.border_color_button)

        self.text_color_button = ColorChoiceButton("选择字体颜色", self)
        self.text_color_button.set_color(str(values.get("theme_text_color") or values.get("note_text_color") or "#f3f5f8"))
        form.addRow("字体颜色", self.text_color_button)

        self.font_size_spin: QSpinBox | None = None
        if self._include_font_size:
            self.font_size_spin = QSpinBox(self)
            self.font_size_spin.setRange(11, 28)
            self.font_size_spin.setValue(int(values.get("note_font_size", 15) or 15))
            form.addRow("字体大小", self.font_size_spin)

        layout.addLayout(form)
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def values(self) -> dict[str, Any]:
        values = {
            "theme_body_color": self.body_color_button.color_name(),
            "theme_border_color": self.border_color_button.color_name(),
            "theme_text_color": self.text_color_button.color_name(),
        }
        if self._include_font_size and self.font_size_spin is not None:
            values["note_font_size"] = int(self.font_size_spin.value())
        return values


@dataclass
class EditorBinding:
    widget: QWidget
    setter: Callable[[Any], None]
    read_only_setter: Callable[[bool], None]


class ValidationIssueItem(QWidget):
    jumpRequested = pyqtSignal(str)

    def __init__(self, issue, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.issue = issue
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)
        self.text_box = CopyFriendlyPlainTextEdit()
        self.text_box.setReadOnly(True)
        self.text_box.setPlainText(text)
        self.text_box.setMaximumBlockCount(0)
        self.text_box.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.text_box.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.text_box.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.text_box.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.text_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.jump_button = QPushButton("<跳转>")
        target_uuid = issue.related_node_uuids[0] if issue.related_node_uuids else issue.node_uuid
        self.jump_button.setEnabled(bool(target_uuid))
        self.jump_button.clicked.connect(lambda: target_uuid and self.jumpRequested.emit(target_uuid))
        layout.addWidget(self.text_box, 1)
        layout.addWidget(self.jump_button, 0, Qt.AlignmentFlag.AlignTop)
        self._refresh_height()

    def _refresh_height(self, available_width: int | None = None) -> None:
        if available_width is not None:
            self.text_box.setFixedWidth(max(180, available_width))
        document_height = self.text_box.document().size().height()
        self.text_box.setFixedHeight(max(52, min(148, int(document_height + 12))))


class ValidationSummaryWidget(QFrame):
    jumpRequested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("validationSummary")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)
        self.title = QLabel("\u51b2\u7a81\u4e0e\u8b66\u544a")
        self.title.setObjectName("validationSummaryTitle")
        self.list_widget = QListWidget()
        self.list_widget.setAlternatingRowColors(True)
        self.list_widget.setSpacing(6)
        self.empty = QLabel("\u5f53\u524d\u8282\u70b9\u6ca1\u6709\u8b66\u544a")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title)
        layout.addWidget(self.empty)
        layout.addWidget(self.list_widget)
        self.list_widget.hide()

    def set_issues(self, issues) -> None:
        self.list_widget.clear()
        if not issues:
            self.empty.show()
            self.list_widget.hide()
            return
        self.empty.hide()
        self.list_widget.show()
        for issue in issues:
            text = issue.message
            if issue.related_titles:
                text = f"{text}\n\u76f8\u5173\u8282\u70b9: {', '.join(issue.related_titles)}"
            item = QListWidgetItem()
            widget = ValidationIssueItem(issue, text)
            widget.jumpRequested.connect(self.jumpRequested.emit)
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, widget)
        self._refresh_item_sizes()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh_item_sizes()

    def _refresh_item_sizes(self) -> None:
        available_width = max(120, self.list_widget.viewport().width() - 18)
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            widget = self.list_widget.itemWidget(item)
            if not isinstance(widget, ValidationIssueItem):
                continue
            widget._refresh_height(available_width - widget.jump_button.sizeHint().width() - 18)
            item.setSizeHint(widget.sizeHint())


class NodeFormWidget(QFrame):
    fieldCommitted = pyqtSignal(str, object)
    APPEARANCE_KEYS = {
        "theme_body_color",
        "theme_border_color",
        "theme_text_color",
    }
    COMMENT_APPEARANCE_KEYS = {
        "note_box_color",
        "note_box_alpha",
        "note_text_color",
        "note_text_alpha",
        "note_font_size",
    }
    SUMMARY_SPECS = (
        ("tips", "✎", "备注", "#f6c85f"),
        ("draw_able_name", "▣", "框", "#63c7d8"),
        ("action_trigger", "▶", "播放动画", "#f39b55"),
        ("action_trigger_active", "◎", "目标待机", "#78d381"),
    )

    def __init__(self, schema: EditorSchema, inline: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.schema = schema
        self.inline = inline
        self.node = None
        self.global_mode = "simple"
        self.show_json_field_names = False
        self.compact_mode = False
        self._bindings: dict[str, EditorBinding] = {}
        self._form_row_widgets: list[tuple[QWidget, QWidget]] = []
        self._summary_labels: dict[str, QLabel] = {}
        self.setObjectName("inlineNodeForm" if inline else "inspectorNodeForm")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._title = QLabel("未选择节点")
        self._title.setWordWrap(True)
        title_font = QFont("Segoe UI Variable Text", 10 if inline else 12)
        title_font.setBold(True)
        self._title.setFont(title_font)
        self._subtitle = QLabel("")
        self._subtitle.setWordWrap(True)
        header = QVBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(2)
        header.addWidget(self._title)
        header.addWidget(self._subtitle)
        self._header_widget = QWidget(self)
        self._header_widget.setLayout(header)
        self._header_widget.setVisible(not inline)
        self._summary_widget = QWidget(self)
        self._summary_layout = QVBoxLayout(self._summary_widget)
        self._summary_layout.setContentsMargins(0, 0, 0, 0)
        self._summary_layout.setSpacing(4)
        for key, _icon, _label, _color in self.SUMMARY_SPECS:
            row = QLabel("")
            row.setWordWrap(True)
            row.setTextFormat(Qt.TextFormat.RichText)
            row.setVisible(False)
            row.setProperty("summaryKey", key)
            self._summary_layout.addWidget(row)
            self._summary_labels[key] = row
        self._summary_widget.setVisible(inline)
        self._form = QFormLayout()
        self._form.setContentsMargins(0, 0, 0, 0)
        self._form.setSpacing(6 if inline else 8)
        self._form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        layout.addWidget(self._header_widget)
        layout.addWidget(self._summary_widget)
        layout.addLayout(self._form)
        self.setFrameShape(QFrame.Shape.NoFrame)
        if inline:
            self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

    def set_node(
        self,
        node,
        global_mode: str,
        show_json_field_names: bool = False,
        compact_mode: bool = False,
    ) -> None:
        same_node = bool(self.node and self.node.uuid == node.uuid and self.node.type == node.type)
        self.node = node
        self.global_mode = global_mode
        self.show_json_field_names = show_json_field_names
        self.compact_mode = False
        self._apply_dynamic_style()
        if same_node and self._bindings:
            self.refresh()
            return
        self._rebuild()

    def refresh(
        self,
        global_mode: str | None = None,
        show_json_field_names: bool | None = None,
        compact_mode: bool | None = None,
    ) -> None:
        if global_mode:
            self.global_mode = global_mode
        if show_json_field_names is not None:
            self.show_json_field_names = show_json_field_names
        if compact_mode is not None:
            self.compact_mode = False
        if not self.node:
            return
        definition = self.schema.nodes[self.node.type]
        visible_keys = tuple(field.key for field in self._visible_fields(definition.fields))
        if visible_keys != tuple(self._bindings.keys()):
            self._rebuild()
            return
        self._apply_dynamic_style()
        self._sync_header()
        self._sync_summary()
        for key, binding in self._bindings.items():
            binding.setter(self.node.fields.get(key))
            binding.read_only_setter(bool(self.node.locked))
        self._apply_inline_visibility()

    def _clear_form(self) -> None:
        while self._form.rowCount():
            self._form.removeRow(0)
        self._bindings.clear()
        self._form_row_widgets.clear()

    def _sync_header(self) -> None:
        if not self.node:
            self._title.setText("未选择节点")
            self._subtitle.setText("")
            return
        definition = self.schema.nodes[self.node.type]
        self._title.setText(definition.title)
        if self.node.type in ("TouchIdle", "TouchDrag", "ParameterTrigger"):
            subtitle = f"ID {self.node.fields.get('id', '-')} | {self.node.fields.get('draw_able_name', '')}"
            if self.node.locked:
                subtitle = f"{subtitle} | 已锁定"
            self._subtitle.setText(subtitle)
        else:
            self._subtitle.setText("已锁定" if self.node.locked else "")

    def _rebuild(self) -> None:
        self._clear_form()
        if not self.node:
            self._sync_header()
            self._sync_summary()
            return
        definition = self.schema.nodes[self.node.type]
        self._sync_header()
        self._sync_summary()
        for field in self._visible_fields(definition.fields):
            widget, setter, read_only_setter = self._build_editor(field)
            label = QLabel()
            highlighted_keys = {"draw_able_name", "parameter", "action_trigger", "action_trigger_active"}
            label_text = field.key if self.show_json_field_names else field.label
            if field.key in highlighted_keys:
                label.setTextFormat(Qt.TextFormat.RichText)
                label.setText(
                    f"<span style='color:#f8d66d;font-weight:700;text-decoration: underline;'>★ {label_text}</span>"
                )
            else:
                label.setTextFormat(
                    Qt.TextFormat.RichText if (field.label_html and not self.show_json_field_names) else Qt.TextFormat.PlainText
                )
                label.setText(field.key if self.show_json_field_names else (field.label_html or field.label))
            role = self._field_role(field)
            label.setProperty("fieldRole", role)
            widget.setProperty("fieldRole", role)
            label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            self._form.addRow(label, widget)
            self._form_row_widgets.append((label, widget))
            self._bindings[field.key] = EditorBinding(widget=widget, setter=setter, read_only_setter=read_only_setter)
            setter(self.node.fields.get(field.key, field.default))
            read_only_setter(bool(self.node.locked))
        self._add_appearance_row()
        self._apply_inline_visibility()
        self.adjustSize()

    def _visible_fields(self, fields: tuple[FieldSchema, ...]) -> list[FieldSchema]:
        if not self.node:
            return []
        result: list[FieldSchema] = []
        for field in fields:
            if field.key in self.APPEARANCE_KEYS:
                continue
            if self.node.type == "Comment" and field.key in self.COMMENT_APPEARANCE_KEYS:
                continue
            if field_visible(field, self.node.fields, self.global_mode):
                result.append(field)
        return result

    def _add_appearance_row(self) -> None:
        if not self.node:
            return
        button = QPushButton("外观设置")
        button.setProperty("accentButton", True)
        button.clicked.connect(self._show_appearance_dialog)
        button.setEnabled(not bool(self.node and self.node.locked))
        label = QLabel("外观")
        self._form.addRow(label, button)
        self._form_row_widgets.append((label, button))

    @staticmethod
    def _field_role(field: FieldSchema) -> str:
        if field.key in {"draw_able_name", "parameter", "action_trigger", "action_trigger_active"}:
            return "generated"
        if field.key.endswith("_kind_ui") or field.key.endswith("_reserved_ui"):
            return "diagnostic"
        return "standard"

    def _show_appearance_dialog(self) -> None:
        if not self.node:
            return
        dialog = NodeAppearanceDialog(self.node.fields, self, include_font_size=self.node.type == "Comment")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        for key, value in dialog.values().items():
            if self.node.fields.get(key) != value:
                self.fieldCommitted.emit(key, value)

    def content_height_hint(self) -> int:
        layout = self.layout()
        margins = layout.contentsMargins()
        total = margins.top() + margins.bottom()

        if self._header_widget.isVisible():
            header_height = self._title.sizeHint().height()
            if self._subtitle.text():
                header_height += 2 + self._subtitle.sizeHint().height()
            total += header_height

        row_heights: list[int] = []
        for label_widget, field_widget in self._form_row_widgets:
            if not label_widget.isVisible() or not field_widget.isVisible():
                continue
            label_height = label_widget.sizeHint().height()
            field_height = field_widget.sizeHint().height()
            row_heights.append(max(label_height, field_height))

        if row_heights:
            total += layout.spacing()
            total += sum(row_heights)
            total += self._form.spacing() * (len(row_heights) - 1)
        return total

    def summary_items(self) -> list[tuple[str, str]]:
        if not self.node:
            return []
        items: list[tuple[str, str]] = []
        for key, _icon, label, _color in self.SUMMARY_SPECS:
            value = self._summary_value(key)
            if value:
                items.append((label, value))
        return items

    def summary_display_rows(self) -> list[tuple[str, str, str, str]]:
        if not self.node:
            return []
        rows: list[tuple[str, str, str, str]] = []
        for key, icon, label, color in self.SUMMARY_SPECS:
            value = self._summary_value(key)
            if value:
                rows.append((icon, label, value, color))
        return rows

    def commit_pending_edits(self) -> None:
        for binding in self._bindings.values():
            widget = binding.widget
            if isinstance(widget, NumericLineEdit):
                widget._emit_commit()
                continue
            if isinstance(widget, CommitLineEdit):
                widget._emit_commit()
                continue
            if isinstance(widget, CommitPlainTextEdit):
                widget.committed.emit(widget.toPlainText())
                continue
            if isinstance(widget, CommitComboBox):
                widget.committed.emit(widget.currentData())
                continue
            if isinstance(widget, ColorFieldWidget):
                widget.committed.emit(widget.edit.text().strip())
                continue

    def focus_field(self, key: str) -> bool:
        binding = self._bindings.get(key)
        if not binding:
            return False
        widget = binding.widget
        target = widget.edit if isinstance(widget, ColorFieldWidget) else widget
        target.setFocus(Qt.FocusReason.MouseFocusReason)
        if isinstance(target, QLineEdit):
            target.selectAll()
        return True

    def _build_editor(self, field: FieldSchema) -> tuple[QWidget, Callable[[Any], None], Callable[[bool], None]]:
        if field.editor == "text":
            if field.multiline:
                widget = CommitPlainTextEdit()
                widget.setMinimumHeight(92 if not self.inline else 72)
                widget.committed.connect(lambda value, key=field.key: self.fieldCommitted.emit(key, value))
                widget.setReadOnly(field.read_only)
                return (
                    widget,
                    lambda value, target=widget, key=field.key: self._set_plain_text(
                        target, "" if value is None else str(self._display_value(key, value))
                    ),
                    lambda locked, target=widget, base=field.read_only: target.setReadOnly(base or locked),
                )
            widget = CommitLineEdit()
            widget.setPlaceholderText(field.placeholder)
            widget.committed.connect(lambda value, key=field.key: self.fieldCommitted.emit(key, value))
            widget.setReadOnly(field.read_only)
            return (
                widget,
                lambda value, target=widget, key=field.key: self._set_line_text(
                    target, "" if value is None else str(self._display_value(key, value))
                ),
                lambda locked, target=widget, base=field.read_only: target.setReadOnly(base or locked),
            )

        if field.editor in {"int", "float", "nullable_int"}:
            widget = NumericLineEdit(field.editor)
            widget.setPlaceholderText(field.placeholder)
            widget.setReadOnly(field.read_only)
            widget.committed.connect(lambda value, key=field.key: self.fieldCommitted.emit(key, value))
            return (
                widget,
                lambda value, target=widget, key=field.key: self._set_line_text(
                    target, "" if value is None else str(self._display_value(key, value))
                ),
                lambda locked, target=widget, base=field.read_only: target.setReadOnly(base or locked),
            )

        if field.editor == "date":
            widget = QDateEdit()
            widget.setCalendarPopup(True)
            widget.setDisplayFormat("yyyy-MM-dd")
            widget.setEnabled(not field.read_only)
            widget.dateChanged.connect(
                lambda _, key=field.key, target=widget: self.fieldCommitted.emit(key, target.date().toString("yyyy-MM-dd"))
            )
            return (
                widget,
                lambda value, target=widget: self._set_date(target, value),
                lambda locked, target=widget, base=field.read_only: target.setEnabled(not (base or locked)),
            )

        if field.editor == "bool":
            widget = QCheckBox()
            widget.setEnabled(not field.read_only)
            widget.stateChanged.connect(lambda state, key=field.key: self.fieldCommitted.emit(key, 1 if state else 0))
            return (
                widget,
                lambda value, target=widget: self._set_checked(target, bool(int(value or 0))),
                lambda locked, target=widget, base=field.read_only: target.setEnabled(not (base or locked)),
            )

        if field.editor == "combo":
            widget = CommitComboBox()
            for option in field.options:
                widget.addItem(option.label, option.value)
            widget.setEnabled(not field.read_only)
            widget.committed.connect(lambda value, key=field.key: self.fieldCommitted.emit(key, value))
            return (
                widget,
                lambda value, target=widget: self._set_combo_value(target, value),
                lambda locked, target=widget, base=field.read_only: target.setEnabled(not (base or locked)),
            )

        if field.editor == "range":
            widget = CommitLineEdit()
            widget.setPlaceholderText(field.placeholder or "{0,1}")
            widget.setReadOnly(field.read_only)
            widget.committed.connect(lambda value, key=field.key: self.fieldCommitted.emit(key, value))
            return (
                widget,
                lambda value, target=widget: self._set_line_text(target, "" if value is None else str(value)),
                lambda locked, target=widget, base=field.read_only: target.setReadOnly(base or locked),
            )

        if field.editor == "color":
            widget = ColorFieldWidget()
            widget.set_placeholder_text(field.placeholder or "#ffffff")
            widget.set_read_only(field.read_only)
            widget.committed.connect(lambda value, key=field.key: self.fieldCommitted.emit(key, value))
            return (
                widget,
                lambda value, target=widget: self._set_line_text(target.edit, "" if value is None else str(value)),
                lambda locked, target=widget, base=field.read_only: target.set_read_only(base or locked),
            )

        raise ValueError(f"Unsupported editor type: {field.editor}")

    @staticmethod
    def _set_line_text(widget: QLineEdit, value: str) -> None:
        blocked = widget.blockSignals(True)
        widget.setText(value)
        widget.blockSignals(blocked)

    @staticmethod
    def _set_plain_text(widget: QPlainTextEdit, value: str) -> None:
        blocked = widget.blockSignals(True)
        if widget.toPlainText() != value:
            widget.setPlainText(value)
        widget.blockSignals(blocked)

    @staticmethod
    def _set_checked(widget: QCheckBox, value: bool) -> None:
        blocked = widget.blockSignals(True)
        widget.setChecked(value)
        widget.blockSignals(blocked)

    @staticmethod
    def _set_combo_value(widget: QComboBox, value: Any) -> None:
        blocked = widget.blockSignals(True)
        index = widget.findData(value)
        if index >= 0:
            widget.setCurrentIndex(index)
        widget.blockSignals(blocked)

    @staticmethod
    def _set_date(widget: QDateEdit, value: Any) -> None:
        blocked = widget.blockSignals(True)
        if isinstance(value, str):
            date = QDate.fromString(value, "yyyy-MM-dd")
            if date.isValid():
                widget.setDate(date)
        widget.blockSignals(blocked)

    def _display_value(self, key: str, value: Any) -> Any:
        if not self.node:
            return value
        return display_value_for_field(self.schema, self.node, key, value)

    def _summary_value(self, key: str) -> str:
        if not self.node or key not in self.node.fields:
            return ""
        if key == "action_trigger_active" and self.node.type in {"TouchDrag", "ParameterTrigger"}:
            return ""
        value = self._display_value(key, self.node.fields.get(key))
        return "" if value is None else str(value).strip()

    def _sync_summary(self) -> None:
        has_visible_rows = False
        for key, icon, label, color in self.SUMMARY_SPECS:
            row = self._summary_labels[key]
            value = self._summary_value(key)
            row.setVisible(bool(self.inline and value))
            if not value:
                row.clear()
                continue
            has_visible_rows = True
            row.setText(
                f"<span style='color:{color}; font-weight:700;'>{icon}</span> "
                f"<span style='color:{color}; font-weight:600;'>{label}：</span>"
                f"<span style='color:#dfe5ef;'>{value}</span>"
            )
        self._summary_widget.setVisible(bool(self.inline and has_visible_rows))

    def _apply_inline_visibility(self) -> None:
        if not self.inline:
            self._header_widget.setVisible(True)
            self._summary_widget.setVisible(False)
            return
        for label_widget, field_widget in self._form_row_widgets:
            label_widget.setVisible(True)
            field_widget.setVisible(True)
        self._summary_widget.setVisible(False)

    def _apply_dynamic_style(self) -> None:
        if not self.node:
            self.setStyleSheet("")
            return
        text_key = "note_text_color" if self.node.type == "Comment" else "theme_text_color"
        default_text = "#f3f5f8" if self.node.type == "Comment" else "#f7f8fa"
        text_color = QColor(str(self.node.fields.get(text_key) or default_text))
        if not text_color.isValid():
            text_color = QColor(default_text)
        if self.node.type == "Comment":
            try:
                alpha = max(0, min(100, int(self.node.fields.get("note_text_alpha", 100))))
            except (TypeError, ValueError):
                alpha = 100
            text_color.setAlphaF(alpha / 100.0)
        try:
            font_size = max(11, min(28, int(self.node.fields.get("note_font_size", 15)))) if self.node.type == "Comment" else 13
        except (TypeError, ValueError):
            font_size = 15 if self.node.type == "Comment" else 13
        rgba = f"rgba({text_color.red()}, {text_color.green()}, {text_color.blue()}, {text_color.alpha()})"
        self.setStyleSheet(
            f"QLabel {{ color: {rgba}; }}"
            f" QPlainTextEdit {{ color: {rgba}; font-size: {font_size}px; }}"
            f" QLineEdit {{ color: {rgba}; }}"
        )
