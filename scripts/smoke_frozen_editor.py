"""Smoke-test the frozen editor in isolated settings before signing a release.

Verify startup, actual JSON activation, and single-instance file handoff.
A missing/incompatible DLL fails closed instead of producing an unusable installer.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from PySide6.QtCore import QCoreApplication, QSettings
from l2d_config_editor.logic import create_document, save_document
from l2d_config_editor.schema import load_editor_schema

app = QCoreApplication([])
executable = root / 'dist/pyinstaller/L2DConfigEditor/L2DConfigEditor.exe'
with tempfile.TemporaryDirectory(prefix='l2d-frozen-smoke-') as temporary:
    sandbox = Path(temporary)
    settings_dir = sandbox / 'settings'
    (settings_dir / '4S4H1').mkdir(parents=True)
    settings = QSettings(str(settings_dir / '4S4H1/L2DConfigEditor.ini'), QSettings.Format.IniFormat)
    settings.setValue('settings/migrated_from_openai_v1', True)
    settings.setValue('updates/last_check_at', datetime.now(timezone.utc).isoformat())
    settings.sync()
    schema = load_editor_schema()
    document = create_document(schema)
    first, second = sandbox / 'first.json', sandbox / 'second.json'
    save_document(schema, document, first)
    save_document(schema, document, second)
    environment = dict(os.environ)
    environment.update({
        'QT_QPA_PLATFORM': 'offscreen',
        'USERNAME': 'L2D-QA-' + uuid.uuid4().hex,
        'L2D_CONFIG_EDITOR_SETTINGS_DIR': str(settings_dir),
        'L2D_CONFIG_EDITOR_TEST_CLOSE_EVENT_POLICY': 'discard',
    })
    process = subprocess.Popen([str(executable), str(first)], cwd=sandbox, env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
    def wait_loaded(path):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            assert process.poll() is None, f'editor exited: {process.returncode}'
            settings.sync()
            if Path(str(settings.value('last_document_path', ''))) == path:
                return
            time.sleep(0.1)
        raise AssertionError(f'frozen editor did not open {path.name}')
    try:
        wait_loaded(first)
        duplicate = subprocess.run([str(executable), str(second)], cwd=sandbox, env=environment, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        assert duplicate.returncode == 0
        wait_loaded(second)
        result = {'executable': str(executable), 'startup': 'passed', 'load_json': 'passed', 'second_instance_file_handoff': 'passed'}
        (root / 'build/frozen-editor-smoke.json').write_bytes(json.dumps(result, indent=2).encode('utf-8'))
        print(json.dumps(result), flush=True)
    finally:
        process.terminate()
        process.wait(timeout=15)
os._exit(0)
