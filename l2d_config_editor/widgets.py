"""Reusable PyQt widgets for node editing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from PyQt6.QtCore import QDate, Qt, pyqtSignal
from PyQt6.QtGui import QFocusEvent, QFont, QRegularExpressionValidator
from PyQt6.QtCore import QRegularExpression
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

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


@dataclass
class EditorBinding:
    widget: QWidget
    setter: Callable[[Any], None]


class ValidationSummaryWidget(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("validationSummary")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)
        self.title = QLabel("冲突与警告")
        self.title.setObjectName("validationSummaryTitle")
        self.list_widget = QListWidget()
        self.list_widget.setAlternatingRowColors(True)
        self.empty = QLabel("当前节点没有警告")
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
                text = f"{text} | 相关节点: {', '.join(issue.related_titles)}"
            item = QListWidgetItem(text)
            self.list_widget.addItem(item)


class NodeFormWidget(QFrame):
    fieldCommitted = pyqtSignal(str, object)

    def __init__(self, schema: EditorSchema, inline: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.schema = schema
        self.inline = inline
        self.node = None
        self.global_mode = "simple"
        self._bindings: dict[str, EditorBinding] = {}
        self._title = QLabel("未选择节点")
        self._title.setWordWrap(True)
        title_font = QFont("Segoe UI", 10 if inline else 11)
        title_font.setBold(True)
        self._title.setFont(title_font)
        self._subtitle = QLabel("")
        self._subtitle.setWordWrap(True)
        header = QVBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(2)
        header.addWidget(self._title)
        header.addWidget(self._subtitle)
        self._form = QFormLayout()
        self._form.setContentsMargins(0, 0, 0, 0)
        self._form.setSpacing(6 if inline else 8)
        self._form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        layout.addLayout(header)
        layout.addLayout(self._form)
        self.setFrameShape(QFrame.Shape.NoFrame)
        if inline:
            self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

    def set_node(self, node, global_mode: str) -> None:
        self.node = node
        self.global_mode = global_mode
        self._rebuild()

    def refresh(self, global_mode: str | None = None) -> None:
        if global_mode:
            self.global_mode = global_mode
        if not self.node:
            return
        definition = self.schema.nodes[self.node.type]
        visible_keys = [field.key for field in definition.fields if field_visible(field, self.node.fields, self.global_mode)]
        if set(visible_keys) != set(self._bindings.keys()):
            self._rebuild()
            return
        self._sync_header()
        for key, binding in self._bindings.items():
            binding.setter(self.node.fields.get(key))

    def _clear_form(self) -> None:
        while self._form.rowCount():
            self._form.removeRow(0)
        self._bindings.clear()

    def _sync_header(self) -> None:
        if not self.node:
            self._title.setText("未选择节点")
            self._subtitle.setText("")
            return
        definition = self.schema.nodes[self.node.type]
        self._title.setText(definition.title)
        if self.node.type in ("TouchIdle", "TouchDrag"):
            self._subtitle.setText(
                f"ID {self.node.fields.get('id', '-')} | {self.node.fields.get('draw_able_name', '')}"
            )
        else:
            self._subtitle.setText("")

    def _rebuild(self) -> None:
        self._clear_form()
        if not self.node:
            self._sync_header()
            return
        definition = self.schema.nodes[self.node.type]
        self._sync_header()
        for field in definition.fields:
            if not field_visible(field, self.node.fields, self.global_mode):
                continue
            widget, setter = self._build_editor(field)
            label = QLabel(field.label)
            label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            self._form.addRow(label, widget)
            self._bindings[field.key] = EditorBinding(widget=widget, setter=setter)
            setter(self.node.fields.get(field.key, field.default))
        self.adjustSize()

    def _build_editor(self, field: FieldSchema) -> tuple[QWidget, Callable[[Any], None]]:
        if field.editor == "text":
            if field.multiline:
                widget = CommitPlainTextEdit()
                widget.setMinimumHeight(92 if not self.inline else 72)
                widget.committed.connect(lambda value, key=field.key: self.fieldCommitted.emit(key, value))
                if field.read_only:
                    widget.setReadOnly(True)
                return widget, lambda value, target=widget: self._set_plain_text(target, "" if value is None else str(value))
            widget = CommitLineEdit()
            widget.setPlaceholderText(field.placeholder)
            widget.committed.connect(lambda value, key=field.key: self.fieldCommitted.emit(key, value))
            widget.setReadOnly(field.read_only)
            return widget, lambda value, target=widget: self._set_line_text(target, "" if value is None else str(value))

        if field.editor in {"int", "float", "nullable_int"}:
            widget = NumericLineEdit(field.editor)
            widget.setPlaceholderText(field.placeholder)
            widget.setReadOnly(field.read_only)
            widget.committed.connect(lambda value, key=field.key: self.fieldCommitted.emit(key, value))
            return widget, lambda value, target=widget: self._set_line_text(target, "" if value is None else str(value))

        if field.editor == "date":
            widget = QDateEdit()
            widget.setCalendarPopup(True)
            widget.setDisplayFormat("yyyy-MM-dd")
            widget.dateChanged.connect(
                lambda _, key=field.key, target=widget: self.fieldCommitted.emit(key, target.date().toString("yyyy-MM-dd"))
            )
            return widget, lambda value, target=widget: self._set_date(target, value)

        if field.editor == "bool":
            widget = QCheckBox()
            widget.stateChanged.connect(lambda state, key=field.key: self.fieldCommitted.emit(key, 1 if state else 0))
            return widget, lambda value, target=widget: self._set_checked(target, bool(int(value or 0)))

        if field.editor == "combo":
            widget = QComboBox()
            for option in field.options:
                widget.addItem(option.label, option.value)
            widget.currentIndexChanged.connect(
                lambda _, key=field.key, target=widget: self.fieldCommitted.emit(key, target.currentData())
            )
            return widget, lambda value, target=widget: self._set_combo_value(target, value)

        if field.editor == "range":
            widget = CommitLineEdit()
            widget.setPlaceholderText(field.placeholder or "{0,1}")
            widget.committed.connect(lambda value, key=field.key: self.fieldCommitted.emit(key, value))
            return widget, lambda value, target=widget: self._set_line_text(target, "" if value is None else str(value))

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
