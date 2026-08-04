"""Pure plan-layout helpers.

The formal graph remains the content source of truth.  This module only
chooses a stable primary tree for presentation and stores plan-specific
metadata such as titles, sibling order and collapsed state.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Iterable

from .models import (
    CanvasViewState,
    ConnectionRecord,
    DocumentModel,
    NodeRecord,
    PlanLayout,
    PlanTopicRecord,
)
from .schema import EditorSchema


PLAN_UNCONNECTED_UUID = "__plan_unconnected__"
PLAN_UNCONNECTED_TITLE = "未连接"
PLAN_BRANCH_COLORS = (
    "#F57C00",
    "#43A047",
    "#E53935",
    "#7E57C2",
    "#1E88E5",
    "#00897B",
    "#D81B60",
    "#6D4C41",
)
PLAN_ROOT_COLOR = "#2F80ED"
PLAN_TOUCHIDLE_COLOR = "#39A96B"
PLAN_TOUCHDRAG_COLOR = "#8B5CF6"
PLAN_TITLE_MAX_LENGTH = 4096
PLAN_FORMALIZATION_STATES = frozenset(
    {"formal", "draft", "virtual", "materialized"}
)
_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")
_TOUCHIDLE_PLAN_TITLE_PATTERN = re.compile(
    r"^\s*(?P<draw>touchidle(?P<draw_index>[0-9]+))"
    r"(?P<separator>\s*-\s*)"
    r"(?P<action>touch_idle(?P<action_index>[0-9]+))"
    r"(?:(?P<note_separator>\s*-\s*|\s+)"
    r"(?P<note>\S(?:.*\S)?))?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TouchIdlePlanTitle:
    """A parsed plan title while preserving the user's visible spelling."""

    draw_text: str
    separator_text: str
    action_text: str
    draw_index: int
    action_index: int
    note_text: str = ""
    note_separator_text: str = ""


def parse_touchidle_plan_title(title: str) -> TouchIdlePlanTitle | None:
    match = _TOUCHIDLE_PLAN_TITLE_PATTERN.fullmatch(str(title or ""))
    if match is None:
        return None
    return TouchIdlePlanTitle(
        draw_text=match.group("draw"),
        separator_text=match.group("separator"),
        action_text=match.group("action"),
        draw_index=int(match.group("draw_index")),
        action_index=int(match.group("action_index")),
        note_text=str(match.group("note") or "").strip(),
        note_separator_text=str(match.group("note_separator") or ""),
    )


def placeholder_fields_for_title(title: str) -> dict[str, str]:
    """Return editor-only hints for an unmaterialized plan topic."""

    resolved = str(title or "").strip()
    parsed = parse_touchidle_plan_title(resolved)
    if parsed is not None:
        return {
            "plan_source_title": resolved,
            "planned_draw_name": parsed.draw_text,
            "planned_action_name": parsed.action_text,
        }
    left, separator, right = resolved.partition("-")
    return {
        "plan_source_title": resolved,
        "planned_draw_name": left.strip() if separator else "",
        "planned_action_name": right.strip() if separator else "",
    }


def _node_index(document: DocumentModel) -> dict[str, int]:
    return {node.uuid: index for index, node in enumerate(document.nodes)}


def plan_root_uuid(document: DocumentModel) -> str | None:
    root = next((node for node in document.nodes if node.type == "Idle0"), None)
    return root.uuid if root is not None else None


def _valid_color(value: Any) -> str:
    text = str(value or "").strip()
    return text.upper() if _COLOR_PATTERN.fullmatch(text) else ""


