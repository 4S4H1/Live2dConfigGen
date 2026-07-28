"""Main application window."""

from __future__ import annotations

import json
import ntpath
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QMimeData, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QColor, QDesktopServices, QGuiApplication, QKeySequence, QUndoStack
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QKeySequenceEdit,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QLineEdit,
    QButtonGroup,
    QPlainTextEdit,
    QProgressDialog,
)

from .app_settings import create_app_settings
from .canvas import NodeCanvasView
from .constants import CLIPBOARD_MIME
from .controller import EditorController
from .logic import (
    build_csv_export_filename,
    create_document,
    export_documents_to_csv,
    load_document,
)
from .perf_tools import PerformanceToolDialog
from .reference_images import read_reference_image
from .styles import ThemeMode, normalize_theme_mode, stylesheet_for_theme
from .svn_tools import SvnCommitRunner, discover_svn_executable
from .template_batch import BatchTemplateDialog, create_base_template_files
from .update_client import UpdateClient, bundled_public_key_pem
from .update_host import UpdateHostWindow
from .update_installer import launch_installer_after_exit
from .update_manifest import UpdateValidationError
from .version import PRODUCT_NAME, PUBLISHER, VERSION
from .widgets import (
    NodeFormWidget,
    ValidationSummaryWidget,
)

HELP_PAGE_URL = "https://ooia5293gn.feishu.cn/wiki/YvmxwxAKSitp3WkfFz3cY74Jnvg"


def is_path_within_install_root(
    path: str | os.PathLike[str],
    install_root: str | os.PathLike[str] | None,
) -> bool:
    """Return whether *path* is owned by a frozen Windows installation."""

    if install_root is None:
        return False
    candidate = ntpath.normcase(ntpath.normpath(os.fspath(path)))
    root = ntpath.normcase(ntpath.normpath(os.fspath(install_root)))
    if not ntpath.isabs(candidate) or not ntpath.isabs(root):
        return False
    try:
        return ntpath.commonpath((candidate, root)) == root
    except ValueError:
        return False


class SvnCommitDialog(QDialog):
    """Visible progress and output for the one-click SVN operation."""

    def __init__(self, runner: SvnCommitRunner, file_path: Path, message: str, parent=None) -> None:
        super().__init__(parent)
        self.runner = runner
        self._running = True
        self.setWindowTitle("提交当前 JSON 到 SVN")
        self.resize(760, 480)
        layout = QVBoxLayout(self)
        summary = QLabel(f"文件：{file_path}\n提交说明：{message}")
        summary.setWordWrap(True)
        layout.addWidget(summary)
        self.phase_label = QLabel("准备提交…")
        self.phase_label.setObjectName("sectionTitle")
        layout.addWidget(self.phase_label)
        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setPlaceholderText("SVN CLI 输出将显示在这里。")
        layout.addWidget(self.log_edit, 1)
        self.action_button = QPushButton("取消")
        self.action_button.clicked.connect(self._handle_action)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(self.action_button)
        layout.addLayout(button_row)
        runner.phaseChanged.connect(self.phase_label.setText)
        runner.outputReceived.connect(self._append_output)
        runner.finished.connect(self._handle_finished)

    def _append_output(self, output: str) -> None:
        cursor = self.log_edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(output)
        self.log_edit.setTextCursor(cursor)
        self.log_edit.ensureCursorVisible()

    def _handle_finished(self, success: bool, message: str) -> None:
        self._running = False
        self.phase_label.setText(("成功：" if success else "失败：") + message)
        self._append_output(f"\n{message}\n")
        self.action_button.setText("关闭")

    def _handle_action(self) -> None:
        if self._running:
            self.phase_label.setText("正在取消…")
            self.runner.cancel()
        else:
            self.accept()

    def reject(self) -> None:
        if self._running:
            self._handle_action()
            return
        super().reject()


class CsvPreviewDialog(QDialog):
    def __init__(self, schema, parent=None) -> None:
        super().__init__(parent)
        self.schema = schema
        self.setWindowTitle("CSV 预览")
        self.resize(1200, 680)
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, len(self.schema.csv_columns))
        self.table.setHorizontalHeaderLabels(list(self.schema.csv_columns))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)

    def set_schema(self, schema) -> None:
        self.schema = schema
        self.table.setColumnCount(len(schema.csv_columns))
        self.table.setHorizontalHeaderLabels(list(schema.csv_columns))

    def update_rows(self, rows) -> None:
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column_index, column in enumerate(self.schema.csv_columns):
                self.table.setItem(row_index, column_index, QTableWidgetItem(str(row.values.get(column, ""))))


class ConciseDisplayDialog(QDialog):
    FIELD_OPTIONS = (
        ("tips", "备注"),
        ("draw_able_name", "绘制 / 帧名"),
        ("parameter", "参数"),
        ("action_trigger", "过渡动画"),
        ("action_trigger_active", "目标待机"),
    )
    ELEMENT_OPTIONS = (
        ("groups", "分组框"),
        ("tables", "参数表"),
        ("images", "参考图片"),
        ("strokes", "画笔线条"),
    )

    def __init__(self, fields: set[str], elements: dict[str, bool], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("简洁展示设置")
        self.resize(420, 430)
        layout = QVBoxLayout(self)
        description = QLabel("只改变画布显示，不会删除字段、节点或画布内容。可见字段仍可双击编辑。")
        description.setWordWrap(True)
        layout.addWidget(description)
        layout.addWidget(QLabel("节点字段"))
        self.field_checkboxes: dict[str, QCheckBox] = {}
        for key, label in self.FIELD_OPTIONS:
            checkbox = QCheckBox(label)
            checkbox.setChecked(key in fields)
            self.field_checkboxes[key] = checkbox
            layout.addWidget(checkbox)
        layout.addWidget(QLabel("画布元素"))
        self.element_checkboxes: dict[str, QCheckBox] = {}
        for key, label in self.ELEMENT_OPTIONS:
            checkbox = QCheckBox(label)
            checkbox.setChecked(bool(elements.get(key, False)))
            self.element_checkboxes[key] = checkbox
            layout.addWidget(checkbox)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> tuple[set[str], dict[str, bool]]:
        fields = {key for key, checkbox in self.field_checkboxes.items() if checkbox.isChecked()}
        elements = {key: checkbox.isChecked() for key, checkbox in self.element_checkboxes.items()}
        return fields, elements


class ExportCsvDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("\u5bfc\u51fa\u5230 CSV")
        self.resize(760, 620)
        layout = QVBoxLayout(self)
        description = QLabel("\u9009\u62e9\u672c\u6b21\u8981\u5bfc\u51fa\u7684 JSON \u914d\u7f6e\u3002\u5bfc\u51fa\u6587\u4ef6\u4f1a\u81ea\u52a8\u5e26\u65f6\u95f4\u6233\uff0c\u907f\u514d\u8986\u76d6\u65e7 CSV\u3002")
        description.setWordWrap(True)
        layout.addWidget(description)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("\u641c\u7d22\u914d\u7f6e\u6587\u4ef6")
        self.search_edit.textChanged.connect(self._filter_items)
        layout.addWidget(self.search_edit)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        layout.addWidget(self.list_widget, 1)

        quick_row = QHBoxLayout()
        self.select_all_button = QPushButton("\u5168\u9009")
        self.clear_button = QPushButton("\u6e05\u7a7a")
        self.select_all_button.clicked.connect(lambda: self._set_all_checked(True))
        self.clear_button.clicked.connect(lambda: self._set_all_checked(False))
        quick_row.addWidget(self.select_all_button)
        quick_row.addWidget(self.clear_button)
        quick_row.addStretch(1)
        layout.addLayout(quick_row)

        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    def set_files(self, files: list[tuple[str, str]]) -> None:
        self.list_widget.clear()
        for relative_path, display_name in files:
            item = QListWidgetItem(display_name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, relative_path)
            item.setToolTip(relative_path)
            self.list_widget.addItem(item)
        self._filter_items()

    def selected_files(self) -> list[str]:
        selected: list[str] = []
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                relative_path = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(relative_path, str) and relative_path:
                    selected.append(relative_path)
        return selected

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            if not item.isHidden():
                item.setCheckState(state)

    def _filter_items(self) -> None:
        needle = self.search_edit.text().strip().lower()
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            haystack = f"{item.text()} {item.toolTip()}".lower()
            item.setHidden(bool(needle) and needle not in haystack)


class FileDirectoryDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("配置文件")
        self.resize(640, 760)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        description = QLabel("低频切换文件时使用。可按名称搜索、双击打开。")
        description.setWordWrap(True)
        layout.addWidget(description)

        top_row = QHBoxLayout()
        self.refresh_button = QPushButton("刷新")
        self.new_button = QPushButton("新建")
        self.delete_button = QPushButton("删除")
        top_row.addWidget(self.refresh_button)
        top_row.addWidget(self.new_button)
        top_row.addWidget(self.delete_button)
        top_row.addStretch(1)
        layout.addLayout(top_row)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索当前路径下的 JSON 配置")
        layout.addWidget(self.search_edit)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("configFileList")
        layout.addWidget(self.list_widget, 1)

        button_row = QHBoxLayout()
        self.open_button = QPushButton("打开选中")
        self.close_button = QPushButton("关闭")
        button_row.addWidget(self.open_button)
        button_row.addStretch(1)
        button_row.addWidget(self.close_button)
        layout.addLayout(button_row)
        self.close_button.clicked.connect(self.close)


class NodeDirectoryDialog(QDialog):
    nodeRequested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("节点目录")
        self.resize(560, 620)
        layout = QVBoxLayout(self)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索节点")
        self.search_edit.textChanged.connect(self._filter_items)
        layout.addWidget(self.search_edit)

        self.list_widget = QListWidget()
        self.list_widget.itemClicked.connect(self._emit_current_node)
        self.list_widget.itemActivated.connect(self._emit_current_node)
        layout.addWidget(self.list_widget, 1)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        button_box.rejected.connect(self.close)
        button_box.accepted.connect(self.close)
        layout.addWidget(button_box)

    def set_nodes(self, rows: list[tuple[str, str]]) -> None:
        current = self.selected_node_uuid()
        self.list_widget.clear()
        for node_uuid, label in rows:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, node_uuid)
            self.list_widget.addItem(item)
            if current and current == node_uuid:
                self.list_widget.setCurrentItem(item)
        self._filter_items()

    def selected_node_uuid(self) -> str | None:
        item = self.list_widget.currentItem()
        value = item.data(Qt.ItemDataRole.UserRole) if item else None
        return value if isinstance(value, str) and value else None

    def _emit_current_node(self, item: QListWidgetItem) -> None:
        value = item.data(Qt.ItemDataRole.UserRole) if item else None
        if isinstance(value, str) and value:
            self.nodeRequested.emit(value)

    def _filter_items(self) -> None:
        needle = self.search_edit.text().strip().lower()
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            item.setHidden(bool(needle) and needle not in item.text().lower())


