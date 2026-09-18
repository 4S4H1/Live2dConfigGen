"""User-level, provider-neutral tool-call surface for the editor.

Only graph/document operations declared in :meth:`tool_definitions` are
reachable.  The service never exposes arbitrary object access, filesystem
paths, processes, shell commands, update functions, or LAN services.
"""

from __future__ import annotations
from . import features

import copy
import csv
import dataclasses
import json
import math
import re
import secrets
import threading
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtGui import QUndoCommand

from .csv_export import (
    current_document_identifier,
    export_current_document_csv,
)
from .logic import (
    EDITOR_DOCUMENT_FORMAT_VERSION,
    HIDDEN_NODE_FIELDS,
    MAX_CANVAS_COORDINATE,
    apply_auto_rules,
    apply_node_appearance_defaults,
    create_node,
    export_document_dict,
    function_node_types,
    new_uuid,
    node_title,
    normalize_field_input,
    normalized_document_groups,
    reassign_function_ids,
    search_document,
    sync_comment_legacy_appearance,
    sync_comment_theme_appearance,
    validate_canvas_strokes,
    validate_document,
)
from .models import ConnectionRecord, DocumentModel, GroupRecord, NodeRecord
from .plan import placeholder_fields_for_title


_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_QUERY_TOOLS = frozenset(
    {
        "get_editor_schema",
        "get_current_graph",
        "find_nodes",
        "validate_current_graph",
    }
)
_CONFIRMATION_TOOLS = frozenset(
    {
        "apply_graph_edits",
        "optimize_layout",
        "undo_last_edit",
        "redo_last_edit",
        "save_current_graph",
        "export_current_graph_csv",
    }
)
_SUPPORTED_VIEWS = frozenset({"formal", "plan"})
_OPERATION_FIELDS: dict[str, frozenset[str]] = {
    "set_metadata": frozenset({"op", "action", "values", "metadata"}),
    "add_node": frozenset(
        {
            "op",
            "action",
            "node_type",
            "type",
            "position",
            "uuid",
            "fields",
            "locked",
            "client_id",
            "group_uuid",
            "plan",
        }
    ),
    "update_node": frozenset(
        {
            "op",
            "action",
            "node_uuid",
            "uuid",
            "fields",
            "position",
            "locked",
            "group_uuid",
            "plan_title",
            "plan",
        }
    ),
    "delete_node": frozenset({"op", "action", "node_uuid", "uuid"}),
    "add_connection": frozenset(
        {"op", "action", "from_uuid", "from", "to_uuid", "to"}
    ),
    "delete_connection": frozenset(
        {"op", "action", "from_uuid", "from", "to_uuid", "to"}
    ),
    "add_group": frozenset(
        {
            "op",
            "action",
            "uuid",
            "node_uuids",
            "title",
            "theme_body_color",
            "theme_border_color",
            "theme_text_color",
        }
    ),
    "update_group": frozenset(
        {
            "op",
            "action",
            "group_uuid",
            "uuid",
            "node_uuids",
            "title",
            "theme_body_color",
            "theme_border_color",
            "theme_text_color",
            "position",
            "size",
        }
    ),
    "delete_group": frozenset({"op", "action", "group_uuid", "uuid"}),
    "add_plan_topic": frozenset(
        {
            "op",
            "action",
            "title",
            "parent_uuid",
            "client_id",
            "order",
            "branch_color",
            "collapsed",
            "position",
            "uuid",
        }
    ),
    "update_plan_topic": frozenset(
        {
            "op",
            "action",
            "node_uuid",
            "values",
            "plan_title",
            "title",
            "collapsed",
            "branch_color",
            "order",
        }
    ),
    "reparent_plan_topic": frozenset(
        {"op", "action", "node_uuid", "new_parent_uuid", "order"}
    ),
    "delete_plan_subtree": frozenset({"op", "action", "node_uuid"}),
}
_PLAN_VALUE_FIELDS = frozenset(
    {"parent_uuid", "order", "plan_title", "title", "collapsed", "branch_color"}
)


