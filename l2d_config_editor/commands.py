"""Undoable editor commands."""

from __future__ import annotations

from PyQt6.QtGui import QUndoCommand


class AddNodesCommand(QUndoCommand):
    def __init__(self, controller, nodes, connections) -> None:
        super().__init__("添加节点")
        self.controller = controller
        self.nodes = [node.clone() for node in nodes]
        self.connections = list(connections)

    def redo(self) -> None:
        self.controller._insert_nodes([node.clone() for node in self.nodes], list(self.connections))

    def undo(self) -> None:
        node_uuids = [node.uuid for node in self.nodes]
        pairs = [(connection.from_uuid, connection.to_uuid) for connection in self.connections]
        self.controller._remove_nodes(node_uuids, pairs)


class RemoveNodesCommand(QUndoCommand):
    def __init__(self, controller, nodes, connections, trash_entries) -> None:
        super().__init__("删除节点")
        self.controller = controller
        self.nodes = [node.clone() for node in nodes]
        self.connections = list(connections)
        self.trash_entries = list(trash_entries)

    def redo(self) -> None:
        self.controller._delete_nodes([node.clone() for node in self.nodes], list(self.connections), list(self.trash_entries))

    def undo(self) -> None:
        self.controller._restore_deleted_nodes(
            [node.clone() for node in self.nodes],
            list(self.connections),
            [entry.entry_id for entry in self.trash_entries],
        )


class UpdateFieldCommand(QUndoCommand):
    def __init__(self, controller, node_uuid, key, old_value, new_value, source_mode) -> None:
        super().__init__("修改字段")
        self.controller = controller
        self.node_uuid = node_uuid
        self.key = key
        self.old_value = old_value
        self.new_value = new_value
        self.source_mode = source_mode

    def redo(self) -> None:
        self.controller._set_field(self.node_uuid, self.key, self.new_value, self.source_mode)

    def undo(self) -> None:
        self.controller._set_field(self.node_uuid, self.key, self.old_value, self.source_mode)


class MoveNodeCommand(QUndoCommand):
    def __init__(self, controller, node_uuid, old_pos, new_pos) -> None:
        super().__init__("移动节点")
        self.controller = controller
        self.node_uuid = node_uuid
        self.old_pos = old_pos
        self.new_pos = new_pos

    def redo(self) -> None:
        self.controller._move_node(self.node_uuid, self.new_pos)

    def undo(self) -> None:
        self.controller._move_node(self.node_uuid, self.old_pos)


class MoveNodesCommand(QUndoCommand):
    def __init__(self, controller, old_positions, new_positions, label: str = "整理节点布局") -> None:
        super().__init__(label)
        self.controller = controller
        self.old_positions = dict(old_positions)
        self.new_positions = dict(new_positions)

    def redo(self) -> None:
        self.controller._move_nodes(self.new_positions)

    def undo(self) -> None:
        self.controller._move_nodes(self.old_positions)


class AddConnectionCommand(QUndoCommand):
    def __init__(self, controller, connection) -> None:
        super().__init__("添加连线")
        self.controller = controller
        self.connection = connection

    def redo(self) -> None:
        self.controller._add_connection(self.connection)

    def undo(self) -> None:
        self.controller._remove_connection((self.connection.from_uuid, self.connection.to_uuid))


class RemoveConnectionCommand(QUndoCommand):
    def __init__(self, controller, connection) -> None:
        super().__init__("删除连线")
        self.controller = controller
        self.connection = connection

    def redo(self) -> None:
        self.controller._remove_connection((self.connection.from_uuid, self.connection.to_uuid))

    def undo(self) -> None:
        self.controller._add_connection(self.connection)