class ShortcutConfigDialog(QDialog):
    WHEEL_OPTIONS = (
        ("Ctrl", "ctrl"),
        ("Alt", "alt"),
        ("Shift", "shift"),
        ("无修饰键", "none"),
    )
    HORIZONTAL_WHEEL_OPTIONS = (
        ("Alt 或 Shift", "alt_shift"),
        ("Alt", "alt"),
        ("Shift", "shift"),
        ("Ctrl", "ctrl"),
        ("无修饰键", "none"),
    )

    def __init__(
        self,
        shortcuts: dict[str, tuple[str, QKeySequence]],
        default_shortcuts: dict[str, QKeySequence],
        wheel_settings: dict[str, str],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("快捷键设置")
        self.resize(760, 700)
        self._shortcut_rows = shortcuts
        self._default_shortcuts = default_shortcuts
        layout = QVBoxLayout(self)

        wheel_row = QHBoxLayout()
        wheel_row.addWidget(QLabel("缩放滚轮"))
        self.zoom_modifier_combo = QComboBox()
        for label, value in self.WHEEL_OPTIONS:
            self.zoom_modifier_combo.addItem(label, value)
        wheel_row.addWidget(self.zoom_modifier_combo)
        wheel_row.addWidget(QLabel("横向平移滚轮"))
        self.horizontal_modifier_combo = QComboBox()
        for label, value in self.HORIZONTAL_WHEEL_OPTIONS:
            self.horizontal_modifier_combo.addItem(label, value)
        wheel_row.addWidget(self.horizontal_modifier_combo)
        wheel_row.addStretch(1)
        layout.addLayout(wheel_row)

        self.table = QTableWidget(len(shortcuts), 2)
        self.table.setHorizontalHeaderLabels(["动作", "快捷键"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        layout.addWidget(self.table, 1)

        self._editors: dict[str, QKeySequenceEdit] = {}
        for row, (action_id, (label, sequence)) in enumerate(shortcuts.items()):
            self.table.setItem(row, 0, QTableWidgetItem(label))
            editor = QKeySequenceEdit(sequence)
            self.table.setCellWidget(row, 1, editor)
            self._editors[action_id] = editor

        defaults_button = QPushButton("恢复默认")
        defaults_button.clicked.connect(self.restore_defaults)
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.button_box.accepted.connect(self._accept_with_validation)
        self.button_box.rejected.connect(self.reject)
        button_row = QHBoxLayout()
        button_row.addWidget(defaults_button)
        button_row.addStretch(1)
        button_row.addWidget(self.button_box)
        layout.addLayout(button_row)

        self._set_combo_value(self.zoom_modifier_combo, wheel_settings.get("zoom_modifier", "ctrl"))
        self._set_combo_value(self.horizontal_modifier_combo, wheel_settings.get("horizontal_modifier", "alt_shift"))

    def restore_defaults(self) -> None:
        for action_id, sequence in self._default_shortcuts.items():
            self._editors[action_id].setKeySequence(sequence)
        self._set_combo_value(self.zoom_modifier_combo, "ctrl")
        self._set_combo_value(self.horizontal_modifier_combo, "alt_shift")

    def configuration(self) -> tuple[dict[str, str], dict[str, str]]:
        shortcuts = {
            action_id: editor.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
            for action_id, editor in self._editors.items()
        }
        wheel = {
            "zoom_modifier": str(self.zoom_modifier_combo.currentData()),
            "horizontal_modifier": str(self.horizontal_modifier_combo.currentData()),
        }
        return shortcuts, wheel

    def _accept_with_validation(self) -> None:
        seen: dict[str, str] = {}
        duplicates: list[str] = []
        for action_id, editor in self._editors.items():
            sequence = editor.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
            if not sequence:
                continue
            if sequence in seen:
                duplicates.append(sequence)
            else:
                seen[sequence] = action_id
        if duplicates:
            QMessageBox.warning(self, "快捷键冲突", f"存在重复快捷键：{', '.join(sorted(set(duplicates)))}")
            return
        self.accept()

    @staticmethod
    def _set_combo_value(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)


class MainWindow(QMainWindow):
    AUTOSAVE_DELAY_MS = 1800
    PASTE_GAP = 96.0
    # Application-only preferences; graph content continues to live in JSON.
    SETTINGS_WORKSPACE_ROOT = "workspace_root"
    SETTINGS_LAST_DOCUMENT = "last_document_path"
    SETTINGS_THEME_MODE = "ui/theme_mode"
    SETTINGS_SVN_EXECUTABLE = "tools/svn_executable"
    SETTINGS_CONCISE_ENABLED = "view/concise/enabled"
    SETTINGS_CONCISE_FIELDS = "view/concise/fields"
    SETTINGS_PEN_COLOR = "canvas/pen/color"
    SETTINGS_PEN_WIDTH = "canvas/pen/width"
    SETTINGS_UPDATE_BASE_URL = "updates/base_url"
    SETTINGS_UPDATE_LAST_CHECK = "updates/last_check_at"

    def __init__(
        self,
        workdir: str | Path,
        *,
        prefer_saved_workspace: bool = True,
        install_root: str | Path | None = None,
    ) -> None:
        super().__init__()
        self.settings = create_app_settings()
        self.install_root = Path(install_root).resolve() if install_root is not None else None
        self.theme_mode = normalize_theme_mode(self.settings.value(self.SETTINGS_THEME_MODE, ThemeMode.DARK.value))
        if prefer_saved_workspace:
            self.workdir = self._resolved_workspace_path(workdir)
        else:
            self.workdir = Path(workdir).resolve()
        if self._is_install_owned_path(self.workdir):
            raise ValueError("JSON 工作区不能位于程序安装目录内。")
        self.controller = EditorController(self)
        self.controller.set_workspace_root(self.workdir)
        self.validation_cache: dict[str, list] = {}
        self.csv_dialog = CsvPreviewDialog(self.controller.schema, self)
        self.export_csv_dialog = ExportCsvDialog(self)
        self.file_directory_dialog = FileDirectoryDialog(self)
        self.file_search_edit = self.file_directory_dialog.search_edit
        self.file_list = self.file_directory_dialog.list_widget
        self.refresh_button = self.file_directory_dialog.refresh_button
        self.new_button = self.file_directory_dialog.new_button
        self.delete_button = self.file_directory_dialog.delete_button
        self.node_directory_dialog: NodeDirectoryDialog | None = None
        self.performance_dialog: PerformanceToolDialog | None = None
        self._update_host_window: UpdateHostWindow | None = None
        self._auto_save_timer = QTimer(self)
        self._auto_save_timer.setSingleShot(True)
        self._auto_save_timer.timeout.connect(self._run_auto_save)
        self._last_saved_undo_index = 0
        self._has_saved_snapshot = False
        self._last_paste_payload: bytes | None = None
        self._paste_repeat_count = 0
        self._collapsed_groups: set[str] = set()
        self._pending_group_dir = ""
        self._document_sessions: dict[str, dict[str, object]] = {}
        self._current_session_key: str | None = None
        self._connected_undo_stack: QUndoStack | None = None
        self._refresh_file_list_after_save = False
        self.svn_commit_dialog: SvnCommitDialog | None = None
        self._update_client: UpdateClient | None = None
        self._update_progress: QProgressDialog | None = None
        self._update_check_is_manual = False
        self._pending_update_manifest: dict[str, object] | None = None
        self._approved_update_exit = False

        self.controller.pathChanged.connect(self._update_window_title)
        self.controller.pathChanged.connect(self._remember_last_opened_document)
        self.controller.selectionChanged.connect(self._update_inspector)
        self.controller.csvPreviewChanged.connect(self._update_csv_preview)
        self.controller.statusMessage.connect(self._show_status)
        self.controller.documentSaved.connect(self._handle_document_saved)
        self.controller.nodeAdded.connect(lambda _uuid: self._refresh_node_list_panel())
        self.controller.nodeRemoved.connect(lambda _uuid: self._refresh_node_list_panel())
        self.controller.nodeUpdated.connect(self._handle_node_updated)
        self.controller.documentLoaded.connect(self._refresh_search_results)
        self.controller.documentLoaded.connect(self._refresh_node_list_panel)
        self.controller.documentLoaded.connect(self._refresh_node_directory_dialog)
        self.controller.groupsChanged.connect(self._refresh_node_list_panel)
        self.controller.validationChanged.connect(self._store_validation)
        self.controller.documentStateChanged.connect(self._update_document_state)
        self.controller.globalModeChanged.connect(self._handle_global_mode_changed)
        self.controller.interactionCreationModeChanged.connect(self._handle_interaction_creation_mode_changed)
        self.controller.schemaChanged.connect(self._handle_schema_changed)
        self.controller.metaActionBlocked.connect(self._focus_initial_node_guidance)
        self.controller.editorSettingsChanged.connect(self._handle_editor_settings_changed)
        self._set_active_undo_stack(self.controller.undo_stack)
        self._shortcut_actions: dict[str, QAction] = {}
        self._shortcut_defaults: dict[str, QKeySequence] = {}

        self.setWindowTitle(PRODUCT_NAME)
        self.resize(1680, 980)
        self._build_ui()
        self._build_actions()
        self._apply_ui_theme(self.theme_mode, persist=False)
        self._build_hidden_inspector_compat()
        self.file_search_edit.textChanged.connect(self._refresh_file_list)
        self.file_list.itemClicked.connect(self._handle_file_list_item_clicked)
        self.file_list.itemDoubleClicked.connect(self._open_selected_file)
        self.refresh_button.clicked.connect(self._refresh_file_list)
        self.new_button.clicked.connect(self._create_new_file)
        self.delete_button.clicked.connect(self._delete_selected_file)
        self.file_directory_dialog.open_button.clicked.connect(self._open_selected_file_from_dialog)
        self._apply_saved_preferences()
        restored_last_document = self._restore_last_opened_document()
        self._refresh_file_list()
        self._refresh_node_list_panel()
        if not restored_last_document:
            self._mark_saved_checkpoint(saved=False)
        self._update_window_title(self.controller.document.path)
        self._update_inspector(None)
        self._sync_undo_actions()
        QTimer.singleShot(1500, self._maybe_check_updates)

    def _resolved_workspace_path(self, default: str | Path) -> Path:
        default_path = Path(default).resolve()
        raw = self.settings.value(self.SETTINGS_WORKSPACE_ROOT)
        if raw is None or raw == "":
            return default_path
        candidate = Path(str(raw).strip())
        if candidate.is_dir() and not self._is_install_owned_path(candidate):
            return candidate.resolve()
        return default_path

    def _remember_last_opened_document(self, path: str | None) -> None:
        if not path:
            return
        candidate = Path(path)
        if candidate.is_file():
            self.settings.setValue(self.SETTINGS_LAST_DOCUMENT, str(candidate.resolve()))
            self.settings.sync()

    def _restore_last_opened_document(self) -> bool:
        raw = self.settings.value(self.SETTINGS_LAST_DOCUMENT)
        if raw in (None, ""):
            return False
        candidate = Path(str(raw)).resolve()
        try:
            candidate.relative_to(self.workdir.resolve())
        except Exception:
            return False
        if not candidate.is_file():
            return False
        try:
            self._open_existing_session_or_file(candidate)
        except Exception:
            return False
        return True

    def _choose_workspace_directory(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "选择 JSON 配置文件所在目录", str(self.workdir))
        if not chosen:
            return
        new_root = Path(chosen).resolve()
        if self._is_install_owned_path(new_root):
            self._warn_install_owned_workspace()
            return
        if new_root == self.workdir.resolve():
            if not self.settings.contains(self.SETTINGS_WORKSPACE_ROOT):
                self.settings.setValue(self.SETTINGS_WORKSPACE_ROOT, str(self.workdir))
                self.settings.sync()
            return
        if not self._ensure_safe_before_workspace_change():
            return
        self.workdir = new_root
        self.controller.set_workspace_root(self.workdir)
        self.settings.setValue(self.SETTINGS_WORKSPACE_ROOT, str(self.workdir))
        self.settings.remove(self.SETTINGS_LAST_DOCUMENT)
        self.settings.sync()
        self._document_sessions.clear()
        self._current_session_key = None
        self._create_blank_document_session()
        self._refresh_file_list()
        self._refresh_node_list_panel()

    def _is_install_owned_path(self, path: str | Path) -> bool:
        return is_path_within_install_root(Path(path).expanduser().resolve(), self.install_root)

    def _warn_install_owned_workspace(self) -> None:
        QMessageBox.warning(
            self,
            "工作区位置不安全",
            "不能把 JSON 工作区放在程序安装目录内；"
            "为保护其中的数据，安装器会拒绝更新或卸载。"
            "\n请选择“文档”等安装目录以外的位置。",
        )

    def _warn_install_owned_json(self) -> None:
        QMessageBox.warning(
            self,
            "文件位置不安全",
            "不能直接打开程序安装目录内的 JSON 文件；"
            "为保护其中的数据，安装器会拒绝更新或卸载。"
            "\n请先把文件移到“文档”等安装目录以外的位置。",
        )

    def _open_workspace_directory(self) -> None:
        if not self.workdir.exists():
            QMessageBox.warning(self, "无法打开", "当前工作目录不存在。")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.workdir))):
            QMessageBox.warning(self, "无法打开", str(self.workdir))

    def _ensure_safe_before_workspace_change(self) -> bool:
        self._auto_save_timer.stop()
        if not self._is_dirty():
            return True
        close_policy = str(os.environ.get("L2D_CONFIG_EDITOR_TEST_CLOSE_POLICY") or "").strip().lower()
        if close_policy == "save":
            if self.controller.document.path:
                return bool(self._save_current_file(silent=True, allow_incomplete=True))
            return True
        if close_policy in {"discard", "ignore"} or os.environ.get("L2D_CONFIG_EDITOR_NO_CLOSE_PROMPT") == "1":
            return True
        box = QMessageBox(self)
        box.setWindowTitle("保存当前更改")
        box.setText("切换工程目录前，是否保存当前文档的更改？")
        save_button = box.addButton("保存", QMessageBox.ButtonRole.AcceptRole)
        discard_button = box.addButton("不保存", QMessageBox.ButtonRole.DestructiveRole)
        cancel_button = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked == save_button:
            return bool(self._save_current_file(silent=False, allow_incomplete=True))
        if clicked == discard_button:
            return True
        return False

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("appRoot")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        self.top_toolbar = self._build_top_toolbar()
        layout.addWidget(self.top_toolbar)

        horizontal = QSplitter(Qt.Orientation.Horizontal)
        horizontal.setChildrenCollapsible(False)
        layout.addWidget(horizontal)

        horizontal.addWidget(self._build_file_panel())
        horizontal.addWidget(self._build_canvas_panel())
        horizontal.setStretchFactor(0, 0)
        horizontal.setStretchFactor(1, 1)

        self.setCentralWidget(root)
        self.setStatusBar(QStatusBar(self))

    def _build_hidden_inspector_compat(self) -> None:
        self._inspector_compat_host = QWidget(self)
        self._inspector_compat_host.hide()
        compat_layout = QVBoxLayout(self._inspector_compat_host)
        compat_layout.setContentsMargins(0, 0, 0, 0)
        compat_layout.setSpacing(0)
        self.inspector_meta = QLabel("", self._inspector_compat_host)
        self.inspector_placeholder = QLabel("请选择一个节点", self._inspector_compat_host)
        self.inspector_form = NodeFormWidget(self.controller.schema, inline=False, parent=self._inspector_compat_host)
        self.inspector_form.fieldCommitted.connect(self._commit_inspector_field)
        self.inspector_form.fieldsCommitted.connect(self._commit_inspector_fields)
        self.validation_summary = ValidationSummaryWidget(self._inspector_compat_host)
        self.validation_summary.jumpRequested.connect(self._jump_to_validation_node)
        compat_layout.addWidget(self.inspector_meta)
        compat_layout.addWidget(self.inspector_placeholder)
        compat_layout.addWidget(self.inspector_form)
        compat_layout.addWidget(self.validation_summary)
        self.inspector_meta.hide()
        self.inspector_placeholder.hide()
        self.inspector_form.hide()
        self.validation_summary.hide()

    def _build_top_toolbar(self) -> QToolBar:
        toolbar = QToolBar("画布工具栏", self)
        toolbar.setObjectName("topControlBar")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)

        rule_widget = QWidget(toolbar)
        rule_layout = QHBoxLayout(rule_widget)
        rule_layout.setContentsMargins(0, 0, 0, 0)
        rule_layout.setSpacing(8)
        rule_layout.addWidget(QLabel("创建规则"))
        self.auto_create_rule_radio = QRadioButton("自动")
        self.manual_create_rule_radio = QRadioButton("手动")
        self.create_rule_button_group = QButtonGroup(self)
        self.create_rule_button_group.setExclusive(True)
        self.create_rule_button_group.addButton(self.auto_create_rule_radio)
        self.create_rule_button_group.addButton(self.manual_create_rule_radio)
        self.auto_create_rule_radio.toggled.connect(lambda checked: checked and self.controller.set_interaction_creation_mode("auto"))
        self.manual_create_rule_radio.toggled.connect(lambda checked: checked and self.controller.set_interaction_creation_mode("manual"))
        rule_layout.addWidget(self.auto_create_rule_radio)
        rule_layout.addWidget(self.manual_create_rule_radio)
        toolbar.addWidget(rule_widget)

        toolbar.addSeparator()

        self.numeric_linkage_checkbox = QCheckBox("数值联动")
        self.numeric_linkage_checkbox.toggled.connect(self._toggle_numeric_linkage)
        toolbar.addWidget(self.numeric_linkage_checkbox)

        self.pen_mode_checkbox = QCheckBox("画笔")
        self.pen_mode_checkbox.setToolTip("开启后：Ctrl+左键自由绘制，Ctrl+右键删除整条线")
        self.pen_mode_checkbox.toggled.connect(lambda checked: self.canvas.set_pen_mode(checked))
        toolbar.addWidget(self.pen_mode_checkbox)
        self.pen_color_button = QPushButton("颜色")
        self.pen_color_button.clicked.connect(self._choose_pen_color)
        toolbar.addWidget(self.pen_color_button)
        self.pen_width_combo = QComboBox()
        self.pen_width_combo.addItem("细", 2.0)
        self.pen_width_combo.addItem("中", 4.0)
        self.pen_width_combo.addItem("粗", 8.0)
        self.pen_width_combo.currentIndexChanged.connect(self._apply_pen_controls)
        toolbar.addWidget(self.pen_width_combo)
        self.concise_mode_checkbox = QCheckBox("简洁展示")
        self.concise_mode_checkbox.toggled.connect(self._toggle_concise_display)
        toolbar.addWidget(self.concise_mode_checkbox)
        self.concise_settings_button = QPushButton("简洁设置")
        self.concise_settings_button.clicked.connect(self._show_concise_settings)
        toolbar.addWidget(self.concise_settings_button)

        toolbar.addSeparator()

        self.restore_layout_button = QPushButton("还原视角")
        self.restore_layout_button.clicked.connect(self._restore_canvas_layout)
        toolbar.addWidget(self.restore_layout_button)

        self.optimize_layout_button = QPushButton("优化连线")
        self.optimize_layout_button.clicked.connect(self._optimize_connection_layout)
        toolbar.addWidget(self.optimize_layout_button)
        self.group_selected_button = QPushButton("打组")
        self.group_selected_button.clicked.connect(self._group_selected_nodes)
        toolbar.addWidget(self.group_selected_button)

        self.file_directory_button = QPushButton("配置文件")
        self.file_directory_button.clicked.connect(self._show_file_directory_dialog)
        toolbar.addWidget(self.file_directory_button)

        self.create_base_templates_button = QPushButton("创建配置底座")
        self.create_base_templates_button.clicked.connect(self._show_batch_template_dialog)
        toolbar.addWidget(self.create_base_templates_button)

        self.svn_commit_button = QPushButton("提交当前 JSON 到 SVN")
        self.svn_commit_button.setToolTip("自动保存当前文件，必要时执行 svn add，然后提交")
        self.svn_commit_button.clicked.connect(self._commit_current_json_to_svn)
        toolbar.addWidget(self.svn_commit_button)

        return toolbar

    def _create_card(self, object_name: str = "filePanelCard") -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName(object_name)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        return card, layout

    def _build_file_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("filePanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        list_card, list_layout = self._create_card()
        list_eyebrow = QLabel("\u8282\u70b9")
        list_eyebrow.setObjectName("sectionEyebrow")
        list_layout.addWidget(list_eyebrow)
        self.node_search_edit = QLineEdit()
        self.node_search_edit.setPlaceholderText("\u641c\u7d22\u5f53\u524d\u56fe\u5185\u8282\u70b9")
        self.node_search_edit.textChanged.connect(self._refresh_node_list_panel)
        list_layout.addWidget(self.node_search_edit)

        self.node_list = QTreeWidget()
        self.node_list.setObjectName("nodeDirectoryList")
        self.node_list.setHeaderHidden(True)
        self.node_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.node_list.itemClicked.connect(self._focus_node_from_tree_item)
        self.node_list.itemActivated.connect(self._focus_node_from_tree_item)
        list_layout.addWidget(self.node_list, 1)
        layout.addWidget(list_card, 1)
        panel.setMinimumWidth(290)
        return panel

    def _build_canvas_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("canvasPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.canvas = NodeCanvasView(self.controller.schema, self.controller)
        self.canvas.selectionSummaryChanged.connect(self._handle_selection_summary)
        self.canvas.interactionBusyChanged.connect(self._handle_canvas_busy_changed)
        layout.addWidget(self.canvas, 1)

        self.search_panel = QFrame(self, Qt.WindowType.Popup)
        self.search_panel.setObjectName("searchPanel")
        self.search_panel.setFixedWidth(620)
        search_layout = QVBoxLayout(self.search_panel)
        search_layout.setContentsMargins(12, 12, 12, 12)
        search_layout.setSpacing(6)
        search_row = QHBoxLayout()
        search_title = QLabel("\u641c\u7d22")
        search_title.setObjectName("searchTitle")
        self.search_edit = QLineEdit()
        self.search_edit.setMinimumWidth(360)
        self.search_edit.setPlaceholderText("\u8f93\u5165\u5b57\u6bb5\u503c\uff0c\u4f8b\u5982 idle=13")
        self.search_edit.textChanged.connect(self._refresh_search_results)
        search_row.addWidget(search_title)
        search_row.addWidget(self.search_edit, 1)
        self.search_results = QListWidget()
        self.search_results.setMaximumHeight(180)
        self.search_results.itemClicked.connect(self._jump_to_search_result)
        self.search_results.hide()
        search_layout.addLayout(search_row)
        search_layout.addWidget(self.search_results)
        self.search_panel.hide()
        return panel

    def _build_inspector_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("inspectorPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.control_panel = QFrame()
        self.control_panel.setObjectName("sidebarToolPanel")
        control_layout = QVBoxLayout(self.control_panel)
        control_layout.setContentsMargins(12, 12, 12, 12)
        control_layout.setSpacing(12)

        mode_block = QVBoxLayout()
        mode_block.setContentsMargins(0, 0, 0, 0)
        mode_block.setSpacing(6)
        mode_eyebrow = QLabel("\u7f16\u8f91")
        mode_eyebrow.setObjectName("sectionEyebrow")
        mode_block.addWidget(mode_eyebrow)

        sequence_rule_label = QLabel("\u4e92\u52a8\u5e8f\u53f7\u521b\u5efa\u89c4\u5219")
        sequence_rule_label.setObjectName("searchTitle")
        mode_block.addWidget(sequence_rule_label)
        sequence_rule_row = QHBoxLayout()
        sequence_rule_row.setContentsMargins(0, 0, 0, 0)
        sequence_rule_row.setSpacing(8)
        self.auto_create_rule_radio = QRadioButton("\u81ea\u52a8")
        self.manual_create_rule_radio = QRadioButton("\u624b\u52a8")
        self.create_rule_button_group = QButtonGroup(self)
        self.create_rule_button_group.setExclusive(True)
        self.create_rule_button_group.addButton(self.auto_create_rule_radio)
        self.create_rule_button_group.addButton(self.manual_create_rule_radio)
        self.auto_create_rule_radio.toggled.connect(lambda checked: checked and self.controller.set_interaction_creation_mode("auto"))
        self.manual_create_rule_radio.toggled.connect(lambda checked: checked and self.controller.set_interaction_creation_mode("manual"))
        sequence_rule_row.addWidget(self.auto_create_rule_radio)
        sequence_rule_row.addWidget(self.manual_create_rule_radio)
        sequence_rule_row.addStretch(1)
        mode_block.addLayout(sequence_rule_row)
        self.numeric_linkage_checkbox = QCheckBox("数值联动")
        self.numeric_linkage_checkbox.toggled.connect(self._toggle_numeric_linkage)
        mode_block.addWidget(self.numeric_linkage_checkbox)
        control_layout.addLayout(mode_block)

        actions_block = QVBoxLayout()
        actions_block.setContentsMargins(0, 0, 0, 0)
        actions_block.setSpacing(6)
        actions_label = QLabel("\u5de5\u5177")
        actions_label.setObjectName("searchTitle")
        actions_block.addWidget(actions_label)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(8)
        self.restore_layout_button = QPushButton("\u8fd8\u539f\u89c6\u89d2")
        self.restore_layout_button.clicked.connect(self._restore_canvas_layout)
        self.optimize_layout_button = QPushButton("\u4f18\u5316\u8fde\u7ebf")
        self.optimize_layout_button.clicked.connect(self._optimize_connection_layout)
        self.file_directory_button = QPushButton("配置文件")
        self.file_directory_button.clicked.connect(self._show_file_directory_dialog)
        action_row.addWidget(self.restore_layout_button)
        action_row.addWidget(self.optimize_layout_button)
        action_row.addWidget(self.file_directory_button)
        actions_block.addLayout(action_row)

        action_hint = QLabel("\u4f18\u5316\u8fde\u7ebf\u53ea\u4f1a\u6574\u7406\u5df2\u8fde\u7ebf\u8282\u70b9\u7684\u4f4d\u7f6e\uff0c\u4e0d\u4f1a\u6539\u52a8\u8282\u70b9\u5b57\u6bb5\u548c\u8fde\u7ebf\u5173\u7cfb\u3002")
        action_hint.setObjectName("sidebarHint")
        action_hint.setWordWrap(True)
        actions_block.addWidget(action_hint)
        help_row = QHBoxLayout()
        self.help_doc_button = QPushButton("使用说明")
        self.help_doc_button.clicked.connect(self._open_help_page)
        self.changelog_button = QPushButton("更新日志")
        self.changelog_button.clicked.connect(self._open_changelog_page)
        help_row.addWidget(self.help_doc_button)
        help_row.addWidget(self.changelog_button)
        actions_block.addLayout(help_row)
        control_layout.addLayout(actions_block)

        layout.addWidget(self.control_panel)

        self.inspector_shell = QFrame()
        self.inspector_shell.setObjectName("inspectorShell")
        inspector_layout = QVBoxLayout(self.inspector_shell)
        inspector_layout.setContentsMargins(12, 12, 12, 12)
        inspector_layout.setSpacing(10)

        inspector_eyebrow = QLabel("Inspector")
        inspector_eyebrow.setObjectName("sectionEyebrow")
        inspector_layout.addWidget(inspector_eyebrow)
        title = QLabel("Inspector")
        title.setObjectName("sectionTitle")
        inspector_layout.addWidget(title)
        self.inspector_meta = QLabel("")
        self.inspector_meta.setObjectName("inspectorMeta")
        self.inspector_meta.setWordWrap(True)
        inspector_layout.addWidget(self.inspector_meta)
        self.inspector_placeholder = QLabel("\u8bf7\u9009\u62e9\u4e00\u4e2a\u8282\u70b9")
        self.inspector_placeholder.setObjectName("inspectorPlaceholder")
        self.inspector_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.inspector_form = NodeFormWidget(self.controller.schema, inline=False)
        self.inspector_form.fieldCommitted.connect(self._commit_inspector_field)
        self.inspector_form.fieldsCommitted.connect(self._commit_inspector_fields)
        self.validation_summary = ValidationSummaryWidget()
        inspector_body = QWidget()
        body_layout = QVBoxLayout(inspector_body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(10)
        body_layout.addWidget(self.inspector_form)
        body_layout.addWidget(self.validation_summary)
        body_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inspector_body)
        inspector_layout.addWidget(self.inspector_placeholder)
        inspector_layout.addWidget(scroll, 1)
        layout.addWidget(self.inspector_shell, 1)
        panel.setMinimumWidth(380)
        return panel

    def _build_actions(self) -> None:
        self.file_menu = self.menuBar().addMenu("文件")
        self.edit_menu = self.menuBar().addMenu("编辑")
        self.view_menu = self.menuBar().addMenu("视图")
        self.tools_menu = self.menuBar().addMenu("工具")
        self.help_menu = self.menuBar().addMenu("帮助")
        file_menu = self.file_menu
        edit_menu = self.edit_menu
        view_menu = self.view_menu
        tools_menu = self.tools_menu
        help_menu = self.help_menu

        self.appearance_menu = view_menu.addMenu("外观")
        appearance_menu = self.appearance_menu
        self.theme_action_group = QActionGroup(self)
        self.theme_action_group.setExclusive(True)
        self.dark_theme_action = QAction("夜间模式", self, checkable=True)
        self.light_theme_action = QAction("白天模式", self, checkable=True)
        self.theme_action_group.addAction(self.dark_theme_action)
        self.theme_action_group.addAction(self.light_theme_action)
        appearance_menu.addAction(self.dark_theme_action)
        appearance_menu.addAction(self.light_theme_action)
        self.dark_theme_action.triggered.connect(lambda checked: checked and self._apply_ui_theme(ThemeMode.DARK))
        self.light_theme_action.triggered.connect(lambda checked: checked and self._apply_ui_theme(ThemeMode.LIGHT))

        open_action = QAction("\u6253\u5f00", self)
        open_action.triggered.connect(self._open_dialog)
        file_menu.addAction(open_action)
        self._register_shortcut_action("open", open_action, QKeySequence(QKeySequence.StandardKey.Open))

        self.save_action = QAction("\u4fdd\u5b58", self)
        self.save_action.triggered.connect(self._save_current_file)
        file_menu.addAction(self.save_action)
        self._register_shortcut_action("save", self.save_action, QKeySequence(QKeySequence.StandardKey.Save))

        file_menu.addSeparator()
        change_workspace_action = QAction("更改工作区…", self)
        change_workspace_action.triggered.connect(self._choose_workspace_directory)
        file_menu.addAction(change_workspace_action)
        open_workspace_action = QAction("在资源管理器中打开工作区", self)
        open_workspace_action.triggered.connect(self._open_workspace_directory)
        file_menu.addAction(open_workspace_action)

        self.export_csv_action = QAction("\u5bfc\u51fa\u5230 CSV", self)
        self.export_csv_action.triggered.connect(self._show_export_csv_dialog)
        self.menuBar().addAction(self.export_csv_action)
        self._register_shortcut_action("export_csv", self.export_csv_action, QKeySequence())

        template_create_action = QAction("批量创建配置底座…", self)
        template_create_action.triggered.connect(self._show_batch_template_dialog)
        tools_menu.addAction(template_create_action)
        self._register_shortcut_action("template_create", template_create_action, QKeySequence())
        reference_image_action = QAction("添加参考图…", self)
        reference_image_action.triggered.connect(self._add_reference_image_from_file)
        tools_menu.addAction(reference_image_action)
        self._register_shortcut_action("add_reference_image", reference_image_action, QKeySequence())
        update_host_action = QAction("局域网更新主机…", self)
        update_host_action.triggered.connect(self._open_update_host)
        tools_menu.addAction(update_host_action)
        performance_action = QAction("性能测试工具", self)
        performance_action.triggered.connect(self._open_performance_tool)
        tools_menu.addAction(performance_action)
        self._register_shortcut_action("performance_tool", performance_action, QKeySequence("Ctrl+Shift+P"))

        reload_schema_action = QAction("\u91cd\u8f7d\u5b57\u6bb5\u914d\u7f6e", self)
        reload_schema_action.triggered.connect(self._reload_schema)
        file_menu.addAction(reload_schema_action)
        self._register_shortcut_action("reload_schema", reload_schema_action, QKeySequence())

        self.undo_action = QAction("\u64a4\u9500", self)
        self.undo_action.triggered.connect(self._trigger_undo)
        edit_menu.addAction(self.undo_action)
        self._register_shortcut_action("undo", self.undo_action, QKeySequence(QKeySequence.StandardKey.Undo))

        self.redo_action = QAction("\u91cd\u505a", self)
        self.redo_action.triggered.connect(self._trigger_redo)
        edit_menu.addAction(self.redo_action)
        self._register_shortcut_action("redo", self.redo_action, QKeySequence(QKeySequence.StandardKey.Redo))

        copy_action = QAction("\u590d\u5236", self)
        copy_action.triggered.connect(self._copy_selection)
        edit_menu.addAction(copy_action)
        self._register_shortcut_action("copy", copy_action, QKeySequence(QKeySequence.StandardKey.Copy))

        paste_action = QAction("\u7c98\u8d34", self)
        paste_action.triggered.connect(self._paste_selection)
        edit_menu.addAction(paste_action)
        self._register_shortcut_action("paste", paste_action, QKeySequence(QKeySequence.StandardKey.Paste))

        duplicate_action = QAction("\u590d\u5236\u8282\u70b9", self)
        duplicate_action.triggered.connect(self._duplicate_selection)
        edit_menu.addAction(duplicate_action)
        self._register_shortcut_action("duplicate", duplicate_action, QKeySequence("Ctrl+D"))

        delete_action = QAction("\u5220\u9664", self)
        delete_action.triggered.connect(self._delete_selection)
        edit_menu.addAction(delete_action)
        self._register_shortcut_action("delete", delete_action, QKeySequence(QKeySequence.StandardKey.Delete))

        search_action = QAction("\u641c\u7d22\u8282\u70b9", self)
        search_action.triggered.connect(self._focus_search)
        view_menu.addAction(search_action)
        self._register_shortcut_action("search_nodes", search_action, QKeySequence(QKeySequence.StandardKey.Find))

        file_search_action = QAction("\u641c\u7d22\u914d\u7f6e\u6587\u4ef6", self)
        file_search_action.triggered.connect(self._focus_file_search)
        view_menu.addAction(file_search_action)
        self._register_shortcut_action("search_files", file_search_action, QKeySequence("Ctrl+Shift+F"))

        layout_action = QAction("\u4f18\u5316\u8fde\u7ebf", self)
        layout_action.triggered.connect(self._optimize_connection_layout)
        view_menu.addAction(layout_action)
        self._register_shortcut_action("optimize_layout", layout_action, QKeySequence("Ctrl+L"))

        restore_action = QAction("\u8fd8\u539f\u89c6\u89d2", self)
        restore_action.triggered.connect(self._restore_canvas_layout)
        view_menu.addAction(restore_action)
        self._register_shortcut_action("restore_view", restore_action, QKeySequence("Shift+R"))

        self.concise_action = QAction("简洁展示", self, checkable=True)
        self.concise_action.toggled.connect(self._toggle_concise_display)
        view_menu.addAction(self.concise_action)
        concise_settings_action = QAction("简洁展示设置…", self)
        concise_settings_action.triggered.connect(self._show_concise_settings)
        view_menu.addAction(concise_settings_action)

        csv_action = QAction("CSV \u9884\u89c8", self)
        csv_action.triggered.connect(self._show_csv_preview)
        view_menu.addAction(csv_action)
        self._register_shortcut_action("csv_preview", csv_action, QKeySequence())

        node_directory_action = QAction("节点目录", self)
        node_directory_action.triggered.connect(self._show_node_directory_dialog)
        view_menu.addAction(node_directory_action)
        self._register_shortcut_action("node_directory", node_directory_action, QKeySequence("Ctrl+Shift+G"))
        group_action = QAction("打组", self)
        group_action.triggered.connect(self._group_selected_nodes)
        edit_menu.addAction(group_action)
        self._register_shortcut_action("group_selection", group_action, QKeySequence("Ctrl+G"))

        focus_selected_action = QAction("定位当前节点", self)
        focus_selected_action.triggered.connect(self._focus_selected_node)
        view_menu.addAction(focus_selected_action)
        self._register_shortcut_action("focus_selected", focus_selected_action, QKeySequence("F"))

        shortcut_settings_action = QAction("快捷键设置", self)
        shortcut_settings_action.triggered.connect(self._show_shortcut_settings_dialog)
        edit_menu.addAction(shortcut_settings_action)
        self._register_shortcut_action("shortcut_settings", shortcut_settings_action, QKeySequence())

        self.debug_json_fields_action = QAction("\u8c03\u8bd5\u6a21\u5f0f\uff1a\u663e\u793a JSON \u5b57\u6bb5\u540d", self, checkable=True)
        self.debug_json_fields_action.toggled.connect(self._set_debug_json_field_names)
        view_menu.addAction(self.debug_json_fields_action)

        help_doc_action = QAction("使用说明", self)
        help_doc_action.triggered.connect(self._open_help_page)
        help_menu.addAction(help_doc_action)
        self._register_shortcut_action("help_docs", help_doc_action, QKeySequence("F1"))

        changelog_action = QAction("更新日志", self)
        changelog_action.triggered.connect(self._open_changelog_page)
        help_menu.addAction(changelog_action)
        self._register_shortcut_action("help_changelog", changelog_action, QKeySequence("Ctrl+F1"))

        help_menu.addSeparator()
        update_settings_action = QAction("更新设置…", self)
        update_settings_action.triggered.connect(self._configure_update_host)
        help_menu.addAction(update_settings_action)
        check_update_action = QAction("检查更新…", self)
        check_update_action.triggered.connect(lambda: self._check_for_updates(manual=True))
        help_menu.addAction(check_update_action)
        reinstall_action = QAction("重装上一版本…", self)
        reinstall_action.triggered.connect(self._reinstall_previous_version)
        help_menu.addAction(reinstall_action)
        help_menu.addSeparator()
        about_action = QAction(f"关于 {PRODUCT_NAME}", self)
        about_action.triggered.connect(self._show_about_dialog)
        help_menu.addAction(about_action)

    def _register_shortcut_action(self, action_id: str, action: QAction, default_sequence: QKeySequence) -> None:
        self._shortcut_actions[action_id] = action
        self._shortcut_defaults[action_id] = QKeySequence(default_sequence)
        self.addAction(action)
        setting_key = f"shortcuts/{action_id}"
        stored = self.settings.value(setting_key)
        if isinstance(stored, str):
            action.setShortcut(QKeySequence(stored))
        else:
            action.setShortcut(default_sequence)

    def _wheel_shortcut_settings(self) -> dict[str, str]:
        return {
            "zoom_modifier": str(self.settings.value("wheel/zoom_modifier", "ctrl")),
            "horizontal_modifier": str(self.settings.value("wheel/horizontal_modifier", "alt_shift")),
        }

    def _apply_wheel_settings(self, settings: dict[str, str]) -> None:
        self.canvas.zoom_wheel_modifier = settings.get("zoom_modifier", "ctrl")
        self.canvas.horizontal_wheel_modifier = settings.get("horizontal_modifier", "alt_shift")

    def _show_shortcut_settings_dialog(self) -> None:
        shortcuts = {
            action_id: (
                action.text(),
                action.shortcut(),
            )
            for action_id, action in self._shortcut_actions.items()
        }
        dialog = ShortcutConfigDialog(shortcuts, self._shortcut_defaults, self._wheel_shortcut_settings(), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        shortcut_values, wheel_values = dialog.configuration()
        for action_id, value in shortcut_values.items():
            self.settings.setValue(f"shortcuts/{action_id}", value)
            if action_id in self._shortcut_actions:
                self._shortcut_actions[action_id].setShortcut(QKeySequence(value))
        self.settings.setValue("wheel/zoom_modifier", wheel_values["zoom_modifier"])
        self.settings.setValue("wheel/horizontal_modifier", wheel_values["horizontal_modifier"])
        self._apply_wheel_settings(wheel_values)
        self.settings.sync()

    def _trigger_undo(self) -> None:
        if self.controller.undo_stack.canUndo():
            self.controller.undo_stack.undo()

    def _trigger_redo(self) -> None:
        if self.controller.undo_stack.canRedo():
            self.controller.undo_stack.redo()

    def _sync_undo_actions(self, *_args) -> None:
        if hasattr(self, "undo_action"):
            self.undo_action.setEnabled(self.controller.undo_stack.canUndo())
        if hasattr(self, "redo_action"):
            self.redo_action.setEnabled(self.controller.undo_stack.canRedo())

    def _set_active_undo_stack(self, stack: QUndoStack) -> None:
        if self._connected_undo_stack is stack:
            self._sync_undo_actions()
            return
        if self._connected_undo_stack is not None:
            try:
                self._connected_undo_stack.indexChanged.disconnect(self._handle_undo_index_changed)
            except TypeError:
                pass
            try:
                self._connected_undo_stack.canUndoChanged.disconnect(self._sync_undo_actions)
            except TypeError:
                pass
            try:
                self._connected_undo_stack.canRedoChanged.disconnect(self._sync_undo_actions)
            except TypeError:
                pass
        self._connected_undo_stack = stack
        stack.indexChanged.connect(self._handle_undo_index_changed)
        stack.canUndoChanged.connect(self._sync_undo_actions)
        stack.canRedoChanged.connect(self._sync_undo_actions)
        self._sync_undo_actions()

    @staticmethod
    def _session_key_for_path(path: str | Path | None) -> str | None:
        if not path:
            return None
        return str(Path(path).resolve())

    def _stash_current_document_session(self) -> None:
        key = self._session_key_for_path(self.controller.document.path)
        if not key:
            self._current_session_key = None
            return
        self._document_sessions[key] = {
            "document": self.controller.document,
            "undo_stack": self.controller.undo_stack,
            "last_saved_undo_index": self._last_saved_undo_index,
            "has_saved_snapshot": self._has_saved_snapshot,
            "group_dir": self._pending_group_dir,
        }
        self._current_session_key = key

    def _switch_to_document(self, document, *, undo_stack: QUndoStack, saved: bool, session_key: str | None, group_dir: str = "") -> None:
        self._auto_save_timer.stop()
        self.controller.document = document
        self.controller.undo_stack = undo_stack
        self.controller.selected_node_uuid = None
        # Global simple/advanced UI was removed. Keep the legacy document field
        # readable, but use one deterministic compatibility mode internally.
        self.controller.preferences.global_mode = "simple"
        self._pending_group_dir = group_dir
        self._current_session_key = session_key
        self._set_active_undo_stack(undo_stack)
        self._mark_saved_checkpoint(saved=saved)
        self.controller.globalModeChanged.emit(self.controller.preferences.global_mode)
        self.controller.interactionCreationModeChanged.emit(self.controller.document.interaction_creation_mode)
        self.controller.documentLoaded.emit()
        self.controller.pathChanged.emit(document.path)
        self.controller.refresh_derived()
        self._sync_undo_actions()

    def _open_existing_session_or_file(self, path: str | Path) -> None:
        candidate = Path(path).expanduser().resolve()
        if self._is_install_owned_path(candidate):
            raise ValueError(
                "不能打开程序安装目录内的 JSON 文件；请先把文件移到安装目录以外的位置。"
            )
        path = candidate
        session_key = self._session_key_for_path(path)
        current_key = self._session_key_for_path(self.controller.document.path)
        if current_key and current_key != session_key:
            self._stash_current_document_session()
        session = self._document_sessions.get(session_key or "")
        if session:
            self._switch_to_document(
                session["document"],
                undo_stack=session["undo_stack"],
                saved=bool(session.get("has_saved_snapshot", False)),
                session_key=session_key,
                group_dir=str(session.get("group_dir") or Path(path).parent.name),
            )
            self._last_saved_undo_index = int(session.get("last_saved_undo_index", 0))
            self._update_window_title(self.controller.document.path)
            return
        document = load_document(self.controller.schema, path)
        undo_stack = QUndoStack(self)
        try:
            group_dir = str(Path(path).resolve().parent.relative_to(self.workdir.resolve())).replace("\\", "/")
        except Exception:
            group_dir = ""
        if group_dir == ".":
            group_dir = ""
        self._switch_to_document(
            document,
            undo_stack=undo_stack,
            saved=True,
            session_key=session_key,
            group_dir=group_dir,
        )

    def open_external_file(self, path: str | Path) -> bool:
        """Open a path delivered by Windows or a second application instance."""

        candidate = Path(path).expanduser().resolve()
        if self._is_install_owned_path(candidate):
            self._warn_install_owned_json()
            self.activate_from_external_request()
            return False
        if not candidate.is_file() or candidate.suffix.lower() != ".json":
            QMessageBox.warning(self, "无法打开", f"不是可用的 JSON 文件：\n{candidate}")
            self.activate_from_external_request()
            return False
        current = self._session_key_for_path(self.controller.document.path)
        requested = self._session_key_for_path(candidate)
        if current != requested:
            if not self._ensure_safe_to_leave_document(candidate):
                self.activate_from_external_request()
                return False
            try:
                self._stash_current_document_session()
                self._open_existing_session_or_file(candidate)
            except Exception as exc:
                QMessageBox.warning(self, "无法打开", str(exc))
                self.activate_from_external_request()
                return False
            self._refresh_file_list()
            relative_path = self._relative_path_for_document(candidate)
            if relative_path:
                self._select_file_in_list(relative_path)
        self.activate_from_external_request()
        return True

    def activate_from_external_request(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _create_blank_document_session(self, *, group_dir: str = "") -> None:
        document = create_document(self.controller.schema)
        undo_stack = QUndoStack(self)
        self._switch_to_document(document, undo_stack=undo_stack, saved=False, session_key=None, group_dir=group_dir)

    def _sanitize_filename_stem(self, value: str) -> str:
        stem = re.sub(r'[<>:\"/\\\\|?*]+', "_", str(value or "").strip())
        stem = stem.strip(" ._")
        return stem or "config"

    def _generated_save_path(self) -> Path | None:
        allowed, _reason = self.controller.can_create_graph_content()
        if not allowed:
            return None
        group_dir = self._pending_group_dir.strip().replace("\\", "/")
        parent = self.workdir / group_dir if group_dir else self.workdir
        parent.mkdir(parents=True, exist_ok=True)
        base_name = self._sanitize_filename_stem(self.controller.document.meta.CharName or "config")
        candidate = parent / f"{base_name}.json"
        if not candidate.exists():
            return candidate
        index = 2
        while True:
            numbered = parent / f"{base_name}_{index}.json"
            if not numbered.exists():
                return numbered
            index += 1

    def _draft_save_path(self) -> Path:
        candidate = self.workdir / "未完成草稿.json"
        index = 2
        while candidate.exists():
            candidate = self.workdir / f"未完成草稿_{index}.json"
            index += 1
        return candidate

    def _update_save_action_state(self) -> None:
        allowed, _reason = self.controller.can_create_graph_content()
        can_save = bool(self.controller.document.path) or allowed
        if hasattr(self, "save_action"):
            self.save_action.setEnabled(can_save)

    def _apply_saved_preferences(self) -> None:
        self.controller.preferences.global_mode = "simple"
        debug_json_fields = self.settings.value("ui/debug_json_field_names", self.controller.preferences.debug_json_field_names)
        debug_enabled = debug_json_fields in (True, "true", "1", 1)
        self.controller.preferences.debug_json_field_names = bool(debug_enabled)
        self.debug_json_fields_action.setChecked(bool(debug_enabled))
        self._handle_interaction_creation_mode_changed(self.controller.document.interaction_creation_mode)
        self._apply_wheel_settings(self._wheel_shortcut_settings())
        self._handle_editor_settings_changed(self.controller.document.editor_settings)
        pen_color = str(self.settings.value(self.SETTINGS_PEN_COLOR, "#2F80ED") or "#2F80ED")
        try:
            pen_width = float(self.settings.value(self.SETTINGS_PEN_WIDTH, 4.0))
        except (TypeError, ValueError):
            pen_width = 4.0
        width_index = self.pen_width_combo.findData(pen_width)
        self.pen_width_combo.setCurrentIndex(width_index if width_index >= 0 else 1)
        self._set_pen_color_button(pen_color)
        self.canvas.set_pen_style(pen_color, pen_width)
        concise_enabled = self._settings_bool(self.SETTINGS_CONCISE_ENABLED, False)
        fields = self._saved_concise_fields()
        elements = {
            key: self._settings_bool(f"view/concise/elements/{key}", False)
            for key in ("groups", "tables", "images", "strokes")
        }
        self.canvas.set_concise_display(concise_enabled, fields, elements)
        self._sync_concise_controls(concise_enabled)
        self._update_save_action_state()

    def _settings_bool(self, key: str, default: bool) -> bool:
        return self.settings.value(key, default) in (True, "true", "1", 1)

    def _saved_concise_fields(self) -> set[str]:
        default = ["tips", "action_trigger", "action_trigger_active"]
        raw = self.settings.value(self.SETTINGS_CONCISE_FIELDS, default)
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed = [part.strip() for part in raw.split(",") if part.strip()]
            raw = parsed
        if not isinstance(raw, (list, tuple, set)):
            raw = default
        allowed = {"tips", "draw_able_name", "parameter", "action_trigger", "action_trigger_active"}
        return {str(key) for key in raw if str(key) in allowed}

    def _set_pen_color_button(self, color: str) -> None:
        resolved = QColor(color)
        if not resolved.isValid():
            resolved = QColor("#2F80ED")
        foreground = "#101828" if resolved.lightness() > 145 else "#ffffff"
        self.pen_color_button.setProperty("penColor", resolved.name())
        self.pen_color_button.setStyleSheet(
            f"QPushButton {{ background: {resolved.name()}; color: {foreground}; font-weight: 600; }}"
        )

    def _choose_pen_color(self) -> None:
        current = QColor(str(self.pen_color_button.property("penColor") or "#2F80ED"))
        selected = QColorDialog.getColor(current, self, "选择画笔颜色")
        if not selected.isValid():
            return
        self._set_pen_color_button(selected.name())
        self._apply_pen_controls()

    def _apply_pen_controls(self, *_args) -> None:
        color = str(self.pen_color_button.property("penColor") or "#2F80ED")
        width = float(self.pen_width_combo.currentData() or 4.0)
        self.canvas.set_pen_style(color, width)
        self.settings.setValue(self.SETTINGS_PEN_COLOR, color)
        self.settings.setValue(self.SETTINGS_PEN_WIDTH, width)
        self.settings.sync()

    def _sync_concise_controls(self, enabled: bool) -> None:
        for control in (getattr(self, "concise_mode_checkbox", None), getattr(self, "concise_action", None)):
            if control is None:
                continue
            blocked = control.blockSignals(True)
            control.setChecked(bool(enabled))
            control.blockSignals(blocked)

    def _toggle_concise_display(self, enabled: bool) -> None:
        enabled = bool(enabled)
        self._sync_concise_controls(enabled)
        fields = self._saved_concise_fields()
        elements = {
            key: self._settings_bool(f"view/concise/elements/{key}", False)
            for key in ("groups", "tables", "images", "strokes")
        }
        self.canvas.set_concise_display(enabled, fields, elements)
        self.settings.setValue(self.SETTINGS_CONCISE_ENABLED, enabled)
        self.settings.sync()

    def _show_concise_settings(self) -> None:
        fields = self._saved_concise_fields()
        elements = {
            key: self._settings_bool(f"view/concise/elements/{key}", False)
            for key in ("groups", "tables", "images", "strokes")
        }
        dialog = ConciseDisplayDialog(fields, elements, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        fields, elements = dialog.values()
        self.settings.setValue(self.SETTINGS_CONCISE_FIELDS, sorted(fields))
        for key, visible in elements.items():
            self.settings.setValue(f"view/concise/elements/{key}", bool(visible))
        self.settings.sync()
        self.canvas.set_concise_display(self.canvas.concise_enabled, fields, elements)

    def _apply_ui_theme(self, mode: ThemeMode | str, *, persist: bool = True) -> None:
        self.theme_mode = normalize_theme_mode(mode)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(stylesheet_for_theme(self.theme_mode))
        if hasattr(self, "canvas"):
            self.canvas.set_ui_theme(self.theme_mode)
        if hasattr(self, "dark_theme_action"):
            self.dark_theme_action.setChecked(self.theme_mode is ThemeMode.DARK)
            self.light_theme_action.setChecked(self.theme_mode is ThemeMode.LIGHT)
        if persist:
            self.settings.setValue(self.SETTINGS_THEME_MODE, self.theme_mode.value)
            self.settings.sync()

    def _set_debug_json_field_names(self, enabled: bool) -> None:
        self.controller.preferences.debug_json_field_names = enabled
        self.settings.setValue("ui/debug_json_field_names", enabled)
        if self.controller.selected_node_uuid:
            node = self.controller.get_node(self.controller.selected_node_uuid)
            if node:
                self.inspector_form.set_node(
                    node,
                    "advanced",
                    self.controller.preferences.debug_json_field_names,
                )
        for node_uuid in list(self.canvas.node_items):
            self.canvas._update_node_item(node_uuid)
        if self.search_edit.text().strip():
            self._refresh_search_results()

    def _refresh_file_list(self) -> None:
        current = self._relative_path_for_document(self.controller.document.path)
        needle = self.file_search_edit.text().strip().lower() if hasattr(self, "file_search_edit") else ""
        self.file_list.clear()
        grouped: dict[str, list[str]] = {}
        for relative_path in self.controller.file_list():
            _, display_name = self._read_file_display_meta(self.workdir / relative_path)
            haystack = f"{relative_path} {display_name}".lower()
            if needle and needle not in haystack:
                continue
            group = str(Path(relative_path).parent).replace("\\", "/")
            if group == ".":
                group = ""
            grouped.setdefault(group, []).append(relative_path)

        for group_name in sorted(grouped.keys(), key=lambda value: (value != "", value)):
            if current and group_name == str(Path(current).parent).replace("\\", "/").replace(".", ""):
                self._collapsed_groups.discard(group_name)
            paths = sorted(grouped[group_name], key=lambda item: self._read_file_display_meta(self.workdir / item)[1])
            is_collapsed = group_name in self._collapsed_groups
            header = QListWidgetItem(self._group_header_text(group_name, len(paths), is_collapsed))
            header.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            header.setData(Qt.ItemDataRole.UserRole, {"kind": "group", "group_dir": group_name})
            header.setForeground(Qt.GlobalColor.white)
            header.setBackground(Qt.GlobalColor.darkGray)
            header_font = header.font()
            header_font.setBold(True)
            header.setFont(header_font)
            self.file_list.addItem(header)
            if is_collapsed:
                continue
            for relative_path in paths:
                _, display_name = self._read_file_display_meta(self.workdir / relative_path)
                item = QListWidgetItem(display_name)
                item.setData(Qt.ItemDataRole.UserRole, {"kind": "file", "path": relative_path})
                item.setToolTip(relative_path)
                self.file_list.addItem(item)
                if relative_path == current:
                    self.file_list.setCurrentItem(item)

    @staticmethod
    def _group_header_text(group_name: str, count: int, collapsed: bool) -> str:
        arrow = "\u25b6" if collapsed else "\u25bc"
        label = group_name or "\u6839\u76ee\u5f55"
        return f"[\u76ee\u5f55] {arrow} {label} · {count} \u4e2a\u914d\u7f6e"

    def _read_file_display_meta(self, path: Path) -> tuple[str, str]:
        fallback_name = path.name
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return "", fallback_name

        meta = payload.get("meta") if isinstance(payload, dict) else {}
        if not isinstance(meta, dict):
            meta = {}
        char_name = str(meta.get("CharName") or "").strip()
        if not char_name:
            nodes = payload.get("nodes") if isinstance(payload, dict) else []
            if isinstance(nodes, list):
                initial = next((node for node in nodes if isinstance(node, dict) and node.get("type") == "Initial"), None)
                if isinstance(initial, dict):
                    char_name = str(initial.get("CharName") or "").strip()
        display_name = char_name or path.stem
        return char_name, display_name

    def _current_file_relative_path(self) -> str | None:
        item = self.file_list.currentItem()
        if not item:
            return None
        payload = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(payload, dict) and payload.get("kind") == "file":
            path = payload.get("path")
            if isinstance(path, str) and path:
                return path
        return None

    def _current_group_dir(self) -> str:
        item = self.file_list.currentItem()
        if not item:
            return ""
        payload = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(payload, dict):
            if payload.get("kind") == "group":
                return str(payload.get("group_dir") or "")
            if payload.get("kind") == "file":
                return str(Path(str(payload.get("path") or "")).parent).replace("\\", "/").replace(".", "")
        return ""

    def _create_new_file(self) -> None:
        self._show_batch_template_dialog()

    def _rename_selected_file(self) -> None:
        selected_relative = self._current_file_relative_path()
        if not selected_relative:
            return
        old_path = self.workdir / selected_relative
        new_name, ok = QInputDialog.getText(self, "重命名配置", "新文件名", text=old_path.name)
        if not ok or not new_name.strip():
            return
        filename = new_name.strip()
        if not filename.endswith(".json"):
            filename += ".json"
        new_path = old_path.parent / filename
        old_path.rename(new_path)
        if self.controller.document.path == str(old_path):
            self.controller.document.path = str(new_path)
            old_key = self._session_key_for_path(old_path)
            if old_key:
                self._document_sessions.pop(old_key, None)
            self._current_session_key = self._session_key_for_path(new_path)
            self.controller.pathChanged.emit(str(new_path))
        self._refresh_file_list()
        self._select_file_in_list(new_path.relative_to(self.workdir).as_posix())

    def _delete_selected_file(self) -> None:
        selected_relative = self._current_file_relative_path()
        if not selected_relative:
            return
        path = self.workdir / selected_relative
        reply = QMessageBox.question(self, "删除配置", f"确认删除 {selected_relative} 吗？")
        if reply != QMessageBox.StandardButton.Yes:
            return
        path.unlink(missing_ok=True)
        last_document = str(self.settings.value(self.SETTINGS_LAST_DOCUMENT, "") or "").strip()
        if last_document and str(path.resolve()) == str(Path(last_document).resolve()):
            self.settings.remove(self.SETTINGS_LAST_DOCUMENT)
        session_key = self._session_key_for_path(path)
        if session_key:
            self._document_sessions.pop(session_key, None)
        if self.controller.document.path == str(path):
            self._create_blank_document_session()
        self._refresh_file_list()

    def _open_selected_file(self, item: QListWidgetItem) -> None:
        payload = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(payload, dict) or payload.get("kind") != "file":
            return
        relative_path = str(payload.get("path") or "")
        if not relative_path:
            return
        path = self.workdir / relative_path
        if not self._ensure_safe_to_leave_document(path):
            return
        try:
            self._stash_current_document_session()
            self._open_existing_session_or_file(path)
        except Exception as exc:
            QMessageBox.warning(self, "无法打开", str(exc))
            self._refresh_file_list()
            return
        self._refresh_file_list()
        self.file_directory_dialog.close()

    def _open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "打开配置", str(self.workdir), "JSON Files (*.json)")
        if not path:
            return
        if self._is_install_owned_path(path):
            self._warn_install_owned_json()
            return
        if not self._ensure_safe_to_leave_document(path):
            return
        try:
            self._stash_current_document_session()
            self._open_existing_session_or_file(path)
        except Exception as exc:
            QMessageBox.warning(self, "无法打开", str(exc))
            return
        self._refresh_file_list()
        relative_path = self._relative_path_for_document(path)
        if relative_path:
            self._select_file_in_list(relative_path)

    def _save_current_file(self, silent: bool = False, *, allow_incomplete: bool = False) -> str | None:
        self._commit_pending_editor_changes()
        allowed, reason = self.controller.can_create_graph_content()
        if not allow_incomplete and not allowed:
            self._focus_initial_node_guidance(reason)
            QMessageBox.warning(self, "无法保存", f"{reason}\n请先通过“批量创建配置底座”补全必要元数据。")
            return None
        target = self.controller.document.path
        path_changed = not bool(target)
        self._refresh_file_list_after_save = path_changed
        if not target:
            generated = self._generated_save_path()
            if generated is None and allow_incomplete:
                draft = self._draft_save_path()
                if silent:
                    generated = draft
                else:
                    chosen, _ = QFileDialog.getSaveFileName(
                        self,
                        "保存未完成草稿",
                        str(draft),
                        "JSON Files (*.json)",
                    )
                    generated = Path(chosen).resolve() if chosen else None
            if not generated:
                self._refresh_file_list_after_save = False
                self._focus_initial_node_guidance(reason)
                if not silent:
                    QMessageBox.warning(self, "无法保存", "当前文件缺少配置底座元数据，无法生成配置文件。")
                return None
            target = str(generated)
        try:
            saved = self.controller.save_document(target)
        except (OSError, ValueError) as exc:
            self._refresh_file_list_after_save = False
            title = "拒绝覆盖" if "refusing to overwrite" in str(exc) else "保存失败"
            QMessageBox.warning(self, title, str(exc))
            return None
        if saved:
            self._mark_saved_checkpoint(saved=True)
            try:
                self._pending_group_dir = str(Path(saved).resolve().parent.relative_to(self.workdir.resolve())).replace("\\", "/")
            except Exception:
                self._pending_group_dir = ""
            if self._pending_group_dir == ".":
                self._pending_group_dir = ""
            self._current_session_key = self._session_key_for_path(saved)
            self._stash_current_document_session()
            if not silent:
                self._show_status(f"\u5df2\u4fdd\u5b58 {Path(saved).name}")
        else:
            self._refresh_file_list_after_save = False
        return saved

    def _commit_pending_editor_changes(self) -> None:
        if hasattr(self, "inspector_form"):
            self.inspector_form.commit_pending_edits()
        if hasattr(self, "canvas"):
            for table_item in list(self.canvas.table_items.values()):
                table_item.commit_pending_edit()
            for group_item in list(self.canvas.group_items.values()):
                group_item.commit_pending_title_edit()
            for item in list(self.canvas.node_items.values()):
                item.commit_pending_inline_edit()
                item.form.commit_pending_edits()

    def _handle_file_list_item_clicked(self, item: QListWidgetItem) -> None:
        payload = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(payload, dict):
            return
        if payload.get("kind") != "group":
            return
        group_dir = str(payload.get("group_dir") or "")
        if group_dir in self._collapsed_groups:
            self._collapsed_groups.remove(group_dir)
        else:
            self._collapsed_groups.add(group_dir)
        self._refresh_file_list()

    def _selected_node_list_uuids(self) -> list[str]:
        if not hasattr(self, "node_list"):
            return []
        result: list[str] = []
        seen: set[str] = set()
        for item in self.node_list.selectedItems():
            payload = item.data(0, Qt.ItemDataRole.UserRole)
            if not isinstance(payload, dict) or payload.get("kind") != "node":
                continue
            node_uuid = payload.get("node_uuid")
            if isinstance(node_uuid, str) and node_uuid and node_uuid not in seen:
                seen.add(node_uuid)
                result.append(node_uuid)
        return result

    def _active_selected_node_uuids(self) -> list[str]:
        if hasattr(self, "node_list") and self.node_list.hasFocus():
            selected = self._selected_node_list_uuids()
            if selected:
                return selected
        return self.canvas.selected_node_uuids()

    def _copy_selection(self) -> None:
        focus_widget = self.focusWidget()
        if isinstance(focus_widget, (QLineEdit, QPlainTextEdit)):
            focus_widget.copy()
            return
        node_uuids = self._active_selected_node_uuids()
        if not node_uuids:
            image_uuids = self.canvas.selected_canvas_image_uuids()
            if len(image_uuids) == 1:
                item = self.canvas.image_items.get(image_uuids[0])
                if item is not None and not item.image.isNull():
                    QGuiApplication.clipboard().setImage(item.image)
                    self._show_status("已复制参考图")
            return
        payload = self.controller.serialize_selection(node_uuids)
        if not payload:
            return
        data = QMimeData()
        data.setData(CLIPBOARD_MIME, payload)
        QGuiApplication.clipboard().setMimeData(data)
        self._last_paste_payload = payload
        self._paste_repeat_count = 0
        self._show_status("已复制节点")

    def _paste_selection(self) -> None:
        focus_widget = self.focusWidget()
        if isinstance(focus_widget, QLineEdit):
            focus_widget.paste()
            return
        if isinstance(focus_widget, QPlainTextEdit) and not focus_widget.isReadOnly():
            focus_widget.paste()
            return
        mime = QGuiApplication.clipboard().mimeData()
        if not mime:
            return
        if mime.hasFormat(CLIPBOARD_MIME):
            payload = bytes(mime.data(CLIPBOARD_MIME))
            try:
                position = self._next_paste_position(payload)
                pasted_node_uuids = self.controller.paste_payload(payload, position)
            except (AttributeError, json.JSONDecodeError, KeyError, OverflowError, TypeError, UnicodeDecodeError, ValueError):
                pasted_node_uuids = []
            if pasted_node_uuids:
                return
        if mime.hasImage():
            image_uuid = self.canvas.add_reference_image(QGuiApplication.clipboard().image(), name="剪贴板截图")
            if image_uuid:
                self._show_status("已粘贴参考图")
            else:
                self._show_status("截图无法粘贴：格式无效、数量已满或图片数据过大")

    def _duplicate_selection(self) -> None:
        node_uuids = self._active_selected_node_uuids()
        payload = self.controller.serialize_selection(node_uuids)
        if not payload:
            return
        connect_from = None
        if len(node_uuids) == 1:
            source_node = self.controller.get_node(node_uuids[0])
            if source_node and source_node.type not in {"Comment", "Initial"}:
                connect_from = source_node.uuid
        self._last_paste_payload = payload
        self._paste_repeat_count = 0
        position = self._next_paste_position(payload)
        if connect_from:
            existing_children = sum(1 for connection in self.controller.document.connections if connection.from_uuid == connect_from)
            position = (position[0], position[1] + existing_children * 44.0)
        self.controller.paste_payload(payload, position, connect_from=connect_from)

    def _delete_selection(self) -> None:
        node_uuids = self._active_selected_node_uuids()
        image_uuids = self.canvas.selected_canvas_image_uuids()
        connection_pairs = self.canvas.selected_connection_pairs()
        if node_uuids:
            self.controller.remove_nodes(node_uuids)
        if image_uuids:
            self.controller.remove_canvas_images(image_uuids)
        for from_uuid, to_uuid in connection_pairs:
            self.controller.remove_connection(from_uuid, to_uuid)

    def _group_selected_nodes(self) -> None:
        node_uuids = self.canvas.selected_node_uuids()
        if len(node_uuids) < 2:
            self._show_status("请先框选或多选至少两个节点后再打组")
            return
        frame = self.canvas.group_bounds_for_nodes(node_uuids)
        bounds = (frame.x(), frame.y(), frame.width(), frame.height()) if frame is not None else None
        group_uuid = self.controller.create_group(node_uuids, bounds=bounds)
        if not group_uuid:
            self._show_status("当前选择无法打组")
            return
        self.canvas.focus_on_group(group_uuid)

    def _commit_inspector_field(self, key: str, value) -> None:
        node_uuid = self.controller.selected_node_uuid
        if node_uuid:
            self.controller.update_field(node_uuid, key, value, "advanced")

    def _commit_inspector_fields(self, values: dict[str, object]) -> None:
        node_uuid = self.controller.selected_node_uuid
        if node_uuid:
            self.controller.update_fields(node_uuid, values, "advanced", label="应用外观方案")

    def _update_inspector(self, node_uuid: str | None) -> None:
        node = self.controller.get_node(node_uuid) if node_uuid else None
        if node:
            self.inspector_form.set_node(
                node,
                "advanced",
                self.controller.preferences.debug_json_field_names,
            )
            self.validation_summary.set_issues(self.validation_cache.get(node.uuid, []))
        else:
            self.validation_summary.set_issues([])
        self._refresh_node_list_panel()

    def _handle_node_updated(self, node_uuid: str) -> None:
        if node_uuid == self.controller.selected_node_uuid:
            node = self.controller.get_node(node_uuid)
            if node:
                self.inspector_form.set_node(
                    node,
                    "advanced",
                    self.controller.preferences.debug_json_field_names,
                )
                self.validation_summary.set_issues(self.validation_cache.get(node.uuid, []))
        if self.node_directory_dialog and self.node_directory_dialog.isVisible():
            self._refresh_node_directory_dialog()
        self._refresh_node_list_panel()
        if self.search_edit.text().strip():
            self._refresh_search_results()

    def _refresh_search_results(self) -> None:
        text = self.search_edit.text()
        self.search_results.clear()
        if not text.strip():
            self.search_results.hide()
            return
        for hit in self.controller.search(text):
            item = QListWidgetItem(f"{hit.title} | {hit.field_label}: {hit.preview}")
            item.setData(Qt.ItemDataRole.UserRole, hit.node_uuid)
            self.search_results.addItem(item)
        self.search_results.setVisible(self.search_results.count() > 0)
        if self.search_results.isVisible():
            self.search_results.setFixedHeight(min(180, max(42, self.search_results.count() * 34 + 8)))
        if self.search_panel.isVisible():
            self._position_search_popup()

    def _select_canvas_target(self, node_uuid: str) -> None:
        if node_uuid in self.canvas.node_items:
            self.canvas.node_items[node_uuid].setSelected(True)
            return
        table_item = self.canvas.table_row_to_item.get(node_uuid)
        if table_item:
            table_item.select_row(node_uuid)

    def _jump_to_search_result(self, item: QListWidgetItem) -> None:
        node_uuid = item.data(Qt.ItemDataRole.UserRole)
        self.canvas.focus_on_node(node_uuid, target_scale=1.05, emphasize=False)
        self._select_canvas_target(node_uuid)
        self.search_panel.close()

    def _restore_canvas_layout(self) -> None:
        self.canvas.reset_view_layout()

    def _show_csv_preview(self) -> None:
        self.csv_dialog.show()
        self.csv_dialog.raise_()
        self.csv_dialog.activateWindow()

    def _open_update_host(self) -> UpdateHostWindow | None:
        if self._update_host_window is None:
            try:
                public_key_pem = bundled_public_key_pem()
            except UpdateValidationError as exc:
                QMessageBox.warning(self, "更新主机不可用", str(exc))
                return None
            self._update_host_window = UpdateHostWindow(
                public_key_pem=public_key_pem,
                parent=self,
            )
        self._update_host_window.show()
        self._update_host_window.raise_()
        self._update_host_window.activateWindow()
        return self._update_host_window

    def _close_update_host(self) -> None:
        if self._update_host_window is not None:
            self._update_host_window.close()

    def _open_performance_tool(self) -> None:
        if self.performance_dialog is None:
            self.performance_dialog = PerformanceToolDialog(self, self)
        self.performance_dialog.refresh_display()
        self.performance_dialog.show()
        self.performance_dialog.raise_()
        self.performance_dialog.activateWindow()

    def _add_reference_image_from_file(self) -> None:
        image_path, _ = QFileDialog.getOpenFileName(
            self,
            "添加参考图",
            str(self.workdir),
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)",
        )
        if not image_path:
            return
        image = read_reference_image(image_path)
        if image.isNull():
            QMessageBox.warning(self, "添加参考图失败", "无法读取所选图片。")
            return
        image_uuid = self.canvas.add_reference_image(image, name=Path(image_path).name)
        if image_uuid:
            self._show_status(f"已添加参考图 {Path(image_path).name}")
        else:
            QMessageBox.warning(self, "添加参考图失败", "参考图数量已满，或图片数据超过安全限制。")

    def _show_export_csv_dialog(self) -> None:
        files = [(relative_path, self._read_file_display_meta(self.workdir / relative_path)[1]) for relative_path in self.controller.file_list()]
        if not files:
            QMessageBox.information(self, "\u65e0\u53ef\u5bfc\u51fa\u5185\u5bb9", "\u5f53\u524d\u5de5\u4f5c\u76ee\u5f55\u4e0b\u6ca1\u6709\u53ef\u5bfc\u51fa\u7684 JSON \u914d\u7f6e\u3002")
            return
        self.export_csv_dialog.set_files(files)
        if self.export_csv_dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = self.export_csv_dialog.selected_files()
        if not selected:
            QMessageBox.information(self, "\u672a\u9009\u62e9\u914d\u7f6e", "\u8bf7\u5148\u9009\u62e9\u8981\u5bfc\u51fa\u7684 JSON \u914d\u7f6e\u3002")
            return
        self._export_selected_configs_to_csv(selected)

    def _export_selected_configs_to_csv(self, relative_paths: list[str]) -> None:
        documents = []
        current_relative = self._relative_path_for_document(self.controller.document.path)
        for relative_path in relative_paths:
            if current_relative and relative_path == current_relative:
                documents.append(self.controller.document)
                continue
            try:
                documents.append(load_document(self.controller.schema, self.workdir / relative_path))
            except Exception as exc:
                QMessageBox.warning(self, "\u5bfc\u51fa\u5931\u8d25", f"{relative_path}\n{exc}")
                return
        output_path = self.workdir / build_csv_export_filename()
        export_documents_to_csv(
            self.controller.schema,
            documents,
            output_path,
            template_search_roots=(self.workdir, Path(__file__).resolve().parent.parent),
        )
        self._show_status(f"\u5df2\u5bfc\u51fa CSV: {output_path.name}")

    def _update_csv_preview(self, rows) -> None:
        self.csv_dialog.update_rows(rows)

    def _handle_document_saved(self, saved_path: str) -> None:
        self._mark_saved_checkpoint(saved=True)
        if self._refresh_file_list_after_save:
            relative_path = self._relative_path_for_document(saved_path)
            self._refresh_file_list()
            if relative_path:
                self._select_file_in_list(relative_path)
        self._refresh_file_list_after_save = False

    def _update_window_title(self, path: str | None) -> None:
        title = PRODUCT_NAME
        if path:
            title = f"{Path(path).name} - {title}"
        if self._is_dirty():
            title = f"* {title}"
        self.setWindowTitle(title)

    def _show_status(self, message: str) -> None:
        self.statusBar().showMessage(message, 4000)

    def _handle_canvas_busy_changed(self, busy: bool) -> None:
        if not busy and self._has_saved_snapshot and self.controller.document.path and self._is_dirty():
            self._auto_save_timer.start(450)

    def _handle_editor_settings_changed(self, settings) -> None:
        if hasattr(self, "numeric_linkage_checkbox"):
            blocked = self.numeric_linkage_checkbox.blockSignals(True)
            self.numeric_linkage_checkbox.setChecked(bool(settings.numeric_linkage_enabled))
            self.numeric_linkage_checkbox.blockSignals(blocked)

    def _toggle_numeric_linkage(self, checked: bool) -> None:
        if bool(self.controller.document.editor_settings.numeric_linkage_enabled) == bool(checked):
            return
        self.controller.set_numeric_linkage_enabled(bool(checked))

    def _open_help_page(self) -> None:
        QDesktopServices.openUrl(QUrl.fromUserInput(HELP_PAGE_URL))

    def _open_changelog_page(self) -> None:
        QDesktopServices.openUrl(QUrl.fromUserInput(HELP_PAGE_URL))

    def _update_client_or_warn(self, *, quiet: bool = False) -> UpdateClient | None:
        if self._update_client is not None:
            return self._update_client
        try:
            public_key = bundled_public_key_pem()
            client = UpdateClient(public_key, parent=self)
        except (OSError, UpdateValidationError) as exc:
            if not quiet:
                QMessageBox.warning(self, "更新不可用", str(exc))
            return None
        client.updateAvailable.connect(self._update_available)
        client.noUpdate.connect(self._no_update_available)
        client.checkFailed.connect(self._update_check_failed)
        client.downloadProgress.connect(self._update_download_progress)
        client.downloadFinished.connect(self._update_download_finished)
        client.downloadFailed.connect(self._update_download_failed)
        client.cacheFinished.connect(
            lambda path: self._show_status(
                f"已缓存当前版本安装器 {Path(path).name}"
            )
        )
        client.cacheFailed.connect(
            lambda message: self._show_status(f"缓存当前安装器失败：{message}")
        )
        self._update_client = client
        return client

    def _configure_update_host(self) -> bool:
        current = str(self.settings.value(self.SETTINGS_UPDATE_BASE_URL, "") or "")
        value, accepted = QInputDialog.getText(
            self,
            "更新设置",
            "局域网更新主机地址（例如 http://主机名:8765）：",
            QLineEdit.EchoMode.Normal,
            current,
        )
        if not accepted:
            return False
        if not value.strip():
            self.settings.remove(self.SETTINGS_UPDATE_BASE_URL)
            self.settings.remove(self.SETTINGS_UPDATE_LAST_CHECK)
            self.settings.sync()
            self._show_status("已关闭局域网更新检查")
            return False
        try:
            normalized = UpdateClient.normalize_base_url(value)
        except UpdateValidationError as exc:
            QMessageBox.warning(self, "地址无效", str(exc))
            return False
        self.settings.setValue(self.SETTINGS_UPDATE_BASE_URL, normalized.rstrip("/"))
        self.settings.remove(self.SETTINGS_UPDATE_LAST_CHECK)
        self.settings.sync()
        self._show_status(f"更新主机已设置为 {normalized.rstrip('/')}")
        return True

    def _maybe_check_updates(self) -> None:
        base_url = str(self.settings.value(self.SETTINGS_UPDATE_BASE_URL, "") or "").strip()
        if not base_url:
            return
        last_raw = str(self.settings.value(self.SETTINGS_UPDATE_LAST_CHECK, "") or "").strip()
        if last_raw:
            try:
                last = datetime.fromisoformat(last_raw.replace("Z", "+00:00"))
                if last.tzinfo is not None:
                    age = datetime.now(timezone.utc) - last.astimezone(timezone.utc)
                    if age.total_seconds() < 24 * 60 * 60:
                        return
            except ValueError:
                pass
        self._check_for_updates(manual=False)

    def _check_for_updates(self, *, manual: bool) -> None:
        base_url = str(self.settings.value(self.SETTINGS_UPDATE_BASE_URL, "") or "").strip()
        if not base_url:
            if not manual or not self._configure_update_host():
                return
            base_url = str(self.settings.value(self.SETTINGS_UPDATE_BASE_URL, "") or "").strip()
        client = self._update_client_or_warn(quiet=not manual)
        if client is None:
            return
        self._update_check_is_manual = manual
        self._pending_update_manifest = None
        self._show_status("正在检查局域网更新…")
        client.check(base_url)

    def _record_successful_update_check(self) -> None:
        self.settings.setValue(
            self.SETTINGS_UPDATE_LAST_CHECK,
            datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        )
        self.settings.sync()

    def _update_available(self, manifest: dict, _artifact_url: str) -> None:
        self._record_successful_update_check()
        self._pending_update_manifest = dict(manifest)
        version = str(manifest.get("version") or "")
        notes = str(manifest.get("notes") or "").strip()
        message = f"发现新版本 {version}。"
        if notes:
            message += f"\n\n{notes}"
        message += "\n\n是否现在下载？"
        if QMessageBox.question(
            self,
            "发现更新",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        ) != QMessageBox.StandardButton.Yes:
            self._show_status(f"已发现版本 {version}，暂不下载")
            return
        client = self._update_client
        if client is None:
            return
        progress = QProgressDialog("正在下载并校验更新…", "取消", 0, 0, self)
        progress.setWindowTitle("下载更新")
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.canceled.connect(self._cancel_update_download)
        self._update_progress = progress
        progress.show()
        client.download_available()

    def _cancel_update_download(self) -> None:
        if self._update_client is not None:
            self._update_client.cancel_download(remove_partial=False)
        self._close_update_progress()
        self._show_status("已取消下载，保留断点以便下次继续")

    def _no_update_available(self) -> None:
        self._record_successful_update_check()
        self._show_status(f"当前已是最新版本 {VERSION}")
        if self._update_client is not None:
            self._update_client.cache_current_release()
        if self._update_check_is_manual:
            QMessageBox.information(self, "检查更新", f"当前已是最新版本 {VERSION}。")

    def _update_check_failed(self, message: str) -> None:
        self._show_status(message)
        if self._update_check_is_manual:
            QMessageBox.warning(self, "检查更新失败", message)

    def _update_download_progress(self, received: int, total: int) -> None:
        progress = self._update_progress
        if progress is None:
            return
        maximum = min(max(int(total), 0), 2_147_483_647)
        value = min(max(int(received), 0), 2_147_483_647)
        if maximum > 0:
            progress.setRange(0, maximum)
            progress.setValue(min(value, maximum))
        else:
            progress.setRange(0, 0)

    def _close_update_progress(self) -> None:
        progress, self._update_progress = self._update_progress, None
        if progress is not None:
            try:
                progress.canceled.disconnect(self._cancel_update_download)
            except (RuntimeError, TypeError):
                pass
            progress.close()
            progress.deleteLater()

    def _update_download_finished(self, installer_path: str) -> None:
        self._close_update_progress()
        manifest = self._pending_update_manifest or {}
        version = str(manifest.get("version") or "新版本")
        if QMessageBox.question(
            self,
            "更新已就绪",
            f"版本 {version} 已完成签名和 SHA-256 校验。\n"
            "是否保存当前工作并开始安装？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        ) == QMessageBox.StandardButton.Yes:
            self._install_verified_update(installer_path)

    def _update_download_failed(self, message: str) -> None:
        self._close_update_progress()
        QMessageBox.warning(self, "下载更新失败", message)

    def _install_verified_update(self, installer_path: str | Path) -> None:
        client = self._update_client_or_warn()
        if client is None or not client.verify_cached_installer(installer_path):
            QMessageBox.critical(self, "拒绝安装", "安装器的签名缓存或 SHA-256 复验失败。")
            return
        if not self._confirm_safe_to_close():
            return
        restart = sys.executable if getattr(sys, "frozen", False) else None
        if not launch_installer_after_exit(
            installer_path,
            current_pid=os.getpid(),
            restart_executable=restart,
        ):
            QMessageBox.critical(self, "无法安装", "无法启动外部安装程序。")
            return
        self._approved_update_exit = True
        application = QApplication.instance()
        if application is not None:
            application.quit()

    def _reinstall_previous_version(self) -> None:
        client = self._update_client_or_warn()
        if client is None:
            return
        try:
            from packaging.version import Version

            current = Version(VERSION)
            candidates = [
                (version, path)
                for version, path in client.verified_cached_installers()
                if Version(version) < current
            ]
        except ValueError:
            candidates = []
        if not candidates:
            QMessageBox.information(self, "没有可重装版本", "缓存中没有已验证的上一版本安装器。")
            return
        labels = [f"{version} — {path.name}" for version, path in candidates]
        selected, accepted = QInputDialog.getItem(
            self,
            "重装上一版本",
            "选择已验证的安装器：",
            labels,
            0,
            False,
        )
        if not accepted:
            return
        index = labels.index(selected)
        version, installer = candidates[index]
        if QMessageBox.warning(
            self,
            "确认重装",
            f"将启动版本 {version} 的安装器。程序不会自动回滚数据。\n是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes:
            self._install_verified_update(installer)

    def _show_about_dialog(self) -> None:
        QMessageBox.about(
            self,
            f"关于 {PRODUCT_NAME}",
            f"{PRODUCT_NAME}\n"
            f"版本 {VERSION}\n"
            f"发布者：{PUBLISHER}\n\n"
            "Qt for Python / PySide6 采用动态链接，随程序附带 LGPLv3 与第三方许可材料。\n"
            "局域网 HTTP 只负责传输；更新真实性由 Ed25519 签名和 SHA-256 校验保障。",
        )

    def _show_node_directory_dialog(self) -> None:
        if self.node_directory_dialog is None:
            self.node_directory_dialog = NodeDirectoryDialog(self)
            self.node_directory_dialog.nodeRequested.connect(self._focus_node_from_directory)
        self._refresh_node_directory_dialog()
        self.node_directory_dialog.show()
        self.node_directory_dialog.raise_()
        self.node_directory_dialog.activateWindow()

    def _refresh_node_directory_dialog(self) -> None:
        if self.node_directory_dialog is None:
            return
        rows = [(node.uuid, self.controller.node_summary(node.uuid)) for node in self.controller.document.nodes]
        self.node_directory_dialog.set_nodes(rows)

    def _focus_node_from_directory(self, node_uuid: str) -> None:
        if node_uuid:
            self.canvas.focus_on_node(node_uuid, target_scale=None, emphasize=True)
            self._select_canvas_target(node_uuid)

    def _focus_node_from_tree_item(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        payload = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if not isinstance(payload, dict):
            return
        kind = payload.get("kind")
        if kind == "node":
            node_uuid = payload.get("node_uuid")
            if isinstance(node_uuid, str) and node_uuid:
                self._focus_node_from_directory(node_uuid)
        elif kind == "group":
            group_uuid = payload.get("group_uuid")
            if isinstance(group_uuid, str) and group_uuid:
                self.canvas.focus_on_group(group_uuid)
        elif kind == "table":
            table_id = payload.get("table_id")
            if isinstance(table_id, str) and table_id:
                self.canvas.focus_on_parameter_table(table_id)

    def _refresh_node_list_panel(self) -> None:
        if not hasattr(self, "node_list"):
            return
        current_selection = self.controller.selected_node_uuid
        needle = self.node_search_edit.text().strip().lower() if hasattr(self, "node_search_edit") else ""
        self.node_list.blockSignals(True)
        self.node_list.clear()

        nodes_by_uuid = {node.uuid: node for node in self.controller.document.nodes}
        groups = self.controller.group_records()
        tables = self.controller.parameter_tables()
        table_lookup = {table["table_id"]: table for table in tables}
        row_to_table = {
            node_uuid: table["table_id"]
            for table in tables
            for node_uuid in table["node_uuids"]
        }
        consumed_node_ids: set[str] = set()
        consumed_table_ids: set[str] = set()

        def _matches(text: str) -> bool:
            return not needle or needle in text.lower()

        def _make_payload_item(label: str, payload: dict[str, str]) -> QTreeWidgetItem:
            item = QTreeWidgetItem([label])
            item.setData(0, Qt.ItemDataRole.UserRole, payload)
            return item

        def _append_node(parent, node_uuid: str) -> bool:
            label = self.controller.node_summary(node_uuid)
            if not _matches(label):
                return False
            child = _make_payload_item(label, {"kind": "node", "node_uuid": node_uuid})
            parent.addChild(child)
            if current_selection and current_selection == node_uuid:
                self.node_list.setCurrentItem(child)
            return True

        def _append_table(parent, table_id: str) -> bool:
            table = table_lookup.get(table_id)
            if not table:
                return False
            row_ids = [node_uuid for node_uuid in table["node_uuids"] if node_uuid in nodes_by_uuid]
            if not row_ids:
                return False
            title = f"{table['title']} ({len(row_ids)} 行)"
            matches_title = _matches(title)
            table_item = _make_payload_item(title, {"kind": "table", "table_id": table_id})
            row_added = False
            for row_uuid in row_ids:
                row_added = _append_node(table_item, row_uuid) or row_added
            if not matches_title and not row_added:
                return False
            parent.addChild(table_item)
            table_item.setExpanded(True)
            consumed_table_ids.add(table_id)
            consumed_node_ids.update(row_ids)
            return True

        for group in groups:
            group_item = _make_payload_item(group.title or "分组", {"kind": "group", "group_uuid": group.uuid})
            added_any = False
            processed_tables: set[str] = set()
            for node_uuid in group.node_uuids:
                node = nodes_by_uuid.get(node_uuid)
                if node is None:
                    continue
                table_id = row_to_table.get(node_uuid)
                if table_id and table_id not in processed_tables:
                    table = table_lookup.get(table_id)
                    row_ids = [value for value in (table or {}).get("node_uuids", []) if value in nodes_by_uuid]
                    if row_ids and set(row_ids).issubset(set(group.node_uuids)):
                        added_any = _append_table(group_item, table_id) or added_any
                        processed_tables.add(table_id)
                        continue
                added_any = _append_node(group_item, node_uuid) or added_any
                consumed_node_ids.add(node_uuid)
            if added_any or _matches(group.title or "分组"):
                self.node_list.addTopLevelItem(group_item)
                group_item.setExpanded(True)

        for table in tables:
            if table["table_id"] in consumed_table_ids:
                continue
            container = QTreeWidgetItem()
            if _append_table(container, table["table_id"]):
                self.node_list.addTopLevelItem(container.takeChild(0))

        for node in self.controller.document.nodes:
            if node.uuid in consumed_node_ids:
                continue
            if node.uuid in row_to_table:
                continue
            label = self.controller.node_summary(node.uuid)
            if not _matches(label):
                continue
            item = _make_payload_item(label, {"kind": "node", "node_uuid": node.uuid})
            self.node_list.addTopLevelItem(item)
            if current_selection and current_selection == node.uuid:
                self.node_list.setCurrentItem(item)

        self.node_list.expandAll()
        self.node_list.blockSignals(False)

    def _open_selected_file_from_dialog(self) -> None:
        item = self.file_list.currentItem()
        if item:
            self._open_selected_file(item)

    def _show_file_directory_dialog(self) -> None:
        self._refresh_file_list()
        current = self._relative_path_for_document(self.controller.document.path)
        if current:
            self._select_file_in_list(current)
        self.file_directory_dialog.show()
        self.file_directory_dialog.raise_()
        self.file_directory_dialog.activateWindow()

    def _select_svn_executable(self) -> Path | None:
        saved = self.settings.value(self.SETTINGS_SVN_EXECUTABLE)
        executable = discover_svn_executable(str(saved) if saved else None)
        if executable is not None:
            self.settings.setValue(self.SETTINGS_SVN_EXECUTABLE, str(executable))
            self.settings.sync()
            return executable
        filename = "svn.exe" if os.name == "nt" else "svn"
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "选择 SVN CLI 可执行文件",
            str(Path.home()),
            f"SVN CLI ({filename});;所有文件 (*)",
        )
        if not chosen:
            QMessageBox.information(
                self,
                "未找到 SVN CLI",
                "请安装带命令行工具的 SVN 客户端，或选择 svn.exe 后再提交。",
            )
            return None
        executable = Path(chosen).resolve()
        self.settings.setValue(self.SETTINGS_SVN_EXECUTABLE, str(executable))
        self.settings.sync()
        return executable

    def _commit_current_json_to_svn(self) -> None:
        saved_path = self._save_current_file(silent=False)
        if not saved_path:
            return
        file_path = Path(saved_path).resolve()
        executable = self._select_svn_executable()
        if executable is None:
            return
        message = file_path.name
        runner = SvnCommitRunner(executable, self)
        dialog = SvnCommitDialog(runner, file_path, message, self)
        self.svn_commit_dialog = dialog
        self.svn_commit_button.setEnabled(False)

        def finish(_success: bool, result: str) -> None:
            self.svn_commit_button.setEnabled(True)
            if not _success and "无法启动 SVN CLI" in result:
                self.settings.remove(self.SETTINGS_SVN_EXECUTABLE)
                self.settings.sync()
            self._show_status(result)

        runner.finished.connect(finish)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        QTimer.singleShot(0, lambda: runner.start(file_path, message, self.workdir))

    def _show_batch_template_dialog(self) -> None:
        if not self._ensure_safe_to_leave_document(self.workdir / "__new__"):
            return
        dialog = BatchTemplateDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            paths = create_base_template_files(self.controller.schema, self.workdir, dialog.template_specs())
        except Exception as exc:
            QMessageBox.warning(self, "配置底座创建失败", str(exc))
            return
        self._refresh_file_list()
        folders = {path.parent for path in paths}
        QMessageBox.information(
            self,
            "配置底座已创建",
            f"已创建 {len(paths)} 个配置底座，输出到 {len(folders)} 个版本目录。\n"
            "每份配置都以 idle0 为根节点，角色信息已作为隐式字段写入。",
        )
        if not paths:
            return
        self._stash_current_document_session()
        self._open_existing_session_or_file(paths[0])
        relative_path = self._relative_path_for_document(paths[0])
        if relative_path:
            self._select_file_in_list(relative_path)
        self._show_status(f"已创建 {len(paths)} 个配置底座")

    def _focus_selected_node(self) -> None:
        node_uuid = self.controller.selected_node_uuid
        if not node_uuid:
            selected = self.canvas.selected_node_uuids()
            node_uuid = selected[0] if selected else None
        if not node_uuid:
            return
        self.canvas.focus_on_node(node_uuid, target_scale=1.15, emphasize=True)
        self._select_canvas_target(node_uuid)

    def _handle_selection_summary(self, node_uuids, connection_pairs) -> None:
        del connection_pairs
        if hasattr(self, "group_selected_button"):
            self.group_selected_button.setEnabled(len(node_uuids) >= 2)
        if len(node_uuids) == 1:
            self.controller.set_selected_node(node_uuids[0])
        elif not node_uuids:
            self.controller.set_selected_node(None)

    def _select_file_in_list(self, relative_path: str) -> None:
        for index in range(self.file_list.count()):
            item = self.file_list.item(index)
            payload = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(payload, dict) and payload.get("kind") == "file" and payload.get("path") == relative_path:
                self.file_list.setCurrentItem(item)
                return

    def _focus_search(self) -> None:
        self.search_panel.adjustSize()
        self.search_panel.show()
        self._position_search_popup()
        self.search_panel.raise_()
        self.search_edit.setFocus()
        self.search_edit.selectAll()

    def _position_search_popup(self) -> None:
        if not hasattr(self, "search_panel") or not hasattr(self, "canvas"):
            return
        self.search_panel.adjustSize()
        popup_size = self.search_panel.sizeHint()
        width = self.search_panel.width()
        height = max(58, popup_size.height())
        self.search_panel.resize(width, height)
        anchor = self.canvas.viewport().mapToGlobal(self.canvas.viewport().rect().topRight())
        x = anchor.x() - width - 16
        y = anchor.y() + 16
        screen = QGuiApplication.screenAt(anchor)
        if screen is not None:
            available = screen.availableGeometry()
            x = max(available.left() + 8, min(x, available.right() - width - 8))
            y = max(available.top() + 8, min(y, available.bottom() - height - 8))
        self.search_panel.move(x, y)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "search_panel") and self.search_panel.isVisible():
            self._position_search_popup()

    def _focus_file_search(self) -> None:
        self._show_file_directory_dialog()
        self.file_search_edit.setFocus()
        self.file_search_edit.selectAll()

    def _jump_to_validation_node(self, node_uuid: str) -> None:
        self.canvas.focus_on_node(node_uuid, target_scale=1.25, emphasize=True)
        self._select_canvas_target(node_uuid)

    def _focus_initial_node_guidance(self, _reason: str = "") -> None:
        idle0 = next((node for node in self.controller.document.nodes if node.type == "Idle0"), None)
        if idle0:
            self.canvas.focus_on_node(idle0.uuid, target_scale=1.35, emphasize=True)
            self._select_canvas_target(idle0.uuid)
        if _reason:
            self.statusBar().showMessage(_reason, 6000)

    def _store_validation(self, issues) -> None:
        validation_cache: dict[str, list] = {}
        for issue in issues:
            validation_cache.setdefault(issue.node_uuid, []).append(issue)
        self.validation_cache = validation_cache
        if self.controller.selected_node_uuid:
            self.validation_summary.set_issues(validation_cache.get(self.controller.selected_node_uuid, []))

    def _update_document_state(self, state) -> None:
        if state.is_meta_ready:
            self.inspector_meta.setText("配置底座元数据就绪，可以创建节点与连线。")
        else:
            self.inspector_meta.setText(f"配置底座缺少必要元数据: {' / '.join(state.meta_missing_fields)}")
        self._update_save_action_state()

    def _handle_interaction_creation_mode_changed(self, mode: str) -> None:
        self.auto_create_rule_radio.setChecked(mode == "auto")
        self.manual_create_rule_radio.setChecked(mode == "manual")

    def _handle_global_mode_changed(self, _mode: str) -> None:
        if self.controller.selected_node_uuid:
            node = self.controller.get_node(self.controller.selected_node_uuid)
            if node:
                self.inspector_form.set_node(
                    node,
                    "advanced",
                    self.controller.preferences.debug_json_field_names,
                )

    def _reload_schema(self) -> None:
        try:
            self.controller.reload_schema()
            self._show_status("字段配置已重载")
        except Exception as exc:
            QMessageBox.critical(self, "重载失败", str(exc))

    def _handle_schema_changed(self) -> None:
        self.canvas.schema = self.controller.schema
        self.canvas.rebuild_scene()
        self.inspector_form.schema = self.controller.schema
        self.csv_dialog.set_schema(self.controller.schema)
        if self.controller.selected_node_uuid:
            self._update_inspector(self.controller.selected_node_uuid)

    def _optimize_connection_layout(self) -> None:
        self.canvas.optimize_connection_layout()

    def _relative_path_for_document(self, path: str | Path | None) -> str | None:
        if not path:
            return None
        try:
            return Path(path).resolve().relative_to(self.workdir.resolve()).as_posix()
        except Exception:
            return None

    def _mark_saved_checkpoint(self, *, saved: bool) -> None:
        self._last_saved_undo_index = self.controller.undo_stack.index()
        self._has_saved_snapshot = saved
        if not self._is_dirty():
            self._auto_save_timer.stop()
        self._sync_undo_actions()
        self._update_window_title(self.controller.document.path)

    def _is_dirty(self) -> bool:
        if not self._has_saved_snapshot:
            return bool(self.controller.document.path) or self.controller.undo_stack.index() != 0
        return self.controller.undo_stack.index() != self._last_saved_undo_index

    def _handle_undo_index_changed(self, _index: int) -> None:
        if self._has_saved_snapshot and self.controller.document.path and self._is_dirty():
            self._auto_save_timer.start(self.AUTOSAVE_DELAY_MS)
        else:
            self._auto_save_timer.stop()
        self._sync_undo_actions()
        self._update_window_title(self.controller.document.path)

    def _run_auto_save(self) -> None:
        if self._has_saved_snapshot and self.controller.document.path and self._is_dirty():
            if hasattr(self, "canvas") and self.canvas.is_busy():
                self._auto_save_timer.start(500)
                return
            saved = self._save_current_file(silent=True, allow_incomplete=True)
            if saved:
                self.statusBar().showMessage(f"已自动保存 {Path(saved).name}", 2500)

    def _ensure_safe_to_leave_document(self, target_path: str | Path | None) -> bool:
        current = self.controller.document.path
        if current and target_path and Path(current).resolve() == Path(target_path).resolve():
            return True
        self._auto_save_timer.stop()
        if not self._is_dirty():
            return True
        close_policy = str(os.environ.get("L2D_CONFIG_EDITOR_TEST_CLOSE_POLICY") or "").strip().lower()
        if close_policy == "save":
            return bool(self._save_current_file(silent=True, allow_incomplete=True))
        if close_policy in {"discard", "ignore"} or os.environ.get("L2D_CONFIG_EDITOR_NO_CLOSE_PROMPT") == "1":
            return True
        if target_path is not None:
            if self.controller.document.path:
                return bool(self._save_current_file(silent=True, allow_incomplete=True))
            # An untitled draft cannot be stashed by path. Ask before replacing it.
        if self._has_saved_snapshot and self.controller.document.path:
            return bool(self._save_current_file(silent=True, allow_incomplete=True))
        box = QMessageBox(self)
        box.setWindowTitle("保存当前更改")
        box.setText("当前文档尚未保存，是否先保存？")
        save_button = box.addButton("保存", QMessageBox.ButtonRole.AcceptRole)
        discard_button = box.addButton("不保存", QMessageBox.ButtonRole.DestructiveRole)
        cancel_button = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked == save_button:
            return bool(self._save_current_file(silent=False, allow_incomplete=True))
        if clicked == discard_button:
            return True
        return False  # Unknown/dismissed responses must never discard changes.

    def _next_paste_position(self, payload: bytes) -> tuple[float, float]:
        if self._last_paste_payload != payload:
            self._last_paste_payload = payload
            self._paste_repeat_count = 0
        self._paste_repeat_count += 1
        min_x, min_y, max_x, _max_y = self.controller.clipboard_bounds(payload)
        width = max(120.0, max_x - min_x)
        offset = width + self.PASTE_GAP
        return min_x + offset * self._paste_repeat_count, min_y

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._approved_update_exit:
            self._close_update_host()
            event.accept()
            return
        if self._confirm_safe_to_close():
            self._close_update_host()
            event.accept()
        else:
            event.ignore()

    def _confirm_safe_to_close(self) -> bool:
        self._auto_save_timer.stop()
        self._commit_pending_editor_changes()
        close_policy = str(
            os.environ.get("L2D_CONFIG_EDITOR_TEST_CLOSE_EVENT_POLICY")
            or os.environ.get("L2D_CONFIG_EDITOR_TEST_CLOSE_POLICY")
            or ""
        ).strip().lower()
        if close_policy == "save":
            if self._is_dirty():
                return bool(self._save_current_file(silent=True, allow_incomplete=True))
            return True
        if close_policy in {"discard", "ignore"} or os.environ.get("L2D_CONFIG_EDITOR_NO_CLOSE_PROMPT") == "1":
            return True
        if not self._is_dirty():
            return True
        box = QMessageBox(self)
        box.setWindowTitle("保存当前更改")
        box.setText("当前文档尚未保存，退出前是否先保存？")
        save_button = box.addButton("保存", QMessageBox.ButtonRole.AcceptRole)
        discard_button = box.addButton("不保存", QMessageBox.ButtonRole.DestructiveRole)
        cancel_button = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked == save_button:
            return bool(self._save_current_file(silent=False, allow_incomplete=True))
        if clicked == discard_button:
            return True
        return False  # Unknown/dismissed responses must never discard changes.
