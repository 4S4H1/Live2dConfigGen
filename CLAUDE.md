# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Install (PyQt6 only runtime dep):

```bash
python -m pip install -r requirements.txt
```

Run the editor (always invoke as a module from the repo root so the `l2d_config_editor` package resolves):

```bash
python -m l2d_config_editor.main
```

Run the full test suite:

```bash
python -m unittest discover -s tests -v
```

Run a single test:

```bash
python -m unittest tests.test_logic.SomeTestCase.test_something -v
```

Tests instantiate real `QApplication` / `MainWindow`. On non-Windows they auto-set `QT_QPA_PLATFORM=offscreen` (see `tests/test_logic.py:9` and `tests/test_perf.py:6`); on Windows they run against the real platform plugin.

Build a single-file Windows executable:

```bash
pack.bat
```

Mirrors the CI in `.github/workflows/package.yml` — uses PyInstaller with `--add-data l2d_config_editor/editor_schema.json;l2d_config_editor` so the schema gets bundled. CI matrix in `.github/workflows/ci.yml` runs tests on `windows-latest` and `macos-latest` with Python 3.11.

CLI flags handled in `main.py` (used by tests/automation, not real users):
- `--no-close-prompt` → suppress the unsaved-changes dialog
- `--auto-discard-on-close` / `--auto-save-on-close` / `--test-close-policy=discard|save` → predetermine the close-policy choice via env vars

## Architecture

Single-package PyQt6 desktop app under `l2d_config_editor/`. The split is a deliberate MVC-ish layering — pure logic at the bottom, Qt at the top — so most behavior is testable without spinning up the canvas.

**Schema-driven everything (`schema.py` + `editor_schema.json`)** — node types, fields, simple/advanced visibility, default values, CSV column mapping, validation rules, and auto-generation templates all live in `editor_schema.json`. `load_editor_schema()` parses it into frozen dataclasses (`EditorSchema`, `NodeSchema`, `FieldSchema`, `AutoRuleSpec`, `CsvMappingSpec`, `ValidationRuleSpec`). Adding a node type or field is normally a JSON edit, not a code edit. The Reload action in the menu re-parses the schema at runtime. `definitions.py` is a thin re-export shim for backwards compatibility — don't add new symbols there.

**Document model (`models.py`)** — plain dataclasses describing what gets serialized: `DocumentModel` holds `MetaRecord`, `NodeRecord`s, `ConnectionRecord`s, `GroupRecord`s, `TrashEntry`s, `CanvasViewState`, `EditorSettings`, `DocumentState`. Each `NodeRecord.fields` is the schema-defined payload; `manual_fields` tracks which keys the user overrode versus auto-generated. `clone()` methods exist because the undo stack copies records.

**Pure logic (`logic.py`, ~1.6k lines)** — load/save JSON (`load_document` / `save_document` / `export_document_dict`), `validate_document`, `apply_auto_rules` (the template-driven generator that fills `draw_able_name`, `parameter`, `action_trigger`, etc. from the schema), `document_to_csv_rows` and `export_documents_to_csv`, search, parameter-table grouping, comment appearance sync. No Qt imports here — call from tests directly. The `_table_*` and `target_idle` keys plus everything in `HIDDEN_NODE_FIELDS` are reserved/internal and shouldn't appear in user-facing UI.

**Controller (`controller.py`)** — `EditorController(QObject)` is the single source of truth at runtime. Holds `self.document`, `self.schema`, the `QUndoStack`, and broadcasts changes via pyqtSignals (`nodeAdded`, `nodeUpdated`, `connectionsChanged`, `validationChanged`, `csvPreviewChanged`, `globalModeChanged`, `groupsChanged`, …). All mutations go through `QUndoCommand`s in `commands.py` (`AddNodesCommand`, `RemoveNodesCommand`, `UpdateFieldCommand`, `UpdateManyFieldsCommand`, `MoveNodesCommand`, `SetGroupsCommand`, `UpdateEditorSettingsCommand`, …). The commands call back into `controller._insert_nodes` / `_set_field` / `_move_node` / etc. — those underscore methods are the actual mutators and must stay symmetric with their command pair so undo/redo round-trips cleanly. **Don't mutate `controller.document` directly from views; always push a command.**

**View layer** is split:
- `canvas.py` (~3.4k lines) — `GridScene` + `NodeCanvasView` (QGraphicsScene/QGraphicsView). Renders nodes, pins, bezier connections, group/parameter-table overlays, marquee selection, middle-mouse pan, scroll-zoom, drag-to-quick-create.
- `widgets.py` — reusable `NodeFormWidget`, commit-on-blur inputs (`CommitLineEdit`, `CommitComboBox`, `CommitPlainTextEdit`, `NumericLineEdit`), color pickers, `NodeAppearanceDialog` / `CommentAppearanceDialog`, `ValidationSummaryWidget`. Used both inside canvas nodes and in the right-side Inspector.
- `main_window.py` (~2.3k lines) — `MainWindow` wires the file tree, canvas, Inspector, toolbar/menus, dialogs (CSV preview, search, settings, schema reload), close-prompt logic, and persists window state via `QSettings`.
- `styles.py` — global stylesheet applied in `main.py`.
- `perf_tools.py` — `PerformanceRecorder` (context-manager `measure(...)` calls instrument `controller.*` and `canvas.*` operations), plus `PerformanceToolDialog` and `PerformanceScenarioRunner` for benchmarking. Disabled by default.

**Display modes** — `EditorPreferences.global_mode` is `"simple"` or `"advanced"`. `field_visible()` in `schema.py` enforces the rule that **advanced mode shows everything visible in simple mode** (the comment in that file calls out the historical bug it fixes — don't regress it). `interaction_creation_mode` is `"auto"` vs `"manual"` and gates whether dragging from a pin auto-creates the next node.

**Meta-ready gate** — `Initial` node fields (`author`, `ship_skin_id`, `memo`, `CharName`) must be filled before other nodes/connections can be created. `DocumentState.is_meta_ready` and `meta_missing_fields` drive UI blocking and the `metaActionBlocked` signal.

**Workspace root** — the editor lists JSON files under one directory. `controller.set_workspace_root(...)` must stay in sync with `MainWindow.workdir`. When frozen by PyInstaller, `_project_root()` in `main.py` resolves to the directory of the `.exe`, not the bundled package — that's intentional so end users keep configs next to the executable.

## Notes specific to this repo

- User-facing strings, `README.md`, `使用说明.md`, and `编辑器需求.md` are in Chinese. Keep new UI strings in Chinese to match.
- `CSVtemplate.csv` and `TestConfig.json` at the repo root are sample fixtures, not application data.
- `changes.patch` and `design/` contain historical/reference material — not part of the build.
