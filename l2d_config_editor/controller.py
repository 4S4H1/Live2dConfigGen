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
    RemoveConnectionCommand,
    RemoveNodesCommand,
    SetModeCommand,
    UpdateFieldCommand,
)
from .constants import CLIPBOARD_MIME
from .logic import (
    apply_auto_rules,
    create_document,
    export_document_dict,
    load_document,
    new_uuid,
    node_title,
    reassign_function_ids,
    save_document,
    search_document,
    sync_meta_from_initial,
    validate_document,
    document_to_csv_rows,
)
from .models import ConnectionRecord, DocumentModel, NodeRecord


class EditorController(QObject):
    documentLoaded = pyqtSignal()
    nodeAdded = pyqtSignal(str)
    nodeRemoved = pyqtSignal(str)
    nodeUpdated = pyqtSignal(str)
    connectionsChanged = pyqtSignal()
    validationChanged = pyqtSignal(object)
    csvPreviewChanged = pyqtSignal(object)
    selectionChanged = pyqtSignal(object)
    pathChanged = pyqtSignal(object)
    statusMessage = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.undo_stack = QUndoStack(self)
        self.document = create_document()
        self.selected_node_uuid: str | None = None
        self.refresh_derived()

    def new_document(self) -> None:
        self.undo_stack.clear()
        self.document = create_document()
        self.selected_node_uuid = None
        self.documentLoaded.emit()
        self.pathChanged.emit(None)
        self.refresh_derived()

    def open_document(self, path: str | Path) -> None:
        self.undo_stack.clear()
        self.document = load_document(path)
        self.selected_node_uuid = None
        self.documentLoaded.emit()
        self.pathChanged.emit(str(path))
        self.refresh_derived()

    def save_document(self, path: str | None = None) -> str | None:
        target = path or self.document.path
        if not target:
            return None
        save_document(self.document, target)
        self.pathChanged.emit(str(target))
        self.refresh_derived()
        return str(target)

    def refresh_derived(self) -> None:
        sync_meta_from_initial(self.document)
        reassign_function_ids(self.document)
        for node in self.document.nodes:
            self.nodeUpdated.emit(node.uuid)
        self.validationChanged.emit(validate_document(self.document))
        self.csvPreviewChanged.emit(document_to_csv_rows(self.document))

    def get_node(self, node_uuid: str) -> NodeRecord | None:
        return next((node for node in self.document.nodes if node.uuid == node_uuid), None)

    def set_selected_node(self, node_uuid: str | None) -> None:
        self.selected_node_uuid = node_uuid
        self.selectionChanged.emit(node_uuid)

    def can_copy_node(self, node_uuid: str) -> bool:
        node = self.get_node(node_uuid)
        if not node:
            return False
        return node.type != "Initial"

    def add_node(self, node: NodeRecord) -> None:
        self.undo_stack.push(AddNodesCommand(self, [node], []))

    def remove_nodes(self, node_uuids: list[str]) -> None:
        nodes = [node for node in self.document.nodes if node.uuid in node_uuids and node.type != "Initial"]
        if not nodes:
            return
        connections = [
            connection
            for connection in self.document.connections
            if connection.from_uuid in node_uuids or connection.to_uuid in node_uuids
        ]
        self.undo_stack.push(RemoveNodesCommand(self, nodes, connections))

    def update_field(self, node_uuid: str, key: str, value: Any) -> None:
        node = self.get_node(node_uuid)
        if not node:
            return
        old = node.fields.get(key)
        if old == value:
            return
        self.undo_stack.push(UpdateFieldCommand(self, node_uuid, key, old, value))

    def set_mode(self, node_uuid: str, mode: str) -> None:
        node = self.get_node(node_uuid)
        if not node or node.mode_variant == mode:
            return
        self.undo_stack.push(SetModeCommand(self, node_uuid, node.mode_variant, mode))

    def move_node(self, node_uuid: str, old_pos: tuple[float, float], new_pos: tuple[float, float]) -> None:
        if old_pos == new_pos:
            return
        self.undo_stack.push(MoveNodeCommand(self, node_uuid, old_pos, new_pos))

    def add_connection(self, from_uuid: str, to_uuid: str) -> None:
        if from_uuid == to_uuid:
            return
        if any(
            connection.from_uuid == from_uuid and connection.to_uuid == to_uuid
            for connection in self.document.connections
        ):
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

    def remove_selected_connections(self, pairs: list[tuple[str, str]]) -> None:
        for from_uuid, to_uuid in pairs:
            self.remove_connection(from_uuid, to_uuid)

    def search(self, text: str):
        return search_document(self.document, text)

    def serialize_selection(self, node_uuids: list[str]) -> bytes | None:
        selected_nodes = [node.clone() for node in self.document.nodes if node.uuid in node_uuids and node.type != "Initial"]
        if not selected_nodes:
            return None
        selected_set = {node.uuid for node in selected_nodes}
        connections = [
            {"from_uuid": connection.from_uuid, "to_uuid": connection.to_uuid}
            for connection in self.document.connections
            if connection.from_uuid in selected_set and connection.to_uuid in selected_set
        ]
        payload = {
            "nodes": [self._serialize_node(node) for node in selected_nodes],
            "connections": connections,
        }
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def deserialize_clipboard(self, payload: bytes, position: tuple[float, float] | None = None) -> tuple[list[NodeRecord], list[ConnectionRecord]]:
        raw = json.loads(payload.decode("utf-8"))
        nodes: list[NodeRecord] = []
        uuid_map: dict[str, str] = {}
        base_position = position or (0.0, 0.0)
        source_positions = [item.get("ui_position", {"x": 0.0, "y": 0.0}) for item in raw.get("nodes", [])]
        min_x = min((item.get("x", 0.0) for item in source_positions), default=0.0)
        min_y = min((item.get("y", 0.0) for item in source_positions), default=0.0)
        for index, item in enumerate(raw.get("nodes", [])):
            old_uuid = item["uuid"]
            new_node = NodeRecord(
                uuid=new_uuid(),
                type=item["type"],
                fields={key: value for key, value in item.items() if key not in {"uuid", "type", "mode_variant", "ui_position", "ui_size"}},
                ui_position={
                    "x": base_position[0] + (item.get("ui_position", {}).get("x", 0.0) - min_x) + index * 24,
                    "y": base_position[1] + (item.get("ui_position", {}).get("y", 0.0) - min_y) + index * 24,
                },
                ui_size=item.get("ui_size"),
                mode_variant=item.get("mode_variant", "simple"),
            )
            apply_auto_rules(new_node)
            nodes.append(new_node)
            uuid_map[old_uuid] = new_node.uuid
        connections = [
            ConnectionRecord(from_uuid=uuid_map[item["from_uuid"]], to_uuid=uuid_map[item["to_uuid"]])
            for item in raw.get("connections", [])
            if item["from_uuid"] in uuid_map and item["to_uuid"] in uuid_map
        ]
        return nodes, connections

    def paste_payload(self, payload: bytes, position: tuple[float, float] | None = None) -> list[str]:
        nodes, connections = self.deserialize_clipboard(payload, position)
        if not nodes:
            return []
        self.undo_stack.push(AddNodesCommand(self, nodes, connections))
        self.set_selected_node(nodes[0].uuid)
        return [node.uuid for node in nodes]

    def export_current_document(self) -> dict[str, Any]:
        return export_document_dict(self.document)

    def _serialize_node(self, node: NodeRecord) -> dict[str, Any]:
        payload = {
            "uuid": node.uuid,
            "type": node.type,
            "mode_variant": node.mode_variant,
            "ui_position": dict(node.ui_position),
        }
        if node.ui_size:
            payload["ui_size"] = dict(node.ui_size)
        payload.update(node.fields)
        return payload

    def _insert_nodes(self, nodes: list[NodeRecord], connections: list[ConnectionRecord]) -> None:
        for node in nodes:
            self.document.nodes.append(node)
        for connection in connections:
            if not any(
                current.from_uuid == connection.from_uuid and current.to_uuid == connection.to_uuid
                for current in self.document.connections
            ):
                self.document.connections.append(connection)
        reassign_function_ids(self.document)
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
        reassign_function_ids(self.document)
        for node_uuid in node_uuids:
            self.nodeRemoved.emit(node_uuid)
        self.connectionsChanged.emit()
        self.refresh_derived()

    def _set_field(self, node_uuid: str, key: str, value: Any) -> None:
        node = self.get_node(node_uuid)
        if not node:
            return
        node.fields[key] = value
        if node.type == "Initial":
            sync_meta_from_initial(self.document)
        apply_auto_rules(node)
        reassign_function_ids(self.document)
        self.nodeUpdated.emit(node_uuid)
        self.refresh_derived()

    def _set_mode(self, node_uuid: str, mode: str) -> None:
        node = self.get_node(node_uuid)
        if not node:
            return
        node.mode_variant = mode
        apply_auto_rules(node)
        self.nodeUpdated.emit(node_uuid)
        self.refresh_derived()

    def _move_node(self, node_uuid: str, position: tuple[float, float]) -> None:
        node = self.get_node(node_uuid)
        if not node:
            return
        node.ui_position = {"x": float(position[0]), "y": float(position[1])}
        self.nodeUpdated.emit(node_uuid)

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

    def file_list(self, directory: str | Path) -> list[str]:
        return sorted(path.name for path in Path(directory).glob("*.json"))

    def node_summary(self, node_uuid: str) -> str:
        node = self.get_node(node_uuid)
        return node_title(node) if node else ""

    @staticmethod
    def clipboard_mime() -> str:
        return CLIPBOARD_MIME
