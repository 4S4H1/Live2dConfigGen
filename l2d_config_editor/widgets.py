"""Reusable PyQt widgets for node editing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from PyQt6.QtCore import QDate, Qt, pyqtSignal
from PyQt6.QtGui import QFocusEvent, QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .constants import FUNCTION_NODE_TYPES
from .definitions import FieldDefinition, NODE_DEFINITIONS


class CommitLineEdit(QLineEdit):
    committed = pyqtSignal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.editingFinished.connect(self._emit_commit)

    def _emit_commit(self) -> None:
        self.committed.emit(self.text())


class CommitPlainTextEdit(QPlainTextEdit):
    committed = pyqtSignal(object)

    def focusOutEvent(self, event: QFocusEvent) -> None:
        super().focusOutEvent(event)
        self.committed.emit(self.toPlainText())


@dataclass
class EditorBinding:
    widget: QWidget
    getter: Callable[[], Any]
    setter: Callable[[Any], None]


class NodeFormWidget(QFrame):
    fieldCommitted = pyqtSignal(str, object)
    modeRequested = pyqtSignal(str)

    def __init__(self, inline: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.inline = inline
        self.node = None
        self._bindings: dict[str, EditorBinding] = {}
        self._title = QLabel("未选择节点")
        self._title.setWordWrap(True)
        title_font = QFont()
        title_font.setPointSize(10 if inline else 11)
        title_font.setBold(True)
        self._title.setFont(title_font)
        self._mode_button = QPushButton("高级模式")
        self._mode_button.setCheckable(True)
        self._mode_button.clicked.connect(self._toggle_mode)
        self._mode_button.setVisible(False)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self._title, 1)
        header.addWidget(self._mode_button, 0)
        self._form = QFormLayout()
        self._form.setContentsMargins(0, 0, 0, 0)
        self._form.setSpacing(6 if inline else 8)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        layout.addLayout(header)
        layout.addLayout(self._form)
        self.setFrameShape(QFrame.Shape.NoFrame)
        if inline:
            self.setMinimumWidth(240)

    def set_node(self, node) -> None:
        self.node = node
        self._rebuild()

    def _toggle_mode(self) -> None:
        if not self.node:
            return
        target_mode = "advanced" if self._mode_button.isChecked() else "simple"
        self.modeRequested.emit(target_mode)

    def _clear_form(self) -> None:
        while self._form.rowCount():
            self._form.removeRow(0)
        self._bindings.clear()

    def _rebuild(self) -> None:
        self._clear_form()
        if not self.node:
            self._title.setText("未选择节点")
            self._mode_button.setVisible(False)
            return
        definition = NODE_DEFINITIONS[self.node.type]
        title = definition.title
        if self.node.type in FUNCTION_NODE_TYPES:
            title = f"{title} #{self.node.fields.get('id', '-')}"
        self._title.setText(title)
        self._mode_button.setVisible(self.node.type in FUNCTION_NODE_TYPES)
        if self.node.type in FUNCTION_NODE_TYPES:
            self._mode_button.blockSignals(True)
            self._mode_button.setChecked(self.node.mode_variant == "advanced")
            self._mode_button.setText("高级模式" if self.node.mode_variant == "advanced" else "简易模式")
            self._mode_button.blockSignals(False)
        for field in definition.fields:
            if self.node.mode_variant not in field.modes:
                continue
            if field.visible_if and not field.visible_if(self.node.fields, self.node.mode_variant):
                continue
            widget, getter, setter = self._build_editor(field)
            label = QLabel(field.label)
            label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            self._form.addRow(label, widget)
            self._bindings[field.key] = EditorBinding(widget=widget, getter=getter, setter=setter)
            setter(self.node.fields.get(field.key, field.default))

    def refresh(self) -> None:
        if not self.node:
            return
        definition = NODE_DEFINITIONS[self.node.type]
        visible_keys = []
        for field in definition.fields:
            if self.node.mode_variant not in field.modes:
                continue
            if field.visible_if and not field.visible_if(self.node.fields, self.node.mode_variant):
                continue
            visible_keys.append(field.key)
        if set(visible_keys) != set(self._bindings.keys()):
            self._rebuild()
            return
        self._title.setText(
            f"{definition.title} #{self.node.fields.get('id', '-')}" if self.node.type in FUNCTION_NODE_TYPES else definition.title
        )
        if self.node.type in FUNCTION_NODE_TYPES:
            self._mode_button.blockSignals(True)
            self._mode_button.setChecked(self.node.mode_variant == "advanced")
            self._mode_button.setText("高级模式" if self.node.mode_variant == "advanced" else "简易模式")
            self._mode_button.blockSignals(False)
        for key, binding in self._bindings.items():
            binding.setter(self.node.fields.get(key))

    def _build_editor(self, field: FieldDefinition) -> tuple[QWidget, Callable[[], Any], Callable[[Any], None]]:
        if field.editor == "text":
            if field.multiline:
                widget = CommitPlainTextEdit()
                widget.setMinimumHeight(84 if not self.inline else 72)
                widget.committed.connect(lambda value, key=field.key: self.fieldCommitted.emit(key, value))
                return (
                    widget,
                    widget.toPlainText,
                    lambda value, target=widget: self._set_plain_text(target, "" if value is None else str(value)),
                )
            widget = CommitLineEdit()
            widget.setPlaceholderText(field.placeholder)
            widget.committed.connect(lambda value, key=field.key: self.fieldCommitted.emit(key, value))
            return widget, widget.text, lambda value, target=widget: self._set_line_text(target, "" if value is None else str(value))
        if field.editor == "readonly":
            widget = QPlainTextEdit()
            widget.setReadOnly(True)
            widget.setMinimumHeight(60)
            return (
                widget,
                widget.toPlainText,
                lambda value, target=widget: self._set_plain_text(target, "" if value is None else str(value)),
            )
        if field.editor == "date":
            widget = QDateEdit()
            widget.setCalendarPopup(True)
            widget.setDisplayFormat("yyyy-MM-dd")
            widget.dateChanged.connect(lambda _, key=field.key, target=widget: self.fieldCommitted.emit(key, target.date().toString("yyyy-MM-dd")))
            return (
                widget,
                lambda target=widget: target.date().toString("yyyy-MM-dd"),
                lambda value, target=widget: self._set_date(target, value),
            )
        if field.editor == "int":
            widget = QSpinBox()
            widget.setRange(-999999999, 999999999)
            widget.valueChanged.connect(lambda value, key=field.key: self.fieldCommitted.emit(key, int(value)))
            return widget, widget.value, lambda value, target=widget: self._set_spin_value(target, 0 if value in (None, "") else int(value))
        if field.editor == "nullable_int":
            widget = CommitLineEdit()
            widget.setPlaceholderText("留空")
            widget.committed.connect(lambda value, key=field.key: self.fieldCommitted.emit(key, self._parse_nullable_int(value)))
            return widget, widget.text, lambda value, target=widget: self._set_line_text(target, "" if value is None else str(value))
        if field.editor == "float":
            widget = QDoubleSpinBox()
            widget.setRange(-999999999.0, 999999999.0)
            widget.setDecimals(3)
            widget.valueChanged.connect(lambda value, key=field.key: self.fieldCommitted.emit(key, float(value)))
            return (
                widget,
                widget.value,
                lambda value, target=widget: self._set_double_value(target, 0.0 if value in (None, "") else float(value)),
            )
        if field.editor == "bool":
            widget = QCheckBox()
            widget.stateChanged.connect(lambda state, key=field.key: self.fieldCommitted.emit(key, 1 if state else 0))
            return widget, widget.isChecked, lambda value, target=widget: self._set_checked(target, bool(int(value or 0)))
        if field.editor == "combo":
            widget = QComboBox()
            for option in field.options:
                widget.addItem(option.label, option.value)
            widget.currentIndexChanged.connect(lambda _, key=field.key, target=widget: self.fieldCommitted.emit(key, target.currentData()))
            return widget, widget.currentData, lambda value, target=widget: self._set_combo_value(target, value)
        if field.editor == "range":
            widget = CommitLineEdit()
            widget.setPlaceholderText("{0,1}")
            widget.committed.connect(lambda value, key=field.key: self.fieldCommitted.emit(key, value))
            return widget, widget.text, lambda value, target=widget: self._set_line_text(target, "" if value is None else str(value))
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
    def _set_spin_value(widget: QSpinBox, value: int) -> None:
        blocked = widget.blockSignals(True)
        widget.setValue(value)
        widget.blockSignals(blocked)

    @staticmethod
    def _set_double_value(widget: QDoubleSpinBox, value: float) -> None:
        blocked = widget.blockSignals(True)
        widget.setValue(value)
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

    @staticmethod
    def _parse_nullable_int(value: Any) -> int | None:
        text = str(value).strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None
