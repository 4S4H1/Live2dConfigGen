"""Main application window."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication, QKeySequence
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .constants import CLIPBOARD_MIME, CSV_COLUMNS
from .controller import EditorController
from .widgets import NodeFormWidget
from .canvas import NodeCanvasView


class MainWindow(QMainWindow):
    def __init__(self, workdir: str | Path) -> None:
        super().__init__()
        self.workdir = Path(workdir)
        self.controller = EditorController(self)
        self.controller.pathChanged.connect(self._update_window_title)
        self.controller.selectionChanged.connect(self._update_inspector)
        self.controller.csvPreviewChanged.connect(self._update_csv_preview)
        self.controller.statusMessage.connect(self._show_status)
        self.controller.nodeUpdated.connect(self._handle_node_updated)
        self.controller.documentLoaded.connect(self._refresh_search_results)
        self.setWindowTitle("L2D Config Editor")
        self.resize(1600, 980)
        self._build_ui()
        self._build_actions()
        self._refresh_file_list()
        self._update_window_title(self.controller.document.path)
        self._update_inspector(None)

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        horizontal = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(horizontal)

        horizontal.addWidget(self._build_file_panel())

        center_split = QSplitter(Qt.Orientation.Vertical)
        self.canvas = NodeCanvasView(self.controller)
        self.canvas.selectionSummaryChanged.connect(self._handle_selection_summary)
        center_split.addWidget(self.canvas)
        center_split.addWidget(self._build_bottom_panel())
        center_split.setStretchFactor(0, 4)
        center_split.setStretchFactor(1, 2)
        horizontal.addWidget(center_split)

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
        title = QLabel("文件")
        title.setStyleSheet("font-weight: 600;")
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
        panel.setMinimumWidth(290)
        return panel

    def _build_inspector_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Inspector")
        title.setStyleSheet("font-weight: 600;")
        layout.addWidget(title)
        self.inspector_placeholder = QLabel("请选择一个节点")
        self.inspector_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.inspector_form = NodeFormWidget(inline=False)
        self.inspector_form.fieldCommitted.connect(self._commit_inspector_field)
        self.inspector_form.modeRequested.connect(self._commit_inspector_mode)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.inspector_form)
        layout.addWidget(self.inspector_placeholder)
        layout.addWidget(scroll, 1)
        panel.setMinimumWidth(360)
        return panel

    def _build_bottom_panel(self) -> QWidget:
        self.bottom_tabs = QTabWidget()
        self.bottom_tabs.addTab(self._build_search_panel(), "搜索")
        self.bottom_tabs.addTab(self._build_csv_panel(), "CSV预览")
        return self.bottom_tabs

    def _build_search_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        from PyQt6.QtWidgets import QLineEdit

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("输入字段值，例如 idle=13")
        self.search_edit.textChanged.connect(self._refresh_search_results)
        self.search_results = QListWidget()
        self.search_results.itemClicked.connect(self._jump_to_search_result)
        layout.addWidget(self.search_edit)
        layout.addWidget(self.search_results, 1)
        return panel

    def _build_csv_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.csv_table = QTableWidget(0, len(CSV_COLUMNS))
        self.csv_table.setHorizontalHeaderLabels(CSV_COLUMNS)
        self.csv_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.csv_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.csv_table)
        return panel

    def _build_actions(self) -> None:
        save_action = self.menuBar().addAction("保存")
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self._save_current_file)
        self.addAction(save_action)

        undo_action = self.controller.undo_stack.createUndoAction(self, "撤销")
        undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self.addAction(undo_action)

        redo_action = self.controller.undo_stack.createRedoAction(self, "重做")
        redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        self.addAction(redo_action)

        copy_action = self.menuBar().addAction("复制")
        copy_action.setShortcut(QKeySequence.StandardKey.Copy)
        copy_action.triggered.connect(self._copy_selection)
        self.addAction(copy_action)

        paste_action = self.menuBar().addAction("粘贴")
        paste_action.setShortcut(QKeySequence.StandardKey.Paste)
        paste_action.triggered.connect(self._paste_selection)
        self.addAction(paste_action)

        duplicate_action = self.menuBar().addAction("复制节点")
        duplicate_action.setShortcut(QKeySequence("Ctrl+D"))
        duplicate_action.triggered.connect(self._duplicate_selection)
        self.addAction(duplicate_action)

        delete_action = self.menuBar().addAction("删除")
        delete_action.setShortcut(QKeySequence.StandardKey.Delete)
        delete_action.triggered.connect(self._delete_selection)
        self.addAction(delete_action)

        search_action = self.menuBar().addAction("搜索")
        search_action.setShortcut(QKeySequence.StandardKey.Find)
        search_action.triggered.connect(self._focus_search)
        self.addAction(search_action)

        open_action = self.menuBar().addAction("打开")
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self._open_dialog)
        self.addAction(open_action)

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
        self.controller.paste_payload(payload, (x + 40, y + 40))

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
            self.controller.update_field(node_uuid, key, value)

    def _commit_inspector_mode(self, mode: str) -> None:
        node_uuid = self.controller.selected_node_uuid
        if node_uuid:
            self.controller.set_mode(node_uuid, mode)

    def _update_inspector(self, node_uuid: str | None) -> None:
        node = self.controller.get_node(node_uuid) if node_uuid else None
        self.inspector_placeholder.setVisible(node is None)
        self.inspector_form.setVisible(node is not None)
        if node:
            self.inspector_form.set_node(node)

    def _handle_node_updated(self, node_uuid: str) -> None:
        if node_uuid == self.controller.selected_node_uuid:
            node = self.controller.get_node(node_uuid)
            if node:
                self.inspector_form.set_node(node)
        if self.search_edit.text().strip():
            self._refresh_search_results()

    def _refresh_search_results(self) -> None:
        text = self.search_edit.text()
        self.search_results.clear()
        if not text.strip():
            return
        for hit in self.controller.search(text):
            item = QListWidgetItem(f"{hit.title} | {hit.field_name}: {hit.preview}")
            item.setData(Qt.ItemDataRole.UserRole, hit.node_uuid)
            self.search_results.addItem(item)

    def _jump_to_search_result(self, item: QListWidgetItem) -> None:
        node_uuid = item.data(Qt.ItemDataRole.UserRole)
        self.canvas.center_on_node(node_uuid)
        self.canvas.flash_node(node_uuid)
        if node_uuid in self.canvas.node_items:
            self.canvas.node_items[node_uuid].setSelected(True)

    def _update_csv_preview(self, rows) -> None:
        self.csv_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column_index, column in enumerate(CSV_COLUMNS):
                self.csv_table.setItem(row_index, column_index, QTableWidgetItem(str(row.values.get(column, ""))))

    def _update_window_title(self, path: str | None) -> None:
        title = "L2D Config Editor"
        if path:
            title = f"{Path(path).name} - {title}"
        self.setWindowTitle(title)

    def _show_status(self, message: str) -> None:
        self.statusBar().showMessage(message, 3000)

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
        self.bottom_tabs.setCurrentIndex(0)
        self.search_edit.setFocus()
