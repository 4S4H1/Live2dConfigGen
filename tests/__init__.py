"""Shared isolation for Qt-backed unit tests."""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile


_SETTINGS_ROOT = os.environ.get("L2D_CONFIG_EDITOR_SETTINGS_DIR")
if not _SETTINGS_ROOT:
    _SETTINGS_ROOT = tempfile.mkdtemp(prefix="l2d-config-editor-tests-")
    os.environ["L2D_CONFIG_EDITOR_SETTINGS_DIR"] = _SETTINGS_ROOT
    atexit.register(shutil.rmtree, _SETTINGS_ROOT, ignore_errors=True)
