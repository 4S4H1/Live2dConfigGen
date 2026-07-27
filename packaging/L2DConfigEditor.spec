# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

root = Path(SPECPATH).parent

a = Analysis(
    [str(root / "l2d_config_editor" / "main.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[
        (str(root / "l2d_config_editor" / "editor_schema.json"), "l2d_config_editor"),
        (str(root / "build" / "icons" / "L2DConfigEditor.png"), "assets"),
        (
            str(root / "l2d_config_editor" / "assets" / "release_public_key.pem"),
            "l2d_config_editor/assets",
        ),
        (str(root / "THIRD_PARTY_NOTICES.md"), "."),
        (str(root / "build" / "licenses"), "licenses"),
    ],
    hiddenimports=[],
    excludes=["PyQt6"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="L2DConfigEditor",
    icon=str(root / "build" / "icons" / "L2DConfigEditor.ico"),
    version=str(root / "build" / "windows" / "version_info.txt"),
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="L2DConfigEditor",
)
