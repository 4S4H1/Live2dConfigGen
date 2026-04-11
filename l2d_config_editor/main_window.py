"""Main application window."""

from __future__ import annotations

import json
import re
from pathlib import Path

from PyQt6.QtCore import QSettings, Qt, QTimer
from PyQt6.QtGui import QAction, QActionGroup, QCloseEvent, QGuiApplication, QKeySequence, QUndoStack
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
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
    QVBoxLayout,
    QWidget,
    QLineEdit,
    QButtonGroup,
)

from .canvas import NodeCanvasView
from .constants import CLIPBOARD_MIME
from .controller import EditorController
from .logic import create_document, load_document
from .widgets import NodeFormWidget, ValidationSummaryWidget


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


class MainWindow(QMainWindow):
    AUTOSAVE_DELAY_MS = 1800
    PASTE_GAP = 96.0

    def __init__(self, workdir: str | Path) -> None:
        super().__init__()
        self.workdir = Path(workdir)
        self.settings = QSettings("OpenAI", "L2DConfigEditor")
        self.controller = EditorController(self)
        self.validation_cache: dict[str, list] = {}
        self.csv_dialog = CsvPreviewDialog(self.controller.schema, self)
        self.trash_dialog: TrashDialog | None = None
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

        self.controller.pathChanged.connect(self._update_window_title)
        self.controller.selectionChanged.connect(self._update_inspector)
        self.controller.csvPreviewChanged.connect(self._update_csv_preview)
        self.controller.statusMessage.connect(self._show_status)
        self.controller.nodeUpdated.connect(self._handle_node_updated)
        self.controller.documentLoaded.connect(self._refresh_search_results)
        self.controller.validationChanged.connect(self._store_validation)
        self.controller.documentStateChanged.connect(self._update_document_state)
        self.controller.globalModeChanged.connect(self._handle_global_mode_changed)
        self.controller.schemaChanged.connect(self._handle_schema_changed)
        self.controller.trashBinChanged.connect(self._refresh_trash_dialog)
        self._set_active_undo_stack(self.controller.undo_stack)

        self.setWindowTitle("L2D Config Editor")
        self.resize(1680, 980)
        self._build_ui()
        self._build_actions()
        self._refresh_file_list()
        self._apply_saved_preferences()
        self._mark_saved_checkpoint(saved=False)
        self._update_window_title(self.controller.document.path)
        self._update_inspector(None)
        self._sync_undo_actions()

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        horizontal = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(horizontal)

        horizontal.addWidget(self._build_file_panel())
        horizontal.addWidget(self._build_canvas_panel())
        horizontal.addWidget(self._build_inspector_panel())
        horizontal.setStretchFactor(0, 0)
        horizontal.setStretchFactor(1, 1)
        horizontal.setStretchFactor(2, 0)

        self.setCentralWidget(root)
        self.setStatusBar(QStatusBar(self))

    def _build_file_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        title = QLabel("配置文件")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        button_row = QHBoxLayout()
        self.refresh_button = QPushButton("刷新")
        self.new_button = QPushButton("新建")
        self.rename_button = QPushButton("重命名")
        self.delete_button = QPushButton("删除")
        self.refresh_button.clicked.connect(self._refresh_file_list)
        self.new_button.clicked.connect(self._create_new_file)
        self.rename_button.clicked.connect(self._rename_selected_file)
        self.delete_button.clicked.connect(self._delete_selected_file)
        for button in (self.refresh_button, self.new_button, self.rename_button, self.delete_button):
            button_row.addWidget(button)
        layout.addLayout(button_row)

        self.file_list = QListWidget()
        self.file_list.setObjectName("configFileList")
        self.file_list.itemClicked.connect(self._handle_file_list_item_clicked)
        self.file_list.itemDoubleClicked.connect(self._open_selected_file)
        layout.addWidget(self.file_list, 1)
        panel.setMinimumWidth(290)
        return panel

    def _build_canvas_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.search_panel = QFrame()
        self.search_panel.setObjectName("searchPanel")
        self.search_panel.setMaximumWidth(460)
        search_layout = QVBoxLayout(self.search_panel)
        search_layout.setContentsMargins(10, 10, 10, 10)
        search_layout.setSpacing(6)
        search_row = QHBoxLayout()
        search_title = QLabel("搜索")
        search_title.setObjectName("searchTitle")
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("输入字段值，例如 idle=13")
        self.search_edit.textChanged.connect(self._refresh_search_results)
        self.restore_layout_button = QPushButton("还原布局")
        self.restore_layout_button.clicked.connect(self._restore_canvas_layout)
        search_row.addWidget(search_title)
        search_row.addWidget(self.search_edit, 1)
        search_row.addWidget(self.restore_layout_button, 0)
        self.search_results = QListWidget()
        self.search_results.setMaximumHeight(180)
        self.search_results.itemClicked.connect(self._jump_to_search_result)
        self.search_results.hide()
        search_layout.addLayout(search_row)
        search_layout.addWidget(self.search_results)
        layout.addWidget(self.search_panel, 0, Qt.AlignmentFlag.AlignLeft)

        self.canvas = NodeCanvasView(self.controller.schema, self.controller)
        self.canvas.selectionSummaryChanged.connect(self._handle_selection_summary)
        layout.addWidget(self.canvas, 1)
        return panel

    def _build_inspector_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.control_panel = QFrame()
        self.control_panel.setObjectName("sidebarToolPanel")
        control_layout = QVBoxLayout(self.control_panel)
        control_layout.setContentsMargins(12, 12, 12, 12)
        control_layout.setSpacing(10)

        mode_block = QVBoxLayout()
        mode_block.setContentsMargins(0, 0, 0, 0)
        mode_block.setSpacing(6)
        mode_label = QLabel("编辑模式")
        mode_label.setObjectName("searchTitle")
        mode_block.addWidget(mode_label)

        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(8)
        self.simple_mode_radio = QRadioButton("简易")
        self.advanced_mode_radio = QRadioButton("高级")
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
        control_layout.addLayout(mode_block)

        actions_block = QVBoxLayout()
        actions_block.setContentsMargins(0, 0, 0, 0)
        actions_block.setSpacing(6)
        actions_label = QLabel("工具")
        actions_label.setObjectName("searchTitle")
        actions_block.addWidget(actions_label)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(8)
        self.optimize_layout_button = QPushButton("优化布局")
        self.optimize_layout_button.clicked.connect(self._optimize_connection_layout)
        self.trash_button = QPushButton("节点垃圾箱")
        self.trash_button.clicked.connect(self._show_trash_dialog)
        action_row.addWidget(self.optimize_layout_button)
        action_row.addWidget(self.trash_button)
        actions_block.addLayout(action_row)

        action_hint = QLabel("优化布局只会整理已连线节点的位置，不会改动节点字段和连线关系。")
        action_hint.setObjectName("sidebarHint")
        action_hint.setWordWrap(True)
        actions_block.addWidget(action_hint)
        control_layout.addLayout(actions_block)

        layout.addWidget(self.control_panel)

        self.inspector_shell = QFrame()
        self.inspector_shell.setObjectName("inspectorShell")
        inspector_layout = QVBoxLayout(self.inspector_shell)
        inspector_layout.setContentsMargins(12, 12, 12, 12)
        inspector_layout.setSpacing(8)

        title = QLabel("Inspector")
        title.setObjectName("sectionTitle")
        inspector_layout.addWidget(title)
        self.inspector_meta = QLabel("")
        self.inspector_meta.setWordWrap(True)
        inspector_layout.addWidget(self.inspector_meta)
        self.inspector_placeholder = QLabel("请选择一个节点")
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
        file_menu = self.menuBar().addMenu("File")
        edit_menu = self.menuBar().addMenu("Edit")
        view_menu = self.menuBar().addMenu("View")

        open_action = QAction("打开", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self._open_dialog)
        file_menu.addAction(open_action)
        self.addAction(open_action)

        self.save_action = QAction("保存", self)
        self.save_action.setShortcut(QKeySequence.StandardKey.Save)
        self.save_action.triggered.connect(self._save_current_file)
        file_menu.addAction(self.save_action)
        self.addAction(self.save_action)

        reload_schema_action = QAction("重载字段配置", self)
        reload_schema_action.triggered.connect(self._reload_schema)
        file_menu.addAction(reload_schema_action)

        self.undo_action = QAction("撤销", self)
        self.undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self.undo_action.triggered.connect(self._trigger_undo)
        edit_menu.addAction(self.undo_action)
        self.addAction(self.undo_action)

        self.redo_action = QAction("重做", self)
        self.redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        self.redo_action.triggered.connect(self._trigger_redo)
        edit_menu.addAction(self.redo_action)
        self.addAction(self.redo_action)

        copy_action = QAction("复制", self)
        copy_action.setShortcut(QKeySequence.StandardKey.Copy)
        copy_action.triggered.connect(self._copy_selection)
        edit_menu.addAction(copy_action)
        self.addAction(copy_action)

        paste_action = QAction("粘贴", self)
        paste_action.setShortcut(QKeySequence.StandardKey.Paste)
        paste_action.triggered.connect(self._paste_selection)
        edit_menu.addAction(paste_action)
        self.addAction(paste_action)

        duplicate_action = QAction("复制节点", self)
        duplicate_action.setShortcut(QKeySequence("Ctrl+D"))
        duplicate_action.triggered.connect(self._duplicate_selection)
        edit_menu.addAction(duplicate_action)
        self.addAction(duplicate_action)

        delete_action = QAction("删除", self)
        delete_action.setShortcut(QKeySequence.StandardKey.Delete)
        delete_action.triggered.connect(self._delete_selection)
        edit_menu.addAction(delete_action)
        self.addAction(delete_action)

        search_action = QAction("搜索", self)
        search_action.setShortcut(QKeySequence.StandardKey.Find)
        search_action.triggered.connect(self._focus_search)
        view_menu.addAction(search_action)
        self.addAction(search_action)

        layout_action = QAction("优化连线布局", self)
        layout_action.triggered.connect(self._optimize_connection_layout)
        view_menu.addAction(layout_action)

        trash_action = QAction("节点垃圾箱", self)
        trash_action.triggered.connect(self._show_trash_dialog)
        view_menu.addAction(trash_action)

        csv_action = QAction("CSV 预览", self)
        csv_action.triggered.connect(self._show_csv_preview)
        view_menu.addAction(csv_action)

        self.debug_json_fields_action = QAction("调试模式：显示 JSON 字段名", self, checkable=True)
        self.debug_json_fields_action.toggled.connect(self._set_debug_json_field_names)
        view_menu.addAction(self.debug_json_fields_action)

        mode_menu = view_menu.addMenu("编辑模式")
        mode_group = QActionGroup(self)
        mode_group.setExclusive(True)
        self.simple_mode_action = QAction("简易模式", self, checkable=True)
        self.advanced_mode_action = QAction("高级模式", self, checkable=True)
        self.simple_mode_action.triggered.connect(lambda: self.controller.set_global_mode("simple"))
        self.advanced_mode_action.triggered.connect(lambda: self.controller.set_global_mode("advanced"))
        mode_group.addAction(self.simple_mode_action)
        mode_group.addAction(self.advanced_mode_action)
        mode_menu.addAction(self.simple_mode_action)
        mode_menu.addAction(self.advanced_mode_action)

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
        base_name = self._sanitize_filename_stem(self.controller.document.meta.memo or self.controller.document.meta.CharName or "config")
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
        self.file_list.clear()
        grouped: dict[str, list[str]] = {}
        for relative_path in self.controller.file_list(self.workdir):
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
                item = QListWidgetItem(f"配置 · {display_name}")
                item.setData(Qt.ItemDataRole.UserRole, {"kind": "file", "path": relative_path})
                item.setToolTip(relative_path)
                self.file_list.addItem(item)
                if relative_path == current:
                    self.file_list.setCurrentItem(item)

    @staticmethod
    def _group_header_text(group_name: str, count: int, collapsed: bool) -> str:
        arrow = "▶" if collapsed else "▼"
        label = group_name or "根目录"
        return f"[目录] {arrow} {label} · {count} 个配置"

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
        display_name = f"{char_name} ({path.name})" if char_name else path.name
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
        allowed, reason = self.controller.can_create_graph_content()
        if not allow_incomplete and not allowed:
            QMessageBox.warning(self, "无法保存", f"{reason}\n首次保存前请先完成初始节点。")
            return None
        target = self.controller.document.path
        if not target:
            generated = self._generated_save_path()
            if not generated:
                if not silent:
                    QMessageBox.warning(self, "无法保存", "请先完成初始节点内容，再生成配置文件。")
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
                self._show_status(f"已保存 {Path(saved).name}")
            self._refresh_file_list()
            relative_path = self._relative_path_for_document(saved)
            if relative_path:
                self._select_file_in_list(relative_path)
        return saved

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
        self.inspector_placeholder.setVisible(node is None)
        self.inspector_form.setVisible(node is not None)
        self.validation_summary.setVisible(node is not None)
        if node:
            self.inspector_form.set_node(
                node,
                self.controller.preferences.global_mode,
                self.controller.preferences.debug_json_field_names,
            )
            self.validation_summary.set_issues(self.validation_cache.get(node.uuid, []))
        else:
            self.validation_summary.set_issues([])

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
        self.canvas.center_on_node(node_uuid)
        self.canvas.flash_node(node_uuid)
        if node_uuid in self.canvas.node_items:
            self.canvas.node_items[node_uuid].setSelected(True)

    def _restore_canvas_layout(self) -> None:
        self.canvas.reset_view_layout()

    def _show_csv_preview(self) -> None:
        self.csv_dialog.show()
        self.csv_dialog.raise_()
        self.csv_dialog.activateWindow()

    def _update_csv_preview(self, rows) -> None:
        self.csv_dialog.update_rows(rows)

    def _update_window_title(self, path: str | None) -> None:
        title = "L2D Config Editor"
        if path:
            title = f"{Path(path).name} - {title}"
        if self._is_dirty():
            title = f"* {title}"
        self.setWindowTitle(title)

    def _show_status(self, message: str) -> None:
        self.statusBar().showMessage(message, 4000)

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

    def _store_validation(self, issues) -> None:
        validation_cache: dict[str, list] = {}
        for issue in issues:
            validation_cache.setdefault(issue.node_uuid, []).append(issue)
        self.validation_cache = validation_cache
        if self.controller.selected_node_uuid:
            self.validation_summary.set_issues(validation_cache.get(self.controller.selected_node_uuid, []))

    def _update_document_state(self, state) -> None:
        if state.is_meta_ready:
            self.inspector_meta.setText("初始节点已完成，允许创建节点与连线。")
        else:
            self.inspector_meta.setText(f"需先完成初始节点字段：{' / '.join(state.meta_missing_fields)}")
        self._update_save_action_state()

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
        if self._ensure_safe_to_leave_document(None):
            event.accept()
        else:
            event.ignore()
