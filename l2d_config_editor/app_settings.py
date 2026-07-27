"""Application settings namespace and legacy migration."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QSettings


ORGANIZATION_NAME = "4S4H1"
APPLICATION_NAME = "L2DConfigEditor"
LEGACY_ORGANIZATION_NAME = "OpenAI"
MIGRATION_MARKER = "settings/migrated_from_openai_v1"


def create_app_settings() -> QSettings:
    """Return the product settings store, migrating safe legacy preferences once."""

    settings_dir = str(os.environ.get("L2D_CONFIG_EDITOR_SETTINGS_DIR") or "").strip()
    if settings_dir:
        resolved = Path(settings_dir).resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        current_path = resolved / ORGANIZATION_NAME / f"{APPLICATION_NAME}.ini"
        legacy_path = resolved / LEGACY_ORGANIZATION_NAME / f"{APPLICATION_NAME}.ini"
        current_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        current = QSettings(str(current_path), QSettings.Format.IniFormat)
        legacy = QSettings(str(legacy_path), QSettings.Format.IniFormat)
    else:
        current = QSettings(ORGANIZATION_NAME, APPLICATION_NAME)
        legacy = QSettings(LEGACY_ORGANIZATION_NAME, APPLICATION_NAME)
    if current.value(MIGRATION_MARKER, False) in (True, "true", "1", 1):
        return current

    for key in legacy.allKeys():
        normalized = str(key).lower()
        if "trash" in normalized or current.contains(key):
            continue
        current.setValue(key, legacy.value(key))
    current.setValue(MIGRATION_MARKER, True)
    current.sync()
    return current