class _DisjointSet:
    """Small union/find helper used while selecting a stable primary forest."""

    def __init__(self, node_ids: Iterable[str]) -> None:
        self._parent = {node_uuid: node_uuid for node_uuid in node_ids}
        self._rank = {node_uuid: 0 for node_uuid in node_ids}

    def find(self, node_uuid: str) -> str:
        parent = self._parent[node_uuid]
        while parent != self._parent[parent]:
            parent = self._parent[parent]
        root = parent
        current = node_uuid
        while self._parent[current] != current:
            next_uuid = self._parent[current]
            self._parent[current] = root
            current = next_uuid
        return root

    def union(self, first_uuid: str, second_uuid: str) -> bool:
        first_root = self.find(first_uuid)
        second_root = self.find(second_uuid)
        if first_root == second_root:
            return False
        first_rank = self._rank[first_root]
        second_rank = self._rank[second_root]
        if first_rank < second_rank:
            first_root, second_root = second_root, first_root
        self._parent[second_root] = first_root
        if first_rank == second_rank:
            self._rank[first_root] += 1
        return True


def _valid_graph_pairs(document: DocumentModel) -> list[tuple[str, str]]:
    node_ids = {node.uuid for node in document.nodes}
    root_uuid = plan_root_uuid(document)
    result: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for connection in document.connections:
        pair = (connection.from_uuid, connection.to_uuid)
        if (
            pair in seen
            or pair[0] not in node_ids
            or pair[1] not in node_ids
            or pair[0] == pair[1]
            or pair[1] == root_uuid
        ):
            continue
        seen.add(pair)
        result.append(pair)
    return result


