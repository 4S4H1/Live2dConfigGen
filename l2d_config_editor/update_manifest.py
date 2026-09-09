"""Signed update manifest and release-bundle primitives.

Signatures cover the *exact bytes* of ``manifest.json``.  Callers must verify
the signature before decoding JSON so an attacker cannot exploit differences
between parsed and signed representations.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urljoin, urlparse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from packaging.version import InvalidVersion, Version

from .version import PRODUCT_ID, UPDATE_CHANNEL, UPDATE_MANIFEST_SCHEMA

MAX_MANIFEST_BYTES = 256 * 1024
MAX_SIGNATURE_BYTES = 512
MAX_BUNDLE_MEMBERS = 8
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024
STABLE_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
logger = logging.getLogger(__name__)


class UpdateValidationError(ValueError):
    """Raised when an update is malformed, untrusted, or incompatible."""


class UpdateNotAvailableError(UpdateValidationError):
    """Raised when a valid release is not newer than the installed version."""


@dataclass(frozen=True)
class ValidatedArtifact:
    filename: str
    size: int
    sha256: str
    version: Version
    notes: str


def canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    """Return deterministic UTF-8 bytes suitable for signing."""

    return json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def load_private_key(data: bytes, password: bytes | None = None) -> Ed25519PrivateKey:
    try:
        key = serialization.load_pem_private_key(data, password=password)
    except (TypeError, ValueError) as exc:
        raise UpdateValidationError("无法读取 Ed25519 私钥") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise UpdateValidationError("发布私钥不是 Ed25519 密钥")
    return key


def load_public_key(data: bytes) -> Ed25519PublicKey:
    try:
        key = serialization.load_pem_public_key(data)
    except (TypeError, ValueError) as exc:
        raise UpdateValidationError("无法读取 Ed25519 公钥") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise UpdateValidationError("发布公钥不是 Ed25519 密钥")
    return key


def sign_manifest(raw_manifest: bytes, private_key: Ed25519PrivateKey) -> bytes:
    if len(raw_manifest) > MAX_MANIFEST_BYTES:
        raise UpdateValidationError("更新清单过大")
    return base64.b64encode(private_key.sign(raw_manifest))


def verify_manifest_signature(
    raw_manifest: bytes,
    encoded_signature: bytes,
    public_key: Ed25519PublicKey,
) -> None:
    if len(raw_manifest) > MAX_MANIFEST_BYTES:
        raise UpdateValidationError("更新清单过大")
    if len(encoded_signature) > MAX_SIGNATURE_BYTES:
        raise UpdateValidationError("更新签名过大")
    try:
        signature = base64.b64decode(encoded_signature.strip(), validate=True)
        public_key.verify(signature, raw_manifest)
    except (ValueError, InvalidSignature) as exc:
        raise UpdateValidationError("更新清单签名无效") from exc


def _required_string(value: Any, name: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise UpdateValidationError(f"{name} 必须是有效字符串")
    return value.strip()


def _safe_relative_artifact_url(value: Any) -> str:
    url = _required_string(value, "artifact.url", maximum=512)
    parsed = urlparse(url)
    posix = PurePosixPath(parsed.path)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.path.startswith("/")
        or "\\" in url
        or ".." in posix.parts
        or posix.name != parsed.path
    ):
        raise UpdateValidationError("artifact.url 必须是同源目录中的简单相对文件名")
    return posix.name


def _stable_version(value: Any, name: str) -> Version:
    raw = _required_string(value, name, maximum=64)
    if STABLE_SEMVER.fullmatch(raw) is None:
        raise UpdateValidationError(f"{name} 必须是 x.y.z 格式的稳定 SemVer")
    try:
        return Version(raw)
    except InvalidVersion as exc:  # pragma: no cover - guarded by the regex
        raise UpdateValidationError(f"{name} 包含无效版本") from exc


def resolve_same_origin(base_manifest_url: str, relative_artifact: str) -> str:
    """Resolve an artifact URL and enforce the manifest's HTTP(S) origin."""

    base = urlparse(base_manifest_url)
    if base.scheme not in {"http", "https"} or not base.netloc:
        raise UpdateValidationError("清单 URL 必须使用 HTTP 或 HTTPS")
    result = urljoin(base_manifest_url, relative_artifact)
    resolved = urlparse(result)
    if (
        resolved.scheme.lower() != base.scheme.lower()
        or resolved.hostname != base.hostname
        or resolved.port != base.port
        or resolved.username
        or resolved.password
    ):
        raise UpdateValidationError("安装器 URL 与清单不同源")
    return result


