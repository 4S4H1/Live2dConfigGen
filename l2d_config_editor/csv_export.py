"""Safe, current-document CSV export helpers.

This module intentionally has no UI dependencies.  The main window and the
tool-call service both use the same entry point so exporting from either path
has identical naming and atomic-write behaviour.
"""

from __future__ import annotations

import copy
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from .logic import csv_template_header_rows, document_to_csv_rows, write_csv_rows_atomic
from .models import DocumentModel
from .schema import EditorSchema


_WINDOWS_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_STEMS = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_DEFAULT_UNSAVED_STEM = "未命名图表"
_MAX_IDENTIFIER_LENGTH = 96


def sanitize_csv_identifier(value: object, *, fallback: str = _DEFAULT_UNSAVED_STEM) -> str:
    """Return a Windows-safe chart identifier suitable for an export filename."""

    text = str(value or "").strip()
    text = _WINDOWS_INVALID_FILENAME_CHARS.sub("_", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    text = re.sub(r"_+", "_", text)
    if not text:
        text = fallback
    # Windows treats the portion before the first dot as a device name.
    if text.split(".", 1)[0].upper() in _WINDOWS_RESERVED_STEMS:
        text = f"_{text}"
    text = text[:_MAX_IDENTIFIER_LENGTH].rstrip(" .")
    return text or fallback


def current_document_identifier(document: DocumentModel) -> str:
    """Resolve JSON stem -> CharName -> the localized unsaved fallback."""

    path_text = str(document.path or "").strip()
    if path_text:
        stem = Path(path_text).stem
        if stem.strip():
            return sanitize_csv_identifier(stem)
    char_name = str(getattr(document.meta, "CharName", "") or "").strip()
    if char_name:
        return sanitize_csv_identifier(char_name)
    return _DEFAULT_UNSAVED_STEM


def build_current_csv_export_filename(
    document: DocumentModel,
    *,
    timestamp: datetime | None = None,
    collision_index: int = 1,
) -> str:
    """Build the stable 1.2 export filename without consulting the filesystem."""

    resolved_timestamp = timestamp or datetime.now()
    identifier = current_document_identifier(document)
    suffix = "" if collision_index <= 1 else f"_{int(collision_index)}"
    return (
        f"ship_l2d_export_{identifier}_"
        f"{resolved_timestamp.strftime('%Y%m%d_%H%M%S')}{suffix}.csv"
    )


def _reserve_output_path(
    workspace: Path,
    document: DocumentModel,
    *,
    timestamp: datetime,
) -> tuple[Path, Path]:
    """Reserve a unique name without making a partial CSV visible.

    The sidecar lock is deliberately not the destination itself: a crash can
    leave a harmless lock file, but consumers will never observe a zero-byte or
    half-written ``.csv``.
    """

    collision_index = 1
    while True:
        candidate = workspace / build_current_csv_export_filename(
            document,
            timestamp=timestamp,
            collision_index=collision_index,
        )
        lock_path = workspace / f".{candidate.name}.lock"
        if candidate.exists():
            collision_index += 1
            continue
        try:
            descriptor = os.open(
                lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            collision_index += 1
            continue
        os.close(descriptor)
        # A different writer may have activated the final path just before our
        # lock was created (for example an older editor without sidecar locks).
        if candidate.exists():
            try:
                lock_path.unlink()
            except OSError:
                pass
            collision_index += 1
            continue
        return candidate, lock_path


def export_current_document_csv(
    schema: EditorSchema,
    document: DocumentModel,
    workspace: str | Path,
    *,
    template_search_roots: Iterable[str | Path] = (),
    _now: Callable[[], datetime] | None = None,
) -> Path:
    """Atomically export only ``document`` to a new CSV in ``workspace``.

    ``document_to_csv_rows`` refreshes generated sequence fields, so it is run
    against a deep copy.  Export therefore cannot mutate the live controller
    model or create an undo command.
    """

    root = Path(workspace).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"CSV export workspace does not exist: {root}")

    snapshot = copy.deepcopy(document)
    timestamp = (_now or datetime.now)()
    header_rows = csv_template_header_rows(schema, tuple(template_search_roots))
    preview_rows = document_to_csv_rows(schema, snapshot)
    # Finish every fallible conversion before reserving a target name.
    output_path, lock_path = _reserve_output_path(root, snapshot, timestamp=timestamp)

    try:
        rows = header_rows + [
            [row.values.get(column, "") for column in schema.csv_columns]
            for row in preview_rows
        ]
        return write_csv_rows_atomic(output_path, rows)
    finally:
        if lock_path.exists():
            try:
                lock_path.unlink()
            except OSError:
                pass
