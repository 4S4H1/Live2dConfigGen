"""Main application window."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QAction, QActionGroup, QGuiApplication, QKeySequence
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
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QLineEdit,
)

from .canvas import NodeCanvasView
from .constants import CLIPBOARD_MIME
from .controller import EditorController
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


class MainWindow(QMainWindow):
    def __init__(self, workdir: str | Path) -> None:
        super().__init__()
        self.workdir = Path(workdir)
        self.settings = QSettings("OpenAI", "L2DConfigEditor")
        self.controller = EditorController(self)
        self.validation_cache: dict[str, list] = {}
        self.csv_dialog = CsvPreviewDialog(self.controller.schema, self)
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
        self.setWindowTitle("L2D Config Editor")
        self.resize(1680, 980)
        self._build_ui()
        self._build_actions()
        self._refresh_file_list()
        self._apply_saved_preferences()
        self._update_window_title(self.controller.document.path)
        self._update_inspector(None)

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
        self.file_list.itemDoubleClicked.connect(self._open_selected_file)
        layout.addWidget(self.file_list, 1)
        panel.setMinimumWidth(270)
        return panel

    def _build_canvas_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        self.search_panel = QFrame()
        self.search_panel.setObjectName("searchPanel")
        search_layout = QVBoxLayout(self.search_panel)
        search_layout.setContentsMargins(10, 10, 10, 10)
        search_layout.setSpacing(6)
        search_title = QLabel("搜索")
        search_title.setObjectName("searchTitle")
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("输入字段值，例如 idle=13")
        self.search_edit.textChanged.connect(self._refresh_search_results)
        self.search_results = QListWidget()
        self.search_results.setMaximumHeight(180)
        self.search_results.itemClicked.connect(self._jump_to_search_result)
        self.search_results.hide()
        search_layout.addWidget(search_title)
        search_layout.addWidget(self.search_edit)
        search_layout.addWidget(self.search_results)
        top_row.addWidget(self.search_panel, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        top_row.addStretch(1)
        layout.addLayout(top_row)

        self.canvas = NodeCanvasView(self.controller.schema, self.controller)
        self.canvas.selectionSummaryChanged.connect(self._handle_selection_summary)
        layout.addWidget(self.canvas, 1)
        return panel

    def _build_inspector_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        title = QLabel("Inspector")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        self.inspector_meta = QLabel("")
        self.inspector_meta.setWordWrap(True)
        layout.addWidget(self.inspector_meta)
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
        layout.addWidget(self.inspector_placeholder)
        layout.addWidget(scroll, 1)
        panel.setMinimumWidth(360)
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

        save_action = QAction("保存", self)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self._save_current_file)
        file_menu.addAction(save_action)
        self.addAction(save_action)

        reload_schema_action = QAction("重载字段配置", self)
        reload_schema_action.triggered.connect(self._reload_schema)
        file_menu.addAction(reload_schema_action)

        undo_action = self.controller.undo_stack.createUndoAction(self, "撤销")
        undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        edit_menu.addAction(undo_action)
        self.addAction(undo_action)

        redo_action = self.controller.undo_stack.createRedoAction(self, "重做")
        redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        edit_menu.addAction(redo_action)
        self.addAction(redo_action)

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

        csv_action = QAction("CSV 预览", self)
        csv_action.triggered.connect(self._show_csv_preview)
        view_menu.addAction(csv_action)

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

    def _apply_saved_preferences(self) -> None:
        self.simple_mode_action.setChecked(self.controller.preferences.global_mode == "simple")
        self.advanced_mode_action.setChecked(self.controller.preferences.global_mode == "advanced")

    def _refresh_file_list(self) -> None:
        current = Path(self.controller.document.path).name if self.controller.document.path else None
        self.file_list.clear()
        for name in self.controller.file_list(self.workdir):
            item = QListWidgetItem(name)
            self.file_list.addItem(item)
            if name == current:
                item.setSelected(True)

    def _create_new_file(self) -> None:
        name, ok = QInputDialog.getText(self, "新建配置", "文件名")
        if not ok or not name.strip():
            return
        filename = name.strip()
        if not filename.endswith(".json"):
            filename += ".json"
        path = self.workdir / filename
        if path.exists():
            QMessageBox.warning(self, "文件已存在", f"{filename} 已存在")
            return
        self.controller.new_document()
        self.controller.save_document(str(path))
        self._refresh_file_list()
        self._select_file_in_list(filename)

    def _rename_selected_file(self) -> None:
        item = self.file_list.currentItem()
        if not item:
            return
        old_path = self.workdir / item.text()
        new_name, ok = QInputDialog.getText(self, "重命名配置", "新文件名", text=item.text())
        if not ok or not new_name.strip():
            return
        filename = new_name.strip()
        if not filename.endswith(".json"):
            filename += ".json"
        new_path = self.workdir / filename
        old_path.rename(new_path)
        if self.controller.document.path == str(old_path):
            self.controller.document.path = str(new_path)
            self.controller.pathChanged.emit(str(new_path))
        self._refresh_file_list()
        self._select_file_in_list(filename)

    def _delete_selected_file(self) -> None:
        item = self.file_list.currentItem()
        if not item:
            return
        path = self.workdir / item.text()
        reply = QMessageBox.question(self, "删除配置", f"确认删除 {path.name} 吗？")
        if reply != QMessageBox.StandardButton.Yes:
            return
        path.unlink(missing_ok=True)
        if self.controller.document.path == str(path):
            self.controller.new_document()
        self._refresh_file_list()

    def _open_selected_file(self, item: QListWidgetItem) -> None:
        path = self.workdir / item.text()
        self.controller.open_document(path)
        self._refresh_file_list()

    def _open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "打开配置", str(self.workdir), "JSON Files (*.json)")
        if not path:
            return
        self.controller.open_document(path)
        self._refresh_file_list()
        self._select_file_in_list(Path(path).name)

    def _save_current_file(self) -> None:
        target = self.controller.document.path
        if not target:
            target, _ = QFileDialog.getSaveFileName(self, "保存配置", str(self.workdir / "config.json"), "JSON Files (*.json)")
            if not target:
                return
        saved = self.controller.save_document(target)
        if saved:
            self._show_status(f"已保存 {Path(saved).name}")
            self._refresh_file_list()
            self._select_file_in_list(Path(saved).name)

    def _copy_selection(self) -> None:
        node_uuids = self.canvas.selected_node_uuids()
        payload = self.controller.serialize_selection(node_uuids)
        if not payload:
            return
        from PyQt6.QtCore import QMimeData

        data = QMimeData()
        data.setData(CLIPBOARD_MIME, payload)
        QGuiApplication.clipboard().setMimeData(data)
        self._show_status("已复制节点")

    def _paste_selection(self) -> None:
        mime = QGuiApplication.clipboard().mimeData()
        if not mime or not mime.hasFormat(CLIPBOARD_MIME):
            return
        payload = bytes(mime.data(CLIPBOARD_MIME))
        self.controller.paste_payload(payload, self.canvas.paste_position())

    def _duplicate_selection(self) -> None:
        node_uuids = self.canvas.selected_node_uuids()
        payload = self.controller.serialize_selection(node_uuids)
        if not payload:
            return
        x, y = self.canvas.paste_position()
        self.controller.paste_payload(payload, (x + 42, y + 42))

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
            self.inspector_form.set_node(node, self.controller.preferences.global_mode)
            self.validation_summary.set_issues(self.validation_cache.get(node.uuid, []))
        else:
            self.validation_summary.set_issues([])

    def _handle_node_updated(self, node_uuid: str) -> None:
        if node_uuid == self.controller.selected_node_uuid:
            node = self.controller.get_node(node_uuid)
            if node:
                self.inspector_form.set_node(node, self.controller.preferences.global_mode)
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
        self.setWindowTitle(title)

    def _show_status(self, message: str) -> None:
        self.statusBar().showMessage(message, 4000)

    def _handle_selection_summary(self, node_uuids, connection_pairs) -> None:
        del connection_pairs
        if len(node_uuids) == 1:
            self.controller.set_selected_node(node_uuids[0])
        elif not node_uuids:
            self.controller.set_selected_node(None)

    def _select_file_in_list(self, filename: str) -> None:
        for index in range(self.file_list.count()):
            item = self.file_list.item(index)
            if item.text() == filename:
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
            self.inspector_meta.setText("Initial 节点已完成，允许创建节点与连线。")
        else:
            self.inspector_meta.setText(f"需先完成 Initial 节点字段：{' / '.join(state.meta_missing_fields)}")

    def _handle_global_mode_changed(self, mode: str) -> None:
        self.settings.setValue("ui/global_mode", mode)
        self.simple_mode_action.setChecked(mode == "simple")
        self.advanced_mode_action.setChecked(mode == "advanced")
        if self.controller.selected_node_uuid:
            node = self.controller.get_node(self.controller.selected_node_uuid)
            if node:
                self.inspector_form.set_node(node, mode)

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
