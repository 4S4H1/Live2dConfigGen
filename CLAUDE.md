# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Install the locked Python 3.13.14 / PySide6 environment:

```bash
uv sync --python .tools/python-3.13.14/python.exe --extra build --locked
```

Run the editor (always invoke as a module from the repo root so the `l2d_config_editor` package resolves):

```bash
uv run python -m l2d_config_editor.main
```

Run the full test suite:

```bash
uv run python scripts/run_tests.py
```

Run a single test:

```bash
uv run python scripts/run_tests.py --module test_logic
```

Tests instantiate real `QApplication` / `MainWindow`, with isolated settings and a separate process per module. Use `QT_QPA_PLATFORM=windows` for native font verification: the offscreen Windows plugin lacks native Chinese font fallback. The runner validates atomic result markers and test counts before bypassing Qt interpreter-shutdown issues.

Build the complete Windows installer and signed LAN update bundle:

```bash
pack.bat
```

Release builds run locally through `scripts/Build-Release.ps1`, the two `packaging/*.spec` onedir definitions and NSIS 3.12. Do not reintroduce onefile packaging or CI publishing. See `BUILDING.md` for the exact toolchain, original signing-key requirements, user-data preservation and rollback checks. Private keys remain outside the repository.

CLI flags handled in `main.py` (used by tests/automation, not real users):
- `--no-close-prompt` → suppress the unsaved-changes dialog
- `--auto-discard-on-close` / `--auto-save-on-close` / `--test-close-policy=discard|save` → predetermine the close-policy choice via env vars

## Architecture

Single-package PySide6 desktop app under `l2d_config_editor/`. The split is a deliberate MVC-ish layering — pure logic at the bottom, Qt at the top — so most behavior is testable without spinning up the canvas.

**Schema-driven everything (`schema.py` + `editor_schema.json`)** — node types, fields, simple/advanced visibility, default values, CSV column mapping, validation rules, and auto-generation templates all live in `editor_schema.json`. `load_editor_schema()` parses it into frozen dataclasses (`EditorSchema`, `NodeSchema`, `FieldSchema`, `AutoRuleSpec`, `CsvMappingSpec`, `ValidationRuleSpec`). Adding a node type or field is normally a JSON edit, not a code edit. The Reload action in the menu re-parses the schema at runtime. `definitions.py` is a thin re-export shim for backwards compatibility — don't add new symbols there.

**Document model (`models.py`)** — plain dataclasses describe metadata, nodes, connections, persistent groups, reference images, separate formal/plan stroke layers, plan layout and settings. Format v5 is current; v1-v4 migrate in memory and newer unknown formats are rejected. `manual_fields` records overrides of generated fields. Commands clone records for undo. Legacy trash data is ignored; do not reintroduce it.

**Disk baseline and history (`file_tracking.py`, `document_history.py`)** — document disk stamps/digests and `history_snapshot` are runtime save bookkeeping, not undoable graph content. Snapshot-replacement commands must preserve this bookkeeping and `history` from the live document. Optional v5 `history.version: 1` stores bounded reverse deltas (50 versions / 512 KiB), anchored in the saved graph; viewport-only saves create no revision. Only a successful atomic write advances the baseline/history. `MainWindow` watches the current file and parent directory, with a stat-only polling fallback; clean documents reload automatically, while drafts/external conflicts block overwrite saves.

**Graphical history (`history_view.py`)** — embedded versions and SVN revisions share an isolated, read-only `GraphComparisonWidget`. Do not attach preview items to the live editor controller. Added/deleted/modified objects retain their identity so the change list can locate nodes, connections, groups, images and strokes. History replay is lazy and validates each reconstructed version.

**Pure logic (`logic.py`, ~1.6k lines)** — load/save JSON (`load_document` / `save_document` / `export_document_dict`), `validate_document`, `apply_auto_rules` (the template-driven generator that fills `draw_able_name`, `parameter`, `action_trigger`, etc. from the schema), `document_to_csv_rows` and `export_documents_to_csv`, search, parameter-table grouping, comment appearance sync. No Qt imports here — call from tests directly. The `_table_*` and `target_idle` keys plus everything in `HIDDEN_NODE_FIELDS` are reserved/internal and shouldn't appear in user-facing UI.

