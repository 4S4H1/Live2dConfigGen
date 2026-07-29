"""Small deterministic helpers used by the PowerShell release pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import zipfile
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from packaging.version import Version

from l2d_config_editor.update_manifest import (
    canonical_manifest_bytes,
    load_private_key,
    sign_manifest,
)
from l2d_config_editor.version import (
    PRODUCT_ID,
    PRODUCT_NAME,
    PUBLISHER,
    MINIMUM_SUPPORTED_VERSION,
    UPDATE_CHANNEL,
    UPDATE_MANIFEST_SCHEMA,
    VERSION,
)

def init_key(private_path: Path, public_path: Path) -> None:
    if private_path.exists() or public_path.exists():
        raise SystemExit("拒绝覆盖已有发布密钥")
    private_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    private_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public_path.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )


STABLE_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def _stable_version(value: str, label: str) -> Version:
    if STABLE_SEMVER.fullmatch(value) is None:
        raise SystemExit(f"{label} 必须是 x.y.z 格式的稳定 SemVer")
    return Version(value)


def verify_keypair(
    private_path: Path,
    public_path: Path,
    embedded_public_path: Path | None = None,
) -> None:
    """Fail unless the external signing key matches the tracked public key."""

    key = load_private_key(private_path.read_bytes())
    derived = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if derived != public_path.read_bytes():
        raise SystemExit("发布私钥与外部发布公钥不匹配")
    if embedded_public_path is not None and derived != embedded_public_path.read_bytes():
        raise SystemExit("发布私钥与仓库内嵌公钥不匹配")


def verify_environment() -> None:
    """Check the interpreter and every release-critical package exactly."""

    expected_python = (3, 13, 14)
    if sys.version_info[:3] != expected_python:
        raise SystemExit(
            "Python 版本不匹配："
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
            "，要求 3.13.14"
        )
    expected = {
        "PySide6": "6.11.1",
        "PySide6_Essentials": "6.11.1",
        "PySide6_Addons": "6.11.1",
        "shiboken6": "6.11.1",
        "cryptography": "48.0.0",
        "packaging": "26.2",
        "pyinstaller": "6.21.0",
        "pillow": "12.1.1",
    }
    mismatches: list[str] = []
    for name, wanted in expected.items():
        try:
            actual = distribution(name).version
        except PackageNotFoundError:
            actual = "未安装"
        if actual != wanted:
            mismatches.append(f"{name}={actual}（要求 {wanted}）")
    if mismatches:
        raise SystemExit("锁定构建环境版本不匹配：" + "；".join(mismatches))


def version_info(output: Path, *, host: bool = False) -> None:
    parts = [int(part) for part in Version(VERSION).release]
    parts += [0] * (4 - len(parts))
    product_id = "L2DUpdateHost" if host else PRODUCT_ID
    product_name = "L2D 局域网更新主机" if host else PRODUCT_NAME
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={tuple(parts[:4])}, prodvers={tuple(parts[:4])},
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('080404B0', [
    StringStruct('CompanyName', '{PUBLISHER}'),
    StringStruct('FileDescription', '{product_name}'),
    StringStruct('FileVersion', '{VERSION}'),
    StringStruct('InternalName', '{product_id}'),
    StringStruct('OriginalFilename', '{product_id}.exe'),
    StringStruct('ProductName', '{product_name}'),
    StringStruct('ProductVersion', '{VERSION}')])]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])])
""",
        encoding="utf-8",
    )