def normalize_plan_layout(document: DocumentModel) -> PlanLayout:
    """Return and install a deterministic, complete primary-tree layout.

    Existing valid primary-parent choices win.  New/unmapped nodes are then
    reached from the root in formal node/edge order.  Remaining components
    become roots of the virtual ``未连接`` branch.  Formal reference edges are
    never removed by normalization.
    """

    nodes = [node for node in document.nodes if node.type != "DrawFrame"]
    node_ids = {node.uuid for node in nodes}
    node_index = _node_index(document)
    root_uuid = plan_root_uuid(document)
    old_layout = document.plan_layout if isinstance(document.plan_layout, PlanLayout) else PlanLayout()
    existing: dict[str, PlanTopicRecord] = {}
    for topic in old_layout.topics:
        if topic.node_uuid in node_ids and topic.node_uuid not in existing:
            existing[topic.node_uuid] = topic

    graph_pairs = _valid_graph_pairs(document)
    graph_pair_set = set(graph_pairs)
    adjacency: dict[str, list[str]] = defaultdict(list)
    incoming: dict[str, list[str]] = defaultdict(list)
    for from_uuid, to_uuid in graph_pairs:
        adjacency[from_uuid].append(to_uuid)
        incoming[to_uuid].append(from_uuid)
    for parent_uuid in adjacency:
        adjacency[parent_uuid].sort(key=lambda node_uuid: (node_index.get(node_uuid, 10**9), node_uuid))
    for node_uuid in incoming:
        incoming[node_uuid].sort(key=lambda parent_uuid: (node_index.get(parent_uuid, 10**9), parent_uuid))

    parents: dict[str, str | None] = {}
    forest = _DisjointSet(node_ids)
    if root_uuid is not None:
        parents[root_uuid] = None

    # A persisted ``None`` on a non-root topic is an explicit placement under
    # the virtual disconnected branch, not an uninitialized parent.
    for topic in existing.values():
        if topic.node_uuid != root_uuid and topic.parent_uuid is None:
            parents[topic.node_uuid] = None

    # Preserve stored primary choices where the corresponding formal edge
    # still exists and accepting it does not create a plan-tree cycle.
    stored_candidates = sorted(
        (
            topic
            for topic in existing.values()
            if topic.node_uuid != root_uuid
            and topic.parent_uuid is not None
            and (topic.parent_uuid, topic.node_uuid) in graph_pair_set
        ),
        key=lambda topic: (
            node_index.get(topic.parent_uuid or "", 10**9),
            int(topic.order),
            node_index.get(topic.node_uuid, 10**9),
            topic.node_uuid,
        ),
    )
    for topic in stored_candidates:
        if topic.node_uuid in parents:
            continue
        if forest.union(topic.node_uuid, topic.parent_uuid):
            parents[topic.node_uuid] = topic.parent_uuid

    # Expand every already anchored component in one pass.  This includes the
    # real root and disconnected components whose stored primary choices were
    # accepted above.  Union/find replaces repeated ancestor walks, keeping a
    # deep chain linear instead of quadratic.
    initial_anchors = sorted(
        parents,
        key=lambda node_uuid: (
            0 if node_uuid == root_uuid else 1,
            node_index.get(node_uuid, 10**9),
            node_uuid,
        ),
    )
    queue: deque[str] = deque(initial_anchors)
    traversed: set[str] = set()
    while queue:
        parent_uuid = queue.popleft()
        if parent_uuid in traversed:
            continue
        traversed.add(parent_uuid)
        for child_uuid in adjacency.get(parent_uuid, ()):
            if child_uuid not in parents:
                if forest.union(child_uuid, parent_uuid):
                    parents[child_uuid] = parent_uuid
            if parents.get(child_uuid) == parent_uuid:
                queue.append(child_uuid)

    # Forests, isolated nodes, and pure cycles live under the virtual
    # disconnected branch.  Prefer zero-in-degree DAG roots; for a pure cycle,
    # fall back to the first formal node.  The pre-sorted root candidates avoid
    # repeatedly scanning a large forest.
    remaining = {node.uuid for node in nodes if node.uuid not in parents}
    ordered_remaining = sorted(
        remaining,
        key=lambda node_uuid: (node_index.get(node_uuid, 10**9), node_uuid),
    )
    zero_indegree_roots = [
        node_uuid
        for node_uuid in ordered_remaining
        if not any(parent_uuid in remaining for parent_uuid in incoming.get(node_uuid, ()))
    ]
    root_candidates = [*zero_indegree_roots, *ordered_remaining]
    for component_root in root_candidates:
        if component_root not in remaining:
            continue
        parents[component_root] = None
        remaining.remove(component_root)
        queue = deque([component_root])
        while queue:
            parent_uuid = queue.popleft()
            for child_uuid in adjacency.get(parent_uuid, ()):
                if child_uuid not in remaining:
                    continue
                if not forest.union(child_uuid, parent_uuid):
                    continue
                parents[child_uuid] = parent_uuid
                remaining.remove(child_uuid)
                queue.append(child_uuid)

    children: dict[str | None, list[str]] = defaultdict(list)
    for node in nodes:
        if node.uuid == root_uuid:
            continue
        children[parents.get(node.uuid)].append(node.uuid)

    # Sibling order is stable across graph edits: persisted order first, then
    # formal node order for newly discovered topics.
    normalized_order: dict[str, int] = {}
    for parent_uuid, child_ids in children.items():
        child_ids.sort(
            key=lambda node_uuid: (
                0 if node_uuid in existing else 1,
                int(existing[node_uuid].order) if node_uuid in existing else node_index.get(node_uuid, 10**9),
                node_index.get(node_uuid, 10**9),
                node_uuid,
            )
        )
        for order, node_uuid in enumerate(child_ids):
            normalized_order[node_uuid] = order

    colors: dict[str, str] = {}
    if root_uuid is not None:
        colors[root_uuid] = PLAN_ROOT_COLOR
    top_level = list(children.get(root_uuid, ())) + list(children.get(None, ()))
    for index, node_uuid in enumerate(top_level):
        saved = _valid_color(existing.get(node_uuid).branch_color if node_uuid in existing else "")
        colors[node_uuid] = saved or PLAN_BRANCH_COLORS[index % len(PLAN_BRANCH_COLORS)]
        queue = deque([node_uuid])
        while queue:
            parent_uuid = queue.popleft()
            for child_uuid in children.get(parent_uuid, ()):
                saved = _valid_color(
                    existing.get(child_uuid).branch_color
                    if child_uuid in existing
                    else ""
                )
                colors[child_uuid] = saved or colors[parent_uuid]
                queue.append(child_uuid)

    topics: list[PlanTopicRecord] = []
    for node in nodes:
        old = existing.get(node.uuid)
        topics.append(
            PlanTopicRecord(
                node_uuid=node.uuid,
                parent_uuid=parents.get(node.uuid),
                order=normalized_order.get(node.uuid, 0),
                plan_title=(str(old.plan_title)[:PLAN_TITLE_MAX_LENGTH] if old else ""),
                collapsed=bool(old.collapsed) if old else False,
                branch_color=colors.get(node.uuid, PLAN_ROOT_COLOR),
                formalization_state=(
                    old.formalization_state
                    if old and old.formalization_state in PLAN_FORMALIZATION_STATES
                    else "formal"
                ),
            )
        )
    topics.sort(
        key=lambda topic: (
            0 if topic.node_uuid == root_uuid else 1,
            node_index.get(topic.node_uuid, 10**9),
            topic.node_uuid,
        )
    )
    view = old_layout.view if isinstance(old_layout.view, CanvasViewState) else CanvasViewState()
    normalized = PlanLayout(
        topics=topics,
        view=CanvasViewState(
            scale=max(0.03, min(8.0, float(view.scale))) if math.isfinite(float(view.scale)) else 1.0,
            offset_x=float(view.offset_x) if math.isfinite(float(view.offset_x)) else 0.0,
            offset_y=float(view.offset_y) if math.isfinite(float(view.offset_y)) else 0.0,
        ),
    )
    document.plan_layout = normalized
    return normalized