**Controller (`controller.py`)** — `EditorController(QObject)` is the single source of truth at runtime. Holds `self.document`, `self.schema`, the `QUndoStack`, and broadcasts changes via Qt signals (`nodeAdded`, `nodeUpdated`, `connectionsChanged`, `validationChanged`, `csvPreviewChanged`, `globalModeChanged`, `groupsChanged`, …). All mutations go through `QUndoCommand`s in `commands.py` (`AddNodesCommand`, `RemoveNodesCommand`, `UpdateFieldCommand`, `UpdateManyFieldsCommand`, `MoveNodesCommand`, `SetGroupsCommand`, `UpdateEditorSettingsCommand`, …). The commands call back into `controller._insert_nodes` / `_set_field` / `_move_node` / etc. — those underscore methods are the actual mutators and must stay symmetric with their command pair so undo/redo round-trips cleanly. **Don't mutate `controller.document` directly from views; always push a command.**

**View layer** is split:
- `canvas.py`  — `GridScene` + `NodeCanvasView` (QGraphicsScene/QGraphicsView). Renders nodes, pins, bezier connections, group/parameter-table overlays, marquee selection, middle-mouse pan, scroll-zoom, drag-to-quick-create.
- `widgets.py` — reusable `NodeFormWidget`, commit-on-blur inputs (`CommitLineEdit`, `CommitComboBox`, `CommitPlainTextEdit`, `NumericLineEdit`), color pickers, `NodeAppearanceDialog` / `CommentAppearanceDialog`, `ValidationSummaryWidget`. Used both inside canvas nodes and in the right-side Inspector.
- `main_window.py`  — `MainWindow` wires the file tree, canvas, Inspector, toolbar/menus, dialogs (CSV preview, search, settings, schema reload), close-prompt logic, and persists window state via `QSettings`.
- `styles.py` — global stylesheet applied in `main.py`.
- `perf_tools.py` — `PerformanceRecorder` (context-manager `measure(...)` calls instrument `controller.*` and `canvas.*` operations), plus `PerformanceToolDialog` and `PerformanceScenarioRunner` for benchmarking. Disabled by default.

**Display modes** — the old application-wide simple/advanced controls were removed. `EditorPreferences.global_mode` and `field_visible()` remain as document/backwards-compatibility machinery and for a possible future per-node mode; do not add a new global UI entry. Expanded node cards already render the complete field form. `interaction_creation_mode` is `"auto"` vs `"manual"` and gates whether dragging from a pin auto-creates the next node.

**idle0 root + hidden metadata** — format v2 has exactly one non-copyable `Idle0` root node and no visible `Initial` metadata node. Every graph starts from idle0. Character metadata (`author`, `ship_skin_id`, `memo`, `CharName`, version, etc.) is collected by the batch base-template dialog and lives only in top-level `meta`; `default_state` is always `idle0`. `DocumentState.is_meta_ready` and `meta_missing_fields` still gate graph creation. Loading v1 migrates `Initial` in place to `Idle0`, preserving its UUID, position, and outgoing connections.

**Canvas reference images** — screenshots are normalized to bounded PNG assets and embedded under top-level `canvas_images`. Loading and controller insertion validate Base64/content, use bounded decoding, cap image count and compressed bytes, and ignore invalid legacy assets safely.

**Canvas-only assets** — groups own persistent `ui_position`/`ui_size`, may remain empty, and enforce one group per node. Reference screenshots are stored self-contained as PNG base64 in top-level `canvas_images`; they are editor-only and never become CSV rows. Their add/remove/move operations must remain undoable.

**Workspace root** — the editor lists JSON files under one directory. Keep `controller.set_workspace_root(...)` and `MainWindow.workdir` synchronized. Frozen builds resolve bundled/install assets beside the executable, but the JSON workspace must remain outside the installation roots. First launch requests a workspace; updates preserve workspace preferences and unmanaged user files.

## Notes specific to this repo

- User-facing strings, `README.md`, `使用说明.md`, and `编辑器需求.md` are in Chinese. Keep new UI strings in Chinese to match.
- `TestConfig.json` at the repo root is a sample fixture, not application data. The old CSV-to-template creation flow and `CSVtemplate.csv` were removed; CSV preview/export remain supported.
- `changes.patch` and `design/` contain historical/reference material — not part of the build.

## Editing and save invariants

- Controller signals must not overwrite an unrelated pending field draft or reset its cursor/IME. `EditorBinding.model_value` tracks the last rendered model value. Collect all form drafts before emitting a batch update.
- Background autosave waits for active editors. Explicit save/export/close commits every inline editor, including compact card fields.
- Use `QUndoStack.setClean()` / `isClean()` for save state. A numeric stack index is insufficient after undo followed by a different edit. Preserve clean state when restoring a cached document session.
- Every CSV export uses `write_csv_rows_atomic`: UTF-8 with BOM, same-directory temporary file and atomic replacement. Derive CSV rows from a copy of the live document.
- Publish the signed Host latest pointer before pruning older caches. Failed activation must preserve the old release and permit retry; a locked obsolete cache must not fail a successful publication.
