"""Batch creation dialog for self-contained configuration bases."""

from __future__ import annotations

import csv
import os
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from io import StringIO
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True, slots=True)
class BaseTemplateSpec:
    version: str
    author: str
    char_name: str
    memo: str
    ship_skin_id: int
    tips: str = ""
    react_condition: str = "0"
    default_state: str = "idle0"


def create_base_template_files(schema: Any, workspace: str | Path, specs: list[BaseTemplateSpec]) -> list[Path]:
    """Validate and stage a batch, rolling back files when publishing raises."""

    from .logic import build_template_version_folder_name, create_template_document, save_document

    if not specs:
        raise ValueError("请至少提供一个配置底座。")
    root = Path(workspace)
    reserved: set[Path] = set()
    plans: list[tuple[Path, Path, Any]] = []
    for spec in specs:
        try:
            datetime.strptime(str(spec.version or "").strip(), "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"{spec.char_name or '未命名配置'}的版本必须使用 yyyy-MM-dd 格式。") from exc
        if not str(spec.char_name or "").strip() or not str(spec.memo or "").strip() or int(spec.ship_skin_id or 0) <= 0:
            raise ValueError("角色名、角色资源名和大于 0 的角色 ID 都是必填项。")
        target_dir = root / build_template_version_folder_name(spec.version)
        target = _available_output_path(target_dir, spec.char_name, reserved)
        reserved.add(target)
        document = create_template_document(
            schema,
            version=spec.version,
            char_name=spec.char_name,
            memo=spec.memo,
            ship_skin_id=spec.ship_skin_id,
            author=spec.author,
            tips=spec.tips,
            react_condition=spec.react_condition,
            default_state=spec.default_state,
        )
        if not document.state.is_meta_ready:
            raise ValueError(f"{spec.char_name} 的模板字段不完整：{' / '.join(document.state.meta_missing_fields)}")
        temp_name = f".{target.name}.{uuid.uuid4().hex}.tmp"
        plans.append((target_dir / temp_name, target, document))

    staged: list[Path] = []
    published: list[Path] = []
    try:
        for temp_path, _target, document in plans:
            temp_path.parent.mkdir(parents=True, exist_ok=True)
            save_document(schema, document, temp_path)
            staged.append(temp_path)
        for temp_path, target, _document in plans:
            _publish_staged_template(temp_path, target)
            staged.remove(temp_path)
            published.append(target)
    except Exception:
        for path in staged:
            path.unlink(missing_ok=True)
        for path in published:
            path.unlink(missing_ok=True)
        raise
    return published


def _publish_staged_template(source: Path, target: Path) -> None:
    """Atomically publish a complete file without replacing a competing writer."""

    if os.name == "nt":
        # Windows rename is atomic and refuses an already existing destination.
        source.rename(target)
    else:
        # POSIX rename replaces destinations, so create a no-clobber hard link
        # within the same directory before removing the staging name.
        os.link(source, target)
        try:
            source.unlink()
        except OSError:
            target.unlink(missing_ok=True)
            raise


def _available_output_path(directory: Path, char_name: str, reserved: set[Path]) -> Path:
    from .csv_export import sanitize_csv_identifier

    stem = sanitize_csv_identifier(char_name, fallback="config")
    index = 1
    while True:
        suffix = "" if index == 1 else f"_{index}"
        candidate = directory / f"{stem}{suffix}.json"
        if candidate not in reserved and not candidate.exists():
            return candidate
        index += 1


TEMPLATE_COLUMN_KEYS = (
    "char_name",
    "memo",
    "ship_skin_id",
    "version",
    "author",
    "react_condition",
    "tips",
)

HEADER_ALIASES = {
    "char_name": {"角色名", "charname", "name"},
    "memo": {"角色资源名", "资源名", "memo"},
    "ship_skin_id": {"角色id", "shipskinid", "ship_skin_id"},
    "version": {"版本", "version", "日期"},
    "author": {"作者", "author"},
    "react_condition": {"允许目光拖拽的待机", "目光拖拽待机", "reactcondition", "react_condition"},
    "tips": {"备注", "tips"},
}


def _normalized_header(value: str) -> str:
    return str(value or "").replace(" ", "").replace("_", "").lower()


def _header_mapping(row: list[str]) -> dict[int, str]:
    normalized_aliases = {
        _normalized_header(alias): key
        for key, aliases in HEADER_ALIASES.items()
        for alias in aliases
    }
    return {
        index: normalized_aliases[normalized]
        for index, value in enumerate(row)
        if (normalized := _normalized_header(value)) in normalized_aliases
    }


def parse_pasted_template_rows(text: str, *, default_version: str | None = None) -> list[list[str]]:
    """Parse current seven-column and legacy four-column copied tables."""

    # Tabs at the boundaries represent empty cells; stripping them shifts
    # every field and can also mistake a seven-column row for the legacy form.
    source = str(text or "").strip("\r\n")
    if not source.strip():
        return []
    first_line = next(line for line in source.splitlines() if line.strip())
    delimiter = "\t" if "\t" in first_line else ("|" if "|" in first_line else ",")
    raw_rows = [
        [str(value or "").strip() for value in row]
        for row in csv.reader(StringIO(source), delimiter=delimiter)
        if any(str(value or "").strip() for value in row)
    ]
    if not raw_rows:
        return []

    resolved_version = str(default_version or date.today().isoformat())
    mapping = _header_mapping(raw_rows[0]) if _looks_like_header(raw_rows[0]) else {}
    source_rows = raw_rows[1:] if mapping else raw_rows
    normalized: list[list[str]] = []
    for row in source_rows:
        values_by_key = {
            "char_name": "",
            "memo": "",
            "ship_skin_id": "",
            "version": resolved_version,
            "author": "",
            "react_condition": "0",
            "tips": "",
        }
        if mapping:
            for index, key in mapping.items():
                if index < len(row):
                    values_by_key[key] = row[index]
        elif len(row) <= 4:
            legacy = (row + ["", "", "", ""])[:4]
            values_by_key.update(
                char_name=legacy[0], memo=legacy[1], ship_skin_id=legacy[2], tips=legacy[3]
            )
        else:
            for key, value in zip(TEMPLATE_COLUMN_KEYS, row[: len(TEMPLATE_COLUMN_KEYS)]):
                values_by_key[key] = value
        normalized.append([values_by_key[key] for key in TEMPLATE_COLUMN_KEYS])
    return normalized


def _looks_like_header(row: list[str]) -> bool:
    # One alias may be a real character name or note (e.g. "Name" or "tips").
    # A header must identify multiple distinct columns to avoid dropping data.
    return len(set(_header_mapping(row).values())) >= 2


class BatchTemplateDialog(QDialog):
    """Collect one or more independently configured base-template rows."""

    COLUMN_CHAR_NAME = 0
    COLUMN_MEMO = 1
    COLUMN_SHIP_ID = 2
    COLUMN_VERSION = 3
    COLUMN_AUTHOR = 4
    COLUMN_REACT_CONDITION = 5
    COLUMN_TIPS = 6

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("批量创建配置底座")
        self.resize(1260, 620)

        layout = QVBoxLayout(self)
        description = QLabel(
            "每行创建一个独立配置；版本、作者和允许目光拖拽的待机均可按角色设置。"
            "每个配置会自动包含不可删除的 idle0 根节点，作者可留空。"
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        self.table = QTableWidget(0, len(TEMPLATE_COLUMN_KEYS), self)
        self.table.setHorizontalHeaderLabels(
            ["角色名", "角色资源名", "角色 ID", "版本", "作者（可选）", "允许目光拖拽的待机", "备注（可选）"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(self.COLUMN_CHAR_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(self.COLUMN_MEMO, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(self.COLUMN_SHIP_ID, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COLUMN_VERSION, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COLUMN_AUTHOR, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(self.COLUMN_REACT_CONDITION, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COLUMN_TIPS, QHeaderView.ResizeMode.Stretch)
        self.add_row()
        layout.addWidget(self.table, 1)

        row_actions = QHBoxLayout()
        add_button = QPushButton("添加一行")
        add_button.clicked.connect(self.add_row)
        remove_button = QPushButton("删除所选")
        remove_button.clicked.connect(self.remove_selected_rows)
        paste_button = QPushButton("粘贴多行")
        paste_button.setToolTip("支持从 Excel 复制的制表符表格，也支持逗号或 | 分隔文本")
        paste_button.clicked.connect(self.paste_rows)
        row_actions.addWidget(add_button)
        row_actions.addWidget(remove_button)
        row_actions.addWidget(paste_button)
        row_actions.addStretch(1)
        layout.addLayout(row_actions)

        hint = QLabel(
            "新粘贴顺序：角色名、角色资源名、角色 ID、版本、作者、允许目光拖拽的待机、备注。"
            "也兼容旧的四列格式和带表头的任意列序。"
        )
        hint.setObjectName("panelHint")
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.create_button = buttons.addButton("创建配置底座", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _ensure_row_items(self, row: int) -> None:
        for column in range(self.table.columnCount()):
            if self.table.item(row, column) is None:
                self.table.setItem(row, column, QTableWidgetItem(""))

    def add_row(self, values: list[str] | None = None) -> int:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._ensure_row_items(row)
        for column, value in enumerate(["", "", "", date.today().isoformat(), "", "0", ""]):
            self.table.item(row, column).setText(value)
        if values:
            for column, value in enumerate(values[: self.table.columnCount()]):
                self.table.item(row, column).setText(str(value or ""))
        self.table.setCurrentCell(row, self.COLUMN_CHAR_NAME)
        return row

    def remove_selected_rows(self) -> None:
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()}, reverse=True)
        for row in rows:
            self.table.removeRow(row)
        if self.table.rowCount() == 0:
            self.add_row()

    def paste_rows(self) -> None:
        clipboard = QGuiApplication.clipboard()
        rows = parse_pasted_template_rows(
            clipboard.text() if clipboard is not None else "",
            default_version=date.today().isoformat(),
        )
        if not rows:
            QMessageBox.information(self, "没有可粘贴内容", "剪贴板中没有可识别的表格文本。")
            return
        first_empty = self.table.rowCount() == 1 and not any(
            self.table.item(0, column).text().strip()
            for column in (self.COLUMN_CHAR_NAME, self.COLUMN_MEMO, self.COLUMN_SHIP_ID, self.COLUMN_AUTHOR, self.COLUMN_TIPS)
        )
        if first_empty:
            self.table.removeRow(0)
        for values in rows:
            self.add_row(values)

    def template_specs(self) -> list[BaseTemplateSpec]:
        result: list[BaseTemplateSpec] = []
        errors: list[str] = []
        used_names: set[tuple[str, str]] = set()
        used_ids: set[tuple[str, int]] = set()
        for row in range(self.table.rowCount()):
            values = [self.table.item(row, column).text().strip() for column in range(self.table.columnCount())]
            if not any(values):
                continue
            char_name, memo, ship_id_text, version, author, react_condition, tips = values
            react_condition = re.sub(r"\s+", "", react_condition) or "0"
            label = f"第 {row + 1} 行"
            if not char_name:
                errors.append(f"{label}缺少角色名")
            if not memo:
                errors.append(f"{label}缺少角色资源名")
            try:
                ship_skin_id = int(ship_id_text)
            except ValueError:
                ship_skin_id = 0
            if ship_skin_id <= 0:
                errors.append(f"{label}的角色 ID 必须是大于 0 的整数")
            try:
                datetime.strptime(version, "%Y-%m-%d")
            except ValueError:
                errors.append(f"{label}的版本必须使用 yyyy-MM-dd 格式")
            if not re.fullmatch(r"\d+(?:,\d+)*", react_condition):
                errors.append(f"{label}的允许目光拖拽待机必须是逗号分隔的非负整数")
            normalized_name = char_name.casefold()
            name_key = (version, normalized_name)
            id_key = (version, ship_skin_id)
            if normalized_name and name_key in used_names:
                errors.append(f"{label}的角色名在版本 {version} 中重复：{char_name}")
            if ship_skin_id > 0 and id_key in used_ids:
                errors.append(f"{label}的角色 ID 在版本 {version} 中重复：{ship_skin_id}")
            used_names.add(name_key)
            used_ids.add(id_key)
            result.append(
                BaseTemplateSpec(
                    version=version,
                    author=author,
                    char_name=char_name,
                    memo=memo,
                    ship_skin_id=ship_skin_id,
                    tips=tips,
                    react_condition=react_condition,
                )
            )
        if not result:
            errors.append("请至少填写一行配置。")
        if errors:
            raise ValueError("\n".join(errors))
        return result

    def accept(self) -> None:
        try:
            self.template_specs()
        except ValueError as exc:
            QMessageBox.warning(self, "请检查配置", str(exc))
            return
        super().accept()