def plan_topic_map(document: DocumentModel) -> dict[str, PlanTopicRecord]:
    normalize_plan_layout(document)
    return {topic.node_uuid: topic for topic in document.plan_layout.topics}


def plan_children_map(document: DocumentModel) -> dict[str | None, list[str]]:
    topics = plan_topic_map(document)
    root_uuid = plan_root_uuid(document)
    result: dict[str | None, list[str]] = defaultdict(list)
    for topic in topics.values():
        if topic.node_uuid == root_uuid:
            continue
        result[topic.parent_uuid].append(topic.node_uuid)
    for children in result.values():
        children.sort(key=lambda node_uuid: (topics[node_uuid].order, node_uuid))
    return dict(result)


def plan_topic_type_from_color(topic: PlanTopicRecord) -> str | None:
    """Return the explicit formal type represented by a plan-card color."""

    color = _valid_color(topic.branch_color)
    if color == PLAN_TOUCHIDLE_COLOR:
        return "TouchIdle"
    if color == PLAN_TOUCHDRAG_COLOR:
        return "TouchDrag"
    return None


def plan_formal_positions(
    document: DocumentModel,
    *,
    horizontal_gap: float = 460.0,
    vertical_gap: float = 300.0,
) -> dict[str, tuple[float, float]]:
    """Lay out the formal graph in the same hierarchy as the plan tree."""

    layout = normalize_plan_layout(document)
    topics = {topic.node_uuid: topic for topic in layout.topics}
    nodes = {node.uuid: node for node in document.nodes}
    root_uuid = plan_root_uuid(document)
    if root_uuid is None or root_uuid not in nodes:
        return {}
    children: dict[str | None, list[str]] = defaultdict(list)
    for topic in layout.topics:
        if topic.node_uuid != root_uuid:
            children[topic.parent_uuid].append(topic.node_uuid)
    for node_uuids in children.values():
        node_uuids.sort(key=lambda node_uuid: (topics[node_uuid].order, node_uuid))

    raw_positions: dict[str, tuple[int, float]] = {}
    visited_nodes: set[str] = set()
    next_leaf_y = 0.0
    component_roots = [
        root_uuid,
        *(
            node_uuid
            for node_uuid in children.get(None, ())
            if node_uuid != root_uuid
        ),
    ]
    for component_root in component_roots:
        if component_root in visited_nodes or component_root not in nodes:
            continue
        component_preorder: list[str] = []
        depth_by_uuid: dict[str, int] = {}
        stack: list[tuple[str, int]] = [(component_root, 0)]
        while stack:
            node_uuid, depth = stack.pop()
            if node_uuid in visited_nodes or node_uuid not in nodes:
                continue
            visited_nodes.add(node_uuid)
            component_preorder.append(node_uuid)
            depth_by_uuid[node_uuid] = depth
            child_ids = [
                child_uuid
                for child_uuid in children.get(node_uuid, ())
                if child_uuid in nodes
            ]
            for child_uuid in reversed(child_ids):
                stack.append((child_uuid, depth + 1))

        raw_y: dict[str, float] = {}
        for node_uuid in component_preorder:
            if not children.get(node_uuid):
                raw_y[node_uuid] = next_leaf_y
                next_leaf_y += vertical_gap
        for node_uuid in reversed(component_preorder):
            if node_uuid in raw_y:
                continue
            child_ids = [
                child_uuid
                for child_uuid in children.get(node_uuid, ())
                if child_uuid in raw_y
            ]
            if child_ids:
                raw_y[node_uuid] = (raw_y[child_ids[0]] + raw_y[child_ids[-1]]) * 0.5
            else:
                raw_y[node_uuid] = next_leaf_y
                next_leaf_y += vertical_gap
        for node_uuid in component_preorder:
            raw_positions[node_uuid] = (depth_by_uuid[node_uuid], raw_y[node_uuid])
        next_leaf_y += vertical_gap
    root = nodes[root_uuid]
    root_x = float(root.ui_position.get("x", 0.0))
    root_y = float(root.ui_position.get("y", 0.0))
    raw_root_y = raw_positions[root_uuid][1]
    return {
        node_uuid: (
            root_x + depth * horizontal_gap,
            root_y + raw_y - raw_root_y,
        )
        for node_uuid, (depth, raw_y) in raw_positions.items()
    }


