"""Controller layer for editor state and commands."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QUndoStack

from .commands import (
    AddConnectionCommand,
    AddCanvasImagesCommand,
    AddCanvasStrokesCommand,
    AddNodesCommand,
    MoveCanvasImagesCommand,
    MoveNodeCommand,
    MoveNodesCommand,
    MaterializePlanTopicsCommand,
    RemoveConnectionCommand,
    RemoveCanvasImagesCommand,
    RemoveCanvasStrokesCommand,
    RemoveNodesCommand,
    ResizeCanvasImagesCommand,
    SetGroupsCommand,
    SetPlanLayoutCommand,
    SetPlanViewCommand,
    UpdateEditorSettingsCommand,
    UpdateFieldCommand,
    UpdateFieldsCommand,
    UpdateManyFieldsCommand,
    UpdateNodeLockCommand,
    UpdateSequenceLocksCommand,
    UpdatePlanGraphCommand,
)
from .constants import CLIPBOARD_MIME
from .logic import (
    _should_persist_target_idle,
    apply_clone_sequence_fields,
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
    validate_canvas_stroke,
    validate_canvas_strokes,
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
    NODE_THEME_FIELD_KEYS,
)
from .models import (
    CanvasImageRecord,
    CanvasStrokeRecord,
    ConnectionRecord,
    DocumentModel,
    EditorPreferences,
    GroupRecord,
    NodeRecord,
    PlanLayout,
    PlanTopicRecord,
)
from .plan import (
    PLAN_TOUCHDRAG_COLOR,
    PLAN_TOUCHIDLE_COLOR,
    PLAN_TITLE_MAX_LENGTH,
    clone_connections,
    normalize_plan_layout,
    plan_children_map,
    plan_formal_positions,
    plan_is_descendant,
    plan_root_uuid,
    plan_subtree_uuids,
    plan_topic_map,
    plan_topic_type_from_color,
    plan_topic_title,
    parse_touchidle_plan_title,
    placeholder_fields_for_title,
)
from .perf_tools import get_performance_recorder
from .reference_images import (
    MAX_DOCUMENT_REFERENCE_IMAGE_BYTES,
    MAX_DOCUMENT_REFERENCE_IMAGE_PIXELS,
    MAX_REFERENCE_IMAGE_BYTES,
    MAX_REFERENCE_IMAGE_COUNT,
    canonicalize_reference_image,
    reference_image_dimensions,
    trusted_reference_image_size,
    validated_reference_image_display_size,
)
from .schema import load_editor_schema

performance_recorder = get_performance_recorder()


class EditorController(QObject):
    documentLoaded = Signal()
    nodeAdded = Signal(str)
    nodeRemoved = Signal(str)
    nodeUpdated = Signal(str)
    nodeMoved = Signal(str)
    connectionsChanged = Signal()
    validationChanged = Signal(object)
    csvPreviewChanged = Signal(object)
    selectionChanged = Signal(object)
    pathChanged = Signal(object)
    documentSaved = Signal(str)
    statusMessage = Signal(str)
    documentStateChanged = Signal(object)
    globalModeChanged = Signal(str)
    interactionCreationModeChanged = Signal(str)
    schemaChanged = Signal()
    metaActionBlocked = Signal(str)
    editorSettingsChanged = Signal(object)
    groupsChanged = Signal()
    canvasImagesChanged = Signal()
    canvasStrokesChanged = Signal()
    planCanvasStrokesChanged = Signal()
    planLayoutChanged = Signal()
    planViewChanged = Signal(object)

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
            self.refresh_derived(emit_node_updates=True)
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
        }
        new_settings = dict(old_settings)
        new_settings["numeric_linkage_enabled"] = enabled
        self.undo_stack.push(UpdateEditorSettingsCommand(self, old_settings, new_settings, label="切换数值联动"))

    def set_node_locked(self, node_uuid: str, locked: bool) -> None:
        node = self.get_node(node_uuid)
        if not node or node.locked == bool(locked):
            return
        self.undo_stack.push(UpdateNodeLockCommand(self, node_uuid, node.locked, bool(locked)))

    def set_nodes_sequence_locked(
        self,
        node_uuids: list[str],
        locked: bool,
    ) -> bool:
        """Persist or release visible sequence numbers for function nodes."""

        target = bool(locked)
        function_types = set(function_node_types(self.schema))
        changes: dict[str, tuple[bool, bool]] = {}
        for node_uuid in dict.fromkeys(node_uuids):
            node = self.get_node(node_uuid)
            if (
                node is None
                or node.type not in function_types
                or not isinstance(node.type_slot, int)
                or node.type_slot <= 0
                or node.sequence_locked == target
            ):
                continue
            changes[node_uuid] = (node.sequence_locked, target)
        if not changes:
            return False
        self.undo_stack.push(UpdateSequenceLocksCommand(self, changes))
        return True

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

    def refresh_derived(self, *, emit_node_updates: bool = False) -> None:
        with performance_recorder.measure("controller.refresh_derived", "controller", self._perf_document_meta()):
            self.ensure_plan_layout()
            self._ensure_parameter_table_metadata()
            self.document.groups = normalized_document_groups(self.document)
            with performance_recorder.measure("controller.reassign_function_ids", "controller", self._perf_document_meta()):
                reassign_function_ids(self.schema, self.document)
            if emit_node_updates:
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

    def get_canvas_image(self, image_uuid: str) -> CanvasImageRecord | None:
        return next((image for image in self.document.canvas_images if image.uuid == image_uuid), None)

    def _canvas_stroke_records(self, layer: str = "formal") -> list[CanvasStrokeRecord]:
        return (
            self.document.plan_canvas_strokes
            if layer == "plan"
            else self.document.canvas_strokes
        )

    def get_canvas_stroke(
        self, stroke_uuid: str, layer: str = "formal"
    ) -> CanvasStrokeRecord | None:
        return next(
            (
                stroke
                for stroke in self._canvas_stroke_records(layer)
                if stroke.uuid == stroke_uuid
            ),
            None,
        )

    def ensure_plan_layout(self) -> PlanLayout:
        before = self.document.plan_layout.clone()
        normalized = normalize_plan_layout(self.document)
        if normalized != before:
            self.planLayoutChanged.emit()
        return normalized

    def plan_topic(self, node_uuid: str) -> PlanTopicRecord | None:
        topic = plan_topic_map(self.document).get(node_uuid)
        return topic.clone() if topic is not None else None

    def plan_topic_records(self) -> list[PlanTopicRecord]:
        self.ensure_plan_layout()
        return [topic.clone() for topic in self.document.plan_layout.topics]

    def plan_title(self, node_uuid: str) -> str:
        node = self.get_node(node_uuid)
        if node is None:
            return ""
        return plan_topic_title(self.schema, self.document, node)

    def plan_children(self, parent_uuid: str | None) -> list[str]:
        return list(plan_children_map(self.document).get(parent_uuid, ()))

    def plan_subtree_uuids(self, node_uuid: str) -> list[str]:
        return plan_subtree_uuids(self.document, node_uuid)

    def set_plan_view_state(
        self,
        scale: float,
        offset_x: float,
        offset_y: float,
    ) -> bool:
        values = (float(scale), float(offset_x), float(offset_y))
        if not all(math.isfinite(value) for value in values):
            return False
        normalized = (
            max(0.03, min(8.0, values[0])),
            values[1],
            values[2],
        )
        current_view = self.document.plan_layout.view
        current = (
            float(current_view.scale),
            float(current_view.offset_x),
            float(current_view.offset_y),
        )
        if all(
            math.isclose(old, new, rel_tol=0.0, abs_tol=1e-6)
            for old, new in zip(current, normalized)
        ):
            return False
        self.undo_stack.push(SetPlanViewCommand(self, current, normalized))
        return True

    def _normalized_plan_layout_for(
        self,
        *,
        nodes: list[NodeRecord] | None = None,
        connections: list[ConnectionRecord] | None = None,
        layout: PlanLayout | None = None,
    ) -> PlanLayout:
        staging = copy.copy(self.document)
        staging.nodes = [node.clone() for node in (nodes if nodes is not None else self.document.nodes)]
        staging.connections = clone_connections(connections if connections is not None else self.document.connections)
        staging.plan_layout = (layout or self.document.plan_layout).clone()
        return normalize_plan_layout(staging).clone()

    @staticmethod
    def _set_plan_sibling_order(
        layout: PlanLayout,
        parent_uuid: str | None,
        ordered_node_uuids: list[str],
    ) -> None:
        order_by_uuid = {
            node_uuid: order for order, node_uuid in enumerate(ordered_node_uuids)
        }
        for topic in layout.topics:
            if topic.parent_uuid == parent_uuid and topic.node_uuid in order_by_uuid:
                topic.order = order_by_uuid[topic.node_uuid]

    @staticmethod
    def _mark_plan_subtree_structure_dirty(
        layout: PlanLayout,
        node_uuid: str,
    ) -> None:
        children: dict[str, list[str]] = {}
        for topic in layout.topics:
            if topic.parent_uuid is not None:
                children.setdefault(topic.parent_uuid, []).append(topic.node_uuid)
        pending = [node_uuid]
        dirty: set[str] = set()
        while pending:
            current = pending.pop()
            if current in dirty:
                continue
            dirty.add(current)
            pending.extend(children.get(current, ()))
        for topic in layout.topics:
            if topic.node_uuid in dirty:
                topic.structure_dirty = True

    def create_plan_topic(
        self,
        parent_uuid: str | None,
        title: str = "新主题",
        *,
        after_uuid: str | None = None,
    ) -> str | None:
        allowed, reason = self.can_create_graph_content()
        if not allowed:
            self._emit_meta_blocked(reason)
            return None
        if parent_uuid is not None and self.get_node(parent_uuid) is None:
            return None
        resolved_title = str(title or "").strip() or "新主题"
        old_layout = self.ensure_plan_layout().clone()
        siblings = list(plan_children_map(self.document).get(parent_uuid, ()))
        insert_index = len(siblings)
        if after_uuid in siblings:
            insert_index = siblings.index(after_uuid) + 1

        if parent_uuid is not None:
            parent = self.get_node(parent_uuid)
            base_x = float(parent.ui_position.get("x", 0.0)) + 420.0
            base_y = float(parent.ui_position.get("y", 0.0)) + 120.0 * insert_index
        else:
            base_x = 420.0
            base_y = 120.0 * insert_index
        node = create_node(
            self.schema,
            self.document,
            "PlanPlaceholder",
            (base_x, base_y),
        )
        node.fields.update(placeholder_fields_for_title(resolved_title))
        new_layout = old_layout.clone()
        if parent_uuid is not None:
            for record in new_layout.topics:
                if record.node_uuid == parent_uuid:
                    # Creating a child must reveal it immediately.  Keeping
                    # this in the same layout command makes Tab a single undo
                    # transaction even when its parent was collapsed.
                    record.collapsed = False
                    break
        new_layout.topics.append(
            PlanTopicRecord(
                node_uuid=node.uuid,
                parent_uuid=parent_uuid,
                order=insert_index,
                plan_title=resolved_title,
                collapsed=False,
                branch_color=PLAN_TOUCHIDLE_COLOR,
                formalization_state="draft",
                structure_dirty=True,
            )
        )
        siblings.insert(insert_index, node.uuid)
        self._set_plan_sibling_order(new_layout, parent_uuid, siblings)
        new_connections = clone_connections(self.document.connections)
        added_connections: list[ConnectionRecord] = []
        if parent_uuid is not None:
            connection = ConnectionRecord(from_uuid=parent_uuid, to_uuid=node.uuid)
            new_connections.append(connection)
            added_connections.append(connection)
        new_layout = self._normalized_plan_layout_for(
            nodes=[*self.document.nodes, node],
            connections=new_connections,
            layout=new_layout,
        )
        self.undo_stack.beginMacro("新增计划主题")
        try:
            self.undo_stack.push(AddNodesCommand(self, [node], added_connections))
            self.undo_stack.push(
                SetPlanLayoutCommand(
                    self,
                    old_layout,
                    new_layout,
                    label="设置计划主题",
                )
            )
        finally:
            self.undo_stack.endMacro()
        self.set_selected_node(node.uuid)
        return node.uuid

    def set_plan_title(self, node_uuid: str, title: str) -> bool:
        topic = self.plan_topic(node_uuid)
        if topic is None:
            return False
        resolved = str(title or "").strip()
        if topic.plan_title == resolved:
            return False
        old_layout = self.document.plan_layout.clone()
        new_layout = old_layout.clone()
        for record in new_layout.topics:
            if record.node_uuid == node_uuid:
                record.plan_title = resolved
                if record.formalization_state in {"formal", "virtual", "materialized"}:
                    record.formalization_state = "draft"
                break
        node = self.get_node(node_uuid)
        placeholder_updates: list[tuple[str, Any, Any]] = []
        if node is not None and node.type == "PlanPlaceholder":
            for key, value in placeholder_fields_for_title(resolved).items():
                old_value = node.fields.get(key)
                if old_value != value:
                    placeholder_updates.append((key, old_value, value))
        self.undo_stack.beginMacro("修改计划标题")
        try:
            self.undo_stack.push(
                SetPlanLayoutCommand(
                    self, old_layout, new_layout, label="修改计划标题"
                )
            )
            if placeholder_updates:
                self.undo_stack.push(
                    UpdateFieldsCommand(
                        self,
                        node_uuid,
                        placeholder_updates,
                        "advanced",
                        label="同步虚节点占位字段",
                    )
                )
        finally:
            self.undo_stack.endMacro()
        return True

    def materialize_plan_topics(self) -> bool:
        """Convert all pending plan topics as one undoable transaction."""

        old_layout = self.ensure_plan_layout().clone()
        inferred = {
            topic.node_uuid: node_type
            for topic in old_layout.topics
            if (node_type := plan_topic_type_from_color(topic)) is not None
        }
        candidates = [
            topic
            for topic in old_layout.topics
            if (
                # Green/purple topics are plan-owned generated nodes.  Include
                # materialized nodes as well so title edits, plan reordering,
                # and newly added siblings cannot leave stale formal fields or
                # old sequence numbers behind.
                (
                    topic.node_uuid in inferred
                    and topic.formalization_state != "formal"
                )
                or (
                    topic.formalization_state in {"draft", "virtual"}
                    and (
                        topic.formalization_state == "draft"
                        or parse_touchidle_plan_title(topic.plan_title) is not None
                    )
                )
            )
        ]
        if not candidates:
            return False
        candidate_ids = {topic.node_uuid for topic in candidates}
        candidate_topic_by_uuid = {
            topic.node_uuid: topic for topic in candidates
        }
        formal_positions = plan_formal_positions(self.document)
        tree_children = plan_children_map(self.document)
        root_uuid = plan_root_uuid(self.document)
        branch_order: dict[str, int] = {}
        top_level_uuids = [
            *tree_children.get(root_uuid, ()),
            *tree_children.get(None, ()),
        ]
        for branch_uuid in top_level_uuids:
            # Finish one horizontal root branch before moving down to the next
            # one.  Depth-first preorder follows the upper child all the way
            # to the right before returning to a lower sibling, e.g.
            # 趴下1 -> 摸头1 -> 摸头3 -> 摸头2.
            stack = [branch_uuid]
            while stack:
                node_uuid = stack.pop()
                if node_uuid in branch_order:
                    continue
                branch_order[node_uuid] = len(branch_order)
                stack.extend(reversed(tree_children.get(node_uuid, ())))
        old_nodes = [
            node.clone()
            for node in self.document.nodes
            if node.uuid in candidate_ids
        ]

        def visual_order(node: NodeRecord) -> tuple[int, float, float, str]:
            planned = formal_positions.get(
                node.uuid,
                (
                    float(node.ui_position.get("x", 0.0)),
                    float(node.ui_position.get("y", 0.0)),
                ),
            )
            topic = candidate_topic_by_uuid.get(node.uuid)
            # Follow the upper path from left to right before returning to its
            # lower sibling, then move down to the next root branch.  Planned
            # coordinates are stable fallbacks for malformed/legacy topics.
            return (
                branch_order.get(node.uuid, 10**9),
                float(planned[1]),
                float(planned[0]) + (int(topic.order) if topic is not None else 0) * 1e-6,
                node.uuid,
            )

        old_nodes.sort(key=visual_order)
        staging = copy.copy(self.document)
        staging.nodes = [
            node.clone()
            for node in self.document.nodes
            if node.uuid not in candidate_ids
        ]

        def sequence_namespace(node_type: str) -> str:
            if node_type in {"TouchIdle", "ReturnDefaultIdle"}:
                return "TouchIdle"
            if node_type in {"TouchDrag", "ParameterTrigger"}:
                return "TouchDrag"
            return node_type

        occupied_slots: dict[str, set[int]] = {}
        for node in staging.nodes:
            if not isinstance(node.type_slot, int) or node.type_slot <= 0:
                continue
            occupied_slots.setdefault(sequence_namespace(node.type), set()).add(
                int(node.type_slot)
            )

        assigned_semantic_slots: dict[str, int] = {}

        def next_free_slot(namespace: str) -> int:
            occupied = occupied_slots.setdefault(namespace, set())
            slot = 1
            while slot in occupied:
                slot += 1
            occupied.add(slot)
            return slot

        # Reserve every fixed number before assigning any automatic number.
        # A rare namespace collision (for example after changing a fixed node
        # from Idle to Drag) keeps the first fixed node and moves the later one
        # to the next available slot so the document remains valid.
        for old_node in old_nodes:
            target_type = inferred.get(old_node.uuid)
            if target_type is None or not old_node.sequence_locked:
                continue
            namespace = sequence_namespace(target_type)
            preferred = (
                int(old_node.type_slot)
                if isinstance(old_node.type_slot, int) and old_node.type_slot > 0
                else 0
            )
            occupied = occupied_slots.setdefault(namespace, set())
            if preferred > 0 and preferred not in occupied:
                occupied.add(preferred)
                assigned_semantic_slots[old_node.uuid] = preferred
            else:
                assigned_semantic_slots[old_node.uuid] = next_free_slot(namespace)
        for old_node in old_nodes:
            target_type = inferred.get(old_node.uuid)
            if target_type is None or old_node.uuid in assigned_semantic_slots:
                continue
            assigned_semantic_slots[old_node.uuid] = next_free_slot(
                sequence_namespace(target_type)
            )

        new_layout = old_layout.clone()
        topic_by_uuid = {
            topic.node_uuid: topic for topic in new_layout.topics
        }
        new_nodes: list[NodeRecord] = []
        formal_node_types = set(function_node_types(self.schema))
        for old_node in old_nodes:
            topic = topic_by_uuid[old_node.uuid]
            semantic = inferred.get(old_node.uuid)
            # Explicit green/purple plan colors are authoritative.  Parsing
            # the legacy numbered title is only a fallback for old files
            # whose topics have not been assigned a semantic color yet.
            parsed = (
                None
                if semantic is not None
                else parse_touchidle_plan_title(topic.plan_title)
            )
            planned_position = formal_positions.get(old_node.uuid)
            replacement_position = (
                (
                    float(old_node.ui_position.get("x", 0.0)),
                    float(old_node.ui_position.get("y", 0.0)),
                )
                if (
                    old_node.locked
                    or not topic.structure_dirty
                    or planned_position is None
                )
                else planned_position
            )
            if parsed is None and semantic is None:
                replacement = old_node.clone()
                replacement.type = "PlanPlaceholder"
                replacement.fields = placeholder_fields_for_title(topic.plan_title)
                replacement.ui_position = {
                    "x": float(replacement_position[0]),
                    "y": float(replacement_position[1]),
                }
                replacement.type_slot = None
                replacement.export_slot = None
                replacement.sequence_no = None
                replacement.numeric_linkage_enabled = False
                replacement.sequence_locked = False
                replacement.manual_fields.clear()
                apply_node_appearance_defaults(self.schema, replacement)
                topic.formalization_state = "virtual"
                topic.structure_dirty = False
            else:
                replacement_type = semantic or "TouchIdle"
                replacement = create_node(
                    self.schema,
                    staging,
                    replacement_type,
                    replacement_position,
                )
                replacement.uuid = old_node.uuid
                replacement.locked = old_node.locked
                replacement.sequence_locked = old_node.sequence_locked
                replacement.ui_size = (
                    dict(old_node.ui_size) if old_node.ui_size else None
                )
                assigned_slot = assigned_semantic_slots.get(old_node.uuid)
                if assigned_slot is not None:
                    replacement.type_slot = assigned_slot
                    replacement.sequence_no = assigned_slot
                elif (
                    parsed is not None
                    and old_node.sequence_locked
                    and isinstance(old_node.type_slot, int)
                    and old_node.type_slot > 0
                ):
                    replacement.type_slot = int(old_node.type_slot)
                    replacement.sequence_no = int(old_node.type_slot)
                if parsed is not None:
                    replacement.fields["transition_type"] = "animated"
                    replacement.fields["draw_able_name"] = (
                        f"TouchIdle{parsed.draw_index}"
                    )
                    replacement.fields["parameter"] = "empty"
                    replacement.fields["action_trigger"] = normalize_field_input(
                        self.schema,
                        replacement,
                        "action_trigger",
                        f"touch_idle{parsed.action_index}",
                    )
                    replacement.manual_fields.update(
                        {"draw_able_name", "parameter", "action_trigger"}
                    )
                    if parsed.note_text:
                        replacement.fields["tips"] = parsed.note_text
                        replacement.manual_fields.add("tips")
                    apply_auto_rules(
                        self.schema,
                        staging,
                        replacement,
                        source_mode="advanced",
                        force_generated=False,
                    )
                else:
                    sequence = int(assigned_slot or replacement.type_slot or replacement.sequence_no or 1)
                    if topic.plan_title:
                        replacement.fields["tips"] = topic.plan_title
                        replacement.manual_fields.add("tips")
                    if replacement.type == "TouchIdle":
                        target_idle = sequence
                        replacement.fields["draw_able_name"] = f"TouchIdle{sequence}"
                        replacement.fields["transition_type"] = "animated"
                        replacement.fields["target_idle"] = target_idle
                        replacement.fields["parameter"] = "empty"
                        replacement.fields["action_trigger_active"] = normalize_field_input(
                            self.schema,
                            replacement,
                            "action_trigger_active",
                            target_idle,
                        )
                        replacement.fields["action_trigger"] = normalize_field_input(
                            self.schema,
                            replacement,
                            "action_trigger",
                            f"touch_idle{sequence}",
                        )
                        replacement.manual_fields.update(
                            {"parameter", "action_trigger"}
                        )
                    else:
                        replacement.fields["draw_able_name"] = f"TouchDrag{sequence}"
                        replacement.fields["parameter"] = f"touch_drag{sequence}"
                        replacement.fields["result_type"] = "action"
                        replacement.fields["target_idle"] = 0
                        replacement.fields["action_trigger"] = normalize_field_input(
                            self.schema,
                            replacement,
                            "action_trigger",
                            f"touch_drag{sequence}",
                        )
                        replacement.fields["action_trigger_active"] = ""
                        replacement.fields["action_trigger_active_kind_ui"] = "empty"
                        replacement.fields["action_trigger_active_reserved_ui"] = ""
                        replacement.manual_fields.add("action_trigger")
                if old_node.type in formal_node_types:
                    for key in NODE_THEME_FIELD_KEYS:
                        if key in old_node.fields:
                            replacement.fields[key] = old_node.fields[key]
                topic.formalization_state = "materialized"
                topic.structure_dirty = False
            staging.nodes.append(replacement)
            new_nodes.append(replacement)
        # Compute the same derived IDs/auto fields the command will install so
        # an already synchronized plan does not create a no-op undo entry.
        reassign_function_ids(self.schema, staging)
        if old_nodes == new_nodes and old_layout == new_layout:
            return False
        self.undo_stack.push(
            MaterializePlanTopicsCommand(
                self,
                old_nodes,
                new_nodes,
                old_layout,
                new_layout,
            )
        )
        return True

    def _materialize_virtual_placeholder_from_expected_fields(
        self,
        node_uuid: str,
        updated_values: dict[str, Any],
        source_mode: str,
    ) -> bool:
        """Promote a virtual plan node as soon as either expected name is set.

        The replacement command includes the just-entered field value.  This
        makes direct-card and Inspector edits a single, reversible action
        instead of briefly committing a placeholder update and then adding a
        second conversion command.
        """

        if not ({"planned_draw_name", "planned_action_name"} & set(updated_values)):
            return False
        node = self.get_node(node_uuid)
        if node is None or node.type != "PlanPlaceholder" or node.locked:
            return False

        expected_draw = str(
            updated_values.get(
                "planned_draw_name",
                node.fields.get("planned_draw_name", ""),
            )
            or ""
        ).strip()
        expected_action = str(
            updated_values.get(
                "planned_action_name",
                node.fields.get("planned_action_name", ""),
            )
            or ""
        ).strip()
        if not expected_draw and not expected_action:
            return False

        old_layout = self.ensure_plan_layout().clone()
        new_layout = old_layout.clone()
        topic = next(
            (record for record in new_layout.topics if record.node_uuid == node_uuid),
            None,
        )
        if topic is None:
            return False

        old_node = node.clone()
        staging = copy.copy(self.document)
        staging.nodes = [
            current.clone()
            for current in self.document.nodes
            if current.uuid != node_uuid
        ]
        replacement = create_node(
            self.schema,
            staging,
            "TouchIdle",
            (
                float(node.ui_position.get("x", 0.0)),
                float(node.ui_position.get("y", 0.0)),
            ),
        )
        replacement.uuid = node.uuid
        replacement.locked = node.locked
        replacement.ui_size = dict(node.ui_size) if node.ui_size else None
        replacement.fields["transition_type"] = "animated"
        replacement.fields["parameter"] = "empty"
        replacement.manual_fields.add("parameter")
        if expected_draw:
            replacement.fields["draw_able_name"] = expected_draw
            replacement.manual_fields.add("draw_able_name")
        if expected_action:
            replacement.fields["action_trigger"] = normalize_field_input(
                self.schema,
                replacement,
                "action_trigger",
                expected_action,
            )
            replacement.manual_fields.add("action_trigger")

        source_title = str(node.fields.get("plan_source_title") or "").strip()
        parsed_source = parse_touchidle_plan_title(source_title)
        note = parsed_source.note_text if parsed_source is not None else source_title
        if note:
            replacement.fields["tips"] = note
            replacement.manual_fields.add("tips")
        apply_auto_rules(
            self.schema,
            staging,
            replacement,
            source_mode=source_mode,
            force_generated=False,
        )
        topic.formalization_state = "materialized"
        topic.structure_dirty = False
        self.undo_stack.push(
            MaterializePlanTopicsCommand(
                self,
                [old_node],
                [replacement],
                old_layout,
                new_layout,
            )
        )
        return True

    def set_plan_collapsed(self, node_uuid: str, collapsed: bool) -> bool:
        topic = self.plan_topic(node_uuid)
        if topic is None or topic.collapsed == bool(collapsed):
            return False
        old_layout = self.document.plan_layout.clone()
        new_layout = old_layout.clone()
        for record in new_layout.topics:
            if record.node_uuid == node_uuid:
                record.collapsed = bool(collapsed)
                break
        self.undo_stack.push(
            SetPlanLayoutCommand(self, old_layout, new_layout, label="折叠计划分支")
        )
        return True

    def toggle_plan_collapsed(self, node_uuid: str) -> bool:
        topic = self.plan_topic(node_uuid)
        return bool(topic and self.set_plan_collapsed(node_uuid, not topic.collapsed))

    def set_plan_topic_color(self, node_uuid: str, color: str) -> bool:
        """Set one plan node's explicit TouchIdle/TouchDrag semantic color."""

        resolved = str(color or "").strip().upper()
        if resolved not in {PLAN_TOUCHIDLE_COLOR, PLAN_TOUCHDRAG_COLOR}:
            return False
        topic = self.plan_topic(node_uuid)
        if topic is None or node_uuid == plan_root_uuid(self.document):
            return False
        if topic.branch_color.upper() == resolved:
            return False
        old_layout = self.document.plan_layout.clone()
        new_layout = old_layout.clone()
        for record in new_layout.topics:
            if record.node_uuid == node_uuid:
                record.branch_color = resolved
                record.formalization_state = "draft"
                break
        self.undo_stack.push(
            SetPlanLayoutCommand(self, old_layout, new_layout, label="设置计划节点类型")
        )
        return True

    def set_plan_branch_color(self, node_uuid: str, color: str) -> bool:
        resolved = str(color or "").strip().upper()
        if len(resolved) != 7 or not resolved.startswith("#"):
            return False
        try:
            int(resolved[1:], 16)
        except ValueError:
            return False
        topics = plan_topic_map(self.document)
        topic = topics.get(node_uuid)
        root_uuid = plan_root_uuid(self.document)
        if topic is None or node_uuid == root_uuid:
            return False
        branch_root = node_uuid
        while topics.get(branch_root) and topics[branch_root].parent_uuid not in {None, root_uuid}:
            branch_root = topics[branch_root].parent_uuid or branch_root
        descendants = set(plan_subtree_uuids(self.document, branch_root))
        if all(
            record.branch_color.upper() == resolved
            for record in self.document.plan_layout.topics
            if record.node_uuid in descendants
        ):
            return False
        old_layout = self.document.plan_layout.clone()
        new_layout = old_layout.clone()
        for record in new_layout.topics:
            if record.node_uuid in descendants:
                record.branch_color = resolved
        self.undo_stack.push(
            SetPlanLayoutCommand(self, old_layout, new_layout, label="修改计划分支颜色")
        )
        return True

    def reorder_plan_topic(self, node_uuid: str, index: int) -> bool:
        topic = self.plan_topic(node_uuid)
        if topic is None or node_uuid == plan_root_uuid(self.document):
            return False
        siblings = list(plan_children_map(self.document).get(topic.parent_uuid, ()))
        if node_uuid not in siblings:
            return False
        old_index = siblings.index(node_uuid)
        siblings.remove(node_uuid)
        resolved_index = max(0, min(int(index), len(siblings)))
        siblings.insert(resolved_index, node_uuid)
        if old_index == resolved_index:
            return False
        old_layout = self.document.plan_layout.clone()
        new_layout = old_layout.clone()
        self._set_plan_sibling_order(new_layout, topic.parent_uuid, siblings)
        self._mark_plan_subtree_structure_dirty(new_layout, node_uuid)
        self.undo_stack.push(
            SetPlanLayoutCommand(self, old_layout, new_layout, label="重排计划主题")
        )
        return True

    def reparent_plan_topic(
        self,
        node_uuid: str,
        new_parent_uuid: str | None,
        *,
        index: int | None = None,
    ) -> bool:
        root_uuid = plan_root_uuid(self.document)
        topic = self.plan_topic(node_uuid)
        if topic is None or node_uuid == root_uuid:
            return False
        if new_parent_uuid is not None and self.get_node(new_parent_uuid) is None:
            return False
        if new_parent_uuid == node_uuid or (
            new_parent_uuid is not None
            and plan_is_descendant(self.document, new_parent_uuid, node_uuid)
        ):
            return False
        if topic.parent_uuid == new_parent_uuid:
            return self.reorder_plan_topic(
                node_uuid,
                topic.order if index is None else index,
            )

        old_layout = self.document.plan_layout.clone()
        new_layout = old_layout.clone()
        old_siblings = list(plan_children_map(self.document).get(topic.parent_uuid, ()))
        if node_uuid in old_siblings:
            old_siblings.remove(node_uuid)
        new_siblings = list(plan_children_map(self.document).get(new_parent_uuid, ()))
        if node_uuid in new_siblings:
            new_siblings.remove(node_uuid)
        resolved_index = len(new_siblings) if index is None else max(0, min(int(index), len(new_siblings)))
        new_siblings.insert(resolved_index, node_uuid)
        for record in new_layout.topics:
            if record.node_uuid == node_uuid:
                record.parent_uuid = new_parent_uuid
                record.order = resolved_index
                break
        self._mark_plan_subtree_structure_dirty(new_layout, node_uuid)
        self._set_plan_sibling_order(new_layout, topic.parent_uuid, old_siblings)
        self._set_plan_sibling_order(new_layout, new_parent_uuid, new_siblings)

        old_connections = clone_connections(self.document.connections)
        new_connections = [
            connection
            for connection in clone_connections(self.document.connections)
            if not (
                topic.parent_uuid is not None
                and connection.from_uuid == topic.parent_uuid
                and connection.to_uuid == node_uuid
            )
        ]
        if new_parent_uuid is not None and not any(
            connection.from_uuid == new_parent_uuid and connection.to_uuid == node_uuid
            for connection in new_connections
        ):
            new_connections.append(
                ConnectionRecord(from_uuid=new_parent_uuid, to_uuid=node_uuid)
            )
        new_layout = self._normalized_plan_layout_for(
            connections=new_connections,
            layout=new_layout,
        )
        self.undo_stack.push(
            UpdatePlanGraphCommand(
                self,
                old_layout,
                new_layout,
                old_connections,
                new_connections,
                label="调整计划主题层级",
            )
        )
        return True

    def promote_plan_topic(self, node_uuid: str) -> bool:
        topics = plan_topic_map(self.document)
        topic = topics.get(node_uuid)
        root_uuid = plan_root_uuid(self.document)
        if topic is None or node_uuid == root_uuid:
            return False
        if topic.parent_uuid is None:
            # A top-level item in the virtual "unconnected" branch is
            # promoted into the real root rather than becoming a no-op.
            return bool(
                root_uuid
                and self.reparent_plan_topic(node_uuid, root_uuid)
            )
        parent = topics.get(topic.parent_uuid)
        if parent is None or parent.parent_uuid is None:
            return False
        parent_siblings = list(plan_children_map(self.document).get(parent.parent_uuid, ()))
        try:
            insert_index = parent_siblings.index(parent.node_uuid) + 1
        except ValueError:
            insert_index = len(parent_siblings)
        return self.reparent_plan_topic(
            node_uuid,
            parent.parent_uuid,
            index=insert_index,
        )

    def delete_plan_subtree(self, node_uuid: str) -> bool:
        return self.delete_plan_subtrees([node_uuid])

    def delete_plan_subtrees(self, node_uuids: list[str]) -> bool:
        root_uuid = plan_root_uuid(self.document)
        subtree: set[str] = set()
        for node_uuid in node_uuids:
            if node_uuid == root_uuid:
                continue
            subtree.update(plan_subtree_uuids(self.document, node_uuid))
        nodes = [
            node
            for node in self.document.nodes
            if node.uuid in subtree and self.schema.nodes[node.type].copyable
        ]
        if not nodes:
            return False
        removed_ids = {node.uuid for node in nodes}
        connections = [
            connection
            for connection in self.document.connections
            if connection.from_uuid in removed_ids or connection.to_uuid in removed_ids
        ]
        old_layout = self.ensure_plan_layout().clone()
        remaining_nodes = [
            node for node in self.document.nodes if node.uuid not in removed_ids
        ]
        remaining_connections = [
            connection
            for connection in self.document.connections
            if connection.from_uuid not in removed_ids
            and connection.to_uuid not in removed_ids
        ]
        pruned_layout = old_layout.clone()
        pruned_layout.topics = [
            topic for topic in pruned_layout.topics if topic.node_uuid not in removed_ids
        ]
        new_layout = self._normalized_plan_layout_for(
            nodes=remaining_nodes,
            connections=remaining_connections,
            layout=pruned_layout,
        )
        self.undo_stack.beginMacro(f"删除计划子树（{len(nodes)} 个节点）")
        try:
            self.undo_stack.push(
                SetPlanLayoutCommand(
                    self,
                    old_layout,
                    new_layout,
                    label="删除计划主题布局",
                )
            )
            self.undo_stack.push(
                RemoveNodesCommand(
                    self,
                    nodes,
                    connections,
                    self.group_records(),
                    plan_layout=old_layout,
                )
            )
        finally:
            self.undo_stack.endMacro()
        return True

    def add_canvas_stroke(
        self,
        points: list[tuple[float, float]],
        color: str = "#2F80ED",
        width: float = 4.0,
        layer: str = "formal",
    ) -> str | None:
        resolved_layer = "plan" if layer == "plan" else "formal"
        try:
            stroke = validate_canvas_stroke(
                CanvasStrokeRecord(
                    uuid=new_uuid(),
                    points=points,
                    color=color,
                    width=width,
                )
            )
            # Reject before mutating the undo stack if the document-wide
            # stroke/point budget would make the next save impossible.
            validate_canvas_strokes(
                [*self._canvas_stroke_records(resolved_layer), stroke]
            )
        except ValueError:
            return None
        self.undo_stack.push(
            AddCanvasStrokesCommand(self, [stroke], resolved_layer)
        )
        return stroke.uuid

    def remove_canvas_stroke(
        self, stroke_uuid: str, layer: str = "formal"
    ) -> None:
        self.remove_canvas_strokes([stroke_uuid], layer)

    def remove_canvas_strokes(
        self, stroke_uuids: list[str], layer: str = "formal"
    ) -> None:
        resolved_layer = "plan" if layer == "plan" else "formal"
        selected = set(stroke_uuids)
        strokes = [
            stroke
            for stroke in self._canvas_stroke_records(resolved_layer)
            if stroke.uuid in selected
        ]
        if strokes:
            self.undo_stack.push(
                RemoveCanvasStrokesCommand(self, strokes, resolved_layer)
            )

    def add_canvas_image(
        self,
        data_base64: str,
        size: tuple[float, float],
        position: tuple[float, float],
        *,
        name: str = "参考图",
        mime_type: str = "image/png",
    ) -> str | None:
        if len(self.document.canvas_images) >= MAX_REFERENCE_IMAGE_COUNT:
            return None
        try:
            position_x = float(position[0])
            position_y = float(position[1])
        except (IndexError, OverflowError, TypeError, ValueError):
            return None
        if not math.isfinite(position_x) or not math.isfinite(position_y):
            return None
        canonical = canonicalize_reference_image(data_base64, mime_type)
        if canonical is None:
            return None
        encoded, actual_size, byte_size = canonical
        current_bytes = sum(
            trusted_reference_image_size(record.data_base64) or MAX_REFERENCE_IMAGE_BYTES
            for record in self.document.canvas_images
        )
        if current_bytes + byte_size > MAX_DOCUMENT_REFERENCE_IMAGE_BYTES:
            return None
        current_pixels = 0.0
        for record in self.document.canvas_images:
            intrinsic_size = reference_image_dimensions(
                record.data_base64,
                record.mime_type,
            )
            if intrinsic_size is None:
                return None
            current_pixels += intrinsic_size[0] * intrinsic_size[1]
        if current_pixels + actual_size[0] * actual_size[1] > MAX_DOCUMENT_REFERENCE_IMAGE_PIXELS:
            return None
        image = CanvasImageRecord(
            uuid=new_uuid(),
            data_base64=encoded,
            mime_type="image/png",
            name=str(name or "参考图"),
            ui_position={"x": position_x, "y": position_y},
            ui_size={"width": actual_size[0], "height": actual_size[1]},
        )
        self.undo_stack.push(AddCanvasImagesCommand(self, [image]))
        return image.uuid

    def remove_canvas_images(self, image_uuids: list[str]) -> None:
        selected = set(image_uuids)
        images = [image for image in self.document.canvas_images if image.uuid in selected and not image.locked]
        if images:
            self.undo_stack.push(RemoveCanvasImagesCommand(self, images))

    def move_canvas_image(self, image_uuid: str, position: tuple[float, float]) -> None:
        image = self.get_canvas_image(image_uuid)
        if not image or image.locked:
            return
        try:
            position_x = float(position[0])
            position_y = float(position[1])
        except (IndexError, OverflowError, TypeError, ValueError):
            return
        if not math.isfinite(position_x) or not math.isfinite(position_y):
            return
        old_position = (float(image.ui_position["x"]), float(image.ui_position["y"]))
        new_position = (position_x, position_y)
        if old_position != new_position:
            self.undo_stack.push(
                MoveCanvasImagesCommand(self, {image_uuid: old_position}, {image_uuid: new_position})
            )

    def resize_canvas_image(self, image_uuid: str, size: tuple[float, float]) -> None:
        image = self.get_canvas_image(image_uuid)
        if not image or image.locked:
            return
        new_size = validated_reference_image_display_size(size)
        old_size = validated_reference_image_display_size(
            (
                image.ui_size.get("width", 1.0),
                image.ui_size.get("height", 1.0),
            )
        )
        if new_size is None or old_size is None or old_size == new_size:
            return
        self.undo_stack.push(
            ResizeCanvasImagesCommand(
                self,
                {image_uuid: old_size},
                {image_uuid: new_size},
            )
        )

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
        if not node or node.locked:
            return False
        definition = self.schema.nodes.get(node.type)
        return bool(definition and definition.category not in {"root", "meta"})

    def can_create_graph_content(self) -> tuple[bool, str]:
        if self.document.state.is_meta_ready:
            return True, ""
        missing = " / ".join(self.document.state.meta_missing_fields)
        return False, f"当前配置底座缺少必要信息：{missing}。请通过“批量创建配置底座”重新创建。"

    def _emit_meta_blocked(self, reason: str) -> None:
        self.statusMessage.emit(reason)
        self.metaActionBlocked.emit(reason)

    def _apply_simple_touchidle_defaults(self, node: NodeRecord) -> None:
        if self.preferences.global_mode != "simple" or node.type != "TouchIdle":
            return
        node.fields["parameter"] = "empty"
        node.manual_fields.add("parameter")

    def create_node(self, node_type: str, position: tuple[float, float], base_node: NodeRecord | None = None) -> str | None:
        definition = self.schema.nodes.get(node_type)
        if definition is None or definition.category in {"root", "meta"}:
            return None
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
        definition = self.schema.nodes.get(node_type)
        if definition is None or definition.category in {"root", "meta"} or self.get_node(from_uuid) is None:
            return None
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

    def create_group(
        self,
        node_uuids: list[str],
        title: str | None = None,
        bounds: tuple[float, float, float, float] | None = None,
    ) -> str | None:
        unique_ids: list[str] = []
        seen: set[str] = set()
        for node_uuid in node_uuids:
            node = self.get_node(node_uuid)
            if not node or node_uuid in seen:
                continue
            if self.schema.nodes[node.type].category == "root":
                continue
            if node.type in {"Initial", "Idle0"}:
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
        old_groups = self.group_records()
        ui_position = None
        ui_size = None
        if bounds is not None:
            x, y, width, height = (float(value) for value in bounds)
            if width > 0.0 and height > 0.0:
                ui_position = {"x": x, "y": y}
                ui_size = {"width": width, "height": height}
        group = GroupRecord(
            uuid=new_uuid(),
            title=str(title or "").strip() or DEFAULT_GROUP_TITLE,
            node_uuids=unique_ids,
            ui_position=ui_position,
            ui_size=ui_size,
        )
        selected_ids = set(unique_ids)
        new_groups: list[GroupRecord] = []
        for current in old_groups:
            updated = current.clone()
            updated.node_uuids = [node_uuid for node_uuid in updated.node_uuids if node_uuid not in selected_ids]
            if updated.node_uuids or (updated.ui_position and updated.ui_size):
                new_groups.append(updated)
        new_groups.append(group)
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

    def initialize_group_geometry(self, group_uuid: str, bounds: tuple[float, float, float, float]) -> GroupRecord | None:
        group = self.get_group(group_uuid)
        if not group:
            return None
        if not (group.ui_position and group.ui_size):
            x, y, width, height = (float(value) for value in bounds)
            if width <= 0.0 or height <= 0.0:
                return None
            group.ui_position = {"x": x, "y": y}
            group.ui_size = {"width": width, "height": height}
        return group.clone()

    def set_group_geometry(
        self,
        group_uuid: str,
        bounds: tuple[float, float, float, float],
        label: str = "调整分组范围",
    ) -> bool:
        x, y, width, height = (float(value) for value in bounds)
        if width <= 0.0 or height <= 0.0:
            return False
        old_groups = self.group_records()
        changed = False
        new_groups: list[GroupRecord] = []
        for group in old_groups:
            updated = group.clone()
            if updated.uuid == group_uuid:
                position = {"x": x, "y": y}
                size = {"width": width, "height": height}
                changed = updated.ui_position != position or updated.ui_size != size
                updated.ui_position = position
                updated.ui_size = size
            new_groups.append(updated)
        if not changed:
            return False
        self.undo_stack.push(SetGroupsCommand(self, old_groups, new_groups, label=label))
        return True

    def remove_group(self, group_uuid: str) -> bool:
        old_groups = self.group_records()
        new_groups = [group.clone() for group in old_groups if group.uuid != group_uuid]
        if len(new_groups) == len(old_groups):
            return False
        self.undo_stack.push(SetGroupsCommand(self, old_groups, new_groups, label="删除分组"))
        return True

    def set_node_group_memberships(self, memberships: dict[str, str | None], label: str = "更新分组成员") -> bool:
        old_groups = self.group_records()
        existing_group_ids = {group.uuid for group in old_groups}
        requested: dict[str, str | None] = {}
        for node_uuid, target_group_uuid in memberships.items():
            node = self.get_node(node_uuid)
            if not node:
                continue
            if self.schema.nodes[node.type].category == "root":
                continue
            normalized_target = str(target_group_uuid).strip() if target_group_uuid else None
            if normalized_target and normalized_target not in existing_group_ids:
                continue
            requested[node.uuid] = normalized_target
        if not requested:
            return False

        changed = False
        new_groups: list[GroupRecord] = []
        for group in old_groups:
            updated = group.clone()
            before = list(updated.node_uuids)
            updated.node_uuids = [node_uuid for node_uuid in updated.node_uuids if node_uuid not in requested]
            for node_uuid, target_group_uuid in requested.items():
                if target_group_uuid == updated.uuid and node_uuid not in updated.node_uuids:
                    updated.node_uuids.append(node_uuid)
            changed = changed or before != updated.node_uuids
            new_groups.append(updated)
        if not changed:
            return False
        self.undo_stack.push(SetGroupsCommand(self, old_groups, new_groups, label=label))
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
        self.undo_stack.push(RemoveNodesCommand(self, nodes, connections, self.group_records()))

    def update_field(self, node_uuid: str, key: str, value: Any, source_mode: str | None = None) -> None:
        node = self.get_node(node_uuid)
        if not node:
            return
        definition = self.schema.nodes[node.type]
        if definition.category in {"root", "meta"}:
            return
        if definition.auto_rules.fixed_target_idle is not None and key in {"target_idle", "action_trigger_active"}:
            return
        if node.locked:
            self.statusMessage.emit("当前节点已锁定")
            return
        normalized_value = normalize_field_input(self.schema, node, key, value)
        old = node.fields.get(key)
        if old == normalized_value:
            return
        if self._materialize_virtual_placeholder_from_expected_fields(
            node_uuid,
            {key: normalized_value},
            source_mode or self.preferences.global_mode,
        ):
            return
        self.undo_stack.push(
            UpdateFieldCommand(self, node_uuid, key, old, normalized_value, source_mode or self.preferences.global_mode)
        )

    def update_fields(self, node_uuid: str, values: dict[str, Any], source_mode: str | None = None, label: str = "批量修改字段") -> None:
        node = self.get_node(node_uuid)
        if not node or not values:
            return
        definition = self.schema.nodes[node.type]
        if definition.category in {"root", "meta"}:
            return
        if definition.auto_rules.fixed_target_idle is not None:
            values = {key: value for key, value in values.items() if key not in {"target_idle", "action_trigger_active"}}
            if not values:
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
        updated_values = {key: value for key, _old_value, value in updates}
        if self._materialize_virtual_placeholder_from_expected_fields(
            node_uuid,
            updated_values,
            source_mode or self.preferences.global_mode,
        ):
            return
        self.undo_stack.push(UpdateFieldsCommand(self, node_uuid, updates, source_mode or self.preferences.global_mode, label))

    def update_fields_for_nodes(
        self,
        node_uuids: list[str],
        values: dict[str, Any],
        source_mode: str | None = None,
        label: str = "批量修改字段",
    ) -> None:
        if not node_uuids or not values:
            return
        source = source_mode or self.preferences.global_mode
        seen: set[str] = set()
        node_updates: dict[str, list[tuple[str, Any, Any]]] = {}
        for node_uuid in node_uuids:
            if node_uuid in seen:
                continue
            seen.add(node_uuid)
            node = self.get_node(node_uuid)
            if not node or node.locked:
                continue
            definition = self.schema.nodes[node.type]
            if definition.category in {"root", "meta"}:
                continue
            editable_values = values
            if definition.auto_rules.fixed_target_idle is not None:
                editable_values = {
                    key: value
                    for key, value in values.items()
                    if key not in {"target_idle", "action_trigger_active"}
                }
            updates: list[tuple[str, Any, Any]] = []
            for key, value in editable_values.items():
                normalized_value = normalize_field_input(self.schema, node, key, value)
                old_value = node.fields.get(key)
                if old_value != normalized_value:
                    updates.append((key, old_value, normalized_value))
            if updates:
                node_updates[node_uuid] = updates
        if node_updates:
            self.undo_stack.push(UpdateManyFieldsCommand(self, node_updates, source, label))

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

    def move_nodes_with_group_memberships(
        self,
        positions: dict[str, tuple[float, float]],
        memberships: dict[str, str | None],
        label: str = "移动节点并更新分组",
    ) -> None:
        self.undo_stack.beginMacro(label)
        try:
            self.move_nodes(positions, label=label)
            self.set_node_group_memberships(memberships, label="更新分组成员")
        finally:
            self.undo_stack.endMacro()

    def move_group_with_nodes(
        self,
        group_uuid: str,
        positions: dict[str, tuple[float, float]],
        bounds: tuple[float, float, float, float],
    ) -> None:
        self.undo_stack.beginMacro("移动分组")
        try:
            self.move_nodes(positions, label="移动分组成员")
            self.set_group_geometry(group_uuid, bounds, label="移动分组范围")
        finally:
            self.undo_stack.endMacro()

    def add_connection(self, from_uuid: str, to_uuid: str) -> None:
        allowed, reason = self.can_create_graph_content()
        if not allowed:
            self._emit_meta_blocked(reason)
            return
        if from_uuid == to_uuid:
            return
        from_node = self.get_node(from_uuid)
        to_node = self.get_node(to_uuid)
        if not from_node or not to_node or self.schema.nodes[to_node.type].category == "root":
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
        topics = plan_topic_map(self.document)
        plan_topics = [
            {
                "node_uuid": node.uuid,
                "parent_uuid": topics[node.uuid].parent_uuid,
                "order": int(topics[node.uuid].order),
                "plan_title": topics[node.uuid].plan_title,
                "collapsed": bool(topics[node.uuid].collapsed),
                "branch_color": topics[node.uuid].branch_color,
                "formalization_state": topics[node.uuid].formalization_state,
                "structure_dirty": bool(topics[node.uuid].structure_dirty),
            }
            for node in selected_nodes
            if node.uuid in topics
        ]
        payload = {
            "clipboard_version": 3,
            "nodes": [self._serialize_node(node) for node in selected_nodes],
            "connections": connections,
            "plan_topics": plan_topics,
            "source_bounds": {"min_x": min_x, "min_y": min_y, "max_x": max_x, "max_y": max_y},
        }
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    @staticmethod
    def _decode_clipboard_document(payload: bytes) -> dict[str, Any]:
        try:
            raw = json.loads(payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("节点剪贴板数据不是有效 JSON") from exc
        if not isinstance(raw, dict):
            raise ValueError("节点剪贴板数据必须是对象")
        raw_nodes = raw.get("nodes", [])
        raw_connections = raw.get("connections", [])
        raw_plan_topics = raw.get("plan_topics", [])
        raw_bounds = raw.get("source_bounds", {})
        if (
            not isinstance(raw_nodes, list)
            or not isinstance(raw_connections, list)
            or not isinstance(raw_plan_topics, list)
            or not isinstance(raw_bounds, dict)
        ):
            raise ValueError("节点剪贴板数据结构无效")

        nodes: list[dict[str, Any]] = []
        for item in raw_nodes:
            if not isinstance(item, dict):
                raise ValueError("节点剪贴板包含无效节点")
            node_uuid = item.get("uuid")
            node_type = item.get("type")
            position = item.get("ui_position", {"x": 0.0, "y": 0.0})
            if not isinstance(node_uuid, str) or not node_uuid or not isinstance(node_type, str) or not node_type:
                raise ValueError("节点剪贴板缺少节点标识或类型")
            if not isinstance(position, dict):
                raise ValueError("节点剪贴板位置无效")
            try:
                position_x = float(position.get("x", 0.0))
                position_y = float(position.get("y", 0.0))
            except (OverflowError, TypeError, ValueError) as exc:
                raise ValueError("节点剪贴板位置无效") from exc
            if not math.isfinite(position_x) or not math.isfinite(position_y):
                raise ValueError("节点剪贴板位置无效")
            ui_size = item.get("ui_size")
            if ui_size is not None and not isinstance(ui_size, dict):
                raise ValueError("节点剪贴板尺寸无效")
            manual_fields = item.get("manual_fields", [])
            if not isinstance(manual_fields, list) or not all(isinstance(key, str) for key in manual_fields):
                raise ValueError("节点剪贴板手工字段列表无效")
            normalized = dict(item)
            normalized["ui_position"] = {"x": position_x, "y": position_y}
            if ui_size is not None:
                try:
                    width = float(ui_size.get("width", 0.0))
                    height = float(ui_size.get("height", 0.0))
                except (OverflowError, TypeError, ValueError) as exc:
                    raise ValueError("节点剪贴板尺寸无效") from exc
                if not math.isfinite(width) or not math.isfinite(height) or width <= 0.0 or height <= 0.0:
                    raise ValueError("节点剪贴板尺寸无效")
                normalized["ui_size"] = {"width": width, "height": height}
            nodes.append(normalized)

        connections: list[dict[str, str]] = []
        for item in raw_connections:
            if not isinstance(item, dict):
                raise ValueError("节点剪贴板包含无效连线")
            from_uuid = item.get("from_uuid")
            to_uuid = item.get("to_uuid")
            if not isinstance(from_uuid, str) or not from_uuid or not isinstance(to_uuid, str) or not to_uuid:
                raise ValueError("节点剪贴板连线端点无效")
            connections.append({"from_uuid": from_uuid, "to_uuid": to_uuid})

        plan_topics: list[dict[str, Any]] = []
        seen_plan_topics: set[str] = set()
        for item in raw_plan_topics:
            if not isinstance(item, dict):
                raise ValueError("节点剪贴板包含无效计划主题")
            node_uuid = item.get("node_uuid")
            parent_uuid = item.get("parent_uuid")
            order = item.get("order", 0)
            title = item.get("plan_title", "")
            collapsed = item.get("collapsed", False)
            branch_color = item.get("branch_color", "")
            formalization_state = item.get("formalization_state", "formal")
            structure_dirty = item.get(
                "structure_dirty",
                formalization_state == "draft",
            )
            if (
                not isinstance(node_uuid, str)
                or not node_uuid
                or node_uuid in seen_plan_topics
            ):
                raise ValueError("节点剪贴板计划主题标识无效")
            if parent_uuid is not None and (
                not isinstance(parent_uuid, str) or not parent_uuid
            ):
                raise ValueError("节点剪贴板计划父主题无效")
            if isinstance(order, bool) or not isinstance(order, int) or order < 0:
                raise ValueError("节点剪贴板计划顺序无效")
            if not isinstance(title, str) or len(title) > PLAN_TITLE_MAX_LENGTH:
                raise ValueError("节点剪贴板计划标题无效")
            if not isinstance(collapsed, bool):
                raise ValueError("节点剪贴板计划折叠状态无效")
            if formalization_state not in {
                "formal",
                "draft",
                "virtual",
                "materialized",
            }:
                raise ValueError("节点剪贴板计划正式化状态无效")
            if not isinstance(structure_dirty, bool):
                raise ValueError("Plan topic structure dirty flag is invalid")
            if (
                not isinstance(branch_color, str)
                or (
                    branch_color
                    and (
                        len(branch_color) != 7
                        or not branch_color.startswith("#")
                        or any(
                            character not in "0123456789abcdefABCDEF"
                            for character in branch_color[1:]
                        )
                    )
                )
            ):
                raise ValueError("节点剪贴板计划分支颜色无效")
            seen_plan_topics.add(node_uuid)
            plan_topics.append(
                {
                    "node_uuid": node_uuid,
                    "parent_uuid": parent_uuid,
                    "order": order,
                    "plan_title": title,
                    "collapsed": collapsed,
                    "branch_color": branch_color.upper(),
                    "formalization_state": formalization_state,
                    "structure_dirty": structure_dirty,
                }
            )

        bounds: dict[str, float] = {}
        for key in ("min_x", "min_y", "max_x", "max_y"):
            try:
                value = float(raw_bounds.get(key, 0.0))
            except (OverflowError, TypeError, ValueError) as exc:
                raise ValueError("节点剪贴板范围无效") from exc
            if not math.isfinite(value):
                raise ValueError("节点剪贴板范围无效")
            bounds[key] = value
        return {
            **raw,
            "nodes": nodes,
            "connections": connections,
            "plan_topics": plan_topics,
            "source_bounds": bounds,
        }

    def clipboard_bounds(self, payload: bytes) -> tuple[float, float, float, float]:
        raw = self._decode_clipboard_document(payload)
        bounds = raw.get("source_bounds") or {}
        return (
            float(bounds.get("min_x", 0.0)),
            float(bounds.get("min_y", 0.0)),
            float(bounds.get("max_x", 0.0)),
            float(bounds.get("max_y", 0.0)),
        )

    def _deserialize_clipboard_content(
        self,
        payload: bytes,
        position: tuple[float, float] | None = None,
    ) -> tuple[
        list[NodeRecord],
        list[ConnectionRecord],
        list[PlanTopicRecord],
    ]:
        raw = self._decode_clipboard_document(payload)
        nodes: list[NodeRecord] = []
        staging_document = copy.copy(self.document)
        staging_document.nodes = list(self.document.nodes)
        uuid_map: dict[str, str] = {}
        table_id_map: dict[str, str] = {}
        source_positions = [item.get("ui_position", {"x": 0.0, "y": 0.0}) for item in raw.get("nodes", [])]
        min_x = min((item.get("x", 0.0) for item in source_positions), default=0.0)
        min_y = min((item.get("y", 0.0) for item in source_positions), default=0.0)
        if position is None:
            base_position = (min_x, min_y)
        else:
            try:
                base_position = (float(position[0]), float(position[1]))
            except (IndexError, OverflowError, TypeError, ValueError) as exc:
                raise ValueError("节点粘贴位置无效") from exc
            if not math.isfinite(base_position[0]) or not math.isfinite(base_position[1]):
                raise ValueError("节点粘贴位置无效")
        for item in raw.get("nodes", []):
            definition = self.schema.nodes.get(str(item.get("type") or ""))
            if definition is None or definition.category in {"root", "meta"}:
                continue
            old_uuid = item["uuid"]
            template = NodeRecord(
                uuid=new_uuid(),
                type=item["type"],
                fields={
                    key: value
                    for key, value in item.items()
                    if key
                    not in {
                        "uuid",
                        "type",
                        "ui_position",
                        "ui_size",
                        "locked",
                        "numeric_linkage_enabled",
                        "manual_fields",
                        "type_slot",
                        "export_slot",
                        "copy_source_type_slot",
                        "copy_source_sequence_no",
                    }
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
            new_node = create_node(
                self.schema,
                staging_document,
                template.type,
                (
                    base_position[0] + (item.get("ui_position", {}).get("x", 0.0) - min_x),
                    base_position[1] + (item.get("ui_position", {}).get("y", 0.0) - min_y),
                ),
            )
            new_node.ui_size = dict(template.ui_size) if template.ui_size else None
            new_node.locked = template.locked
            new_node.numeric_linkage_enabled = template.numeric_linkage_enabled
            generated_keys = {
                "id",
                "draw_able_name",
                "parameter",
                "action_trigger",
                "action_trigger_active",
                "target_idle",
                "action_trigger_kind_ui",
                "action_trigger_reserved_ui",
                "action_trigger_active_kind_ui",
                "action_trigger_active_reserved_ui",
            }
            for key, value in template.fields.items():
                if key not in generated_keys:
                    new_node.fields[key] = value
            if new_node.type in function_node_types(self.schema):
                apply_clone_sequence_fields(
                    self.schema,
                    staging_document,
                    new_node,
                    template,
                    source_type_slot=item.get("copy_source_type_slot"),
                )
            else:
                new_node.fields.update(template.fields)
                new_node.manual_fields = set(template.manual_fields)
            staging_document.nodes.append(new_node)
            nodes.append(new_node)
            uuid_map[old_uuid] = new_node.uuid
        connections = [
            ConnectionRecord(from_uuid=uuid_map[item["from_uuid"]], to_uuid=uuid_map[item["to_uuid"]])
            for item in raw.get("connections", [])
            if item["from_uuid"] in uuid_map and item["to_uuid"] in uuid_map
        ]
        plan_topics: list[PlanTopicRecord] = []
        connection_pairs = {
            (connection.from_uuid, connection.to_uuid)
            for connection in connections
        }
        for item in raw.get("plan_topics", []):
            old_uuid = item["node_uuid"]
            new_uuid_value = uuid_map.get(old_uuid)
            if new_uuid_value is None:
                continue
            source_parent_uuid = item.get("parent_uuid")
            parent_uuid = uuid_map.get(source_parent_uuid or "")
            if (
                parent_uuid is None
                and source_parent_uuid is not None
                and self.get_node(source_parent_uuid) is not None
            ):
                # Same-document paste keeps an external plan parent.  A
                # cross-document paste safely falls back to "unconnected".
                parent_uuid = source_parent_uuid
            plan_topics.append(
                PlanTopicRecord(
                    node_uuid=new_uuid_value,
                    parent_uuid=parent_uuid,
                    order=int(item.get("order", 0)),
                    plan_title=str(item.get("plan_title", "")),
                    collapsed=bool(item.get("collapsed", False)),
                    branch_color=str(item.get("branch_color", "")),
                    formalization_state=(
                        "draft"
                        if item.get("formalization_state") in {"draft", "virtual"}
                        else str(item.get("formalization_state", "formal"))
                    ),
                    structure_dirty=bool(item.get("structure_dirty", False)),
                )
            )
            pair = (parent_uuid, new_uuid_value)
            if parent_uuid is not None and pair not in connection_pairs:
                connections.append(
                    ConnectionRecord(
                        from_uuid=parent_uuid,
                        to_uuid=new_uuid_value,
                    )
                )
                connection_pairs.add(pair)
        return nodes, connections, plan_topics

    def deserialize_clipboard(
        self,
        payload: bytes,
        position: tuple[float, float] | None = None,
    ) -> tuple[list[NodeRecord], list[ConnectionRecord]]:
        nodes, connections, _plan_topics = self._deserialize_clipboard_content(
            payload,
            position,
        )
        return nodes, connections

    def paste_payload(
        self,
        payload: bytes,
        position: tuple[float, float] | None = None,
        *,
        connect_from: str | None = None,
        override_plan_parent: bool = False,
        plan_parent_uuid: str | None = None,
        plan_after_uuid: str | None = None,
    ) -> list[str]:
        nodes, connections, plan_topics = self._deserialize_clipboard_content(
            payload,
            position,
        )
        if not nodes:
            return []
        if connect_from and len(nodes) == 1:
            connections = list(connections)
            if not any(
                connection.from_uuid == connect_from
                and connection.to_uuid == nodes[0].uuid
                for connection in connections
            ):
                connections.append(
                    ConnectionRecord(
                        from_uuid=connect_from,
                        to_uuid=nodes[0].uuid,
                    )
                )
        if len(nodes) == 1 and (override_plan_parent or connect_from):
            destination_parent = (
                plan_parent_uuid
                if override_plan_parent
                else connect_from
            )
            record = next(
                (
                    topic
                    for topic in plan_topics
                    if topic.node_uuid == nodes[0].uuid
                ),
                None,
            )
            previous_parent = record.parent_uuid if record is not None else None
            if record is None:
                record = PlanTopicRecord(node_uuid=nodes[0].uuid)
                plan_topics.append(record)
            record.parent_uuid = destination_parent
            destination_siblings = plan_children_map(self.document).get(
                destination_parent,
                (),
            )
            if plan_after_uuid in destination_siblings:
                after_topic = self.plan_topic(plan_after_uuid or "")
                record.order = int(after_topic.order if after_topic else 0)
            else:
                record.order = len(destination_siblings)
            if previous_parent != destination_parent:
                connections = [
                    connection
                    for connection in connections
                    if not (
                        connection.from_uuid == previous_parent
                        and connection.to_uuid == nodes[0].uuid
                    )
                ]
            if destination_parent is not None and not any(
                connection.from_uuid == destination_parent
                and connection.to_uuid == nodes[0].uuid
                for connection in connections
            ):
                connections.append(
                    ConnectionRecord(
                        from_uuid=destination_parent,
                        to_uuid=nodes[0].uuid,
                    )
                )

        old_layout = self.ensure_plan_layout().clone()
        staged_layout = old_layout.clone()
        staged_layout.topics.extend(topic.clone() for topic in plan_topics)
        new_layout = self._normalized_plan_layout_for(
            nodes=[*self.document.nodes, *nodes],
            connections=[
                *clone_connections(self.document.connections),
                *clone_connections(connections),
            ],
            layout=staged_layout,
        )
        self.undo_stack.beginMacro("粘贴节点")
        try:
            self.undo_stack.push(AddNodesCommand(self, nodes, connections))
            self.undo_stack.push(
                SetPlanLayoutCommand(
                    self,
                    old_layout,
                    new_layout,
                    label="恢复计划主题元数据",
                )
            )
        finally:
            self.undo_stack.endMacro()
        self.set_selected_node(nodes[0].uuid)
        return [node.uuid for node in nodes]

    def export_current_document(self) -> dict[str, Any]:
        return export_document_dict(self.schema, self.document)

    def _serialize_node(self, node: NodeRecord) -> dict[str, Any]:
        payload = {
            "uuid": node.uuid,
            "type": node.type,
            "ui_position": dict(node.ui_position),
            "locked": node.locked,
            "copy_source_type_slot": node.type_slot,
            "copy_source_sequence_no": node.sequence_no,
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

    def _delete_nodes(self, nodes: list[NodeRecord], connections: list[ConnectionRecord]) -> None:
        node_uuids = [node.uuid for node in nodes]
        pairs = [(connection.from_uuid, connection.to_uuid) for connection in connections]
        self.document.nodes = [node for node in self.document.nodes if node.uuid not in node_uuids]
        self.document.connections = [
            connection
            for connection in self.document.connections
            if (connection.from_uuid, connection.to_uuid) not in pairs
        ]
        if self.selected_node_uuid in node_uuids:
            self.set_selected_node(None)
        for node_uuid in node_uuids:
            self.nodeRemoved.emit(node_uuid)
        self.connectionsChanged.emit()
        self.refresh_derived()

    def _restore_deleted_nodes(self, nodes: list[NodeRecord], connections: list[ConnectionRecord]) -> None:
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

    def _sync_plan_title_from_formal_node(self, node: NodeRecord) -> bool:
        """Mirror the visible formal-card note into its plan topic."""

        if node.type not in function_node_types(self.schema):
            return False
        title = str(node.fields.get("tips", "") or "").strip()[:PLAN_TITLE_MAX_LENGTH]
        for topic in self.document.plan_layout.topics:
            if topic.node_uuid != node.uuid:
                continue
            if topic.plan_title == title:
                return False
            topic.plan_title = title
            return True
        return False

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
        definition = self.schema.nodes[node.type]
        if definition.category in {"root", "meta"}:
            return
        if definition.auto_rules.fixed_target_idle is not None:
            values = {key: value for key, value in values.items() if key not in {"target_idle", "action_trigger_active"}}
            if not values:
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
        if node.type in function_node_types(self.schema):
            for manual_key in changed_keys & {"draw_able_name", "parameter", "action_trigger"}:
                node.manual_fields.add(manual_key)
        if node.type in {"TouchDrag", "ParameterTrigger"} and changed_keys & {"action_trigger", "parameter"}:
            node.fields["action_trigger_active"] = ""
        if effective_changed_key in {"action_trigger_active", "action_trigger"} or (
            effective_changed_key == "parameter"
            and (source_mode == "advanced" or node.type == "ParameterTrigger")
        ):
            infer_manual_fields(self.schema, node, self.document)
        apply_auto_rules(self.schema, self.document, node, source_mode=source_mode, changed_key=effective_changed_key)
        plan_title_changed = (
            "tips" in changed_keys
            and self._sync_plan_title_from_formal_node(node)
        )
        reassign_function_ids(self.schema, self.document)
        self.nodeUpdated.emit(node_uuid)
        if plan_title_changed:
            self.planLayoutChanged.emit()
        self.refresh_derived()

    def _set_many_fields(self, node_values: dict[str, dict[str, Any]], source_mode: str) -> None:
        changed_node_uuids: list[str] = []
        plan_title_changed = False
        for node_uuid, values in node_values.items():
            node = self.get_node(node_uuid)
            if not node or not values:
                continue
            definition = self.schema.nodes[node.type]
            if definition.category in {"root", "meta"}:
                continue
            if definition.auto_rules.fixed_target_idle is not None:
                values = {
                    key: value
                    for key, value in values.items()
                    if key not in {"target_idle", "action_trigger_active"}
                }
                if not values:
                    continue
            for key, value in values.items():
                node.fields[key] = value
            apply_node_appearance_defaults(self.schema, node)
            changed_keys = set(values)
            if node.type == "Comment" and changed_keys & {"theme_body_color", "theme_text_color"}:
                sync_comment_legacy_appearance(node)
            if node.type == "Comment" and changed_keys & {"note_box_color", "note_text_color"}:
                sync_comment_theme_appearance(node)
            effective_changed_key = next(iter(changed_keys)) if len(changed_keys) == 1 else None
            if node.type in function_node_types(self.schema):
                for manual_key in changed_keys & {"draw_able_name", "parameter", "action_trigger"}:
                    node.manual_fields.add(manual_key)
            if node.type in {"TouchDrag", "ParameterTrigger"} and changed_keys & {"action_trigger", "parameter"}:
                node.fields["action_trigger_active"] = ""
            if effective_changed_key in {"action_trigger_active", "action_trigger"} or (
                effective_changed_key == "parameter"
                and (source_mode == "advanced" or node.type == "ParameterTrigger")
            ):
                infer_manual_fields(self.schema, node, self.document)
            apply_auto_rules(self.schema, self.document, node, source_mode=source_mode, changed_key=effective_changed_key)
            if "tips" in changed_keys:
                plan_title_changed = (
                    self._sync_plan_title_from_formal_node(node)
                    or plan_title_changed
                )
            changed_node_uuids.append(node_uuid)
        if not changed_node_uuids:
            return
        reassign_function_ids(self.schema, self.document)
        for node_uuid in changed_node_uuids:
            self.nodeUpdated.emit(node_uuid)
        if plan_title_changed:
            self.planLayoutChanged.emit()
        self.refresh_derived()

    def _set_node_locked(self, node_uuid: str, locked: bool) -> None:
        node = self.get_node(node_uuid)
        if not node:
            return
        node.locked = bool(locked)
        self.nodeUpdated.emit(node_uuid)
        self.refresh_derived()

    def _set_node_sequence_locks(self, values: dict[str, bool]) -> None:
        changed = False
        for node_uuid, locked in values.items():
            node = self.get_node(node_uuid)
            if node is None or node.sequence_locked == bool(locked):
                continue
            node.sequence_locked = bool(locked)
            self.nodeUpdated.emit(node_uuid)
            changed = True
        if changed:
            self.refresh_derived()

    def _set_editor_settings(self, settings: dict[str, Any]) -> None:
        self.document.editor_settings.numeric_linkage_enabled = bool(settings.get("numeric_linkage_enabled", False))
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

    def _set_plan_layout(self, layout: PlanLayout) -> None:
        current_view = self.document.plan_layout.view
        replacement = layout.clone()
        replacement.view.scale = float(current_view.scale)
        replacement.view.offset_x = float(current_view.offset_x)
        replacement.view.offset_y = float(current_view.offset_y)
        self.document.plan_layout = replacement
        self.planLayoutChanged.emit()

    def _set_plan_view_state(
        self,
        scale: float,
        offset_x: float,
        offset_y: float,
    ) -> None:
        state = self.document.plan_layout.view
        state.scale = max(0.03, min(8.0, float(scale)))
        state.offset_x = float(offset_x)
        state.offset_y = float(offset_y)
        self.planViewChanged.emit(
            (state.scale, state.offset_x, state.offset_y)
        )

    def _set_plan_graph_state(
        self,
        layout: PlanLayout,
        connections: list[ConnectionRecord],
    ) -> None:
        current_view = self.document.plan_layout.view
        self.document.connections = clone_connections(connections)
        replacement = layout.clone()
        replacement.view.scale = float(current_view.scale)
        replacement.view.offset_x = float(current_view.offset_x)
        replacement.view.offset_y = float(current_view.offset_y)
        self.document.plan_layout = replacement
        normalize_plan_layout(self.document)
        self.connectionsChanged.emit()
        self.planLayoutChanged.emit()
        self.refresh_derived()

    def _replace_plan_nodes(
        self,
        replacements: list[NodeRecord],
        layout: PlanLayout,
    ) -> None:
        replacement_by_uuid = {
            node.uuid: node.clone() for node in replacements
        }
        replaced_uuids = set(replacement_by_uuid)
        self.document.nodes = [
            replacement_by_uuid.get(node.uuid, node)
            for node in self.document.nodes
        ]
        current_view = self.document.plan_layout.view
        self.document.plan_layout = layout.clone()
        self.document.plan_layout.view = copy.copy(current_view)
        reassign_function_ids(self.schema, self.document)
        for node_uuid in replaced_uuids:
            self.nodeRemoved.emit(node_uuid)
            self.nodeAdded.emit(node_uuid)
        self.connectionsChanged.emit()
        self.planLayoutChanged.emit()
        self.refresh_derived()

    def _insert_canvas_images(self, images: list[CanvasImageRecord]) -> None:
        existing = {image.uuid for image in self.document.canvas_images}
        self.document.canvas_images.extend(image for image in images if image.uuid not in existing)
        self.canvasImagesChanged.emit()

    def _remove_canvas_images(self, image_uuids: list[str]) -> None:
        selected = set(image_uuids)
        self.document.canvas_images = [image for image in self.document.canvas_images if image.uuid not in selected]
        self.canvasImagesChanged.emit()

    def _move_canvas_images(self, positions: dict[str, tuple[float, float]]) -> None:
        for image_uuid, position in positions.items():
            image = self.get_canvas_image(image_uuid)
            if image is not None:
                image.ui_position = {"x": float(position[0]), "y": float(position[1])}
        self.canvasImagesChanged.emit()

    def _resize_canvas_images(self, sizes: dict[str, tuple[float, float]]) -> None:
        for image_uuid, size in sizes.items():
            image = self.get_canvas_image(image_uuid)
            validated = validated_reference_image_display_size(size)
            if image is not None and validated is not None:
                image.ui_size = {"width": validated[0], "height": validated[1]}
        self.canvasImagesChanged.emit()

    def _emit_canvas_strokes_changed(self, layer: str) -> None:
        if layer == "plan":
            self.planCanvasStrokesChanged.emit()
        else:
            self.canvasStrokesChanged.emit()

    def _insert_canvas_strokes(
        self,
        strokes: list[CanvasStrokeRecord],
        layer: str = "formal",
    ) -> None:
        records = self._canvas_stroke_records(layer)
        existing = {stroke.uuid for stroke in records}
        records.extend(
            stroke for stroke in strokes if stroke.uuid not in existing
        )
        self._emit_canvas_strokes_changed(layer)

    def _remove_canvas_strokes(
        self,
        stroke_uuids: list[str],
        layer: str = "formal",
    ) -> None:
        selected = set(stroke_uuids)
        records = [
            stroke
            for stroke in self._canvas_stroke_records(layer)
            if stroke.uuid not in selected
        ]
        if layer == "plan":
            self.document.plan_canvas_strokes = records
        else:
            self.document.canvas_strokes = records
        self._emit_canvas_strokes_changed(layer)

    def _restore_canvas_strokes(
        self,
        indexed_strokes: list[tuple[int, CanvasStrokeRecord]],
        layer: str = "formal",
    ) -> None:
        records = self._canvas_stroke_records(layer)
        existing = {stroke.uuid for stroke in records}
        for index, stroke in sorted(indexed_strokes, key=lambda item: item[0]):
            if stroke.uuid in existing:
                continue
            resolved_index = min(max(0, int(index)), len(records))
            records.insert(resolved_index, stroke)
            existing.add(stroke.uuid)
        self._emit_canvas_strokes_changed(layer)

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
        target_topic = next(
            (
                topic
                for topic in self.document.plan_layout.topics
                if topic.node_uuid == connection.to_uuid
            ),
            None,
        )
        if (
            target_topic is not None
            and target_topic.parent_uuid is None
            and target_topic.node_uuid != plan_root_uuid(self.document)
        ):
            siblings = [
                topic
                for topic in self.document.plan_layout.topics
                if topic.parent_uuid == connection.from_uuid
                and topic.node_uuid != connection.to_uuid
            ]
            target_topic.parent_uuid = connection.from_uuid
            target_topic.order = len(siblings)
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