class ToolServiceError(Exception):
    """An expected, stable user-level tool failure."""

    def __init__(self, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


class _DocumentSnapshotCommand(QUndoCommand):
    """One atomic undo step for a fully validated document replacement."""

    def __init__(
        self,
        controller: Any,
        before: DocumentModel,
        after: DocumentModel,
        label: str,
    ) -> None:
        super().__init__(label)
        self._controller = controller
        self._before = copy.deepcopy(before)
        self._after = copy.deepcopy(after)

    def _apply(self, document: DocumentModel) -> None:
        controller = self._controller
        selected_uuid = controller.selected_node_uuid
        replacement = copy.deepcopy(document)
        # Undo changes graph content, never the last successful save or its disk
        # baseline. A save (or Save As) may have happened after this command.
        for name in ("path", "disk_stamp", "disk_digest", "disk_observed", "history", "history_snapshot"):
            setattr(replacement, name, copy.deepcopy(getattr(controller.document, name)))
        controller.document = replacement
        controller.preferences.global_mode = controller.document.global_mode
        if selected_uuid and controller.get_node(selected_uuid) is None:
            selected_uuid = None
        controller.selected_node_uuid = selected_uuid
        controller.globalModeChanged.emit(controller.preferences.global_mode)
        controller.interactionCreationModeChanged.emit(
            controller.document.interaction_creation_mode
        )
        controller.documentLoaded.emit()
        controller.pathChanged.emit(controller.document.path)
        controller.refresh_derived()
        controller.selectionChanged.emit(selected_uuid)

    def redo(self) -> None:
        self._apply(self._after)

    def undo(self) -> None:
        self._apply(self._before)


def _json_copy(value: Any) -> Any:
    """Return a detached JSON DTO, failing loudly on accidental object leaks."""

    return json.loads(json.dumps(value, ensure_ascii=False))


def _issue_dto(issue: Any) -> dict[str, Any]:
    return {
        "node_uuid": str(issue.node_uuid),
        "message": str(issue.message),
        "severity": str(issue.severity),
        "field_keys": list(issue.field_keys),
        "related_node_uuids": list(issue.related_node_uuids),
        "related_titles": list(issue.related_titles),
    }


def _position(value: Any, *, field_name: str = "position") -> tuple[float, float]:
    if isinstance(value, dict):
        raw_x, raw_y = value.get("x"), value.get("y")
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        raw_x, raw_y = value
    else:
        raise ToolServiceError(
            "INVALID_ARGUMENT",
            f"{field_name} must contain finite x/y coordinates",
        )
    try:
        x, y = float(raw_x), float(raw_y)
    except (TypeError, ValueError) as exc:
        raise ToolServiceError(
            "INVALID_ARGUMENT",
            f"{field_name} must contain finite x/y coordinates",
        ) from exc
    if (
        not math.isfinite(x)
        or not math.isfinite(y)
        or abs(x) > MAX_CANVAS_COORDINATE
        or abs(y) > MAX_CANVAS_COORDINATE
    ):
        raise ToolServiceError(
            "INVALID_ARGUMENT",
            f"{field_name} coordinates are out of range",
        )
    return x, y


def _bounded_number(value: Any, *, field_name: str, minimum: float = 1.0) -> float:
    try:
        resolved = float(value)
    except (TypeError, ValueError) as exc:
        raise ToolServiceError("INVALID_ARGUMENT", f"{field_name} must be a number") from exc
    if not math.isfinite(resolved) or resolved < minimum:
        raise ToolServiceError("INVALID_ARGUMENT", f"{field_name} is out of range")
    return resolved


class EditorToolService(QObject):
    """Stable user-level tool registry bound to one ``EditorController``."""

    viewSwitchRequested = Signal(str)
    currentViewChanged = Signal(str)
    revisionChanged = Signal(int)
    documentIdentityChanged = Signal(str, str)
    documentIdentityMigrated = Signal(str, str)
    beforeInvocation = Signal()
    _crossThreadInvoke = Signal(str, object, object)

    def __init__(
        self,
        controller: Any,
        workspace_root: (
            str
            | Path
            | Callable[[], str | Path | None]
            | None
        ) = None,
        parent: QObject | None = None,
        *,
        before_invocation: Callable[[], bool | None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.before_invocation = before_invocation
        self._workspace_provider = workspace_root
        self._revision = 1
        self._current_view = "formal"
        self._document_identity = self._identity_for_document(controller.document.path)
        self._document_load_pending = False
        self._prepared_previews: dict[str, dict[str, Any]] = {}
        self._crossThreadInvoke.connect(
            self._complete_cross_thread_invoke,
            Qt.ConnectionType.QueuedConnection,
        )
        self._connect_controller_signals()

    @property
    def revision(self) -> int:
        return self._revision

    @property
    def current_view(self) -> str:
        return self._current_view

    @property
    def document_identity(self) -> str:
        """Stable normalized identity used for local per-graph chat history."""

        return self._document_identity

    def set_workspace_provider(
        self,
        provider: str | Path | Callable[[], str | Path | None] | None,
    ) -> None:
        self._workspace_provider = provider

    def set_current_view(self, view: str) -> None:
        normalized = str(view or "").strip().lower()
        if normalized not in _SUPPORTED_VIEWS or normalized == self._current_view:
            return
        self._current_view = normalized
        self.currentViewChanged.emit(normalized)

    @staticmethod
    def mutation_tools() -> set[str]:
        return set(_CONFIRMATION_TOOLS)

    @staticmethod
    def requires_confirmation(name: str) -> bool:
        return name in _CONFIRMATION_TOOLS

    def _connect_controller_signals(self) -> None:
        # Some main-window document sessions replace the active QUndoStack.
        # Document signals therefore remain the authoritative revision source.
        signal_names = (
            "nodeAdded",
            "nodeRemoved",
            "nodeUpdated",
            "nodeMoved",
            "connectionsChanged",
            "globalModeChanged",
            "interactionCreationModeChanged",
            "editorSettingsChanged",
            "groupsChanged",
            "canvasImagesChanged",
            "canvasStrokesChanged",
            "planCanvasStrokesChanged",
            "planLayoutChanged",
        )
        for name in signal_names:
            signal = getattr(self.controller, name, None)
            if signal is not None:
                signal.connect(self._note_document_change)
        self.controller.documentLoaded.connect(self._on_document_loaded)
        self.controller.pathChanged.connect(self._on_path_changed)

    def _note_document_change(self, *_args: Any) -> None:
        self._revision += 1
        self._prepared_previews.clear()
        self.revisionChanged.emit(self._revision)

    def _on_document_loaded(self) -> None:
        self._document_load_pending = True
        self._note_document_change()

    @staticmethod
    def _normalize_identity(path: Any) -> str:
        text = str(path or "").strip()
        if not text:
            return "__unsaved__"
        try:
            return str(Path(text).resolve()).replace("\\", "/").casefold()
        except (OSError, ValueError):
            return text.replace("\\", "/").casefold()

    def _identity_for_document(self, path: Any) -> str:
        normalized = self._normalize_identity(path)
        if normalized != "__unsaved__":
            return normalized
        root_uuid = next(
            (
                node.uuid
                for node in self.controller.document.nodes
                if node.type == "Idle0"
            ),
            "missing-root",
        )
        return f"__unsaved__:{root_uuid}"

    def _on_path_changed(self, path: Any) -> None:
        old_identity = self._document_identity
        new_identity = self._identity_for_document(path)
        switched_document = self._document_load_pending
        self._document_load_pending = False
        self._document_identity = new_identity
        self._note_document_change()
        if old_identity != new_identity:
            if not switched_document:
                # Saving an unsaved graph or renaming the active graph keeps its
                # conversation. Opening a different document must not inherit it.
                self.documentIdentityMigrated.emit(old_identity, new_identity)
            self.documentIdentityChanged.emit(old_identity, new_identity)

    def _workspace_root(self) -> Path:
        value: Any
        if callable(self._workspace_provider):
            value = self._workspace_provider()
        else:
            value = self._workspace_provider
        if value is None:
            value = getattr(self.controller, "_workspace_root", None)
        if value is None and self.controller.document.path:
            value = Path(self.controller.document.path).parent
        if value is None:
            raise ToolServiceError(
                "WORKSPACE_NOT_CONFIGURED",
                "No editor workspace is configured",
            )
        root = Path(value).resolve()
        if not root.is_dir():
            raise ToolServiceError(
                "WORKSPACE_NOT_FOUND",
                "The editor workspace is not available",
            )
        return root

    def tool_definitions(self) -> list[dict[str, Any]]:
        """Return OpenAI-compatible function tool definitions."""

        defs = [
            self._definition(
                "get_editor_schema",
                "Get writable metadata, node types, fields, and connection rules.",
                {},
            ),
            self._definition(
                "get_current_graph",
                "Get a detached DTO of the current in-memory graph and its revision.",
                {},
            ),
            self._definition(
                "find_nodes",
                "Find current graph nodes by visible field text or node type.",
                {
                    "query": {"type": "string"},
                    "node_type": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                },
            ),
            self._definition(
                "validate_current_graph",
                "Validate the current graph without changing it.",
                {},
            ),
            self._definition(
                "apply_graph_edits",
                "Atomically apply metadata, node, field, position, lock, edge, group, and plan-topic edits.",
                {
                    "expected_revision": {"type": "integer", "minimum": 1},
                    "operations": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 500,
                        "items": self._graph_operation_parameter_schema(),
                    },
                },
                required=("expected_revision", "operations"),
            ),
            self._definition(
                "optimize_layout",
                "Deterministically arrange unlocked and unfixed formal-graph nodes.",
                {
                    "expected_revision": {"type": "integer", "minimum": 1},
                    "horizontal_spacing": {"type": "number", "minimum": 120},
                    "vertical_spacing": {"type": "number", "minimum": 80},
                },
                required=("expected_revision",),
            ),
            self._definition(
                "switch_graph_view",
                "Switch the editor between formal and plan graph views.",
                {"view": {"type": "string", "enum": ["formal", "plan"]}},
                required=("view",),
            ),
            self._definition(
                "undo_last_edit",
                "Undo the latest editor transaction.",
                {"expected_revision": {"type": "integer", "minimum": 1}},
                required=("expected_revision",),
            ),
            self._definition(
                "redo_last_edit",
                "Redo the latest editor transaction.",
                {"expected_revision": {"type": "integer", "minimum": 1}},
                required=("expected_revision",),
            ),
            self._definition(
                "save_current_graph",
                "Save to the current graph JSON path. No arbitrary path is accepted.",
                {"expected_revision": {"type": "integer", "minimum": 1}},
                required=("expected_revision",),
            ),
            self._definition(
                "export_current_graph_csv",
                "Export only the current in-memory graph to a new workspace CSV.",
                {"expected_revision": {"type": "integer", "minimum": 1}},
                required=("expected_revision",),
            ),
        ]
        return _json_copy(defs)

    @staticmethod
    def _graph_operation_parameter_schema() -> dict[str, Any]:
        node_ref = {
            "anyOf": [
                {"type": "string"},
                {
                    "type": "object",
                    "properties": {"client_id": {"type": "string"}},
                    "required": ["client_id"],
                    "additionalProperties": False,
                },
            ]
        }
        nullable_node_ref = {
            "anyOf": [copy.deepcopy(node_ref), {"type": "null"}]
        }
        position = {
            "type": "object",
            "properties": {
                "x": {"type": "number"},
                "y": {"type": "number"},
            },
            "required": ["x", "y"],
            "additionalProperties": False,
        }
        return {
            "type": "object",
            "properties": {
                "op": {
                    "type": "string",
                    "enum": sorted(_OPERATION_FIELDS),
                },
                "values": {"type": "object"},
                "metadata": {"type": "object"},
                "node_type": {"type": "string"},
                "position": position,
                "size": {
                    "type": "object",
                    "properties": {
                        "width": {"type": "number"},
                        "height": {"type": "number"},
                    },
                    "required": ["width", "height"],
                    "additionalProperties": False,
                },
                "uuid": {"type": "string"},
                "fields": {"type": "object"},
                "locked": {"type": "boolean"},
                "client_id": {"type": "string"},
                "group_uuid": nullable_node_ref,
                "plan": {
                    "type": "object",
                    "properties": {
                        key: {}
                        for key in sorted(_PLAN_VALUE_FIELDS)
                    },
                    "additionalProperties": False,
                },
                "node_uuid": node_ref,
                "from_uuid": node_ref,
                "to_uuid": node_ref,
                "node_uuids": {
                    "type": "array",
                    "items": node_ref,
                },
                "title": {"type": "string"},
                "theme_body_color": {"type": "string"},
                "theme_border_color": {"type": "string"},
                "theme_text_color": {"type": "string"},
                "parent_uuid": nullable_node_ref,
                "new_parent_uuid": nullable_node_ref,
                "order": {"type": "integer", "minimum": 0},
                "branch_color": {"type": "string"},
                "collapsed": {"type": "boolean"},
                "plan_title": {"type": "string"},
            },
            "required": ["op"],
            "additionalProperties": False,
        }

    @staticmethod
    def _definition(
        name: str,
        description: str,
        properties: dict[str, Any],
        *,
        required: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        parameters: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        }
        if required:
            parameters["required"] = list(required)
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            },
        }

    def invoke_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke one whitelisted tool and always return a JSON-safe envelope."""

        if QThread.currentThread() is self.thread():
            return self._invoke_guarded(name, arguments)
        envelope: dict[str, Any] = {
            "event": threading.Event(),
            "result": None,
            "lock": threading.Lock(),
            "state": "pending",
        }
        self._crossThreadInvoke.emit(str(name), arguments, envelope)
        if not envelope["event"].wait(timeout=30.0):
            with envelope["lock"]:
                state = envelope["state"]
                if state == "pending":
                    envelope["state"] = "cancelled"
            if state == "pending":
                return self._error_response(
                    ToolServiceError(
                        "MAIN_THREAD_TIMEOUT",
                        "The editor main thread did not start the tool call in time",
                    )
                )
            # Once execution has begun, returning a timeout would allow a
            # mutation to land after the caller believes it failed. Wait for
            # the bounded, allow-listed editor operation to finish instead.
            envelope["event"].wait()
        return envelope["result"]

    def _complete_cross_thread_invoke(
        self,
        name: str,
        arguments: Any,
        envelope: dict[str, Any],
    ) -> None:
        lock = envelope["lock"]
        with lock:
            if envelope["state"] == "cancelled":
                envelope["event"].set()
                return
            envelope["state"] = "started"
        try:
            envelope["result"] = self._invoke_guarded(name, arguments)
        finally:
            with lock:
                envelope["state"] = "completed"
            envelope["event"].set()

    def _invoke_guarded(self, name: str, arguments: Any) -> dict[str, Any]:
        try:
            if not isinstance(arguments, dict):
                raise ToolServiceError(
                    "INVALID_ARGUMENT",
                    "Tool arguments must be a JSON object",
                )
            handlers = {
                "get_editor_schema": self._get_editor_schema,
                "get_current_graph": self._get_current_graph,
                "find_nodes": self._find_nodes,
                "validate_current_graph": self._validate_current_graph,
                "apply_graph_edits": self._apply_graph_edits,
                "optimize_layout": self._optimize_layout,
                "switch_graph_view": self._switch_graph_view,
                "undo_last_edit": self._undo_last_edit,
                "redo_last_edit": self._redo_last_edit,
                "save_current_graph": self._save_current_graph,
                "export_current_graph_csv": self._export_current_graph_csv,
            }
            handler = handlers.get(str(name))
            if handler is None:
                raise ToolServiceError("TOOL_NOT_FOUND", f"Unknown editor tool: {name}")
            self._finish_pending_input()
            result = handler(dict(arguments))
            envelope = {
                "ok": True,
                "revision": self._revision,
                "result": result,
            }
            return _json_copy(envelope)
        except ToolServiceError as exc:
            return self._error_response(exc)
        except Exception as exc:  # keep implementation details out of the model
            return self._error_response(
                ToolServiceError(
                    "INTERNAL_ERROR",
                    "The editor could not complete this tool call",
                    {"exception_type": type(exc).__name__},
                )
            )

    def _finish_pending_input(self) -> None:
        """A Qt signal cannot propagate a failed editor commit to this call."""
        if self.before_invocation is not None:
            if self.before_invocation() is False:
                raise ToolServiceError("PENDING_EDITOR_INPUT", "请先完成监听器子蓝图中的输入")
        else:
            self.beforeInvocation.emit()

    def _error_response(self, error: ToolServiceError) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": False,
            "revision": self._revision,
            "error": {
                "code": error.code,
                "message": error.message,
            },
        }
        if error.details is not None:
            payload["error"]["details"] = error.details
        return _json_copy(payload)

    def _expect_revision(self, arguments: dict[str, Any]) -> int:
        raw = arguments.get("expected_revision")
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ToolServiceError(
                "INVALID_ARGUMENT",
                "expected_revision must be an integer",
            )
        if raw != self._revision:
            raise ToolServiceError(
                "REVISION_CONFLICT",
                "The graph changed after it was read; fetch it again before editing",
                {"expected_revision": raw, "current_revision": self._revision},
            )
        return raw

    def _get_editor_schema(self, _arguments: dict[str, Any]) -> dict[str, Any]:
        schema = self.controller.schema
        node_types: list[dict[str, Any]] = []
        for type_name, node_schema in schema.nodes.items():
            fields = []
            for field in node_schema.fields:
                if field.key in HIDDEN_NODE_FIELDS:
                    continue
                fields.append(
                    {
                        "key": field.key,
                        "label": field.label,
                        "editor": field.editor,
                        "default": copy.deepcopy(field.default),
                        "writable": (
                            not field.read_only
                            and node_schema.category not in {"root", "meta"}
                        ),
                        "options": [
                            {"label": option.label, "value": copy.deepcopy(option.value)}
                            for option in field.options
                        ],
                    }
                )
            node_types.append(
                {
                    "type": type_name,
                    "title": node_schema.title,
                    "category": node_schema.category,
                    "copyable": node_schema.copyable,
                    "creatable": node_schema.category not in {"root", "meta"} and (type_name != "Listener" or features.LISTENER_EDITOR_ENABLED),
                    "fields": fields,
                }
            )
        return {
            "format_version": EDITOR_DOCUMENT_FORMAT_VERSION,
            "revision": self._revision,
            "metadata_fields": list(
                self.controller.document.meta.__dataclass_fields__.keys()
            ),
            "node_types": node_types,
            "connection_rules": {
                "self_edges": False,
                "duplicate_edges": False,
                "edges_to_root": False,
                "multiple_parents": True,
                "cycles": True,
                "edges_to_or_from_listener_hosts": False,
            },
            "apply_operation_types": [
                "set_metadata",
                "add_node",
                "update_node",
                "delete_node",
                "add_connection",
                "delete_connection",
                "add_group",
                "update_group",
                "delete_group",
                "add_plan_topic",
                "update_plan_topic",
                "reparent_plan_topic",
                "delete_plan_subtree",
            ],
            "graph_edit_operations": {
                "set_metadata": {
                    "required": ["op", "values"],
                    "example": {
                        "op": "set_metadata",
                        "values": {"memo": "用途说明"},
                    },
                },
                "add_node": {
                    "required": ["op", "node_type"],
                    "optional": [
                        "client_id",
                        "uuid",
                        "fields",
                        "position",
                        "locked",
                        "group_uuid",
                        "plan",
                    ],
                    "example": {
                        "op": "add_node",
                        "node_type": "Comment",
                        "client_id": "new_note",
                        "fields": {"content": "说明"},
                        "position": {"x": 500, "y": 200},
                    },
                    "note": (
                        "plan.parent_uuid creates the matching formal primary edge; "
                        "use add_plan_topic for a new plan draft placeholder."
                    ),
                },
                "update_node": {
                    "required": ["op", "node_uuid"],
                    "optional": [
                        "fields",
                        "position",
                        "locked",
                        "group_uuid",
                        "plan_title",
                        "plan",
                    ],
                    "note": (
                        "plan may update title/collapsed/color/order but not parent; "
                        "use reparent_plan_topic for parent changes."
                    ),
                },
                "delete_node": {
                    "required": ["op", "node_uuid"],
                },
                "add_connection": {
                    "required": ["op", "from_uuid", "to_uuid"],
                    "note": "A reference may be a UUID or {\"client_id\":\"...\"}.",
                },
                "delete_connection": {
                    "required": ["op", "from_uuid", "to_uuid"],
                },
                "add_group": {
                    "required": ["op", "node_uuids"],
                    "optional": ["uuid", "title", "theme_*"],
                },
                "update_group": {
                    "required": ["op", "group_uuid"],
                    "optional": [
                        "title",
                        "node_uuids",
                        "theme_*",
                        "position_and_size",
                    ],
                },
                "delete_group": {
                    "required": ["op", "group_uuid"],
                },
                "add_plan_topic": {
                    "required": ["op", "title"],
                    "optional": [
                        "parent_uuid",
                        "client_id",
                        "order",
                        "branch_color",
                        "position",
                    ],
                    "note": "Creates a Comment node and its formal primary edge.",
                },
                "update_plan_topic": {
                    "required": ["op", "node_uuid"],
                    "optional": [
                        "plan_title",
                        "collapsed",
                        "branch_color",
                        "order",
                    ],
                },
                "reparent_plan_topic": {
                    "required": ["op", "node_uuid", "new_parent_uuid"],
                    "optional": ["order"],
                    "note": (
                        "new_parent_uuid may be null for the virtual unconnected "
                        "branch; replaces only the formal primary edge."
                    ),
                },
                "delete_plan_subtree": {
                    "required": ["op", "node_uuid"],
                    "note": "Deletes the whole plan subtree and matching real graph content.",
                },
            },
        }

    def _get_current_graph(self, _arguments: dict[str, Any]) -> dict[str, Any]:
        snapshot = copy.deepcopy(self.controller.document)
        payload = export_document_dict(self.controller.schema, snapshot)
        payload["canvas_images"] = [
            {
                "uuid": image.uuid,
                "name": image.name,
                "mime_type": image.mime_type,
                "encoded_byte_count": len(image.data_base64.encode("ascii", "ignore")),
                "position": copy.deepcopy(image.ui_position),
                "size": copy.deepcopy(image.ui_size),
                "opacity": float(image.opacity),
                "locked": bool(image.locked),
            }
            for image in snapshot.canvas_images
        ]
        def summarize_strokes(strokes) -> list[dict[str, Any]]:
            summaries: list[dict[str, Any]] = []
            for stroke in strokes:
                xs = [float(point[0]) for point in stroke.points]
                ys = [float(point[1]) for point in stroke.points]
                bounds = (
                    {
                        "left": min(xs),
                        "top": min(ys),
                        "right": max(xs),
                        "bottom": max(ys),
                    }
                    if xs and ys
                    else None
                )
                summaries.append(
                    {
                        "id": stroke.uuid,
                        "point_count": len(stroke.points),
                        "color": stroke.color,
                        "width": float(stroke.width),
                        "bounds": bounds,
                    }
                )
            return summaries

        payload["canvas_strokes"] = summarize_strokes(snapshot.canvas_strokes)
        payload["plan_canvas_strokes"] = summarize_strokes(
            snapshot.plan_canvas_strokes
        )
        return {
            "revision": self._revision,
            "current_view": self._current_view,
            "document_name": (
                Path(self.controller.document.path).name
                if self.controller.document.path
                else ""
            ),
            "graph": payload,
        }

    def _find_nodes(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        node_type = str(arguments.get("node_type") or "").strip()
        raw_limit = arguments.get("limit", 50)
        if isinstance(raw_limit, bool) or not isinstance(raw_limit, int):
            raise ToolServiceError("INVALID_ARGUMENT", "limit must be an integer")
        limit = min(200, max(1, raw_limit))
        if node_type and node_type not in self.controller.schema.nodes:
            raise ToolServiceError("INVALID_NODE_TYPE", f"Unknown node type: {node_type}")

        nodes_by_uuid = {node.uuid: node for node in self.controller.document.nodes}
        if query:
            hits = search_document(
                self.controller.schema,
                copy.deepcopy(self.controller.document),
                query,
                use_json_field_names=True,
            )
            matched = []
            seen: set[str] = set()
            for hit in hits:
                node = nodes_by_uuid.get(hit.node_uuid)
                if (
                    node is None
                    or hit.node_uuid in seen
                    or (node_type and node.type != node_type)
                ):
                    continue
                seen.add(hit.node_uuid)
                matched.append(node)
        else:
            matched = [
                node
                for node in self.controller.document.nodes
                if not node_type or node.type == node_type
            ]
        return {
            "count": min(len(matched), limit),
            "nodes": [self._node_dto(node) for node in matched[:limit]],
        }

    def _node_dto(self, node: NodeRecord) -> dict[str, Any]:
        return {
            "uuid": node.uuid,
            "type": node.type,
            "title": node_title(self.controller.schema, node),
            "fields": copy.deepcopy(node.fields),
            "listener_graph": node.listener_graph.to_payload() if node.listener_graph is not None else None,
            "listener_owned_fields": (["listener_data", "parameter", "range", "start_value", "save_parameter"]
                                      if node.listener_graph is not None else []),
            "position": {
                "x": float(node.ui_position["x"]),
                "y": float(node.ui_position["y"]),
            },
            "locked": bool(node.locked),
            "group_uuid": next(
                (
                    group.uuid
                    for group in self.controller.document.groups
                    if node.uuid in group.node_uuids
                ),
                None,
            ),
        }

    def _validate_current_graph(self, _arguments: dict[str, Any]) -> dict[str, Any]:
        snapshot = copy.deepcopy(self.controller.document)
        structural = self._validate_structure(snapshot)
        issues = validate_document(self.controller.schema, snapshot)
        return {
            "valid": not structural,
            "structural_errors": structural,
            "issues": [_issue_dto(issue) for issue in issues],
        }

    def _apply_graph_edits(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._expect_revision(arguments)
        prepared = self._prepare_graph_edits(arguments)
        self.controller.undo_stack.push(
            _DocumentSnapshotCommand(
                self.controller,
                self.controller.document,
                prepared["document"],
                "AI 批量编辑图表",
            )
        )
        return {
            "applied": True,
            "changes": prepared["changes"],
            "client_ids": prepared["client_ids"],
            "validation_issues": prepared["validation_issues"],
        }

    def _prepare_graph_edits(self, arguments: dict[str, Any]) -> dict[str, Any]:
        operations = arguments.get("operations")
        if not isinstance(operations, list) or not operations or len(operations) > 500:
            raise ToolServiceError(
                "INVALID_ARGUMENT",
                "operations must contain between 1 and 500 items",
            )
        document = copy.deepcopy(self.controller.document)
        client_ids: dict[str, str] = {}
        changes: list[dict[str, Any]] = []
        for index, raw_operation in enumerate(operations):
            if not isinstance(raw_operation, dict):
                raise ToolServiceError(
                    "INVALID_ARGUMENT",
                    "Each operation must be an object",
                    {"operation_index": index},
                )
            try:
                change = self._apply_operation(
                    document,
                    dict(raw_operation),
                    client_ids,
                )
            except ToolServiceError as exc:
                details = (
                    dict(exc.details)
                    if isinstance(exc.details, dict)
                    else {"cause": exc.details}
                    if exc.details is not None
                    else {}
                )
                details["operation_index"] = index
                details["operation"] = str(
                    raw_operation.get("op", raw_operation.get("action", ""))
                )
                raise ToolServiceError(exc.code, exc.message, details) from exc
            changes.append(change)
        reassign_function_ids(self.controller.schema, document)
        structural_errors = self._validate_structure(document)
        if structural_errors:
            raise ToolServiceError(
                "INVALID_GRAPH",
                "The edit batch would create an invalid graph",
                {"errors": structural_errors},
            )
        # Serialization validates persistent records such as canvas strokes.
        try:
            export_document_dict(self.controller.schema, copy.deepcopy(document))
        except (TypeError, ValueError) as exc:
            raise ToolServiceError(
                "INVALID_GRAPH",
                "The edit batch could not be serialized safely",
                {"exception_type": type(exc).__name__},
            ) from exc
        issues = validate_document(self.controller.schema, copy.deepcopy(document))
        if document == self.controller.document:
            raise ToolServiceError(
                "NO_CHANGES",
                "The graph edit batch would not change the document",
            )
        return {
            "document": document,
            "changes": changes,
            "client_ids": client_ids,
            "validation_issues": [_issue_dto(issue) for issue in issues],
        }

    @staticmethod
    def _operation_name(operation: dict[str, Any]) -> str:
        return (
            str(operation.get("op", operation.get("action", "")))
            .strip()
            .lower()
            .replace(".", "_")
        )

    def _apply_operation(
        self,
        document: DocumentModel,
        operation: dict[str, Any],
        client_ids: dict[str, str],
    ) -> dict[str, Any]:
        name = self._operation_name(operation)
        aliases = {
            "metadata_set": "set_metadata",
            "node_add": "add_node",
            "node_update": "update_node",
            "node_delete": "delete_node",
            "connection_add": "add_connection",
            "connection_delete": "delete_connection",
            "group_add": "add_group",
            "group_update": "update_group",
            "group_delete": "delete_group",
            "plan_add": "add_plan_topic",
            "plan_update": "update_plan_topic",
            "plan_reparent": "reparent_plan_topic",
            "plan_delete": "delete_plan_subtree",
        }
        name = aliases.get(name, name)
        handlers = {
            "set_metadata": self._op_set_metadata,
            "add_node": self._op_add_node,
            "update_node": self._op_update_node,
            "delete_node": self._op_delete_node,
            "add_connection": self._op_add_connection,
            "delete_connection": self._op_delete_connection,
            "add_group": self._op_add_group,
            "update_group": self._op_update_group,
            "delete_group": self._op_delete_group,
            "add_plan_topic": self._op_add_plan_topic,
            "update_plan_topic": self._op_update_plan_topic,
            "reparent_plan_topic": self._op_reparent_plan_topic,
            "delete_plan_subtree": self._op_delete_plan_subtree,
        }
        handler = handlers.get(name)
        if handler is None:
            raise ToolServiceError(
                "INVALID_OPERATION",
                f"Unsupported graph edit operation: {name or '<empty>'}",
            )
        unknown_fields = sorted(set(operation) - _OPERATION_FIELDS[name])
        if unknown_fields:
            raise ToolServiceError(
                "INVALID_ARGUMENT",
                f"Unsupported field(s) for {name}: {', '.join(unknown_fields)}",
                {"unknown_fields": unknown_fields},
            )
        nested_plan = operation.get("plan")
        if isinstance(nested_plan, dict):
            unknown_plan_fields = sorted(set(nested_plan) - _PLAN_VALUE_FIELDS)
            if unknown_plan_fields:
                raise ToolServiceError(
                    "INVALID_ARGUMENT",
                    "Unsupported plan field(s): "
                    + ", ".join(unknown_plan_fields),
                    {"unknown_fields": unknown_plan_fields},
                )
        values = operation.get("values")
        if name == "update_plan_topic" and isinstance(values, dict):
            unknown_value_fields = sorted(set(values) - _PLAN_VALUE_FIELDS)
            if unknown_value_fields:
                raise ToolServiceError(
                    "INVALID_ARGUMENT",
                    "Unsupported plan field(s): "
                    + ", ".join(unknown_value_fields),
                    {"unknown_fields": unknown_value_fields},
                )
        return handler(document, operation, client_ids)

    @staticmethod
    def _node(document: DocumentModel, node_uuid: str) -> NodeRecord | None:
        return next((node for node in document.nodes if node.uuid == node_uuid), None)

    @staticmethod
    def _group(document: DocumentModel, group_uuid: str) -> GroupRecord | None:
        return next((group for group in document.groups if group.uuid == group_uuid), None)

    def _resolve_ref(
        self,
        raw: Any,
        client_ids: dict[str, str],
        *,
        allow_none: bool = False,
    ) -> str | None:
        if raw is None and allow_none:
            return None
        if isinstance(raw, dict) and set(raw) == {"client_id"}:
            raw = raw["client_id"]
        text = str(raw or "").strip()
        if text in client_ids:
            return client_ids[text]
        if text.startswith("client:") and text[7:] in client_ids:
            return client_ids[text[7:]]
        if not text:
            if allow_none:
                return None
            raise ToolServiceError("INVALID_ARGUMENT", "A node UUID is required")
        return text

    def _required_node(
        self,
        document: DocumentModel,
        raw: Any,
        client_ids: dict[str, str],
    ) -> NodeRecord:
        node_uuid = self._resolve_ref(raw, client_ids)
        node = self._node(document, str(node_uuid))
        if node is None:
            raise ToolServiceError(
                "NODE_NOT_FOUND",
                f"Node not found: {node_uuid}",
                {"node_uuid": node_uuid},
            )
        return node

    def _op_set_metadata(
        self,
        document: DocumentModel,
        operation: dict[str, Any],
        _client_ids: dict[str, str],
    ) -> dict[str, Any]:
        values = operation.get("values", operation.get("metadata"))
        if not isinstance(values, dict) or not values:
            raise ToolServiceError("INVALID_ARGUMENT", "Metadata values are required")
        allowed = set(document.meta.__dataclass_fields__)
        changed: dict[str, Any] = {}
        for key, value in values.items():
            if key not in allowed:
                raise ToolServiceError(
                    "FIELD_NOT_WRITABLE",
                    f"Metadata field is not writable: {key}",
                    {"field": key},
                )
            if key == "default_state":
                value = "idle0"
            elif key == "ship_skin_id":
                try:
                    value = int(value)
                except (TypeError, ValueError) as exc:
                    raise ToolServiceError(
                        "INVALID_ARGUMENT",
                        "ship_skin_id must be an integer",
                    ) from exc
            else:
                value = str(value or "")
            if getattr(document.meta, key) != value:
                setattr(document.meta, key, value)
                changed[key] = value
        return {"op": "set_metadata", "changed_fields": sorted(changed)}

    def _op_add_node(
        self,
        document: DocumentModel,
        operation: dict[str, Any],
        client_ids: dict[str, str],
    ) -> dict[str, Any]:
        node_type = str(operation.get("node_type", operation.get("type", ""))).strip()
        if node_type == "Listener" and not features.LISTENER_EDITOR_ENABLED:
            raise ToolServiceError("FEATURE_DISABLED", "监听器子蓝图暂未开放创建")
        definition = self.controller.schema.nodes.get(node_type)
        if definition is None:
            raise ToolServiceError("INVALID_NODE_TYPE", f"Unknown node type: {node_type}")
        if definition.category in {"root", "meta"}:
            raise ToolServiceError(
                "FIELD_NOT_WRITABLE",
                f"Node type cannot be created: {node_type}",
            )
        x, y = _position(operation.get("position", {"x": 0.0, "y": 0.0}))
        node = create_node(self.controller.schema, document, node_type, (x, y))
        requested_uuid = str(operation.get("uuid") or "").strip()
        if requested_uuid:
            if not _SAFE_ID.fullmatch(requested_uuid):
                raise ToolServiceError("INVALID_ARGUMENT", "Node UUID is invalid")
            node.uuid = requested_uuid
        if self._node(document, node.uuid) is not None:
            raise ToolServiceError(
                "DUPLICATE_NODE_UUID",
                f"Node UUID already exists: {node.uuid}",
            )
        fields = operation.get("fields", {})
        if not isinstance(fields, dict):
            raise ToolServiceError("INVALID_ARGUMENT", "fields must be an object")
        self._set_node_fields(document, node, fields)
        node.locked = bool(operation.get("locked", False))
        document.nodes.append(node)
        client_id = str(operation.get("client_id") or "").strip()
        if client_id:
            if client_id in client_ids:
                raise ToolServiceError(
                    "DUPLICATE_CLIENT_ID",
                    f"client_id is already used: {client_id}",
                )
            client_ids[client_id] = node.uuid
        group_ref = operation.get("group_uuid")
        if group_ref is not None:
            self._set_group_membership(document, node.uuid, str(group_ref))
        if isinstance(operation.get("plan"), dict):
            plan_topic = self._append_plan_topic(
                document,
                node.uuid,
                dict(operation["plan"]),
                client_ids,
            )
            if plan_topic.parent_uuid is not None:
                parent_node = self._node(document, plan_topic.parent_uuid)
                if parent_node is None:
                    raise ToolServiceError(
                        "NODE_NOT_FOUND",
                        f"Plan parent node not found: {plan_topic.parent_uuid}",
                    )
                self._validate_new_connection(document, parent_node, node)
                document.connections.append(
                    ConnectionRecord(
                        from_uuid=parent_node.uuid,
                        to_uuid=node.uuid,
                    )
                )
        return {
            "op": "add_node",
            "node_uuid": node.uuid,
            "client_id": client_id or None,
        }

    def _op_update_node(
        self,
        document: DocumentModel,
        operation: dict[str, Any],
        client_ids: dict[str, str],
    ) -> dict[str, Any]:
        node = self._required_node(
            document,
            operation.get("node_uuid", operation.get("uuid")),
            client_ids,
        )
        fields = operation.get("fields", {})
        if not isinstance(fields, dict):
            raise ToolServiceError("INVALID_ARGUMENT", "fields must be an object")
        changes_other_than_unlock = bool(fields) or any(
            key in operation
            for key in ("position", "group_uuid", "plan_title", "plan")
        )
        if node.locked and changes_other_than_unlock:
            raise ToolServiceError(
                "NODE_LOCKED",
                f"Node is locked: {node.uuid}",
                {"node_uuid": node.uuid},
            )
        changed: list[str] = []
        if fields:
            self._set_node_fields(document, node, fields)
            changed.extend(f"fields.{key}" for key in fields)
        if "position" in operation:
            x, y = _position(operation["position"])
            node.ui_position = {"x": x, "y": y}
            changed.append("position")
        if "locked" in operation:
            node.locked = bool(operation["locked"])
            changed.append("locked")
        if "group_uuid" in operation:
            target_group = self._resolve_ref(
                operation["group_uuid"],
                client_ids,
                allow_none=True,
            )
            self._set_group_membership(document, node.uuid, target_group)
            changed.append("group_uuid")
        plan_values = operation.get("plan")
        if "plan_title" in operation:
            plan_values = {
                **(plan_values if isinstance(plan_values, dict) else {}),
                "plan_title": operation["plan_title"],
            }
        if isinstance(plan_values, dict):
            self._update_plan_topic_values(document, node.uuid, plan_values, client_ids)
            changed.append("plan")
        return {"op": "update_node", "node_uuid": node.uuid, "changed": changed}

    def _op_delete_node(
        self,
        document: DocumentModel,
        operation: dict[str, Any],
        client_ids: dict[str, str],
    ) -> dict[str, Any]:
        node = self._required_node(
            document,
            operation.get("node_uuid", operation.get("uuid")),
            client_ids,
        )
        definition = self.controller.schema.nodes[node.type]
        if definition.category == "root" or not definition.copyable:
            raise ToolServiceError(
                "FIELD_NOT_WRITABLE",
                "The root node cannot be deleted",
            )
        if node.locked:
            raise ToolServiceError(
                "NODE_LOCKED",
                f"Node is locked: {node.uuid}",
                {"node_uuid": node.uuid},
            )
        document.nodes = [current for current in document.nodes if current.uuid != node.uuid]
        document.connections = [
            edge
            for edge in document.connections
            if edge.from_uuid != node.uuid and edge.to_uuid != node.uuid
        ]
        for group in document.groups:
            group.node_uuids = [
                node_uuid for node_uuid in group.node_uuids if node_uuid != node.uuid
            ]
        self._remove_plan_topics(document, {node.uuid})
        return {"op": "delete_node", "node_uuid": node.uuid}

    def _set_node_fields(
        self,
        document: DocumentModel,
        node: NodeRecord,
        fields: dict[str, Any],
    ) -> None:
        if not fields:
            return
        definition = self.controller.schema.nodes[node.type]
        definitions = {
            field.key: field
            for field in definition.fields
            if field.key not in HIDDEN_NODE_FIELDS
        }
        normalized: dict[str, Any] = {}
        for key, value in fields.items():
            if node.listener_graph is not None and key in {
                "listener_data", "parameter", "range", "start_value", "save_parameter",
            }:
                raise ToolServiceError(
                    "FIELD_NOT_WRITABLE",
                    "This field is managed by the listener subgraph; edit it in the listener editor",
                    {"node_uuid": node.uuid, "field": key},
                )
            field = definitions.get(str(key))
            if field is None or field.read_only or definition.category in {"root", "meta"}:
                raise ToolServiceError(
                    "FIELD_NOT_WRITABLE",
                    f"Node field is not writable: {key}",
                    {"node_uuid": node.uuid, "field": key},
                )
            normalized[key] = self._normalize_tool_field(field, node, value)
        for key, value in normalized.items():
            node.fields[key] = normalize_field_input(
                self.controller.schema,
                node,
                key,
                value,
            )
            if (
                node.type in function_node_types(self.controller.schema)
                and key in {"draw_able_name", "parameter", "action_trigger"}
            ):
                node.manual_fields.add(key)
        apply_node_appearance_defaults(self.controller.schema, node)
        changed_keys = set(normalized)
        if node.type == "Comment" and changed_keys & {
            "theme_body_color",
            "theme_text_color",
        }:
            sync_comment_legacy_appearance(node)
        if node.type == "Comment" and changed_keys & {
            "note_box_color",
            "note_text_color",
        }:
            sync_comment_theme_appearance(node)
        changed_key = next(iter(changed_keys)) if len(changed_keys) == 1 else None
        apply_auto_rules(
            self.controller.schema,
            document,
            node,
            source_mode="advanced",
            changed_key=changed_key,
        )

    @staticmethod
    def _normalize_tool_field(field: Any, node: NodeRecord, value: Any) -> Any:
        if field.editor == "int":
            if isinstance(value, bool):
                raise ToolServiceError(
                    "INVALID_ARGUMENT",
                    f"{field.key} must be an integer",
                    {"node_uuid": node.uuid, "field": field.key},
                )
            try:
                return int(value)
            except (TypeError, ValueError) as exc:
                raise ToolServiceError(
                    "INVALID_ARGUMENT",
                    f"{field.key} must be an integer",
                    {"node_uuid": node.uuid, "field": field.key},
                ) from exc
        if field.editor in {"float", "number"}:
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise ToolServiceError(
                    "INVALID_ARGUMENT",
                    f"{field.key} must be a number",
                    {"node_uuid": node.uuid, "field": field.key},
                ) from exc
            if not math.isfinite(number):
                raise ToolServiceError(
                    "INVALID_ARGUMENT",
                    f"{field.key} must be finite",
                    {"node_uuid": node.uuid, "field": field.key},
                )
            return number
        if field.editor in {"checkbox", "bool"}:
            if not isinstance(value, bool):
                raise ToolServiceError(
                    "INVALID_ARGUMENT",
                    f"{field.key} must be a boolean",
                    {"node_uuid": node.uuid, "field": field.key},
                )
            return value
        if field.options:
            allowed = [option.value for option in field.options]
            if value not in allowed:
                raise ToolServiceError(
                    "INVALID_ARGUMENT",
                    f"{field.key} is not one of the allowed values",
                    {
                        "node_uuid": node.uuid,
                        "field": field.key,
                        "allowed": allowed,
                    },
                )
        return copy.deepcopy(value)

    def _op_add_connection(
        self,
        document: DocumentModel,
        operation: dict[str, Any],
        client_ids: dict[str, str],
    ) -> dict[str, Any]:
        from_node = self._required_node(
            document,
            operation.get("from_uuid", operation.get("from")),
            client_ids,
        )
        to_node = self._required_node(
            document,
            operation.get("to_uuid", operation.get("to")),
            client_ids,
        )
        self._validate_new_connection(document, from_node, to_node)
        document.connections.append(
            ConnectionRecord(from_uuid=from_node.uuid, to_uuid=to_node.uuid)
        )
        plan_layout = getattr(document, "plan_layout", None)
        topics = getattr(plan_layout, "topics", None)
        if isinstance(topics, list):
            target_topic = next(
                (topic for topic in topics if topic.node_uuid == to_node.uuid),
                None,
            )
            source_topic = next(
                (topic for topic in topics if topic.node_uuid == from_node.uuid),
                None,
            )
            if (
                target_topic is not None
                and source_topic is not None
                and target_topic.parent_uuid is None
                and to_node.uuid != self._idle0_uuid(document)
            ):
                target_topic.parent_uuid = from_node.uuid
                target_topic.order = len(
                    [
                        topic
                        for topic in topics
                        if topic.parent_uuid == from_node.uuid
                        and topic.node_uuid != to_node.uuid
                    ]
                )
                self._normalize_plan_orders(topics)
                self._validate_plan_tree(document)
        return {
            "op": "add_connection",
            "from_uuid": from_node.uuid,
            "to_uuid": to_node.uuid,
        }

    def _validate_new_connection(
        self,
        document: DocumentModel,
        from_node: NodeRecord,
        to_node: NodeRecord,
    ) -> None:
        if from_node.type == "Listener" or to_node.type == "Listener":
            raise ToolServiceError(
                "INVALID_CONNECTION",
                "Listener hosts have no external graph ports; connect components inside their subgraph",
            )
        if from_node.uuid == to_node.uuid:
            raise ToolServiceError(
                "INVALID_CONNECTION",
                "A node cannot connect to itself",
            )
        if self.controller.schema.nodes[to_node.type].category == "root":
            raise ToolServiceError(
                "INVALID_CONNECTION",
                "Connections cannot target the root node",
            )
        if any(
            edge.from_uuid == from_node.uuid and edge.to_uuid == to_node.uuid
            for edge in document.connections
        ):
            raise ToolServiceError(
                "INVALID_CONNECTION",
                "The connection already exists",
            )

    def _op_delete_connection(
        self,
        document: DocumentModel,
        operation: dict[str, Any],
        client_ids: dict[str, str],
    ) -> dict[str, Any]:
        from_uuid = self._resolve_ref(
            operation.get("from_uuid", operation.get("from")),
            client_ids,
        )
        to_uuid = self._resolve_ref(
            operation.get("to_uuid", operation.get("to")),
            client_ids,
        )
        found = any(
            edge.from_uuid == from_uuid and edge.to_uuid == to_uuid
            for edge in document.connections
        )
        if not found:
            raise ToolServiceError(
                "CONNECTION_NOT_FOUND",
                "The requested connection does not exist",
                {"from_uuid": from_uuid, "to_uuid": to_uuid},
            )
        document.connections = [
            edge
            for edge in document.connections
            if not (edge.from_uuid == from_uuid and edge.to_uuid == to_uuid)
        ]
        return {
            "op": "delete_connection",
            "from_uuid": from_uuid,
            "to_uuid": to_uuid,
        }

    def _op_add_group(
        self,
        document: DocumentModel,
        operation: dict[str, Any],
        client_ids: dict[str, str],
    ) -> dict[str, Any]:
        requested_uuid = str(operation.get("uuid") or new_uuid()).strip()
        if not _SAFE_ID.fullmatch(requested_uuid) or self._group(document, requested_uuid):
            raise ToolServiceError("INVALID_ARGUMENT", "Group UUID is invalid or duplicated")
        raw_nodes = operation.get("node_uuids", [])
        if not isinstance(raw_nodes, list):
            raise ToolServiceError("INVALID_ARGUMENT", "node_uuids must be an array")
        node_uuids = [
            self._required_node(document, raw, client_ids).uuid for raw in raw_nodes
        ]
        group = GroupRecord(
            uuid=requested_uuid,
            title=str(operation.get("title") or "新建分组").strip() or "新建分组",
            node_uuids=[],
            theme_body_color=str(operation.get("theme_body_color") or "#dfeada"),
            theme_border_color=str(operation.get("theme_border_color") or "#69b070"),
            theme_text_color=str(operation.get("theme_text_color") or "#ffffff"),
        )
        document.groups.append(group)
        for node_uuid in node_uuids:
            self._set_group_membership(document, node_uuid, group.uuid)
        return {"op": "add_group", "group_uuid": group.uuid, "node_uuids": node_uuids}

    def _op_update_group(
        self,
        document: DocumentModel,
        operation: dict[str, Any],
        client_ids: dict[str, str],
    ) -> dict[str, Any]:
        group_uuid = str(operation.get("group_uuid", operation.get("uuid", ""))).strip()
        group = self._group(document, group_uuid)
        if group is None:
            raise ToolServiceError("GROUP_NOT_FOUND", f"Group not found: {group_uuid}")
        changed: list[str] = []
        for key in (
            "title",
            "theme_body_color",
            "theme_border_color",
            "theme_text_color",
        ):
            if key in operation:
                setattr(group, key, str(operation[key] or ""))
                changed.append(key)
        if "node_uuids" in operation:
            raw_nodes = operation["node_uuids"]
            if not isinstance(raw_nodes, list):
                raise ToolServiceError("INVALID_ARGUMENT", "node_uuids must be an array")
            requested = [
                self._required_node(document, raw, client_ids).uuid
                for raw in raw_nodes
            ]
            for current in document.groups:
                current.node_uuids = [
                    node_uuid
                    for node_uuid in current.node_uuids
                    if node_uuid not in requested or current.uuid == group.uuid
                ]
            group.node_uuids = list(dict.fromkeys(requested))
            changed.append("node_uuids")
        if "position" in operation or "size" in operation:
            if "position" not in operation or "size" not in operation:
                raise ToolServiceError(
                    "INVALID_ARGUMENT",
                    "Group position and size must be updated together",
                )
            x, y = _position(operation["position"], field_name="group position")
            size = operation["size"]
            if not isinstance(size, dict):
                raise ToolServiceError("INVALID_ARGUMENT", "Group size must be an object")
            width = _bounded_number(size.get("width"), field_name="group width")
            height = _bounded_number(size.get("height"), field_name="group height")
            group.ui_position = {"x": x, "y": y}
            group.ui_size = {"width": width, "height": height}
            changed.extend(["position", "size"])
        return {"op": "update_group", "group_uuid": group.uuid, "changed": changed}

    def _op_delete_group(
        self,
        document: DocumentModel,
        operation: dict[str, Any],
        _client_ids: dict[str, str],
    ) -> dict[str, Any]:
        group_uuid = str(operation.get("group_uuid", operation.get("uuid", ""))).strip()
        if self._group(document, group_uuid) is None:
            raise ToolServiceError("GROUP_NOT_FOUND", f"Group not found: {group_uuid}")
        document.groups = [group for group in document.groups if group.uuid != group_uuid]
        return {"op": "delete_group", "group_uuid": group_uuid}

    def _set_group_membership(
        self,
        document: DocumentModel,
        node_uuid: str,
        group_uuid: str | None,
    ) -> None:
        if group_uuid and self._group(document, group_uuid) is None:
            raise ToolServiceError("GROUP_NOT_FOUND", f"Group not found: {group_uuid}")
        for group in document.groups:
            group.node_uuids = [
                current for current in group.node_uuids if current != node_uuid
            ]
            if group.uuid == group_uuid:
                group.node_uuids.append(node_uuid)

    @staticmethod
    def _plan_topics(document: DocumentModel) -> list[Any]:
        plan_layout = getattr(document, "plan_layout", None)
        topics = getattr(plan_layout, "topics", None)
        if not isinstance(topics, list):
            raise ToolServiceError(
                "PLAN_MODE_UNAVAILABLE",
                "This document model does not support plan topics",
            )
        return topics

    def _append_plan_topic(
        self,
        document: DocumentModel,
        node_uuid: str,
        values: dict[str, Any],
        client_ids: dict[str, str],
    ) -> Any:
        topics = self._plan_topics(document)
        if any(topic.node_uuid == node_uuid for topic in topics):
            raise ToolServiceError(
                "INVALID_PLAN_TOPIC",
                f"Plan topic already exists for node: {node_uuid}",
            )
        try:
            from .models import PlanTopicRecord
        except ImportError as exc:
            raise ToolServiceError(
                "PLAN_MODE_UNAVAILABLE",
                "This build does not support plan topics",
            ) from exc
        parent_uuid = self._resolve_ref(
            values.get("parent_uuid"),
            client_ids,
            allow_none=True,
        )
        if parent_uuid and self._node(document, parent_uuid) is None:
            raise ToolServiceError(
                "NODE_NOT_FOUND",
                f"Plan parent node not found: {parent_uuid}",
            )
        siblings = [topic for topic in topics if topic.parent_uuid == parent_uuid]
        raw_order = values.get("order")
        if raw_order is None:
            raw_order = len(siblings)
        try:
            order = max(0, int(raw_order))
        except (TypeError, ValueError) as exc:
            raise ToolServiceError("INVALID_ARGUMENT", "Plan order must be an integer") from exc
        topic = PlanTopicRecord(
            node_uuid=node_uuid,
            parent_uuid=parent_uuid,
            order=order,
            plan_title=str(values.get("plan_title", values.get("title", "")) or ""),
            collapsed=bool(values.get("collapsed", False)),
            branch_color=str(values.get("branch_color") or ""),
            formalization_state=str(
                values.get("formalization_state", "formal") or "formal"
            ),
            structure_dirty=bool(values.get("structure_dirty", False)),
        )
        topics.append(topic)
        self._normalize_plan_orders(topics)
        self._validate_plan_tree(document)
        return topic

    def _op_add_plan_topic(
        self,
        document: DocumentModel,
        operation: dict[str, Any],
        client_ids: dict[str, str],
    ) -> dict[str, Any]:
        parent_uuid = self._resolve_ref(
            operation.get("parent_uuid"),
            client_ids,
            allow_none=True,
        )
        parent = (
            self._required_node(document, parent_uuid, client_ids)
            if parent_uuid is not None
            else None
        )
        title = str(operation.get("title") or "新主题").strip() or "新主题"
        raw_position = operation.get(
            "position",
            {
                "x": (
                    float(parent.ui_position["x"]) + 460.0
                    if parent is not None
                    else 460.0
                ),
                "y": (
                    float(parent.ui_position["y"])
                    if parent is not None
                    else 0.0
                ),
            },
        )
        x, y = _position(raw_position)
        node = create_node(
            self.controller.schema,
            document,
            "PlanPlaceholder",
            (x, y),
        )
        requested_uuid = str(operation.get("uuid") or "").strip()
        if requested_uuid:
            if not _SAFE_ID.fullmatch(requested_uuid):
                raise ToolServiceError("INVALID_ARGUMENT", "Node UUID is invalid")
            node.uuid = requested_uuid
        if self._node(document, node.uuid) is not None:
            raise ToolServiceError(
                "DUPLICATE_NODE_UUID",
                f"Node UUID already exists: {node.uuid}",
            )
        node.fields.update(placeholder_fields_for_title(title))
        apply_node_appearance_defaults(self.controller.schema, node)
        document.nodes.append(node)
        self._append_plan_topic(
            document,
            node.uuid,
            {
                "parent_uuid": parent.uuid if parent is not None else None,
                "order": operation.get("order"),
                "plan_title": title,
                "collapsed": operation.get("collapsed", False),
                "branch_color": operation.get("branch_color", ""),
                "formalization_state": "draft",
                "structure_dirty": True,
            },
            client_ids,
        )
        if parent is not None:
            self._validate_new_connection(document, parent, node)
            document.connections.append(
                ConnectionRecord(from_uuid=parent.uuid, to_uuid=node.uuid)
            )
        client_id = str(operation.get("client_id") or "").strip()
        if client_id:
            if client_id in client_ids:
                raise ToolServiceError(
                    "DUPLICATE_CLIENT_ID",
                    f"client_id is already used: {client_id}",
                )
            client_ids[client_id] = node.uuid
        return {
            "op": "add_plan_topic",
            "node_uuid": node.uuid,
            "parent_uuid": parent.uuid if parent is not None else None,
            "client_id": client_id or None,
        }

    def _plan_topic(self, document: DocumentModel, node_uuid: str) -> Any:
        topic = next(
            (
                topic
                for topic in self._plan_topics(document)
                if topic.node_uuid == node_uuid
            ),
            None,
        )
        if topic is None:
            raise ToolServiceError(
                "PLAN_TOPIC_NOT_FOUND",
                f"Plan topic not found: {node_uuid}",
            )
        return topic

    def _update_plan_topic_values(
        self,
        document: DocumentModel,
        node_uuid: str,
        values: dict[str, Any],
        client_ids: dict[str, str],
    ) -> list[str]:
        topic = self._plan_topic(document, node_uuid)
        node = self._node(document, node_uuid)
        has_listener = node is not None and (node.type == "Listener" or node.listener_graph is not None)
        changed: list[str] = []
        if "plan_title" in values or "title" in values:
            topic.plan_title = str(
                values.get("plan_title", values.get("title", "")) or ""
            )
            if not has_listener and topic.formalization_state in {"formal", "virtual", "materialized"}:
                topic.formalization_state = "draft"
            if node is not None and node.type == "PlanPlaceholder":
                node.fields.update(
                    placeholder_fields_for_title(topic.plan_title)
                )
            changed.append("plan_title")
        if "collapsed" in values:
            topic.collapsed = bool(values["collapsed"])
            changed.append("collapsed")
        if "branch_color" in values:
            if has_listener:
                raise ToolServiceError("FIELD_NOT_WRITABLE", "Listener subgraphs cannot be retyped through plan colors",
                                       {"field": "branch_color", "node_uuid": node_uuid})
            topic.branch_color = str(values["branch_color"] or "")
            topic.formalization_state = "draft"
            changed.append("branch_color")
        if "parent_uuid" in values:
            raise ToolServiceError(
                "FIELD_NOT_WRITABLE",
                "Use reparent_plan_topic to change a plan parent",
                {"field": "parent_uuid", "node_uuid": node_uuid},
            )
        if "order" in values:
            try:
                topic.order = max(0, int(values["order"]))
            except (TypeError, ValueError) as exc:
                raise ToolServiceError(
                    "INVALID_ARGUMENT",
                    "Plan order must be an integer",
                ) from exc
            self._mark_plan_subtree_structure_dirty(
                self._plan_topics(document),
                node_uuid,
            )
            changed.append("order")
        self._normalize_plan_orders(self._plan_topics(document))
        self._validate_plan_tree(document)
        return changed

    def _op_update_plan_topic(
        self,
        document: DocumentModel,
        operation: dict[str, Any],
        client_ids: dict[str, str],
    ) -> dict[str, Any]:
        node = self._required_node(
            document,
            operation.get("node_uuid"),
            client_ids,
        )
        if node.locked:
            raise ToolServiceError(
                "NODE_LOCKED",
                f"Node is locked: {node.uuid}",
                {"node_uuid": node.uuid},
            )
        values = operation.get("values", operation)
        changed = self._update_plan_topic_values(
            document,
            node.uuid,
            dict(values),
            client_ids,
        )
        return {
            "op": "update_plan_topic",
            "node_uuid": node.uuid,
            "changed": changed,
        }

    def _op_reparent_plan_topic(
        self,
        document: DocumentModel,
        operation: dict[str, Any],
        client_ids: dict[str, str],
    ) -> dict[str, Any]:
        node = self._required_node(
            document,
            operation.get("node_uuid"),
            client_ids,
        )
        if "new_parent_uuid" not in operation:
            raise ToolServiceError(
                "INVALID_ARGUMENT",
                "new_parent_uuid is required (use null for the unconnected branch)",
            )
        parent_uuid = self._resolve_ref(
            operation.get("new_parent_uuid"),
            client_ids,
            allow_none=True,
        )
        parent = (
            self._required_node(document, parent_uuid, client_ids)
            if parent_uuid is not None
            else None
        )
        if node.uuid == self._idle0_uuid(document):
            raise ToolServiceError("FIELD_NOT_WRITABLE", "Idle0 cannot be reparented")
        if node.locked:
            raise ToolServiceError(
                "NODE_LOCKED",
                f"Node is locked: {node.uuid}",
                {"node_uuid": node.uuid},
            )
        topic = self._plan_topic(document, node.uuid)
        old_parent = topic.parent_uuid
        topic.parent_uuid = parent.uuid if parent is not None else None
        self._mark_plan_subtree_structure_dirty(
            self._plan_topics(document),
            node.uuid,
        )
        if "order" in operation:
            try:
                topic.order = max(0, int(operation["order"]))
            except (TypeError, ValueError) as exc:
                raise ToolServiceError(
                    "INVALID_ARGUMENT",
                    "Plan order must be an integer",
                ) from exc
        self._normalize_plan_orders(self._plan_topics(document))
        self._validate_plan_tree(document)
        # Replace only the primary formal edge; reference edges stay intact.
        if old_parent:
            document.connections = [
                edge
                for edge in document.connections
                if not (edge.from_uuid == old_parent and edge.to_uuid == node.uuid)
            ]
        if parent is not None and not any(
            edge.from_uuid == parent.uuid and edge.to_uuid == node.uuid
            for edge in document.connections
        ):
            self._validate_new_connection(document, parent, node)
            document.connections.append(
                ConnectionRecord(from_uuid=parent.uuid, to_uuid=node.uuid)
            )
        return {
            "op": "reparent_plan_topic",
            "node_uuid": node.uuid,
            "old_parent_uuid": old_parent,
            "new_parent_uuid": parent.uuid if parent is not None else None,
        }

    def _op_delete_plan_subtree(
        self,
        document: DocumentModel,
        operation: dict[str, Any],
        client_ids: dict[str, str],
    ) -> dict[str, Any]:
        node = self._required_node(
            document,
            operation.get("node_uuid"),
            client_ids,
        )
        if node.uuid == self._idle0_uuid(document):
            raise ToolServiceError(
                "FIELD_NOT_WRITABLE",
                "Idle0 cannot be deleted",
            )
        topics = self._plan_topics(document)
        children: dict[str | None, list[str]] = defaultdict(list)
        for topic in topics:
            children[topic.parent_uuid].append(topic.node_uuid)
        removed: set[str] = set()
        pending = [node.uuid]
        while pending:
            current = pending.pop()
            if current in removed:
                continue
            record = self._node(document, current)
            if record is not None and record.locked:
                raise ToolServiceError(
                    "NODE_LOCKED",
                    f"Plan subtree contains a locked node: {current}",
                    {"node_uuid": current},
                )
            removed.add(current)
            pending.extend(children.get(current, ()))
        document.nodes = [
            current for current in document.nodes if current.uuid not in removed
        ]
        document.connections = [
            edge
            for edge in document.connections
            if edge.from_uuid not in removed and edge.to_uuid not in removed
        ]
        for group in document.groups:
            group.node_uuids = [
                current for current in group.node_uuids if current not in removed
            ]
        self._remove_plan_topics(document, removed)
        return {
            "op": "delete_plan_subtree",
            "root_uuid": node.uuid,
            "deleted_node_uuids": sorted(removed),
            "deleted_count": len(removed),
        }

    @staticmethod
    def _remove_plan_topics(document: DocumentModel, node_uuids: set[str]) -> None:
        plan_layout = getattr(document, "plan_layout", None)
        topics = getattr(plan_layout, "topics", None)
        if isinstance(topics, list):
            plan_layout.topics = [
                topic for topic in topics if topic.node_uuid not in node_uuids
            ]

    @staticmethod
    def _normalize_plan_orders(topics: list[Any]) -> None:
        siblings: dict[str | None, list[Any]] = defaultdict(list)
        for topic in topics:
            siblings[topic.parent_uuid].append(topic)
        for group in siblings.values():
            group.sort(key=lambda topic: (int(topic.order), topic.node_uuid))
            for index, topic in enumerate(group):
                topic.order = index

    @staticmethod
    def _mark_plan_subtree_structure_dirty(
        topics: list[Any],
        node_uuid: str,
    ) -> None:
        children: dict[str, list[str]] = defaultdict(list)
        for topic in topics:
            if topic.parent_uuid is not None:
                children[topic.parent_uuid].append(topic.node_uuid)
        pending = [node_uuid]
        dirty: set[str] = set()
        while pending:
            current = pending.pop()
            if current in dirty:
                continue
            dirty.add(current)
            pending.extend(children.get(current, ()))
        for topic in topics:
            if topic.node_uuid in dirty:
                topic.structure_dirty = True

    @staticmethod
    def _idle0_uuid(document: DocumentModel) -> str:
        idle0 = next((node for node in document.nodes if node.type == "Idle0"), None)
        return idle0.uuid if idle0 else ""

    def _validate_plan_tree(self, document: DocumentModel) -> None:
        topics = self._plan_topics(document)
        topic_ids = {topic.node_uuid for topic in topics}
        node_ids = {node.uuid for node in document.nodes}
        for topic in topics:
            if topic.node_uuid not in node_ids:
                raise ToolServiceError(
                    "INVALID_PLAN_TOPIC",
                    f"Plan topic references a missing node: {topic.node_uuid}",
                )
            if topic.parent_uuid is not None and topic.parent_uuid not in topic_ids:
                raise ToolServiceError(
                    "INVALID_PLAN_TOPIC",
                    f"Plan topic parent is missing: {topic.parent_uuid}",
                )
        parents = {topic.node_uuid: topic.parent_uuid for topic in topics}
        for start in parents:
            seen: set[str] = set()
            current: str | None = start
            while current is not None:
                if current in seen:
                    raise ToolServiceError(
                        "INVALID_PLAN_TOPIC",
                        "Plan primary parents would form a cycle",
                        {"node_uuid": start},
                    )
                seen.add(current)
                current = parents.get(current)

    def _validate_structure(self, document: DocumentModel) -> list[dict[str, Any]]:
        errors: list[dict[str, Any]] = []
        node_ids: set[str] = set()
        for node in document.nodes:
            if not isinstance(node.uuid, str) or not _SAFE_ID.fullmatch(node.uuid):
                errors.append({"code": "INVALID_NODE_UUID", "node_uuid": node.uuid})
            elif node.uuid in node_ids:
                errors.append({"code": "DUPLICATE_NODE_UUID", "node_uuid": node.uuid})
            node_ids.add(node.uuid)
            if node.type not in self.controller.schema.nodes:
                errors.append(
                    {
                        "code": "INVALID_NODE_TYPE",
                        "node_uuid": node.uuid,
                        "node_type": node.type,
                    }
                )
            try:
                _position(node.ui_position)
            except ToolServiceError:
                errors.append({"code": "INVALID_POSITION", "node_uuid": node.uuid})
            if node.listener_graph is not None:
                definition = self.controller.schema.nodes.get(node.type)
                if node.type != "Listener" and (definition is None or definition.category != "function"):
                    errors.append({"code": "INVALID_LISTENER_OWNER", "node_uuid": node.uuid})
                try:
                    node.listener_graph.to_payload()
                except (ValueError, TypeError, AttributeError):
                    errors.append({"code": "INVALID_LISTENER_GRAPH", "node_uuid": node.uuid})
            elif node.type == "Listener":
                errors.append({"code": "MISSING_LISTENER_GRAPH", "node_uuid": node.uuid})
        idle0 = [node for node in document.nodes if node.type == "Idle0"]
        if len(idle0) != 1:
            errors.append({"code": "INVALID_ROOT_COUNT", "count": len(idle0)})

        edge_pairs: set[tuple[str, str]] = set()
        root_ids = {node.uuid for node in idle0}
        listener_ids = {node.uuid for node in document.nodes if node.type == "Listener"}
        for edge in document.connections:
            pair = (edge.from_uuid, edge.to_uuid)
            if edge.from_uuid not in node_ids or edge.to_uuid not in node_ids:
                errors.append(
                    {
                        "code": "MISSING_CONNECTION_NODE",
                        "from_uuid": edge.from_uuid,
                        "to_uuid": edge.to_uuid,
                    }
                )
            elif (edge.from_uuid == edge.to_uuid or edge.to_uuid in root_ids
                  or edge.from_uuid in listener_ids or edge.to_uuid in listener_ids):
                errors.append(
                    {
                        "code": "INVALID_CONNECTION",
                        "from_uuid": edge.from_uuid,
                        "to_uuid": edge.to_uuid,
                    }
                )
            elif pair in edge_pairs:
                errors.append(
                    {
                        "code": "DUPLICATE_CONNECTION",
                        "from_uuid": edge.from_uuid,
                        "to_uuid": edge.to_uuid,
                    }
                )
            edge_pairs.add(pair)

        group_ids: set[str] = set()
        grouped_nodes: set[str] = set()
        for group in document.groups:
            if group.uuid in group_ids:
                errors.append({"code": "DUPLICATE_GROUP_UUID", "group_uuid": group.uuid})
            group_ids.add(group.uuid)
            for node_uuid in group.node_uuids:
                if node_uuid not in node_ids:
                    errors.append(
                        {
                            "code": "MISSING_GROUP_NODE",
                            "group_uuid": group.uuid,
                            "node_uuid": node_uuid,
                        }
                    )
                elif node_uuid in grouped_nodes:
                    errors.append(
                        {
                            "code": "MULTIPLE_GROUP_MEMBERSHIP",
                            "node_uuid": node_uuid,
                        }
                    )
                grouped_nodes.add(node_uuid)
        try:
            validate_canvas_strokes(document.canvas_strokes)
            validate_canvas_strokes(document.plan_canvas_strokes)
        except ValueError:
            errors.append({"code": "INVALID_CANVAS_STROKES"})
        if hasattr(document, "plan_layout"):
            try:
                self._validate_plan_tree(document)
            except ToolServiceError as exc:
                errors.append({"code": exc.code, "message": exc.message})
        return errors

    def _prepare_optimized_layout(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        horizontal = _bounded_number(
            arguments.get("horizontal_spacing", 460.0),
            field_name="horizontal_spacing",
            minimum=120.0,
        )
        vertical = _bounded_number(
            arguments.get("vertical_spacing", 220.0),
            field_name="vertical_spacing",
            minimum=80.0,
        )
        document = copy.deepcopy(self.controller.document)
        nodes_by_uuid = {node.uuid: node for node in document.nodes}
        adjacency: dict[str, list[str]] = defaultdict(list)
        incoming: dict[str, int] = defaultdict(int)
        for edge in document.connections:
            if edge.from_uuid in nodes_by_uuid and edge.to_uuid in nodes_by_uuid:
                adjacency[edge.from_uuid].append(edge.to_uuid)
                incoming[edge.to_uuid] += 1
        for values in adjacency.values():
            values.sort()
        root_ids = [
            node.uuid for node in document.nodes if node.type == "Idle0"
        ] + sorted(
            node.uuid
            for node in document.nodes
            if node.type != "Idle0" and incoming[node.uuid] == 0
        )
        depths: dict[str, int] = {}
        queue: deque[tuple[str, int]] = deque((node_uuid, 0) for node_uuid in root_ids)
        while queue:
            node_uuid, depth = queue.popleft()
            if node_uuid in depths:
                continue
            depths[node_uuid] = depth
            for child in adjacency.get(node_uuid, ()):
                queue.append((child, depth + 1))
        fallback_depth = max(depths.values(), default=-1) + 1
        for node_uuid in sorted(nodes_by_uuid):
            depths.setdefault(node_uuid, fallback_depth)

        by_depth: dict[int, list[NodeRecord]] = defaultdict(list)
        for node_uuid, depth in depths.items():
            by_depth[depth].append(nodes_by_uuid[node_uuid])
        changes: list[dict[str, Any]] = []
        for depth, nodes in sorted(by_depth.items()):
            nodes.sort(
                key=lambda node: (
                    float(node.ui_position.get("y", 0.0)),
                    node.uuid,
                )
            )
            total_height = (len(nodes) - 1) * vertical
            for index, node in enumerate(nodes):
                if node.locked or node.sequence_locked:
                    continue
                new_position = (
                    72.0 + depth * horizontal,
                    72.0 - total_height / 2.0 + index * vertical,
                )
                old_position = (
                    float(node.ui_position["x"]),
                    float(node.ui_position["y"]),
                )
                if old_position == new_position:
                    continue
                node.ui_position = {
                    "x": new_position[0],
                    "y": new_position[1],
                }
                changes.append(
                    {
                        "node_uuid": node.uuid,
                        "from": {"x": old_position[0], "y": old_position[1]},
                        "to": {"x": new_position[0], "y": new_position[1]},
                    }
                )
        return {"document": document, "moves": changes}

    def _optimize_layout(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._expect_revision(arguments)
        prepared = self._prepare_optimized_layout(arguments)
        if prepared["moves"]:
            self.controller.undo_stack.push(
                _DocumentSnapshotCommand(
                    self.controller,
                    self.controller.document,
                    prepared["document"],
                    "AI 整理节点布局",
                )
            )
        return {
            "moved_count": len(prepared["moves"]),
            "moves": prepared["moves"],
        }

    def _switch_graph_view(self, arguments: dict[str, Any]) -> dict[str, Any]:
        view = str(arguments.get("view") or "").strip().lower()
        if view not in _SUPPORTED_VIEWS:
            raise ToolServiceError(
                "INVALID_ARGUMENT",
                "view must be either formal or plan",
            )
        self.viewSwitchRequested.emit(view)
        # The root window normally echoes the actual state via set_current_view.
        # Updating eagerly keeps headless/test use deterministic.
        self.set_current_view(view)
        return {"view": self._current_view}

    def _undo_last_edit(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._expect_revision(arguments)
        stack = self.controller.undo_stack
        if not stack.canUndo():
            raise ToolServiceError("NOTHING_TO_UNDO", "There is no edit to undo")
        label = stack.undoText()
        stack.undo()
        return {"undone": label}

    def _redo_last_edit(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._expect_revision(arguments)
        stack = self.controller.undo_stack
        if not stack.canRedo():
            raise ToolServiceError("NOTHING_TO_REDO", "There is no edit to redo")
        label = stack.redoText()
        stack.redo()
        return {"redone": label}

    def _save_current_graph(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._expect_revision(arguments)
        if not self.controller.document.path:
            raise ToolServiceError(
                "DOCUMENT_NOT_SAVED",
                "The current graph has no JSON path; save it in the editor first",
            )
        try:
            saved = self.controller.save_document()
        except (OSError, UnicodeError, TypeError, ValueError) as exc:
            raise ToolServiceError(
                "SAVE_FAILED",
                "The current graph could not be saved",
                {"exception_type": type(exc).__name__},
            ) from exc
        if not saved:
            raise ToolServiceError("SAVE_FAILED", "The current graph could not be saved")
        return {"saved": True, "document_name": Path(saved).name}

    def _export_current_graph_csv(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._expect_revision(arguments)
        workspace = self._workspace_root()
        try:
            output = export_current_document_csv(
                self.controller.schema,
                self.controller.document,
                workspace,
                template_search_roots=(
                    workspace,
                    Path(__file__).resolve().parent.parent,
                ),
            )
        except (OSError, UnicodeError, TypeError, ValueError, csv.Error) as exc:
            raise ToolServiceError(
                "CSV_EXPORT_FAILED",
                "The current graph CSV could not be written",
                {"exception_type": type(exc).__name__},
            ) from exc
        return {"exported": True, "resource_name": output.name}

    @staticmethod
    def _stabilized_graph_edit_arguments(
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Give preview-created records stable UUIDs used by the commit."""

        stabilized = copy.deepcopy(arguments)
        operations = stabilized.get("operations")
        if not isinstance(operations, list):
            return stabilized
        for operation in operations:
            if not isinstance(operation, dict) or operation.get("uuid"):
                continue
            name = EditorToolService._operation_name(operation)
            if name in {"add_node", "node_add", "add_group", "group_add",
                        "add_plan_topic", "plan_add"}:
                operation["uuid"] = new_uuid()
        return stabilized

    def invoke_prepared_preview(self, token: str) -> dict[str, Any]:
        """Commit a single-use, revision-bound graph preview."""

        try:
            prepared_entry = self._prepared_previews.get(str(token))
            if prepared_entry is None:
                raise ToolServiceError(
                    "PREVIEW_EXPIRED",
                    "The confirmed preview is missing, expired, or already used",
                )
            self._finish_pending_input()
            if prepared_entry["revision"] != self._revision:
                raise ToolServiceError(
                    "REVISION_CONFLICT",
                    "The graph changed after the preview was created",
                    {
                        "expected_revision": prepared_entry["revision"],
                        "actual_revision": self._revision,
                    },
                )
            self._prepared_previews.pop(str(token), None)
            prepared = prepared_entry["prepared"]
            self.controller.undo_stack.push(
                _DocumentSnapshotCommand(
                    self.controller,
                    self.controller.document,
                    prepared["document"],
                    "AI 批量编辑图表",
                )
            )
            return _json_copy(
                {
                    "ok": True,
                    "revision": self._revision,
                    "result": {
                        "applied": True,
                        "changes": prepared["changes"],
                        "client_ids": prepared["client_ids"],
                        "validation_issues": prepared["validation_issues"],
                    },
                }
            )
        except ToolServiceError as exc:
            return self._error_response(exc)
        except Exception as exc:
            return self._error_response(
                ToolServiceError(
                    "INTERNAL_ERROR",
                    "The editor could not commit the confirmed preview",
                    {"exception_type": type(exc).__name__},
                )
            )

    def discard_prepared_preview(self, token: str | None) -> None:
        if token:
            self._prepared_previews.pop(str(token), None)

    def preview_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Build a side-effect-free structured preview for a confirmation UI."""

        try:
            if name not in _CONFIRMATION_TOOLS:
                return {
                    "ok": True,
                    "revision": self._revision,
                    "result": {"confirmation_required": False},
                }
            self._finish_pending_input()
            self._expect_revision(arguments)
            if name == "apply_graph_edits":
                stabilized = self._stabilized_graph_edit_arguments(arguments)
                prepared = self._prepare_graph_edits(stabilized)
                token = secrets.token_urlsafe(24)
                while token in self._prepared_previews:
                    token = secrets.token_urlsafe(24)
                while len(self._prepared_previews) >= 32:
                    self._prepared_previews.pop(next(iter(self._prepared_previews)))
                self._prepared_previews[token] = {
                    "revision": self._revision,
                    "prepared": prepared,
                }
                result = {
                    "confirmation_required": True,
                    "kind": "graph_edits",
                    "preview_token": token,
                    "changes": prepared["changes"],
                    "client_ids": prepared["client_ids"],
                    "validation_issues": prepared["validation_issues"],
                }
            elif name == "optimize_layout":
                prepared = self._prepare_optimized_layout(arguments)
                result = {
                    "confirmation_required": True,
                    "kind": "layout",
                    "moved_count": len(prepared["moves"]),
                    "moves": prepared["moves"],
                }
            elif name == "undo_last_edit":
                result = {
                    "confirmation_required": True,
                    "kind": "undo",
                    "label": self.controller.undo_stack.undoText(),
                }
            elif name == "redo_last_edit":
                result = {
                    "confirmation_required": True,
                    "kind": "redo",
                    "label": self.controller.undo_stack.redoText(),
                }
            elif name == "save_current_graph":
                result = {
                    "confirmation_required": True,
                    "kind": "save",
                    "document_name": (
                        Path(self.controller.document.path).name
                        if self.controller.document.path
                        else ""
                    ),
                }
            else:
                result = {
                    "confirmation_required": True,
                    "kind": "csv_export",
                    "filename_pattern": (
                        "ship_l2d_export_"
                        f"{current_document_identifier(self.controller.document)}_"
                        "<YYYYMMDD_HHMMSS>[_N].csv"
                    ),
                }
            return _json_copy(
                {"ok": True, "revision": self._revision, "result": result}
            )
        except ToolServiceError as exc:
            return self._error_response(exc)
        except Exception as exc:
            return self._error_response(
                ToolServiceError(
                    "INTERNAL_ERROR",
                    "The editor could not prepare this tool call",
                    {"exception_type": type(exc).__name__},
                )
            )

    def user_rejected_result(self) -> dict[str, Any]:
        return self._error_response(
            ToolServiceError(
                "USER_REJECTED",
                "The user rejected the proposed editor change",
            )
        )