def plan_primary_edges(document: DocumentModel) -> set[tuple[str, str]]:
    return {
        (topic.parent_uuid, topic.node_uuid)
        for topic in normalize_plan_layout(document).topics
        if topic.parent_uuid is not None
    }


def plan_reference_edges(document: DocumentModel) -> list[ConnectionRecord]:
    primary = plan_primary_edges(document)
    return [
        ConnectionRecord(connection.from_uuid, connection.to_uuid)
        for connection in document.connections
        if (connection.from_uuid, connection.to_uuid) not in primary
    ]


def plan_subtree_uuids(document: DocumentModel, node_uuid: str) -> list[str]:
    children = plan_children_map(document)
    if node_uuid not in {node.uuid for node in document.nodes}:
        return []
    result: list[str] = []
    stack = [node_uuid]
    while stack:
        current = stack.pop()
        result.append(current)
        stack.extend(reversed(children.get(current, ())))
    return result


def plan_is_descendant(document: DocumentModel, node_uuid: str, possible_ancestor_uuid: str) -> bool:
    topics = plan_topic_map(document)
    current = topics.get(node_uuid)
    seen: set[str] = set()
    while current is not None and current.parent_uuid is not None:
        if current.parent_uuid == possible_ancestor_uuid:
            return True
        if current.parent_uuid in seen:
            return False
        seen.add(current.parent_uuid)
        current = topics.get(current.parent_uuid)
    return False


def plan_topic_title(
    schema: EditorSchema,
    document: DocumentModel,
    node: NodeRecord,
    topic: PlanTopicRecord | None = None,
) -> str:
    topic = topic or plan_topic_map(document).get(node.uuid)
    explicit = str(topic.plan_title if topic else "").strip()
    if explicit:
        return explicit
    if node.type == "Idle0":
        return str(document.meta.CharName or "").strip() or "角色"
    # Imported lazily to avoid the module-level logic <-> plan dependency.
    # Keeping a single formal-title implementation prevents the two views from
    # drifting when title rules change.
    from .logic import node_title

    return node_title(schema, node)


