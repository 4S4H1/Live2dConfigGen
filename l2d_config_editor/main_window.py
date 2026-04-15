"""Main application window."""

from __future__ import annotations

import json
import re
from pathlib import Path

from PyQt6.QtCore import QSettings, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QCloseEvent, QDesktopServices, QGuiApplication, QKeySequence, QUndoStack
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
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
    QVBoxLayout,
    QWidget,
    QLineEdit,
    QButtonGroup,
    QPlainTextEdit,
)

from .canvas import NodeCanvasView
from .constants import CLIPBOARD_MIME
from .controller import EditorController
from .logic import (
    build_csv_export_filename,
    build_template_version_folder_name,
    create_document,
    create_template_document,
    export_documents_to_csv,
    load_document,
    load_template_csv_rows,
    save_document,
)
from .widgets import (
    ColorFieldWidget,
    CommitComboBox,
    CommitLineEdit,
    CommitPlainTextEdit,
    NodeFormWidget,
    NumericLineEdit,
    ValidationSummaryWidget,
)

HELP_PAGE_URL = "https://ooia5293gn.feishu.cn/wiki/YvmxwxAKSitp3WkfFz3cY74Jnvg"


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


class TrashDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("节点垃圾箱")
        self.resize(720, 520)
        layout = QVBoxLayout(self)
        description = QLabel("已删除节点的编号槽位会先保留在这里。清理后，对应槽位才能被后续新节点复用。")
        description.setWordWrap(True)
        layout.addWidget(description)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self.list_widget, 1)

        button_row = QHBoxLayout()
        self.remove_selected_button = QPushButton("清理选中")
        self.clear_all_button = QPushButton("全部清空")
        self.close_button = QPushButton("关闭")
        button_row.addWidget(self.remove_selected_button)
        button_row.addWidget(self.clear_all_button)
        button_row.addStretch(1)
        button_row.addWidget(self.close_button)
        layout.addLayout(button_row)

    def set_entries(self, entries) -> None:
        self.list_widget.clear()
        for entry in entries:
            detail_bits = []
            if entry.type_slot:
                detail_bits.append(f"类型序号 {entry.type_slot}")
            if entry.export_slot:
                detail_bits.append(f"导出ID槽位 {entry.export_slot}")
            if entry.reserved_fields.get("draw_able_name"):
                detail_bits.append(f"框 {entry.reserved_fields['draw_able_name']}")
            if entry.reserved_fields.get("parameter"):
                detail_bits.append(f"参数 {entry.reserved_fields['parameter']}")
            line = f"{entry.title} | {' / '.join(detail_bits)}" if detail_bits else entry.title
            item = QListWidgetItem(line)
            item.setData(Qt.ItemDataRole.UserRole, entry.entry_id)
            item.setToolTip(line)
            self.list_widget.addItem(item)

    def selected_entry_ids(self) -> list[str]:
        result: list[str] = []
        for item in self.list_widget.selectedItems():
            entry_id = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(entry_id, str) and entry_id:
                result.append(entry_id)
        return result


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
    nodeRequested = pyqtSignal(str)

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
    # QSettings 键：存 Windows 注册表 HKCU\Software\OpenAI\L2DConfigEditor，不是仓库里的 config.json。
    SETTINGS_WORKSPACE_ROOT = "workspace_root"
    SETTINGS_LAST_DOCUMENT = "last_document_path"
    SETTINGS_TRASH_ENABLED_DEFAULT = "trash_enabled_default"

    def __init__(self, workdir: str | Path, *, prefer_saved_workspace: bool = True) -> None:
        super().__init__()
        self.settings = QSettings("OpenAI", "L2DConfigEditor")
        if prefer_saved_workspace:
            self.workdir = self._resolved_workspace_path(workdir)
        else:
            self.workdir = Path(workdir).resolve()
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
        self.trash_dialog: TrashDialog | None = None
        self.node_directory_dialog: NodeDirectoryDialog | None = None
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
        self.controller.validationChanged.connect(self._store_validation)
        self.controller.documentStateChanged.connect(self._update_document_state)
        self.controller.globalModeChanged.connect(self._handle_global_mode_changed)
        self.controller.interactionCreationModeChanged.connect(self._handle_interaction_creation_mode_changed)
        self.controller.schemaChanged.connect(self._handle_schema_changed)
        self.controller.trashBinChanged.connect(self._refresh_trash_dialog)
        self.controller.metaActionBlocked.connect(self._focus_initial_node_guidance)
        self.controller.editorSettingsChanged.connect(self._handle_editor_settings_changed)
        self._set_active_undo_stack(self.controller.undo_stack)
        self._shortcut_actions: dict[str, QAction] = {}
        self._shortcut_defaults: dict[str, QKeySequence] = {}

        self.setWindowTitle("L2D交互图表编辑器")
        self.resize(1680, 980)
        self._build_ui()
        self._update_workspace_path_display()
        self._build_actions()
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

    def _resolved_workspace_path(self, default: str | Path) -> Path:
        default_path = Path(default).resolve()
        raw = self.settings.value(self.SETTINGS_WORKSPACE_ROOT)
        if raw is None or raw == "":
            return default_path
        candidate = Path(str(raw).strip())
        if candidate.is_dir():
            return candidate.resolve()
        return default_path

    def _remember_last_opened_document(self, path: str | None) -> None:
        if not path:
            return
        candidate = Path(path)
        if candidate.is_file():
            self.settings.setValue(self.SETTINGS_LAST_DOCUMENT, str(candidate.resolve()))

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

    def _saved_trash_enabled_preference(self) -> bool:
        raw = self.settings.value(self.SETTINGS_TRASH_ENABLED_DEFAULT, False)
        return raw in (True, "true", "1", 1)

    def _apply_local_document_defaults(self, document=None) -> None:
        target = document or self.controller.document
        target.editor_settings.trash_enabled = self._saved_trash_enabled_preference()

    def _update_workspace_path_display(self) -> None:
        self.workspace_path_edit.setText(str(self.workdir))

    def _choose_workspace_directory(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "选择 JSON 配置文件所在目录", str(self.workdir))
        if not chosen:
            return
        new_root = Path(chosen).resolve()
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
        self.settings.sync()
        self._update_workspace_path_display()
        self._refresh_file_list()
        self._refresh_node_list_panel()

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

        mode_widget = QWidget(toolbar)
        mode_layout = QHBoxLayout(mode_widget)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.setSpacing(8)
        mode_layout.addWidget(QLabel("编辑模式"))
        self.simple_mode_radio = QRadioButton("简易")
        self.advanced_mode_radio = QRadioButton("高级")
        self.mode_button_group = QButtonGroup(self)
        self.mode_button_group.setExclusive(True)
        self.mode_button_group.addButton(self.simple_mode_radio)
        self.mode_button_group.addButton(self.advanced_mode_radio)
        self.simple_mode_radio.toggled.connect(lambda checked: checked and self.controller.set_global_mode("simple"))
        self.advanced_mode_radio.toggled.connect(lambda checked: checked and self.controller.set_global_mode("advanced"))
        mode_layout.addWidget(self.simple_mode_radio)
        mode_layout.addWidget(self.advanced_mode_radio)
        toolbar.addWidget(mode_widget)

        toolbar.addSeparator()

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

        self.trash_enabled_checkbox = QCheckBox("启用回收站")
        self.trash_enabled_checkbox.toggled.connect(self._toggle_trash_enabled)
        toolbar.addWidget(self.trash_enabled_checkbox)

        toolbar.addSeparator()

        self.restore_layout_button = QPushButton("还原视角")
        self.restore_layout_button.clicked.connect(self._restore_canvas_layout)
        toolbar.addWidget(self.restore_layout_button)

        self.optimize_layout_button = QPushButton("优化连线")
        self.optimize_layout_button.clicked.connect(self._optimize_connection_layout)
        toolbar.addWidget(self.optimize_layout_button)

        self.trash_button = QPushButton("已删除节点")
        self.trash_button.clicked.connect(self._show_trash_dialog)
        toolbar.addWidget(self.trash_button)

        self.file_directory_button = QPushButton("配置文件")
        self.file_directory_button.clicked.connect(self._show_file_directory_dialog)
        toolbar.addWidget(self.file_directory_button)

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

        workspace_card, workspace_layout = self._create_card()
        eyebrow = QLabel("\u5de5\u4f5c\u533a")
        eyebrow.setObjectName("sectionEyebrow")
        workspace_layout.addWidget(eyebrow)
        title = QLabel("\u5df2\u521b\u5efa\u8282\u70b9")
        title.setObjectName("sectionTitle")
        workspace_layout.addWidget(title)
        workspace_hint = QLabel("\u5de6\u4fa7\u9ed8\u8ba4\u7528\u4e8e\u67e5\u770b\u5f53\u524d\u56fe\u5185\u8282\u70b9\uff0c\u914d\u7f6e\u6587\u4ef6\u5207\u6362\u6539\u4e3a\u901a\u8fc7\u9876\u90e8\u5de5\u5177\u680f\u5165\u53e3\u6253\u5f00\u3002")
        workspace_hint.setObjectName("panelHint")
        workspace_hint.setWordWrap(True)
        workspace_layout.addWidget(workspace_hint)

        workspace_row = QHBoxLayout()
        self.workspace_path_edit = QLineEdit()
        self.workspace_path_edit.setObjectName("workspacePathField")
        self.workspace_path_edit.setReadOnly(True)
        self.workspace_path_edit.setPlaceholderText("\u672a\u9009\u62e9\u5de5\u7a0b\u76ee\u5f55")
        self.workspace_path_edit.setToolTip("\u5f53\u524d\u5217\u51fa JSON \u914d\u7f6e\u7684\u6839\u76ee\u5f55\uff0c\u53ef\u76f4\u63a5\u590d\u5236\u8def\u5f84")
        self.choose_workspace_button = QPushButton("\u9009\u62e9\u76ee\u5f55")
        self.choose_workspace_button.setToolTip("\u9009\u62e9\u5b58\u653e JSON \u914d\u7f6e\u6587\u4ef6\u7684\u76ee\u5f55")
        self.choose_workspace_button.clicked.connect(self._choose_workspace_directory)
        self.open_workspace_button = QPushButton("\u6253\u5f00\u6240\u9009\u76ee\u5f55")
        self.open_workspace_button.setToolTip("\u7528\u8d44\u6e90\u7ba1\u7406\u5668\u6253\u5f00\u5f53\u524d\u5de5\u4f5c\u76ee\u5f55")
        self.open_workspace_button.clicked.connect(self._open_workspace_directory)
        workspace_row.addWidget(self.workspace_path_edit, 1)
        workspace_row.addWidget(self.choose_workspace_button, 0)
        workspace_row.addWidget(self.open_workspace_button, 0)
        workspace_layout.addLayout(workspace_row)
        layout.addWidget(workspace_card)

        list_card, list_layout = self._create_card()
        list_eyebrow = QLabel("\u8282\u70b9")
        list_eyebrow.setObjectName("sectionEyebrow")
        list_layout.addWidget(list_eyebrow)
        self.node_search_edit = QLineEdit()
        self.node_search_edit.setPlaceholderText("\u641c\u7d22\u5f53\u524d\u56fe\u5185\u8282\u70b9")
        self.node_search_edit.textChanged.connect(self._refresh_node_list_panel)
        list_layout.addWidget(self.node_search_edit)

        self.node_list = QListWidget()
        self.node_list.setObjectName("nodeDirectoryList")
        self.node_list.itemClicked.connect(self._focus_node_from_list_item)
        self.node_list.itemActivated.connect(self._focus_node_from_list_item)
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

        canvas_top_row = QHBoxLayout()
        canvas_top_row.setContentsMargins(0, 0, 0, 0)
        canvas_top_row.setSpacing(8)
        canvas_top_row.addStretch(1)

        self.search_panel = QFrame()
        self.search_panel.setObjectName("searchPanel")
        self.search_panel.setMaximumWidth(720)
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
        canvas_top_row.addWidget(self.search_panel, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(canvas_top_row)

        self.canvas = NodeCanvasView(self.controller.schema, self.controller)
        self.canvas.selectionSummaryChanged.connect(self._handle_selection_summary)
        self.canvas.interactionBusyChanged.connect(self._handle_canvas_busy_changed)
        layout.addWidget(self.canvas, 1)
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
        mode_label = QLabel("\u7f16\u8f91\u6a21\u5f0f")
        mode_label.setObjectName("searchTitle")
        mode_block.addWidget(mode_label)

        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(8)
        self.simple_mode_radio = QRadioButton("\u7b80\u6613")
        self.advanced_mode_radio = QRadioButton("\u9ad8\u7ea7")
        self.mode_button_group = QButtonGroup(self)
        self.mode_button_group.setExclusive(True)
        self.mode_button_group.addButton(self.simple_mode_radio)
        self.mode_button_group.addButton(self.advanced_mode_radio)
        self.simple_mode_radio.toggled.connect(lambda checked: checked and self.controller.set_global_mode("simple"))
        self.advanced_mode_radio.toggled.connect(lambda checked: checked and self.controller.set_global_mode("advanced"))
        mode_row.addWidget(self.simple_mode_radio)
        mode_row.addWidget(self.advanced_mode_radio)
        mode_row.addStretch(1)
        mode_block.addLayout(mode_row)

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
        self.trash_enabled_checkbox = QCheckBox("启用回收站")
        self.trash_enabled_checkbox.toggled.connect(self._toggle_trash_enabled)
        mode_block.addWidget(self.numeric_linkage_checkbox)
        mode_block.addWidget(self.trash_enabled_checkbox)
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
        self.trash_button = QPushButton("\u5df2\u5220\u9664\u8282\u70b9")
        self.trash_button.clicked.connect(self._show_trash_dialog)
        self.file_directory_button = QPushButton("配置文件")
        self.file_directory_button.clicked.connect(self._show_file_directory_dialog)
        action_row.addWidget(self.restore_layout_button)
        action_row.addWidget(self.optimize_layout_button)
        action_row.addWidget(self.trash_button)
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
        file_menu = self.menuBar().addMenu("文件")
        edit_menu = self.menuBar().addMenu("编辑")
        view_menu = self.menuBar().addMenu("视图")
        tools_menu = self.menuBar().addMenu("工具")
        help_menu = self.menuBar().addMenu("帮助")

        open_action = QAction("\u6253\u5f00", self)
        open_action.triggered.connect(self._open_dialog)
        file_menu.addAction(open_action)
        self._register_shortcut_action("open", open_action, QKeySequence(QKeySequence.StandardKey.Open))

        self.save_action = QAction("\u4fdd\u5b58", self)
        self.save_action.triggered.connect(self._save_current_file)
        file_menu.addAction(self.save_action)
        self._register_shortcut_action("save", self.save_action, QKeySequence(QKeySequence.StandardKey.Save))

        self.export_csv_action = QAction("\u5bfc\u51fa\u5230 CSV", self)
        self.export_csv_action.triggered.connect(self._show_export_csv_dialog)
        self.menuBar().addAction(self.export_csv_action)
        self._register_shortcut_action("export_csv", self.export_csv_action, QKeySequence())

        template_create_action = QAction("模板创建", self)
        template_create_action.triggered.connect(self._create_templates_from_csv_dialog)
        tools_menu.addAction(template_create_action)
        self._register_shortcut_action("template_create", template_create_action, QKeySequence())

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

        self.trash_action = QAction("\u5df2\u5220\u9664\u8282\u70b9", self)
        self.trash_action.triggered.connect(self._show_trash_dialog)
        view_menu.addAction(self.trash_action)
        self._register_shortcut_action("trash_dialog", self.trash_action, QKeySequence())

        csv_action = QAction("CSV \u9884\u89c8", self)
        csv_action.triggered.connect(self._show_csv_preview)
        view_menu.addAction(csv_action)
        self._register_shortcut_action("csv_preview", csv_action, QKeySequence())

        node_directory_action = QAction("节点目录", self)
        node_directory_action.triggered.connect(self._show_node_directory_dialog)
        view_menu.addAction(node_directory_action)
        self._register_shortcut_action("node_directory", node_directory_action, QKeySequence("Ctrl+G"))

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

        mode_menu = view_menu.addMenu("\u7f16\u8f91\u6a21\u5f0f")
        mode_group = QActionGroup(self)
        mode_group.setExclusive(True)
        self.simple_mode_action = QAction("\u7b80\u6613\u6a21\u5f0f", self, checkable=True)
        self.advanced_mode_action = QAction("\u9ad8\u7ea7\u6a21\u5f0f", self, checkable=True)
        self.simple_mode_action.triggered.connect(lambda: self.controller.set_global_mode("simple"))
        self.advanced_mode_action.triggered.connect(lambda: self.controller.set_global_mode("advanced"))
        mode_group.addAction(self.simple_mode_action)
        mode_group.addAction(self.advanced_mode_action)
        mode_menu.addAction(self.simple_mode_action)
        mode_menu.addAction(self.advanced_mode_action)

        help_doc_action = QAction("使用说明", self)
        help_doc_action.triggered.connect(self._open_help_page)
        help_menu.addAction(help_doc_action)
        self._register_shortcut_action("help_docs", help_doc_action, QKeySequence("F1"))

        changelog_action = QAction("更新日志", self)
        changelog_action.triggered.connect(self._open_changelog_page)
        help_menu.addAction(changelog_action)
        self._register_shortcut_action("help_changelog", changelog_action, QKeySequence("Ctrl+F1"))

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
        self.controller.preferences.global_mode = document.global_mode
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

    def _create_blank_document_session(self, *, group_dir: str = "") -> None:
        document = create_document(self.controller.schema)
        self._apply_local_document_defaults(document)
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

    def _update_save_action_state(self) -> None:
        allowed, _reason = self.controller.can_create_graph_content()
        can_save = bool(self.controller.document.path) or allowed
        if hasattr(self, "save_action"):
            self.save_action.setEnabled(can_save)

    def _apply_saved_preferences(self) -> None:
        mode = self.settings.value("ui/global_mode", self.controller.preferences.global_mode)
        if isinstance(mode, str):
            self.controller.set_global_mode(mode)
        debug_json_fields = self.settings.value("ui/debug_json_field_names", self.controller.preferences.debug_json_field_names)
        debug_enabled = debug_json_fields in (True, "true", "1", 1)
        self.controller.preferences.debug_json_field_names = bool(debug_enabled)
        self.debug_json_fields_action.setChecked(bool(debug_enabled))
        self.simple_mode_action.setChecked(self.controller.preferences.global_mode == "simple")
        self.advanced_mode_action.setChecked(self.controller.preferences.global_mode == "advanced")
        self.simple_mode_radio.setChecked(self.controller.preferences.global_mode == "simple")
        self.advanced_mode_radio.setChecked(self.controller.preferences.global_mode == "advanced")
        self._apply_local_document_defaults(self.controller.document)
        self._handle_interaction_creation_mode_changed(self.controller.document.interaction_creation_mode)
        self._apply_wheel_settings(self._wheel_shortcut_settings())
        self._handle_editor_settings_changed(self.controller.document.editor_settings)
        self._update_save_action_state()

    def _set_debug_json_field_names(self, enabled: bool) -> None:
        self.controller.preferences.debug_json_field_names = enabled
        self.settings.setValue("ui/debug_json_field_names", enabled)
        if self.controller.selected_node_uuid:
            node = self.controller.get_node(self.controller.selected_node_uuid)
            if node:
                self.inspector_form.set_node(
                    node,
                    self.controller.preferences.global_mode,
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
        group_dir = self._current_group_dir()
        if not self._ensure_safe_to_leave_document(self.workdir / "__new__"):
            return
        self._stash_current_document_session()
        self._create_blank_document_session(group_dir=group_dir)
        self._refresh_file_list()
        self._show_status("已创建草稿，请先完善初始节点后保存。")

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
        self._commit_active_editor_change()
        allowed, reason = self.controller.can_create_graph_content()
        if not allow_incomplete and not allowed:
            self._focus_initial_node_guidance(reason)
            QMessageBox.warning(self, "\u65e0\u6cd5\u4fdd\u5b58", f"{reason}\n\u9996\u6b21\u4fdd\u5b58\u524d\u8bf7\u5148\u5b8c\u6210\u521d\u59cb\u8282\u70b9\u3002")
            return None
        target = self.controller.document.path
        path_changed = not bool(target)
        self._refresh_file_list_after_save = path_changed
        if not target:
            generated = self._generated_save_path()
            if not generated:
                self._refresh_file_list_after_save = False
                self._focus_initial_node_guidance(reason)
                if not silent:
                    QMessageBox.warning(self, "\u65e0\u6cd5\u4fdd\u5b58", "\u8bf7\u5148\u5b8c\u6210\u521d\u59cb\u8282\u70b9\u5185\u5bb9\uff0c\u518d\u751f\u6210\u914d\u7f6e\u6587\u4ef6\u3002")
                return None
            target = str(generated)
        saved = self.controller.save_document(target)
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

    def _commit_active_editor_change(self) -> None:
        focus_widget = self.focusWidget()
        if isinstance(focus_widget, NumericLineEdit):
            focus_widget._emit_commit()
            return
        if isinstance(focus_widget, CommitLineEdit):
            focus_widget._emit_commit()
            return
        if isinstance(focus_widget, CommitPlainTextEdit):
            focus_widget.committed.emit(focus_widget.toPlainText())
            return
        if isinstance(focus_widget, CommitComboBox):
            focus_widget.committed.emit(focus_widget.currentData())
            return
        if isinstance(focus_widget, QLineEdit):
            parent = focus_widget.parent()
            if isinstance(parent, ColorFieldWidget):
                parent.committed.emit(focus_widget.text().strip())
                return
        if hasattr(self, "inspector_form"):
            self.inspector_form.commit_pending_edits()

    def _commit_pending_editor_changes(self) -> None:
        if hasattr(self, "inspector_form"):
            self.inspector_form.commit_pending_edits()
        if hasattr(self, "canvas"):
            for item in self.canvas.node_items.values():
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

    def _copy_selection(self) -> None:
        focus_widget = self.focusWidget()
        if isinstance(focus_widget, (QLineEdit, QPlainTextEdit)):
            focus_widget.copy()
            return
        node_uuids = self.canvas.selected_node_uuids()
        payload = self.controller.serialize_selection(node_uuids)
        if not payload:
            return
        from PyQt6.QtCore import QMimeData

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
        if not mime or not mime.hasFormat(CLIPBOARD_MIME):
            return
        payload = bytes(mime.data(CLIPBOARD_MIME))
        position = self._next_paste_position(payload)
        self.controller.paste_payload(payload, position)

    def _duplicate_selection(self) -> None:
        node_uuids = self.canvas.selected_node_uuids()
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
        node_uuids = self.canvas.selected_node_uuids()
        connection_pairs = self.canvas.selected_connection_pairs()
        if node_uuids:
            self.controller.remove_nodes(node_uuids)
        for from_uuid, to_uuid in connection_pairs:
            self.controller.remove_connection(from_uuid, to_uuid)

    def _commit_inspector_field(self, key: str, value) -> None:
        node_uuid = self.controller.selected_node_uuid
        if node_uuid:
            self.controller.update_field(node_uuid, key, value, self.controller.preferences.global_mode)

    def _update_inspector(self, node_uuid: str | None) -> None:
        node = self.controller.get_node(node_uuid) if node_uuid else None
        if node:
            self.inspector_form.set_node(
                node,
                self.controller.preferences.global_mode,
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
                    self.controller.preferences.global_mode,
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

    def _jump_to_search_result(self, item: QListWidgetItem) -> None:
        node_uuid = item.data(Qt.ItemDataRole.UserRole)
        self.canvas.focus_on_node(node_uuid, target_scale=1.05, emphasize=False)
        if node_uuid in self.canvas.node_items:
            self.canvas.node_items[node_uuid].setSelected(True)

    def _restore_canvas_layout(self) -> None:
        self.canvas.reset_view_layout()

    def _show_csv_preview(self) -> None:
        self.csv_dialog.show()
        self.csv_dialog.raise_()
        self.csv_dialog.activateWindow()

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
        title = "L2D交互图表编辑器"
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
        if hasattr(self, "trash_enabled_checkbox"):
            blocked = self.trash_enabled_checkbox.blockSignals(True)
            self.trash_enabled_checkbox.setChecked(bool(settings.trash_enabled))
            self.trash_enabled_checkbox.blockSignals(blocked)
        if hasattr(self, "trash_button"):
            self.trash_button.setEnabled(bool(settings.trash_enabled))
        if hasattr(self, "trash_action"):
            self.trash_action.setEnabled(bool(settings.trash_enabled))

    def _toggle_numeric_linkage(self, checked: bool) -> None:
        if bool(self.controller.document.editor_settings.numeric_linkage_enabled) == bool(checked):
            return
        self.controller.set_numeric_linkage_enabled(bool(checked))

    def _toggle_trash_enabled(self, checked: bool) -> None:
        checked = bool(checked)
        if bool(self.controller.document.editor_settings.trash_enabled) == checked:
            return
        if not checked:
            reply = QMessageBox.question(self, "关闭回收站", "关闭后会清空当前回收站并立即释放编号槽位，是否继续？")
            if reply != QMessageBox.StandardButton.Yes:
                self._handle_editor_settings_changed(self.controller.document.editor_settings)
                return
        self.controller.set_trash_enabled(checked)
        self.settings.setValue(self.SETTINGS_TRASH_ENABLED_DEFAULT, checked)
        self.settings.sync()

    def _open_help_page(self) -> None:
        QDesktopServices.openUrl(QUrl.fromUserInput(HELP_PAGE_URL))

    def _open_changelog_page(self) -> None:
        QDesktopServices.openUrl(QUrl.fromUserInput(HELP_PAGE_URL))

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
            self.canvas.focus_on_node(node_uuid, target_scale=1.15, emphasize=True)
            if node_uuid in self.canvas.node_items:
                self.canvas.node_items[node_uuid].setSelected(True)

    def _focus_node_from_list_item(self, item: QListWidgetItem) -> None:
        node_uuid = item.data(Qt.ItemDataRole.UserRole) if item else None
        if isinstance(node_uuid, str) and node_uuid:
            self._focus_node_from_directory(node_uuid)

    def _refresh_node_list_panel(self) -> None:
        if not hasattr(self, "node_list"):
            return
        current_selection = self.controller.selected_node_uuid
        needle = self.node_search_edit.text().strip().lower() if hasattr(self, "node_search_edit") else ""
        self.node_list.clear()
        for node in self.controller.document.nodes:
            label = self.controller.node_summary(node.uuid)
            if needle and needle not in label.lower():
                continue
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, node.uuid)
            self.node_list.addItem(item)
            if current_selection and current_selection == node.uuid:
                self.node_list.setCurrentItem(item)

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

    def _create_templates_from_csv_dialog(self) -> None:
        csv_path, _ = QFileDialog.getOpenFileName(self, "选择模板 CSV", str(self.workdir), "CSV Files (*.csv)")
        if not csv_path:
            return
        try:
            created_files, created_folders = self._create_templates_from_csv(Path(csv_path))
        except Exception as exc:
            QMessageBox.warning(self, "模板创建失败", str(exc))
            return
        self._refresh_file_list()
        QMessageBox.information(
            self,
            "模板创建完成",
            f"已创建 {created_files} 个 JSON，输出到 {created_folders} 个版本目录。\n当前工作区：{self.workdir}",
        )

    def _create_templates_from_csv(self, csv_path: Path) -> tuple[int, int]:
        rows = load_template_csv_rows(csv_path)
        if not rows:
            raise ValueError("CSV 中没有可用数据行。")
        created_files = 0
        created_folders: set[str] = set()
        for row in rows:
            version = str(row.get("version") or "").strip()
            folder_name = build_template_version_folder_name(version)
            target_dir = self.workdir / folder_name
            target_dir.mkdir(parents=True, exist_ok=True)
            document = create_template_document(
                self.controller.schema,
                version=version,
                char_name=str(row.get("CharName") or "").strip(),
                memo=str(row.get("memo") or "").strip(),
                ship_skin_id=int(row.get("ship_skin_id") or 0),
            )
            output_path = self._available_template_output_path(target_dir, str(row.get("CharName") or "").strip())
            save_document(self.controller.schema, document, output_path)
            created_files += 1
            created_folders.add(folder_name)
        return created_files, len(created_folders)

    def _available_template_output_path(self, directory: Path, char_name: str) -> Path:
        base_name = self._sanitize_filename_stem(char_name or "config")
        candidate = directory / f"{base_name}.json"
        if not candidate.exists():
            return candidate
        index = 2
        while True:
            numbered = directory / f"{base_name}_{index}.json"
            if not numbered.exists():
                return numbered
            index += 1

    def _focus_selected_node(self) -> None:
        node_uuid = self.controller.selected_node_uuid
        if not node_uuid:
            selected = self.canvas.selected_node_uuids()
            node_uuid = selected[0] if selected else None
        if not node_uuid:
            return
        self.canvas.focus_on_node(node_uuid, target_scale=1.15, emphasize=True)
        if node_uuid in self.canvas.node_items:
            self.canvas.node_items[node_uuid].setSelected(True)

    def _handle_selection_summary(self, node_uuids, connection_pairs) -> None:
        del connection_pairs
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
        self.search_edit.setFocus()
        self.search_edit.selectAll()

    def _focus_file_search(self) -> None:
        self._show_file_directory_dialog()
        self.file_search_edit.setFocus()
        self.file_search_edit.selectAll()

    def _jump_to_validation_node(self, node_uuid: str) -> None:
        self.canvas.focus_on_node(node_uuid, target_scale=1.25, emphasize=True)
        if node_uuid in self.canvas.node_items:
            self.canvas.node_items[node_uuid].setSelected(True)

    def _focus_initial_node_guidance(self, _reason: str = "") -> None:
        initial = next((node for node in self.controller.document.nodes if node.type == "Initial"), None)
        if not initial:
            return
        self.canvas.focus_on_node(initial.uuid, target_scale=1.35, emphasize=True)
        if initial.uuid in self.canvas.node_items:
            self.canvas.node_items[initial.uuid].setSelected(True)

    def _store_validation(self, issues) -> None:
        validation_cache: dict[str, list] = {}
        for issue in issues:
            validation_cache.setdefault(issue.node_uuid, []).append(issue)
        self.validation_cache = validation_cache
        if self.controller.selected_node_uuid:
            self.validation_summary.set_issues(validation_cache.get(self.controller.selected_node_uuid, []))

    def _update_document_state(self, state) -> None:
        if state.is_meta_ready:
            self.inspector_meta.setText("\u521d\u59cb\u8282\u70b9\u5df2\u5b8c\u6210\uff0c\u5141\u8bb8\u521b\u5efa\u8282\u70b9\u4e0e\u8fde\u7ebf\u3002")
        else:
            self.inspector_meta.setText(f"\u9700\u5148\u5b8c\u6210\u521d\u59cb\u8282\u70b9\u5b57\u6bb5: {' / '.join(state.meta_missing_fields)}")
        self._update_save_action_state()

    def _handle_interaction_creation_mode_changed(self, mode: str) -> None:
        self.auto_create_rule_radio.setChecked(mode == "auto")
        self.manual_create_rule_radio.setChecked(mode == "manual")

    def _handle_global_mode_changed(self, mode: str) -> None:
        self.settings.setValue("ui/global_mode", mode)
        self.simple_mode_action.setChecked(mode == "simple")
        self.advanced_mode_action.setChecked(mode == "advanced")
        self.simple_mode_radio.setChecked(mode == "simple")
        self.advanced_mode_radio.setChecked(mode == "advanced")
        if self.controller.selected_node_uuid:
            node = self.controller.get_node(self.controller.selected_node_uuid)
            if node:
                self.inspector_form.set_node(
                    node,
                    mode,
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

    def _show_trash_dialog(self) -> None:
        if not self.controller.document.editor_settings.trash_enabled:
            return
        if self.trash_dialog is None:
            self.trash_dialog = TrashDialog(self)
            self.trash_dialog.remove_selected_button.clicked.connect(self._clear_selected_trash_entries)
            self.trash_dialog.clear_all_button.clicked.connect(self._clear_all_trash_entries)
            self.trash_dialog.close_button.clicked.connect(self.trash_dialog.close)
        self._refresh_trash_dialog(self.controller.document.trash_bin)
        self.trash_dialog.show()
        self.trash_dialog.raise_()
        self.trash_dialog.activateWindow()

    def _refresh_trash_dialog(self, entries) -> None:
        if self.trash_dialog is not None:
            self.trash_dialog.set_entries(entries)

    def _clear_selected_trash_entries(self) -> None:
        if not self.trash_dialog:
            return
        removed = self.controller.clear_trash_entries(self.trash_dialog.selected_entry_ids())
        if removed:
            self._show_status(f"已清理 {removed} 条垃圾箱记录")

    def _clear_all_trash_entries(self) -> None:
        removed = self.controller.clear_all_trash()
        if removed:
            self._show_status("已清空垃圾箱")

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
        if target_path is not None:
            if self.controller.document.path:
                return bool(self._save_current_file(silent=True, allow_incomplete=True))
            return True
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
        return clicked != cancel_button

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
        if self._confirm_safe_to_close():
            event.accept()
        else:
            event.ignore()

    def _confirm_safe_to_close(self) -> bool:
        self._auto_save_timer.stop()
        self._commit_pending_editor_changes()
        if not self._is_dirty():
            return True
        if not self.controller.document.state.is_meta_ready:
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
        return clicked != cancel_button