def validate_manifest(
    raw_manifest: bytes,
    *,
    current_version: str,
    expected_product: str = PRODUCT_ID,
    expected_channel: str = UPDATE_CHANNEL,
    expected_platform: str = "windows",
    expected_arch: str = "x86_64",
    allow_equal: bool = False,
    enforce_minimum_supported: bool = True,
) -> tuple[dict[str, Any], ValidatedArtifact]:
    """Decode and validate a previously signature-verified manifest."""

    if len(raw_manifest) > MAX_MANIFEST_BYTES:
        raise UpdateValidationError("更新清单过大")
    try:
        manifest = json.loads(raw_manifest.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateValidationError("更新清单不是有效 UTF-8 JSON") from exc
    if not isinstance(manifest, dict):
        raise UpdateValidationError("更新清单根节点必须是对象")
    if manifest.get("schema_version") != UPDATE_MANIFEST_SCHEMA:
        raise UpdateValidationError("不支持的更新清单版本")
    if manifest.get("product") != expected_product:
        raise UpdateValidationError("更新产品标识不匹配")
    if manifest.get("channel") != expected_channel:
        raise UpdateValidationError("更新频道不匹配")
    published_at = _required_string(
        manifest.get("published_at"), "published_at", maximum=64
    )
    try:
        published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise UpdateValidationError("published_at 不是有效 ISO-8601 时间") from exc
    if published.tzinfo is None:
        raise UpdateValidationError("published_at 必须包含时区")
    notes_value = manifest.get("notes", "")
    if not isinstance(notes_value, str) or len(notes_value) > 16_384:
        raise UpdateValidationError("notes 必须是长度受限的字符串")
    notes = notes_value
    _required_string(manifest.get("key_id"), "key_id", maximum=64)
    candidate = _stable_version(manifest.get("version"), "version")
    installed = _stable_version(current_version, "current_version")
    minimum = _stable_version(
        manifest.get("minimum_supported_version"), "minimum_supported_version"
    )
    if candidate < installed or (candidate == installed and not allow_equal):
        raise UpdateNotAvailableError("拒绝降级或重复安装")
    if candidate < minimum:
        raise UpdateValidationError("发布版本低于自身最低支持版本")
    if enforce_minimum_supported and installed < minimum:
        raise UpdateValidationError(
            f"当前版本 {installed} 低于该更新支持的最低版本 {minimum}"
        )

    artifact = manifest.get("artifact")
    if not isinstance(artifact, dict):
        raise UpdateValidationError("artifact 必须是对象")
    if artifact.get("platform") != expected_platform:
        raise UpdateValidationError("更新平台不匹配")
    if artifact.get("arch") != expected_arch:
        raise UpdateValidationError("更新架构不匹配")
    filename = _safe_relative_artifact_url(artifact.get("url"))
    size = artifact.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_ARTIFACT_BYTES:
        raise UpdateValidationError("artifact.size 超出允许范围")
    digest = str(artifact.get("sha256", "")).lower()
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise UpdateValidationError("artifact.sha256 无效")
    return manifest, ValidatedArtifact(filename, size, digest, candidate, notes)


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def verify_artifact(path: Path, artifact: ValidatedArtifact) -> None:
    stat = path.stat()
    if stat.st_size != artifact.size:
        raise UpdateValidationError("安装器大小与清单不匹配")
    if sha256_file(path) != artifact.sha256:
        raise UpdateValidationError("安装器 SHA-256 与清单不匹配")


def _safe_zip_members(bundle: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    infos = bundle.infolist()
    if not 1 <= len(infos) <= MAX_BUNDLE_MEMBERS:
        raise UpdateValidationError("更新包文件数量无效")
    result: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        path = PurePosixPath(info.filename)
        if (
            info.is_dir()
            or path.is_absolute()
            or ".." in path.parts
            or len(path.parts) != 1
            or "\\" in info.filename
        ):
            raise UpdateValidationError("更新包包含不安全路径")
        if info.file_size > MAX_ARTIFACT_BYTES:
            raise UpdateValidationError("更新包成员过大")
        if path.name in result:
            raise UpdateValidationError("更新包包含重复文件名")
        # Unix-mode symlinks can otherwise point outside the staging directory.
        if ((info.external_attr >> 16) & 0o170000) == 0o120000:
            raise UpdateValidationError("更新包不得包含符号链接")
        result[path.name] = info
    return result


def import_release_bundle(
    bundle_path: Path,
    releases_root: Path,
    public_key: Ed25519PublicKey,
    *,
    retain: int = 2,
) -> str:
    """Verify, stage, and atomically publish a ``.l2dupdate`` bundle."""

    releases_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bundle_path, "r") as bundle:
        members = _safe_zip_members(bundle)
        try:
            manifest_info = members["manifest.json"]
            signature_info = members["manifest.sig"]
        except KeyError as exc:
            raise UpdateValidationError("更新包缺少 manifest.json 或 manifest.sig") from exc
        if manifest_info.file_size > MAX_MANIFEST_BYTES:
            raise UpdateValidationError("更新清单过大")
        if signature_info.file_size > MAX_SIGNATURE_BYTES:
            raise UpdateValidationError("更新签名过大")
        raw = bundle.read(manifest_info)
        signature = bundle.read(signature_info)
        verify_manifest_signature(raw, signature, public_key)
        manifest, artifact = validate_manifest(
            raw,
            current_version="0.0.0",
            allow_equal=True,
            enforce_minimum_supported=False,
        )
        if artifact.filename not in members:
            raise UpdateValidationError("更新包缺少清单声明的安装器")
        if members[artifact.filename].file_size != artifact.size:
            raise UpdateValidationError("更新包安装器大小与清单不匹配")

        version_name = str(artifact.version)
        latest_path = releases_root / "latest"
        if latest_path.is_file():
            try:
                published = _stable_version(
                    latest_path.read_text(encoding="utf-8").strip(),
                    "latest",
                )
            except (OSError, UpdateValidationError) as exc:
                raise UpdateValidationError("Host 当前版本指针无效") from exc
            if artifact.version <= published:
                raise UpdateValidationError("拒绝发布旧版本或重复版本")
        final_dir = releases_root / version_name
        if final_dir.exists():
            raise UpdateValidationError("该版本已经发布")
        staging = Path(tempfile.mkdtemp(prefix=".import-", dir=releases_root))
        latest_tmp = staging / ".latest.tmp"
        activated = False
        try:
            for name in ("manifest.json", "manifest.sig", artifact.filename):
                source = bundle.open(members[name])
                with source, (staging / name).open("wb") as target:
                    shutil.copyfileobj(source, target, 1024 * 1024)
            verify_artifact(staging / artifact.filename, artifact)
            latest_tmp.write_text(version_name, encoding="utf-8")
            os.replace(staging, final_dir)
            activated = True
            # Publish before pruning: the old pointer must never refer to a
            # removed release. A failed pointer switch remains retryable.
            os.replace(final_dir / latest_tmp.name, latest_path)
        except BaseException:
            if activated:
                os.replace(final_dir, staging)
            shutil.rmtree(staging, ignore_errors=True)
            raise

    versions: list[tuple[Version, Path]] = []
    for candidate_dir in releases_root.iterdir():
        if not candidate_dir.is_dir() or candidate_dir.name.startswith("."):
            continue
        try:
            versions.append((Version(candidate_dir.name), candidate_dir))
        except InvalidVersion:
            continue
    versions.sort(reverse=True)
    for _, obsolete in versions[max(1, retain) :]:
        try:
            shutil.rmtree(obsolete)
        except OSError:
            # A Windows download/file lock should not undo a verified release.
            # The next import retries pruning the obsolete cache.
            logger.warning("旧更新缓存暂时无法清理：%s", obsolete, exc_info=True)
    return version_name
