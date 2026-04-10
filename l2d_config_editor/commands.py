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
    def __init__(self, controller, nodes, connections) -> None:
        super().__init__("删除节点")
        self.controller = controller
        self.nodes = [node.clone() for node in nodes]
        self.connections = list(connections)

    def redo(self) -> None:
        node_uuids = [node.uuid for node in self.nodes]
        pairs = [(connection.from_uuid, connection.to_uuid) for connection in self.connections]
        self.controller._remove_nodes(node_uuids, pairs)

    def undo(self) -> None:
        self.controller._insert_nodes([node.clone() for node in self.nodes], list(self.connections))


class UpdateFieldCommand(QUndoCommand):
    def __init__(self, controller, node_uuid, key, old_value, new_value) -> None:
        super().__init__("修改字段")
        self.controller = controller
        self.node_uuid = node_uuid
        self.key = key
        self.old_value = old_value
        self.new_value = new_value

    def redo(self) -> None:
        self.controller._set_field(self.node_uuid, self.key, self.new_value)

    def undo(self) -> None:
        self.controller._set_field(self.node_uuid, self.key, self.old_value)


class SetModeCommand(QUndoCommand):
    def __init__(self, controller, node_uuid, old_mode, new_mode) -> None:
        super().__init__("切换编辑模式")
        self.controller = controller
        self.node_uuid = node_uuid
        self.old_mode = old_mode
        self.new_mode = new_mode

    def redo(self) -> None:
        self.controller._set_mode(self.node_uuid, self.new_mode)

    def undo(self) -> None:
        self.controller._set_mode(self.node_uuid, self.old_mode)


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
