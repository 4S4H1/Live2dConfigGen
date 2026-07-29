"""Undoable editor commands."""

from __future__ import annotations

from PySide6.QtGui import QUndoCommand


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
    def __init__(
        self,
        controller,
        nodes,
        connections,
        groups=None,
        plan_layout=None,
    ) -> None:
        super().__init__("删除节点")
        self.controller = controller
        self.nodes = [node.clone() for node in nodes]
        self.connections = list(connections)
        self.groups = [group.clone() for group in (groups or [])]
        self.plan_layout = (
            plan_layout.clone()
            if plan_layout is not None
            else controller.document.plan_layout.clone()
        )

    def redo(self) -> None:
        self.controller._delete_nodes([node.clone() for node in self.nodes], list(self.connections))

    def undo(self) -> None:
        self.controller._restore_deleted_nodes(
            [node.clone() for node in self.nodes],
            list(self.connections),
        )
        if self.groups:
            self.controller._set_groups([group.clone() for group in self.groups])
        self.controller._set_plan_layout(self.plan_layout.clone())


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


class UpdateFieldsCommand(QUndoCommand):
    def __init__(self, controller, node_uuid, updates, source_mode, label: str = "批量修改字段") -> None:
        super().__init__(label)
        self.controller = controller
        self.node_uuid = node_uuid
        self.updates = [(key, old_value, new_value) for key, old_value, new_value in updates]
        self.source_mode = source_mode

    def redo(self) -> None:
        self.controller._set_fields(
            self.node_uuid,
            {key: new_value for key, _old_value, new_value in self.updates},
            self.source_mode,
        )

    def undo(self) -> None:
        self.controller._set_fields(
            self.node_uuid,
            {key: old_value for key, old_value, _new_value in self.updates},
            self.source_mode,
        )


class UpdateManyFieldsCommand(QUndoCommand):
    def __init__(self, controller, node_updates, source_mode, label: str = "批量修改字段") -> None:
        super().__init__(label)
        self.controller = controller
        self.node_updates = {
            node_uuid: [(key, old_value, new_value) for key, old_value, new_value in updates]
            for node_uuid, updates in node_updates.items()
        }
        self.source_mode = source_mode

    def redo(self) -> None:
        self.controller._set_many_fields(
            {
                node_uuid: {key: new_value for key, _old_value, new_value in updates}
                for node_uuid, updates in self.node_updates.items()
            },
            self.source_mode,
        )

    def undo(self) -> None:
        self.controller._set_many_fields(
            {
                node_uuid: {key: old_value for key, old_value, _new_value in updates}
                for node_uuid, updates in self.node_updates.items()
            },
            self.source_mode,
        )


class UpdateNodeLockCommand(QUndoCommand):
    def __init__(self, controller, node_uuid, old_locked, new_locked) -> None:
        super().__init__("切换节点锁定")
        self.controller = controller
        self.node_uuid = node_uuid
        self.old_locked = bool(old_locked)
        self.new_locked = bool(new_locked)

    def redo(self) -> None:
        self.controller._set_node_locked(self.node_uuid, self.new_locked)

    def undo(self) -> None:
        self.controller._set_node_locked(self.node_uuid, self.old_locked)


class UpdateEditorSettingsCommand(QUndoCommand):
    def __init__(self, controller, old_settings, new_settings, label: str = "修改文档设置") -> None:
        super().__init__(label)
        self.controller = controller
        self.old_settings = dict(old_settings)
        self.new_settings = dict(new_settings)

    def redo(self) -> None:
        self.controller._set_editor_settings(self.new_settings)

    def undo(self) -> None:
        self.controller._set_editor_settings(self.old_settings)


class SetGroupsCommand(QUndoCommand):
    def __init__(self, controller, old_groups, new_groups, label: str = "更新分组") -> None:
        super().__init__(label)
        self.controller = controller
        self.old_groups = [group.clone() for group in old_groups]
        self.new_groups = [group.clone() for group in new_groups]

    def redo(self) -> None:
        self.controller._set_groups([group.clone() for group in self.new_groups])

    def undo(self) -> None:
        self.controller._set_groups([group.clone() for group in self.old_groups])


class SetPlanLayoutCommand(QUndoCommand):
    def __init__(self, controller, old_layout, new_layout, label: str = "更新计划图") -> None:
        super().__init__(label)
        self.controller = controller
        self.old_layout = old_layout.clone()
        self.new_layout = new_layout.clone()

    def redo(self) -> None:
        self.controller._set_plan_layout(self.new_layout.clone())

    def undo(self) -> None:
        self.controller._set_plan_layout(self.old_layout.clone())


class SetPlanViewCommand(QUndoCommand):
    """Persist a plan viewport change without rebuilding plan topics."""

    def __init__(self, controller, old_state, new_state) -> None:
        super().__init__("调整计划图视角")
        self.controller = controller
        self.old_state = tuple(float(value) for value in old_state)
        self.new_state = tuple(float(value) for value in new_state)

    def redo(self) -> None:
        self.controller._set_plan_view_state(*self.new_state)

    def undo(self) -> None:
        self.controller._set_plan_view_state(*self.old_state)