def build_bundle(
    installer: Path,
    private_key_path: Path,
    output: Path,
    notes: str,
    *,
    release_version: str = VERSION,
    minimum_supported_version: str = MINIMUM_SUPPORTED_VERSION,
) -> None:
    candidate = _stable_version(release_version, "发布版本")
    minimum = _stable_version(minimum_supported_version, "最低支持版本")
    if minimum > candidate:
        raise SystemExit("最低支持版本不得高于发布版本")
    artifact_bytes = installer.read_bytes()
    manifest = {
        "schema_version": UPDATE_MANIFEST_SCHEMA,
        "product": PRODUCT_ID,
        "channel": UPDATE_CHANNEL,
        "version": str(candidate),
        "published_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "minimum_supported_version": str(minimum),
        "notes": notes,
        "key_id": "release-1",
        "artifact": {
            "platform": "windows",
            "arch": "x86_64",
            "url": installer.name,
            "size": len(artifact_bytes),
            "sha256": hashlib.sha256(artifact_bytes).hexdigest(),
        },
    }
    raw = canonical_manifest_bytes(manifest)
    key = load_private_key(private_key_path.read_bytes())
    signature = sign_manifest(raw, key)
    output.parent.mkdir(parents=True, exist_ok=True)
    timestamp = (2020, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, data in (
            ("manifest.json", raw),
            ("manifest.sig", signature),
            (installer.name, artifact_bytes),
        ):
            info = zipfile.ZipInfo(name, timestamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            bundle.writestr(info, data)


def checksums(directory: Path, output: Path) -> None:
    lines = []
    output_resolved = output.resolve()
    for path in sorted(
        directory.rglob("*"),
        key=lambda item: item.relative_to(directory).as_posix().lower(),
    ):
        if path.is_symlink():
            raise SystemExit(f"SHA-256 清单拒绝符号链接：{path}")
        if path.is_file() and path != output:
            if path.resolve() == output_resolved:
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            relative = path.relative_to(directory).as_posix()
            lines.append(f"{digest}  {relative}")
    output.write_text("\n".join(lines) + "\n", encoding="ascii")


def collect_licenses(output: Path) -> None:
    """Collect the exact license texts shipped by the locked distributions."""

    import shutil
    import sys

    output.mkdir(parents=True, exist_ok=True)
    for name in (
        "PySide6",
        "PySide6_Essentials",
        "PySide6_Addons",
        "shiboken6",
        "cryptography",
        "packaging",
        "pyinstaller",
        "pillow",
    ):
        try:
            package = distribution(name)
        except PackageNotFoundError as exc:
            raise SystemExit(f"锁定环境缺少 {name}，无法收集许可证") from exc
        found = 0
        target_dir = output / name
        for entry in package.files or ():
            lowered = str(entry).lower()
            if not any(token in lowered for token in ("license", "copying")):
                continue
            source = Path(package.locate_file(entry))
            if not source.is_file() or source.suffix.lower() in {".py", ".pyc", ".pyd"}:
                continue
            target_dir.mkdir(exist_ok=True)
            shutil.copy2(source, target_dir / source.name)
            found += 1
        if found == 0:
            raise SystemExit(f"{name} 分发包未包含可识别的许可证文件")
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        shutil.copy2(python_license, output / "Python-LICENSE.txt")

    gnu_license_source = ROOT / "packaging" / "licenses"
    required_gnu_licenses = (
        "GPL-3.0-only.txt",
        "LGPL-3.0-only.txt",
    )
    gnu_target = output / "GNU"
    gnu_target.mkdir(exist_ok=True)
    for filename in required_gnu_licenses:
        source = gnu_license_source / filename
        if not source.is_file():
            raise SystemExit(f"缺少受控 GNU 许可证正文：{source}")
        shutil.copy2(source, gnu_target / filename)

    nsis_license = ROOT / ".tools" / "nsis-3.12" / "COPYING"
    if not nsis_license.is_file():
        raise SystemExit(f"缺少锁定 NSIS 3.12 的许可证：{nsis_license}")
    nsis_target = output / "NSIS"
    nsis_target.mkdir(exist_ok=True)
    shutil.copy2(nsis_license, nsis_target / "COPYING.txt")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    key = sub.add_parser("init-key")
    key.add_argument("--private", type=Path, required=True)
    key.add_argument("--public", type=Path, required=True)
    verify = sub.add_parser("verify-keypair")
    verify.add_argument("--private", type=Path, required=True)
    verify.add_argument("--public", type=Path, required=True)
    verify.add_argument("--embedded-public", type=Path)
    sub.add_parser("verify-environment")
    vi = sub.add_parser("version-info")
    vi.add_argument("--output", type=Path, required=True)
    vi.add_argument("--host", action="store_true")
    bundle = sub.add_parser("bundle")
    bundle.add_argument("--installer", type=Path, required=True)
    bundle.add_argument("--private-key", type=Path, required=True)
    bundle.add_argument("--output", type=Path, required=True)
    bundle.add_argument(
        "--notes",
        default=(
            "1.1.1：粘贴到画布的参考图支持自由调整宽高，"
            "并增加持续可见的外框描边。"
        ),
    )
    bundle.add_argument(
        "--minimum-supported-version",
        default=MINIMUM_SUPPORTED_VERSION,
    )
    sums = sub.add_parser("checksums")
    sums.add_argument("--directory", type=Path, required=True)
    sums.add_argument("--output", type=Path, required=True)
    licenses = sub.add_parser("collect-licenses")
    licenses.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "init-key":
        init_key(args.private, args.public)
    elif args.command == "verify-keypair":
        verify_keypair(args.private, args.public, args.embedded_public)
    elif args.command == "verify-environment":
        verify_environment()
    elif args.command == "version-info":
        version_info(args.output, host=args.host)
    elif args.command == "bundle":
        build_bundle(
            args.installer,
            args.private_key,
            args.output,
            args.notes,
            minimum_supported_version=args.minimum_supported_version,
        )
    elif args.command == "checksums":
        checksums(args.directory, args.output)
    elif args.command == "collect-licenses":
        collect_licenses(args.output)


if __name__ == "__main__":
    main()