def serialize_plan_layout(document: DocumentModel) -> dict[str, Any]:
    layout = normalize_plan_layout(document)
    return {
        "topics": [
            {
                "node_uuid": topic.node_uuid,
                "parent_uuid": topic.parent_uuid,
                "order": int(topic.order),
                "plan_title": topic.plan_title,
                "collapsed": bool(topic.collapsed),
                "branch_color": topic.branch_color,
                "formalization_state": topic.formalization_state,
            }
            for topic in layout.topics
        ],
        "view": {
            "scale": float(layout.view.scale),
            "offset_x": float(layout.view.offset_x),
            "offset_y": float(layout.view.offset_y),
        },
    }


def load_plan_layout(
    payload: Any,
    *,
    required: bool = False,
    required_formalization_state: bool = False,
) -> PlanLayout:
    if payload is None and not required:
        return PlanLayout()
    if not isinstance(payload, dict):
        raise ValueError("plan_layout must be an object")
    raw_topics = payload.get("topics", [])
    if not isinstance(raw_topics, list):
        raise ValueError("plan_layout.topics must be a list")
    topics: list[PlanTopicRecord] = []
    seen: set[str] = set()
    for raw in raw_topics:
        if not isinstance(raw, dict):
            raise ValueError("Plan topic must be an object")
        node_uuid = raw.get("node_uuid")
        parent_uuid = raw.get("parent_uuid")
        order = raw.get("order", 0)
        plan_title = raw.get("plan_title", "")
        collapsed = raw.get("collapsed", False)
        branch_color = raw.get("branch_color", "")
        formalization_state = raw.get("formalization_state", "formal")
        if not isinstance(node_uuid, str) or not node_uuid or node_uuid in seen:
            raise ValueError("Plan topic node_uuid is invalid or duplicated")
        if parent_uuid is not None and (not isinstance(parent_uuid, str) or not parent_uuid):
            raise ValueError("Plan topic parent_uuid is invalid")
        if isinstance(order, bool) or not isinstance(order, int) or order < 0:
            raise ValueError("Plan topic order is invalid")
        if not isinstance(plan_title, str) or len(plan_title) > PLAN_TITLE_MAX_LENGTH:
            raise ValueError("Plan topic title is invalid")
        if not isinstance(collapsed, bool):
            raise ValueError("Plan topic collapsed flag is invalid")
        if branch_color and not _COLOR_PATTERN.fullmatch(str(branch_color)):
            raise ValueError("Plan topic branch color is invalid")
        if required_formalization_state and "formalization_state" not in raw:
            raise ValueError("Plan topic formalization state is missing")
        if formalization_state not in PLAN_FORMALIZATION_STATES:
            raise ValueError("Plan topic formalization state is invalid")
        seen.add(node_uuid)
        topics.append(
            PlanTopicRecord(
                node_uuid=node_uuid,
                parent_uuid=parent_uuid,
                order=order,
                plan_title=plan_title,
                collapsed=collapsed,
                branch_color=_valid_color(branch_color),
                formalization_state=formalization_state,
            )
        )
    raw_view = payload.get("view", {})
    if not isinstance(raw_view, dict):
        raise ValueError("plan_layout.view must be an object")

    def finite_view_number(key: str, default: float) -> float:
        raw = raw_view.get(key, default)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError(f"plan_layout.view.{key} is invalid")
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError(f"plan_layout.view.{key} is invalid")
        return value

    return PlanLayout(
        topics=topics,
        view=CanvasViewState(
            scale=max(0.03, min(8.0, finite_view_number("scale", 1.0))),
            offset_x=finite_view_number("offset_x", 0.0),
            offset_y=finite_view_number("offset_y", 0.0),
        ),
    )


def clone_connections(connections: Iterable[ConnectionRecord]) -> list[ConnectionRecord]:
    return [
        ConnectionRecord(from_uuid=connection.from_uuid, to_uuid=connection.to_uuid)
        for connection in connections
    ]