class UpdatePlanGraphCommand(QUndoCommand):
    """Atomically replace the primary hierarchy and its matching formal edge."""

    def __init__(
        self,
        controller,
        old_layout,
        new_layout,
        old_connections,
        new_connections,
        label: str = "调整计划主题",
    ) -> None:
        super().__init__(label)
        self.controller = controller
        self.old_layout = old_layout.clone()
        self.new_layout = new_layout.clone()
        self.old_connections = list(old_connections)
        self.new_connections = list(new_connections)

    def redo(self) -> None:
        self.controller._set_plan_graph_state(
            self.new_layout.clone(),
            list(self.new_connections),
        )

    def undo(self) -> None:
        self.controller._set_plan_graph_state(
            self.old_layout.clone(),
            list(self.old_connections),
        )


class AddCanvasImagesCommand(QUndoCommand):
    def __init__(self, controller, images) -> None:
        super().__init__("添加参考图")
        self.controller = controller
        self.images = [image.clone() for image in images]

    def redo(self) -> None:
        self.controller._insert_canvas_images([image.clone() for image in self.images])

    def undo(self) -> None:
        self.controller._remove_canvas_images([image.uuid for image in self.images])


class RemoveCanvasImagesCommand(QUndoCommand):
    def __init__(self, controller, images) -> None:
        super().__init__("删除参考图")
        self.controller = controller
        self.images = [image.clone() for image in images]

    def redo(self) -> None:
        self.controller._remove_canvas_images([image.uuid for image in self.images])

    def undo(self) -> None:
        self.controller._insert_canvas_images([image.clone() for image in self.images])


class MoveCanvasImagesCommand(QUndoCommand):
    def __init__(self, controller, old_positions, new_positions) -> None:
        super().__init__("移动参考图")
        self.controller = controller
        self.old_positions = dict(old_positions)
        self.new_positions = dict(new_positions)

    def redo(self) -> None:
        self.controller._move_canvas_images(self.new_positions)

    def undo(self) -> None:
        self.controller._move_canvas_images(self.old_positions)


class ResizeCanvasImagesCommand(QUndoCommand):
    def __init__(self, controller, old_sizes, new_sizes) -> None:
        super().__init__("调整参考图大小")
        self.controller = controller
        self.old_sizes = dict(old_sizes)
        self.new_sizes = dict(new_sizes)

    def redo(self) -> None:
        self.controller._resize_canvas_images(self.new_sizes)

    def undo(self) -> None:
        self.controller._resize_canvas_images(self.old_sizes)


class AddCanvasStrokesCommand(QUndoCommand):
    def __init__(self, controller, strokes) -> None:
        super().__init__("添加画笔线条")
        self.controller = controller
        self.strokes = [stroke.clone() for stroke in strokes]

    def redo(self) -> None:
        self.controller._insert_canvas_strokes([stroke.clone() for stroke in self.strokes])

    def undo(self) -> None:
        self.controller._remove_canvas_strokes([stroke.uuid for stroke in self.strokes])


class RemoveCanvasStrokesCommand(QUndoCommand):
    def __init__(self, controller, strokes) -> None:
        super().__init__("删除画笔线条")
        self.controller = controller
        self.strokes = [stroke.clone() for stroke in strokes]
        index_by_uuid = {
            stroke.uuid: index
            for index, stroke in enumerate(controller.document.canvas_strokes)
        }
        self.indexed_strokes = [
            (index_by_uuid[stroke.uuid], stroke.clone())
            for stroke in strokes
            if stroke.uuid in index_by_uuid
        ]

    def redo(self) -> None:
        self.controller._remove_canvas_strokes([stroke.uuid for stroke in self.strokes])

    def undo(self) -> None:
        self.controller._restore_canvas_strokes(
            [(index, stroke.clone()) for index, stroke in self.indexed_strokes]
        )


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
        self.before_plan_layout = controller.document.plan_layout.clone()
        self.after_plan_layout = None

    def redo(self) -> None:
        self.controller._add_connection(self.connection)
        if self.after_plan_layout is None:
            self.after_plan_layout = self.controller.document.plan_layout.clone()
        else:
            self.controller._set_plan_layout(self.after_plan_layout.clone())

    def undo(self) -> None:
        self.controller._remove_connection((self.connection.from_uuid, self.connection.to_uuid))
        self.controller._set_plan_layout(self.before_plan_layout.clone())


class RemoveConnectionCommand(QUndoCommand):
    def __init__(self, controller, connection) -> None:
        super().__init__("删除连线")
        self.controller = controller
        self.connection = connection
        self.before_plan_layout = controller.document.plan_layout.clone()
        self.after_plan_layout = None

    def redo(self) -> None:
        self.controller._remove_connection((self.connection.from_uuid, self.connection.to_uuid))
        if self.after_plan_layout is None:
            self.after_plan_layout = self.controller.document.plan_layout.clone()
        else:
            self.controller._set_plan_layout(self.after_plan_layout.clone())

    def undo(self) -> None:
        self.controller._add_connection(self.connection)
        self.controller._set_plan_layout(self.before_plan_layout.clone())
