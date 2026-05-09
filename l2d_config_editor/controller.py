"""Controller layer for editor state and commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QUndoStack

from .commands import (
    AddConnectionCommand,
    AddNodesCommand,
    MoveNodeCommand,
    MoveNodesCommand,
    RemoveConnectionCommand,
    RemoveNodesCommand,
    SetGroupsCommand,
    UpdateEditorSettingsCommand,
    UpdateFieldCommand,
    UpdateFieldsCommand,
    UpdateNodeLockCommand,
)
from .constants import CLIPBOARD_MIME
from .logic import (
    _should_persist_target_idle,
    apply_auto_rules,
    apply_node_appearance_defaults,
    create_document,
    create_node,
    sync_comment_legacy_appearance,
    sync_comment_theme_appearance,
    export_document_dict,
    function_node_types,
    infer_manual_fields,
    is_editor_document_file,
    load_document,
    make_trash_entry,
    new_uuid,
    node_title,
    normalized_document_groups,
    normalize_field_input,
    parameter_table_colors,
    parameter_table_id,
    parameter_table_order,
    parameter_table_title,
    reassign_function_ids,
    save_document,
    search_document,
    set_parameter_table_order,
    validate_document,
    document_to_csv_rows,
    ensure_parameter_table_metadata,
    DEFAULT_GROUP_TITLE,
    DEFAULT_PARAMETER_TABLE_TITLE,
    TABLE_ID_FIELD,
    TABLE_TITLE_FIELD,
    TABLE_ORDER_FIELD,
    TABLE_BODY_COLOR_FIELD,
    TABLE_BORDER_COLOR_FIELD,
    TABLE_TEXT_COLOR_FIELD,
)
from .models import ConnectionRecord, DocumentModel, EditorPreferences, GroupRecord, NodeRecord
from .perf_tools import get_performance_recorder
from .schema import load_editor_schema

performance_recorder = get_performance_recorder()


class EditorController(QObject):
    documentLoaded = pyqtSignal()
    nodeAdded = pyqtSignal(str)
    nodeRemoved = pyqtSignal(str)
    nodeUpdated = pyqtSignal(str)
    nodeMoved = pyqtSignal(str)
    connectionsChanged = pyqtSignal()
    validationChanged = pyqtSignal(object)
    csvPreviewChanged = pyqtSignal(object)
    selectionChanged = pyqtSignal(object)
    pathChanged = pyqtSignal(object)
    documentSaved = pyqtSignal(str)
    statusMessage = pyqtSignal(str)
    documentStateChanged = pyqtSignal(object)
    globalModeChanged = pyqtSignal(str)
    interactionCreationModeChanged = pyqtSignal(str)
    schemaChanged = pyqtSignal()
    trashBinChanged = pyqtSignal(object)
    metaActionBlocked = pyqtSignal(str)
    editorSettingsChanged = pyqtSignal(object)
    groupsChanged = pyqtSignal()

    def __init__(self, parent: QObject | None = None, schema_path: str | None = None) -> None:
        super().__init__(parent)
        self.undo_stack = QUndoStack(self)
        self.preferences = EditorPreferences(global_mode="simple", schema_path=schema_path)
        self.schema = load_editor_schema(schema_path)
        self.document = create_document(self.schema)
        self.selected_node_uuid: str | None = None
        self._workspace_root: Path | None = None
        self.refresh_derived()

    def _perf_document_meta(self) -> dict[str, Any]:
        return {
            "node_count": len(self.document.nodes),
            "connection_count": len(self.document.connections),
            "group_count": len(self.document.groups),
            "global_mode": self.preferences.global_mode,
        }

    def set_workspace_root(self, path: str | Path) -> None:
        """Root directory for listing editor JSON files (must stay in sync with MainWindow.workdir)."""
        self._workspace_root = Path(path).resolve()

    def reload_schema(self, schema_path: str | None = None) -> None:
        with performance_recorder.measure("controller.reload_schema", "controller"):
            path = schema_path or self.preferences.schema_path
            self.schema = load_editor_schema(path)
            self.preferences.schema_path = str(path) if path else None
            self.refresh_derived()
            self.schemaChanged.emit()

    def set_global_mode(self, mode: str) -> None:
        if mode == self.preferences.global_mode:
            return
        self.preferences.global_mode = mode
        self.document.global_mode = mode
        self.globalModeChanged.emit(mode)
        for node in self.document.nodes:
            self.nodeUpdated.emit(node.uuid)

    def set_interaction_creation_mode(self, mode: str) -> None:
        normalized = "manual" if mode == "manual" else "auto"
        if self.document.interaction_creation_mode == normalized:
            return
        self.document.interaction_creation_mode = normalized
        self.interactionCreationModeChanged.emit(normalized)

    def set_numeric_linkage_enabled(self, enabled: bool) -> None:
        current = bool(self.document.editor_settings.numeric_linkage_enabled)
        enabled = bool(enabled)
        if current == enabled:
            return
        old_settings = {
            "numeric_linkage_enabled": self.document.editor_settings.numeric_linkage_enabled,
            "trash_enabled": self.document.editor_settings.trash_enabled,
        }
        new_settings = dict(old_settings)
        new_settings["numeric_linkage_enabled"] = enabled
        self.undo_stack.push(UpdateEditorSettingsCommand(self, old_settings, new_settings, label="切换数值联动"))

    def set_trash_enabled(self, enabled: bool) -> None:
        current = bool(self.document.editor_settings.trash_enabled)
        enabled = bool(enabled)
        if current == enabled:
            return
        old_settings = {
            "numeric_linkage_enabled": self.document.editor_settings.numeric_linkage_enabled,
            "trash_enabled": self.document.editor_settings.trash_enabled,
        }
        new_settings = dict(old_settings)
        new_settings["trash_enabled"] = enabled
        old_trash_bin = [entry for entry in self.document.trash_bin]
        new_trash_bin = old_trash_bin if enabled else []
        self.undo_stack.push(
            UpdateEditorSettingsCommand(
                self,
                old_settings,
                new_settings,
                old_trash_bin=old_trash_bin,
                new_trash_bin=new_trash_bin,
                label="切换回收站",
            )
        )

    def set_node_locked(self, node_uuid: str, locked: bool) -> None:
        node = self.get_node(node_uuid)
        if not node or node.locked == bool(locked):
            return
        self.undo_stack.push(UpdateNodeLockCommand(self, node_uuid, node.locked, bool(locked)))

    def new_document(self) -> None:
        with performance_recorder.measure("controller.new_document", "controller"):
            self.undo_stack.clear()
            self.document = create_document(self.schema)
            self.preferences.global_mode = self.document.global_mode
            self.selected_node_uuid = None
            self.globalModeChanged.emit(self.preferences.global_mode)
            self.documentLoaded.emit()
            self.pathChanged.emit(None)
            self.refresh_derived()

    def open_document(self, path: str | Path) -> None:
        with performance_recorder.measure("controller.open_document", "controller", {"path": str(path)}):
            self.undo_stack.clear()
            self.document = load_document(self.schema, path)
            self.preferences.global_mode = self.document.global_mode
            self.selected_node_uuid = None
            self.globalModeChanged.emit(self.preferences.global_mode)
            self.documentLoaded.emit()
            self.pathChanged.emit(str(path))
            self.refresh_derived()

    def save_document(self, path: str | None = None) -> str | None:
        target = path or self.document.path
        if not target:
            return None
        with performance_recorder.measure("controller.save_document", "controller", {"path": str(target)}):
            self.document.global_mode = self.preferences.global_mode
            save_document(self.schema, self.document, target)
            self.pathChanged.emit(str(target))
            self.documentSaved.emit(str(target))
            self.refresh_derived()
            return str(target)

    def refresh_derived(self) -> None:
        with performance_recorder.measure("controller.refresh_derived", "controller", self._perf_document_meta()):
            self._ensure_parameter_table_metadata()
            self.document.groups = normalized_document_groups(self.document)
            with performance_recorder.measure("controller.reassign_function_ids", "controller", self._perf_document_meta()):
                reassign_function_ids(self.schema, self.document)
            with performance_recorder.measure(
                "controller.emit_node_updates",
                "controller",
                {**self._perf_document_meta(), "emit_count": len(self.document.nodes)},
            ):
                for node in self.document.nodes:
                    self.nodeUpdated.emit(node.uuid)
            with performance_recorder.measure("controller.validate_document", "controller", self._perf_document_meta()):
                validation_issues = validate_document(self.schema, self.document)
            self.validationChanged.emit(validation_issues)
            with performance_recorder.measure("controller.build_csv_preview", "controller", self._perf_document_meta()):
                csv_rows = document_to_csv_rows(self.schema, self.document)
            self.csvPreviewChanged.emit(csv_rows)
            self.documentStateChanged.emit(self.document.state)
            self.trashBinChanged.emit(list(self.document.trash_bin))
            self.editorSettingsChanged.emit(self.document.editor_settings)
            self.groupsChanged.emit()

    def _ensure_parameter_table_metadata(self) -> None:
        for node in self.document.nodes:
            ensure_parameter_table_metadata(node)

    def group_records(self) -> list[GroupRecord]:
        return [group.clone() for group in normalized_document_groups(self.document)]

    def get_group(self, group_uuid: str) -> GroupRecord | None:
        return next((group for group in self.document.groups if group.uuid == group_uuid), None)

    def group_node_uuids(self, group_uuid: str) -> list[str]:
        group = self.get_group(group_uuid)
        if not group:
            return []
        existing = {node.uuid for node in self.document.nodes}
        return [node_uuid for node_uuid in group.node_uuids if node_uuid in existing]

    def node_group_uuid(self, node_uuid: str) -> str | None:
        for group in normalized_document_groups(self.document):
            if node_uuid in group.node_uuids:
                return group.uuid
        return None

    def parameter_table_rows(self, table_id: str) -> list[NodeRecord]:
        rows = [node for node in self.document.nodes if node.type == "ParameterTrigger" and parameter_table_id(node) == table_id]
        rows.sort(key=lambda node: (parameter_table_order(node), node.ui_position["y"], node.ui_position["x"], node.uuid))
        for index, node in enumerate(rows):
            if parameter_table_order(node) != index:
                set_parameter_table_order(node, index)
        return rows

    def parameter_tables(self) -> list[dict[str, Any]]:
        tables: dict[str, list[NodeRecord]] = {}
        for node in self.document.nodes:
            if node.type != "ParameterTrigger":
                continue
            tables.setdefault(parameter_table_id(node), []).append(node)
        result: list[dict[str, Any]] = []
        for table_id, rows in tables.items():
            rows.sort(key=lambda node: (parameter_table_order(node), node.ui_position["y"], node.ui_position["x"], node.uuid))
            anchor = rows[0]
            colors = parameter_table_colors(anchor)
            result.append(
                {
                    "table_id": table_id,
                    "title": parameter_table_title(anchor) or DEFAULT_PARAMETER_TABLE_TITLE,
                    "node_uuids": [node.uuid for node in rows],
                    "theme_body_color": colors[TABLE_BODY_COLOR_FIELD],
                    "theme_border_color": colors[TABLE_BORDER_COLOR_FIELD],
                    "theme_text_color": colors[TABLE_TEXT_COLOR_FIELD],
                }
            )
        result.sort(key=lambda item: (item["title"].lower(), item["table_id"]))
        return result

    def get_node(self, node_uuid: str) -> NodeRecord | None:
        return next((node for node in self.document.nodes if node.uuid == node_uuid), None)

    def set_selected_node(self, node_uuid: str | None) -> None:
        self.selected_node_uuid = node_uuid
        self.selectionChanged.emit(node_uuid)

    def can_copy_node(self, node_uuid: str) -> bool:
        node = self.get_node(node_uuid)
        if not node:
            return False
        return self.schema.nodes[node.type].copyable

    def can_edit_node(self, node_uuid: str) -> bool:
        node = self.get_node(node_uuid)
        return bool(node and not node.locked)

    def can_create_graph_content(self) -> tuple[bool, str]:
        if self.document.state.is_meta_ready:
            return True, ""
        missing = " / ".join(self.document.state.meta_missing_fields)
        return False, f"请先在初始节点完成以下字段：{missing}"

    def _emit_meta_blocked(self, reason: str) -> None:
        self.statusMessage.emit(reason)
        self.metaActionBlocked.emit(reason)

    def _apply_simple_touchidle_defaults(self, node: NodeRecord) -> None:
        if self.preferences.global_mode != "simple" or node.type != "TouchIdle":
            return
        node.fields["parameter"] = "empty"
        node.manual_fields.add("parameter")

    def create_node(self, node_type: str, position: tuple[float, float], base_node: NodeRecord | None = None) -> str | None:
        if node_type != "Initial":
            allowed, reason = self.can_create_graph_content()
            if not allowed:
                self._emit_meta_blocked(reason)
                return None
        node = create_node(self.schema, self.document, node_type, position, base_node=base_node)
        self._apply_simple_touchidle_defaults(node)
        self.undo_stack.push(AddNodesCommand(self, [node], []))
        self.set_selected_node(node.uuid)
        return node.uuid

    def create_node_with_connection(self, from_uuid: str, node_type: str, position: tuple[float, float]) -> str | None:
        allowed, reason = self.can_create_graph_content()
        if not allowed:
                self._emit_meta_blocked(reason)
                return None
        node = create_node(self.schema, self.document, node_type, position)
        self._apply_simple_touchidle_defaults(node)
        connection = ConnectionRecord(from_uuid=from_uuid, to_uuid=node.uuid)
        self.undo_stack.push(AddNodesCommand(self, [node], [connection]))
        self.set_selected_node(node.uuid)
        return node.uuid

    def create_group(self, node_uuids: list[str], title: str | None = None) -> str | None:
        unique_ids: list[str] = []
        seen: set[str] = set()
        for node_uuid in node_uuids:
            node = self.get_node(node_uuid)
            if not node or node_uuid in seen:
                continue
            if node.type == "ParameterTrigger":
                for row in self.parameter_table_rows(parameter_table_id(node)):
                    if row.uuid in seen:
                        continue
                    seen.add(row.uuid)
                    unique_ids.append(row.uuid)
                continue
            seen.add(node_uuid)
            unique_ids.append(node_uuid)
        if len(unique_ids) < 2:
            return None
        existing_group_id = self._single_group_for_selection(unique_ids)
        if existing_group_id is not None:
            return existing_group_id
        group = GroupRecord(uuid=new_uuid(), title=str(title or "").strip() or DEFAULT_GROUP_TITLE, node_uuids=unique_ids)
        old_groups = self.group_records()
        new_groups = old_groups + [group]
        self.undo_stack.push(SetGroupsCommand(self, old_groups, new_groups, label="创建分组"))
        return group.uuid

    def rename_group(self, group_uuid: str, title: str) -> bool:
        old_groups = self.group_records()
        renamed = False
        new_groups: list[GroupRecord] = []
        for group in old_groups:
            updated = group.clone()
            if updated.uuid == group_uuid:
                updated.title = str(title or "").strip() or DEFAULT_GROUP_TITLE
                renamed = True
            new_groups.append(updated)
        if not renamed:
            return False
        self.undo_stack.push(SetGroupsCommand(self, old_groups, new_groups, label="重命名分组"))
        return True

    def remove_group(self, group_uuid: str) -> bool:
        old_groups = self.group_records()
        new_groups = [group.clone() for group in old_groups if group.uuid != group_uuid]
        if len(new_groups) == len(old_groups):
            return False
        self.undo_stack.push(SetGroupsCommand(self, old_groups, new_groups, label="删除分组"))
        return True

    def add_parameter_table_row(self, table_id: str, reference_node_uuid: str | None = None) -> str | None:
        rows = self.parameter_table_rows(table_id)
        if not rows:
            return None
        reference = next((node for node in rows if node.uuid == reference_node_uuid), rows[-1])
        next_order = max((parameter_table_order(row) for row in rows), default=-1) + 1
        new_node = create_node(
            self.schema,
            self.document,
            "ParameterTrigger",
            (float(reference.ui_position["x"]), float(reference.ui_position["y"]) + 96.0),
            base_node=reference,
        )
        new_node.fields[TABLE_ID_FIELD] = table_id
        new_node.fields[TABLE_TITLE_FIELD] = reference.fields.get(TABLE_TITLE_FIELD, DEFAULT_PARAMETER_TABLE_TITLE)
        new_node.fields[TABLE_BODY_COLOR_FIELD] = reference.fields.get(TABLE_BODY_COLOR_FIELD, parameter_table_colors(reference)[TABLE_BODY_COLOR_FIELD])
        new_node.fields[TABLE_BORDER_COLOR_FIELD] = reference.fields.get(TABLE_BORDER_COLOR_FIELD, parameter_table_colors(reference)[TABLE_BORDER_COLOR_FIELD])
        new_node.fields[TABLE_TEXT_COLOR_FIELD] = reference.fields.get(TABLE_TEXT_COLOR_FIELD, parameter_table_colors(reference)[TABLE_TEXT_COLOR_FIELD])
        set_parameter_table_order(new_node, next_order)
        self.undo_stack.push(AddNodesCommand(self, [new_node], []))
        self.set_selected_node(new_node.uuid)
        return new_node.uuid

    def remove_parameter_table_row(self, node_uuid: str) -> bool:
        node = self.get_node(node_uuid)
        if not node or node.type != "ParameterTrigger":
            return False
        table_rows = self.parameter_table_rows(parameter_table_id(node))
        if len(table_rows) <= 1:
            return False
        self.remove_nodes([node_uuid])
        return True

    def _single_group_for_selection(self, node_uuids: list[str]) -> str | None:
        groups = normalized_document_groups(self.document)
        matches = [group.uuid for group in groups if set(group.node_uuids) == set(node_uuids)]
        return matches[0] if len(matches) == 1 else None

    def remove_nodes(self, node_uuids: list[str]) -> None:
        nodes = [node for node in self.document.nodes if node.uuid in node_uuids and self.schema.nodes[node.type].copyable]
        if not nodes:
            return
        node_uuid_set = {node.uuid for node in nodes}
        connections = [
            connection
            for connection in self.document.connections
            if connection.from_uuid in node_uuid_set or connection.to_uuid in node_uuid_set
        ]
        trash_entries = [make_trash_entry(self.schema, node) for node in nodes] if self.document.editor_settings.trash_enabled else []
        self.undo_stack.push(RemoveNodesCommand(self, nodes, connections, trash_entries))

    def update_field(self, node_uuid: str, key: str, value: Any, source_mode: str | None = None) -> None:
        node = self.get_node(node_uuid)
        if not node:
            return
        if node.locked:
            self.statusMessage.emit("当前节点已锁定")
            return
        normalized_value = normalize_field_input(self.schema, node, key, value)
        old = node.fields.get(key)
        if old == normalized_value:
            return
        self.undo_stack.push(
            UpdateFieldCommand(self, node_uuid, key, old, normalized_value, source_mode or self.preferences.global_mode)
        )

    def update_fields(self, node_uuid: str, values: dict[str, Any], source_mode: str | None = None, label: str = "批量修改字段") -> None:
        node = self.get_node(node_uuid)
        if not node or not values:
            return
        if node.locked:
            self.statusMessage.emit("当前节点已锁定")
            return
        updates: list[tuple[str, Any, Any]] = []
        for key, value in values.items():
            normalized_value = normalize_field_input(self.schema, node, key, value)
            old_value = node.fields.get(key)
            if old_value != normalized_value:
                updates.append((key, old_value, normalized_value))
        if not updates:
            return
        self.undo_stack.push(UpdateFieldsCommand(self, node_uuid, updates, source_mode or self.preferences.global_mode, label))

    def move_node(self, node_uuid: str, old_pos: tuple[float, float], new_pos: tuple[float, float]) -> None:
        if old_pos == new_pos:
            return
        node = self.get_node(node_uuid)
        if not node or node.locked:
            return
        self.undo_stack.push(MoveNodeCommand(self, node_uuid, old_pos, new_pos))

    def move_nodes(self, positions: dict[str, tuple[float, float]], label: str = "整理节点布局") -> None:
        current_positions: dict[str, tuple[float, float]] = {}
        new_positions: dict[str, tuple[float, float]] = {}
        for node_uuid, new_pos in positions.items():
            node = self.get_node(node_uuid)
            if not node or node.locked:
                continue
            old_pos = (float(node.ui_position["x"]), float(node.ui_position["y"]))
            normalized_new = (float(new_pos[0]), float(new_pos[1]))
            if old_pos == normalized_new:
                continue
            current_positions[node_uuid] = old_pos
            new_positions[node_uuid] = normalized_new
        if not new_positions:
            return
        self.undo_stack.push(MoveNodesCommand(self, current_positions, new_positions, label=label))

    def add_connection(self, from_uuid: str, to_uuid: str) -> None:
        allowed, reason = self.can_create_graph_content()
        if not allowed:
            self._emit_meta_blocked(reason)
            return
        if from_uuid == to_uuid:
            return
        if any(connection.from_uuid == from_uuid and connection.to_uuid == to_uuid for connection in self.document.connections):
            return
        self.undo_stack.push(AddConnectionCommand(self, ConnectionRecord(from_uuid=from_uuid, to_uuid=to_uuid)))

    def remove_connection(self, from_uuid: str, to_uuid: str) -> None:
        target = next(
            (
                connection
                for connection in self.document.connections
                if connection.from_uuid == from_uuid and connection.to_uuid == to_uuid
            ),
            None,
        )
        if target:
            self.undo_stack.push(RemoveConnectionCommand(self, target))

    def search(self, text: str):
        with performance_recorder.measure("controller.search_document", "controller", {"query_length": len(text or "")}):
            return search_document(
                self.schema,
                self.document,
                text,
                use_json_field_names=self.preferences.debug_json_field_names,
            )

    def serialize_selection(self, node_uuids: list[str]) -> bytes | None:
        selected_nodes = [
            node.clone()
            for node in self.document.nodes
            if node.uuid in node_uuids and self.schema.nodes[node.type].copyable
        ]
        if not selected_nodes:
            return None
        selected_set = {node.uuid for node in selected_nodes}
        min_x = min(node.ui_position["x"] for node in selected_nodes)
        min_y = min(node.ui_position["y"] for node in selected_nodes)
        max_x = max((node.ui_position["x"] + ((node.ui_size or {}).get("width", 380.0))) for node in selected_nodes)
        max_y = max((node.ui_position["y"] + ((node.ui_size or {}).get("height", 180.0))) for node in selected_nodes)
        connections = [
            {"from_uuid": connection.from_uuid, "to_uuid": connection.to_uuid}
            for connection in self.document.connections
            if connection.from_uuid in selected_set and connection.to_uuid in selected_set
        ]
        payload = {
            "nodes": [self._serialize_node(node) for node in selected_nodes],
            "connections": connections,
            "source_bounds": {"min_x": min_x, "min_y": min_y, "max_x": max_x, "max_y": max_y},
        }
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def clipboard_bounds(self, payload: bytes) -> tuple[float, float, float, float]:
        raw = json.loads(payload.decode("utf-8"))
        bounds = raw.get("source_bounds") or {}
        return (
            float(bounds.get("min_x", 0.0)),
            float(bounds.get("min_y", 0.0)),
            float(bounds.get("max_x", 0.0)),
            float(bounds.get("max_y", 0.0)),
        )

    def deserialize_clipboard(self, payload: bytes, position: tuple[float, float] | None = None) -> tuple[list[NodeRecord], list[ConnectionRecord]]:
        raw = json.loads(payload.decode("utf-8"))
        nodes: list[NodeRecord] = []
        uuid_map: dict[str, str] = {}
        table_id_map: dict[str, str] = {}
        source_positions = [item.get("ui_position", {"x": 0.0, "y": 0.0}) for item in raw.get("nodes", [])]
        min_x = min((item.get("x", 0.0) for item in source_positions), default=0.0)
        min_y = min((item.get("y", 0.0) for item in source_positions), default=0.0)
        base_position = position or (min_x, min_y)
        for item in raw.get("nodes", []):
            old_uuid = item["uuid"]
            template = NodeRecord(
                uuid=new_uuid(),
                type=item["type"],
                fields={
                    key: value
                    for key, value in item.items()
                    if key not in {"uuid", "type", "ui_position", "ui_size", "locked", "numeric_linkage_enabled"}
                },
                ui_position={"x": 0.0, "y": 0.0},
                ui_size=item.get("ui_size"),
                locked=bool(item.get("locked", False)),
                numeric_linkage_enabled=bool(
                    item.get(
                        "numeric_linkage_enabled",
                        self.document.editor_settings.numeric_linkage_enabled,
                    )
                ),
                manual_fields=set(item.get("manual_fields", [])),
            )
            if template.type == "ParameterTrigger":
                old_table_id = str(template.fields.get(TABLE_ID_FIELD) or "").strip()
                if old_table_id:
                    table_id_map.setdefault(old_table_id, new_uuid())
                    template.fields[TABLE_ID_FIELD] = table_id_map[old_table_id]
                else:
                    template.fields[TABLE_ID_FIELD] = new_uuid()
                template.fields.setdefault(TABLE_TITLE_FIELD, DEFAULT_PARAMETER_TABLE_TITLE)
            if not template.manual_fields:
                infer_manual_fields(self.schema, template, self.document)
            new_node = create_node(
                self.schema,
                self.document,
                template.type,
                (
                    base_position[0] + (item.get("ui_position", {}).get("x", 0.0) - min_x),
                    base_position[1] + (item.get("ui_position", {}).get("y", 0.0) - min_y),
                ),
                base_node=template,
            )
            nodes.append(new_node)
            uuid_map[old_uuid] = new_node.uuid
        connections = [
            ConnectionRecord(from_uuid=uuid_map[item["from_uuid"]], to_uuid=uuid_map[item["to_uuid"]])
            for item in raw.get("connections", [])
            if item["from_uuid"] in uuid_map and item["to_uuid"] in uuid_map
        ]
        return nodes, connections

    def paste_payload(
        self,
        payload: bytes,
        position: tuple[float, float] | None = None,
        *,
        connect_from: str | None = None,
    ) -> list[str]:
        nodes, connections = self.deserialize_clipboard(payload, position)
        if not nodes:
            return []
        if connect_from and len(nodes) == 1:
            connections = list(connections)
            connections.append(ConnectionRecord(from_uuid=connect_from, to_uuid=nodes[0].uuid))
        self.undo_stack.push(AddNodesCommand(self, nodes, connections))
        self.set_selected_node(nodes[0].uuid)
        return [node.uuid for node in nodes]

    def export_current_document(self) -> dict[str, Any]:
        return export_document_dict(self.schema, self.document)

    def clear_trash_entries(self, entry_ids: list[str]) -> int:
        if not entry_ids:
            return 0
        entry_set = set(entry_ids)
        before = len(self.document.trash_bin)
        self.document.trash_bin = [entry for entry in self.document.trash_bin if entry.entry_id not in entry_set]
        removed = before - len(self.document.trash_bin)
        if removed:
            self.refresh_derived()
        return removed

    def clear_all_trash(self) -> int:
        removed = len(self.document.trash_bin)
        if removed:
            self.document.trash_bin.clear()
            self.refresh_derived()
        return removed

    def _serialize_node(self, node: NodeRecord) -> dict[str, Any]:
        payload = {
            "uuid": node.uuid,
            "type": node.type,
            "ui_position": dict(node.ui_position),
            "locked": node.locked,
        }
        if node.ui_size:
            payload["ui_size"] = dict(node.ui_size)
        if self.schema.nodes[node.type].category == "function":
            payload["numeric_linkage_enabled"] = bool(node.numeric_linkage_enabled)
        if node.manual_fields:
            payload["manual_fields"] = sorted(node.manual_fields)
        for key, value in node.fields.items():
            if key == "target_idle" and not _should_persist_target_idle(node):
                continue
            if node.type in {"TouchDrag", "ParameterTrigger"} and key == "action_trigger_active":
                continue
            if node.type != "Comment" and key == "tips":
                continue
            payload[key] = value
        return payload

    def _insert_nodes(self, nodes: list[NodeRecord], connections: list[ConnectionRecord]) -> None:
        for node in nodes:
            self.document.nodes.append(node)
        for connection in connections:
            if not any(current.from_uuid == connection.from_uuid and current.to_uuid == connection.to_uuid for current in self.document.connections):
                self.document.connections.append(connection)
        reassign_function_ids(self.schema, self.document)
        for node in nodes:
            self.nodeAdded.emit(node.uuid)
        if connections:
            self.connectionsChanged.emit()
        self.refresh_derived()

    def _remove_nodes(self, node_uuids: list[str], connection_pairs: list[tuple[str, str]]) -> None:
        self.document.nodes = [node for node in self.document.nodes if node.uuid not in node_uuids]
        self.document.connections = [
            connection
            for connection in self.document.connections
            if (connection.from_uuid, connection.to_uuid) not in connection_pairs
        ]
        if self.selected_node_uuid in node_uuids:
            self.set_selected_node(None)
        for node_uuid in node_uuids:
            self.nodeRemoved.emit(node_uuid)
        self.connectionsChanged.emit()
        self.refresh_derived()

    def _delete_nodes(self, nodes: list[NodeRecord], connections: list[ConnectionRecord], trash_entries) -> None:
        node_uuids = [node.uuid for node in nodes]
        pairs = [(connection.from_uuid, connection.to_uuid) for connection in connections]
        self.document.nodes = [node for node in self.document.nodes if node.uuid not in node_uuids]
        self.document.connections = [
            connection
            for connection in self.document.connections
            if (connection.from_uuid, connection.to_uuid) not in pairs
        ]
        self.document.trash_bin.extend(trash_entries)
        if self.selected_node_uuid in node_uuids:
            self.set_selected_node(None)
        for node_uuid in node_uuids:
            self.nodeRemoved.emit(node_uuid)
        self.connectionsChanged.emit()
        self.refresh_derived()

    def _restore_deleted_nodes(self, nodes: list[NodeRecord], connections: list[ConnectionRecord], trash_entry_ids: list[str]) -> None:
        trash_id_set = set(trash_entry_ids)
        self.document.trash_bin = [entry for entry in self.document.trash_bin if entry.entry_id not in trash_id_set]
        for node in nodes:
            self.document.nodes.append(node)
        for connection in connections:
            if not any(current.from_uuid == connection.from_uuid and current.to_uuid == connection.to_uuid for current in self.document.connections):
                self.document.connections.append(connection)
        for node in nodes:
            self.nodeAdded.emit(node.uuid)
        self.connectionsChanged.emit()
        self.refresh_derived()

    def _set_field(self, node_uuid: str, key: str, value: Any, source_mode: str) -> None:
        self._set_fields(node_uuid, {key: value}, source_mode, changed_key=key)

    def _set_fields(
        self,
        node_uuid: str,
        values: dict[str, Any],
        source_mode: str,
        *,
        changed_key: str | None = None,
    ) -> None:
        node = self.get_node(node_uuid)
        if not node or not values:
            return
        for key, value in values.items():
            node.fields[key] = value
        apply_node_appearance_defaults(self.schema, node)
        changed_keys = set(values)
        if node.type == "Comment" and changed_keys & {"theme_body_color", "theme_text_color"}:
            sync_comment_legacy_appearance(node)
        if node.type == "Comment" and changed_keys & {"note_box_color", "note_text_color"}:
            sync_comment_theme_appearance(node)
        effective_changed_key = changed_key
        if effective_changed_key is None and len(changed_keys) == 1:
            effective_changed_key = next(iter(changed_keys))
        if node.type in {"TouchDrag", "ParameterTrigger"} and changed_keys & {"action_trigger", "parameter"}:
            node.fields["action_trigger_active"] = ""
            if source_mode == "simple":
                node.manual_fields.discard("parameter")
        if effective_changed_key in {"action_trigger_active", "action_trigger"} or (
            source_mode == "advanced" and effective_changed_key == "parameter"
        ):
            infer_manual_fields(self.schema, node, self.document)
        if node.type in {"TouchDrag", "ParameterTrigger"} and effective_changed_key == "action_trigger" and source_mode == "simple":
            node.manual_fields.discard("parameter")
        apply_auto_rules(self.schema, self.document, node, source_mode=source_mode, changed_key=effective_changed_key)
        reassign_function_ids(self.schema, self.document)
        self.nodeUpdated.emit(node_uuid)
        self.refresh_derived()

    def _set_node_locked(self, node_uuid: str, locked: bool) -> None:
        node = self.get_node(node_uuid)
        if not node:
            return
        node.locked = bool(locked)
        self.nodeUpdated.emit(node_uuid)
        self.refresh_derived()

    def _set_editor_settings(self, settings: dict[str, Any], trash_bin) -> None:
        self.document.editor_settings.numeric_linkage_enabled = bool(settings.get("numeric_linkage_enabled", False))
        self.document.editor_settings.trash_enabled = bool(settings.get("trash_enabled", False))
        self.document.trash_bin = list(trash_bin)
        linkage_enabled = self.document.editor_settings.numeric_linkage_enabled
        for node in self.document.nodes:
            if node.type not in function_node_types(self.schema):
                continue
            node.numeric_linkage_enabled = linkage_enabled
            infer_manual_fields(self.schema, node, self.document)
            apply_auto_rules(self.schema, self.document, node, source_mode=self.preferences.global_mode, force_generated=False)
            self.nodeUpdated.emit(node.uuid)
        self.refresh_derived()

    def _set_groups(self, groups: list[GroupRecord]) -> None:
        self.document.groups = [group.clone() for group in groups]
        self.refresh_derived()

    def _move_node(self, node_uuid: str, position: tuple[float, float]) -> None:
        node = self.get_node(node_uuid)
        if not node:
            return
        node.ui_position = {"x": float(position[0]), "y": float(position[1])}
        self.nodeMoved.emit(node_uuid)

    def _move_nodes(self, positions: dict[str, tuple[float, float]]) -> None:
        for node_uuid, position in positions.items():
            node = self.get_node(node_uuid)
            if not node:
                continue
            node.ui_position = {"x": float(position[0]), "y": float(position[1])}
            self.nodeMoved.emit(node_uuid)

    def _add_connection(self, connection: ConnectionRecord) -> None:
        self.document.connections.append(connection)
        self.connectionsChanged.emit()
        self.refresh_derived()

    def _remove_connection(self, pair: tuple[str, str]) -> None:
        self.document.connections = [
            connection
            for connection in self.document.connections
            if (connection.from_uuid, connection.to_uuid) != pair
        ]
        self.connectionsChanged.emit()
        self.refresh_derived()

    def file_list(self, directory: str | Path | None = None) -> list[str]:
        """List editor JSON paths relative to *directory*, or to ``set_workspace_root`` if omitted."""
        root = Path(directory).resolve() if directory is not None else self._workspace_root
        if root is None or not root.is_dir():
            return []
        items: list[str] = []
        try:
            for path in root.rglob("*.json"):
                if not path.is_file():
                    continue
                if not is_editor_document_file(path):
                    continue
                try:
                    rel = path.relative_to(root)
                except ValueError:
                    continue
                items.append(rel.as_posix())
        except OSError:
            return []
        return sorted(items, key=lambda s: s.replace("\\", "/").lower())

    def node_summary(self, node_uuid: str) -> str:
        node = self.get_node(node_uuid)
        return node_title(self.schema, node) if node else ""

    @staticmethod
    def clipboard_mime() -> str:
        return CLIPBOARD_MIME
