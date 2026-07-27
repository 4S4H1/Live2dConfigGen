"""Asynchronous LAN update client built on ``QNetworkAccessManager``."""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import urljoin

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import (
    QNetworkAccessManager,
    QNetworkReply,
    QNetworkRequest,
)

from .update_manifest import (
    MAX_MANIFEST_BYTES,
    MAX_SIGNATURE_BYTES,
    UpdateNotAvailableError,
    UpdateValidationError,
    ValidatedArtifact,
    load_public_key,
    resolve_same_origin,
    validate_manifest,
    verify_artifact,
    verify_manifest_signature,
)
from .version import VERSION


def default_update_cache() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    root = Path(local) if local else Path.home()
    return root / "4S4H1" / "L2DConfigEditor" / "updates"


def bundled_public_key_pem() -> bytes:
    """Load the release key embedded by the trusted local build."""

    if getattr(sys, "frozen", False):
        path = (
            Path(getattr(sys, "_MEIPASS"))
            / "l2d_config_editor"
            / "assets"
            / "release_public_key.pem"
        )
    else:
        path = (
            Path(__file__).resolve().parent
            / "assets"
            / "release_public_key.pem"
        )
    try:
        return path.read_bytes()
    except OSError as exc:
        raise UpdateValidationError(f"缺少嵌入的发布公钥：{path}") from exc


class UpdateClient(QObject):
    """Check and download signed updates without blocking the Qt event loop."""

    updateAvailable = Signal(dict, str)
    noUpdate = Signal()
    checkFailed = Signal(str)
    downloadProgress = Signal(object, object)
    downloadFinished = Signal(str)
    downloadFailed = Signal(str)
    cacheFinished = Signal(str)
    cacheFailed = Signal(str)

    def __init__(
        self,
        public_key_pem: bytes,
        *,
        current_version: str = VERSION,
        cache_root: Path | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.public_key = load_public_key(public_key_pem)
        self.current_version = current_version
        self.cache_root = (cache_root or default_update_cache()).resolve()
        self.network = QNetworkAccessManager(self)
        self._check_parts: dict[str, bytes] = {}
        self._check_replies: list[QNetworkReply] = []
        self._check_buffers: dict[QNetworkReply, bytearray] = {}
        self._manifest_url = ""
        self._artifact: ValidatedArtifact | None = None
        self._artifact_url = ""
        self._raw_manifest = b""
        self._manifest_signature = b""
        self._download_reply: QNetworkReply | None = None
        self._download_stream = None
        self._download_part: Path | None = None
        self._download_final: Path | None = None
        self._download_offset = 0
        self._download_initialized = False
        self._cache_only = False

    @staticmethod
    def normalize_base_url(base_url: str) -> str:
        parsed = QUrl(base_url.strip())
        if (
            not parsed.isValid()
            or parsed.scheme().lower() not in {"http", "https"}
            or not parsed.host()
            or parsed.userName()
            or parsed.password()
            or parsed.hasQuery()
            or parsed.hasFragment()
        ):
            raise UpdateValidationError("更新主机地址必须是无账号和查询参数的 HTTP(S) URL")
        parsed.setPath(parsed.path().rstrip("/") + "/")
        return parsed.toString()

    def check(self, base_url: str) -> None:
        """Fetch raw manifest and detached signature, then verify and parse."""

        self.cancel_check()
        self._artifact = None
        self._artifact_url = ""
        self._raw_manifest = b""
        self._manifest_signature = b""
        try:
            base = self.normalize_base_url(base_url)
        except UpdateValidationError as exc:
            self.checkFailed.emit(str(exc))
            return
        self._manifest_url = urljoin(base, "stable/manifest.json")
        self._check_parts = {}
        for kind, url, limit in (
            ("manifest", self._manifest_url, MAX_MANIFEST_BYTES),
            ("signature", urljoin(base, "stable/manifest.sig"), MAX_SIGNATURE_BYTES),
        ):
            request = QNetworkRequest(QUrl(url))
            request.setAttribute(
                QNetworkRequest.Attribute.RedirectPolicyAttribute,
                QNetworkRequest.RedirectPolicy.ManualRedirectPolicy,
            )
            request.setRawHeader(b"Accept", b"application/octet-stream")
            reply = self.network.get(request)
            reply.setProperty("l2d_kind", kind)
            reply.setProperty("l2d_limit", limit)
            self._check_buffers[reply] = bytearray()
            reply.readyRead.connect(lambda reply=reply: self._check_ready(reply))
            reply.finished.connect(lambda reply=reply: self._check_finished(reply))
            self._check_replies.append(reply)

    def cancel_check(self) -> None:
        replies, self._check_replies = self._check_replies, []
        for reply in replies:
            reply.abort()
            reply.deleteLater()
        self._check_parts.clear()
        self._check_buffers.clear()

    def _check_ready(self, reply: QNetworkReply) -> None:
        if reply not in self._check_replies:
            return
        buffer = self._check_buffers.setdefault(reply, bytearray())
        buffer.extend(bytes(reply.readAll()))
        limit = int(reply.property("l2d_limit"))
        if len(buffer) <= limit:
            return
        self._check_replies.remove(reply)
        self._check_buffers.pop(reply, None)
        reply.abort()
        reply.deleteLater()
        self.cancel_check()
        self.checkFailed.emit("更新主机返回的数据过大")

    def _check_finished(self, reply: QNetworkReply) -> None:
        if reply not in self._check_replies:
            return
        self._check_ready(reply)
        if reply not in self._check_replies:
            return
        self._check_replies.remove(reply)
        kind = str(reply.property("l2d_kind"))
        if reply.error() != QNetworkReply.NetworkError.NoError:
            message = reply.errorString()
            self._check_buffers.pop(reply, None)
            reply.deleteLater()
            self.cancel_check()
            self.checkFailed.emit(f"检查更新失败：{message}")
            return
        data = bytes(self._check_buffers.pop(reply, bytearray()))
        reply.deleteLater()
        self._check_parts[kind] = data
        if {"manifest", "signature"} - self._check_parts.keys():
            return
        raw = self._check_parts["manifest"]
        signature = self._check_parts["signature"]
        self._check_parts = {}
        try:
            verify_manifest_signature(raw, signature, self.public_key)
            manifest, artifact = validate_manifest(
                raw,
                current_version=self.current_version,
                allow_equal=True,
            )
            artifact_url = resolve_same_origin(self._manifest_url, artifact.filename)
        except UpdateNotAvailableError:
            self.noUpdate.emit()
            return
        except UpdateValidationError as exc:
            self.checkFailed.emit(str(exc))
            return
        self._artifact = artifact
        self._artifact_url = artifact_url
        self._raw_manifest = raw
        self._manifest_signature = signature
        from packaging.version import Version

        if artifact.version == Version(self.current_version):
            self.noUpdate.emit()
            return
        self.updateAvailable.emit(manifest, artifact_url)

    def download_available(self, *, cache_only: bool = False) -> None:
        self._cache_only = bool(cache_only)
        if self._artifact is None or not self._artifact_url:
            self._download_error("尚未检查到可下载的更新")
            return
        self.cancel_download(remove_partial=False)
        version_dir = self.cache_root / str(self._artifact.version)
        version_dir.mkdir(parents=True, exist_ok=True)
        final = version_dir / self._artifact.filename
        part = final.with_suffix(final.suffix + ".part")
        etag_path = part.with_suffix(part.suffix + ".etag")
        if final.is_file():
            try:
                verify_artifact(final, self._artifact)
                self._write_verification_files(version_dir)
            except UpdateValidationError:
                final.unlink(missing_ok=True)
            except OSError as exc:
                self._download_error(str(exc))
                return
            else:
                self._retain_two_versions()
                self._download_success(final)
                return
        if part.is_file() and part.stat().st_size == self._artifact.size:
            try:
                verify_artifact(part, self._artifact)
                os.replace(part, final)
                etag_path.unlink(missing_ok=True)
                self._write_verification_files(version_dir)
            except UpdateValidationError:
                part.unlink(missing_ok=True)
                etag_path.unlink(missing_ok=True)
            except OSError as exc:
                self._download_error(str(exc))
                return
            else:
                self._retain_two_versions()
                self._download_success(final)
                return
        offset = part.stat().st_size if part.exists() else 0
        if offset > self._artifact.size:
            part.unlink(missing_ok=True)
            etag_path.unlink(missing_ok=True)
            offset = 0

        request = QNetworkRequest(QUrl(self._artifact_url))
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.ManualRedirectPolicy,
        )
        if offset:
            request.setRawHeader(b"Range", f"bytes={offset}-".encode("ascii"))
            try:
                etag = etag_path.read_bytes().strip()
            except OSError:
                etag = b""
            if etag:
                request.setRawHeader(b"If-Range", etag)
        reply = self.network.get(request)
        reply.readyRead.connect(self._download_ready)
        reply.downloadProgress.connect(self._download_progress)
        reply.finished.connect(self._download_finished)
        self._download_reply = reply
        self._download_part = part
        self._download_final = final
        self._download_offset = offset
        self._download_initialized = False

    def _initialize_download_stream(self) -> None:
        if self._download_initialized or self._download_reply is None:
            return
        status = self._download_reply.attribute(
            QNetworkRequest.Attribute.HttpStatusCodeAttribute
        )
        if status is None:
            return
        status_code = int(status)
        append = self._download_offset > 0 and status_code == 206
        if append:
            content_range = bytes(
                self._download_reply.rawHeader("Content-Range")
            ).decode("ascii", errors="replace")
            match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range)
            expected_size = self._artifact.size if self._artifact is not None else -1
            valid_range = bool(
                match
                and int(match.group(1)) == self._download_offset
                and int(match.group(2)) >= self._download_offset
                and int(match.group(2)) < expected_size
                and int(match.group(3)) == expected_size
            )
            assert self._download_part is not None
            etag_path = self._download_part.with_suffix(
                self._download_part.suffix + ".etag"
            )
            try:
                expected_etag = etag_path.read_bytes().strip()
            except OSError:
                expected_etag = b""
            received_etag = bytes(self._download_reply.rawHeader("ETag")).strip()
            if expected_etag and received_etag != expected_etag:
                valid_range = False
            if not valid_range:
                cache_only = self._cache_only
                self.cancel_download(remove_partial=True)
                etag_path.unlink(missing_ok=True)
                QTimer.singleShot(
                    0,
                    lambda: self.download_available(cache_only=cache_only),
                )
                return
        elif self._download_offset > 0 and status_code == 200:
            append = False
        elif status_code != 200:
            self.cancel_download(remove_partial=self._download_offset > 0)
            self._download_error(f"更新主机返回了无效 HTTP 状态 {status_code}")
            return
        mode = "ab" if append else "wb"
        if not append:
            self._download_offset = 0
        assert self._download_part is not None
        etag = bytes(self._download_reply.rawHeader("ETag"))
        self._download_stream = self._download_part.open(mode)
        if etag:
            self._download_part.with_suffix(
                self._download_part.suffix + ".etag"
            ).write_bytes(etag)
        self._download_initialized = True

    def _download_ready(self) -> None:
        if self._download_reply is None:
            return
        self._initialize_download_stream()
        if self._download_stream is not None:
            self._download_stream.write(bytes(self._download_reply.readAll()))
            if (
                self._artifact is not None
                and self._download_stream.tell() > self._artifact.size
            ):
                self._download_reply.abort()

    def _download_progress(self, received: int, total: int) -> None:
        base = self._download_offset
        self.downloadProgress.emit(base + received, base + total if total >= 0 else -1)

    def _download_finished(self) -> None:
        reply = self._download_reply
        if reply is None:
            return
        self._download_ready()
        if self._download_stream is not None:
            self._download_stream.flush()
            os.fsync(self._download_stream.fileno())
            self._download_stream.close()
            self._download_stream = None
        error = reply.error()
        error_message = reply.errorString()
        reply.deleteLater()
        self._download_reply = None
        self._download_initialized = False
        if error != QNetworkReply.NetworkError.NoError:
            self._download_error(f"下载失败：{error_message}")
            return
        assert self._download_part is not None
        assert self._download_final is not None
        assert self._artifact is not None
        try:
            verify_artifact(self._download_part, self._artifact)
            os.replace(self._download_part, self._download_final)
            self._download_part.with_suffix(
                self._download_part.suffix + ".etag"
            ).unlink(missing_ok=True)
            self._write_verification_files(self._download_final.parent)
            self._retain_two_versions()
        except UpdateValidationError as exc:
            self._download_part.unlink(missing_ok=True)
            self._download_part.with_suffix(
                self._download_part.suffix + ".etag"
            ).unlink(missing_ok=True)
            self._download_error(str(exc))
            return
        except OSError as exc:
            self._download_error(str(exc))
            return
        self._download_success(self._download_final)

    def _download_success(self, path: Path) -> None:
        if self._cache_only:
            self.cacheFinished.emit(str(path))
        else:
            self.downloadFinished.emit(str(path))

    def _download_error(self, message: str) -> None:
        if self._cache_only:
            self.cacheFailed.emit(message)
        else:
            self.downloadFailed.emit(message)

    def cache_current_release(self) -> bool:
        """Silently retain the signed installer matching the running version."""

        if self._artifact is None or str(self._artifact.version) != self.current_version:
            return False
        if any(
            version == self.current_version
            for version, _path in self.verified_cached_installers()
        ):
            return False
        self.download_available(cache_only=True)
        return True

    def cancel_download(self, *, remove_partial: bool = False) -> None:
        if self._download_reply is not None:
            reply, self._download_reply = self._download_reply, None
            try:
                reply.readyRead.disconnect(self._download_ready)
                reply.downloadProgress.disconnect(self._download_progress)
                reply.finished.disconnect(self._download_finished)
            except (RuntimeError, TypeError):
                pass
            reply.abort()
            reply.deleteLater()
        if self._download_stream is not None:
            self._download_stream.close()
            self._download_stream = None
        if remove_partial and self._download_part is not None:
            self._download_part.unlink(missing_ok=True)
        self._download_initialized = False

    def _write_verification_files(self, version_dir: Path) -> None:
        """Persist the exact signed metadata beside a verified installer."""

        if not self._raw_manifest or not self._manifest_signature:
            raise UpdateValidationError("缺少已验证更新的签名元数据")
        for name, data in (
            ("manifest.json", self._raw_manifest),
            ("manifest.sig", self._manifest_signature),
        ):
            target = version_dir / name
            temporary = version_dir / f".{name}.tmp"
            with temporary.open("wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)

    def verified_cached_installers(self) -> list[tuple[str, Path]]:
        """Return cached installers whose original signed metadata still verifies."""

        from packaging.version import Version

        verified: list[tuple[Version, Path]] = []
        if not self.cache_root.is_dir():
            return []
        for directory in self.cache_root.iterdir():
            if not directory.is_dir():
                continue
            try:
                raw = (directory / "manifest.json").read_bytes()
                signature = (directory / "manifest.sig").read_bytes()
                verify_manifest_signature(raw, signature, self.public_key)
                _manifest, artifact = validate_manifest(
                    raw,
                    current_version="0.0.0",
                    allow_equal=True,
                    enforce_minimum_supported=False,
                )
                if directory.name != str(artifact.version):
                    raise UpdateValidationError("缓存目录版本与签名清单不一致")
                installer = (directory / artifact.filename).resolve()
                installer.relative_to(directory.resolve())
                verify_artifact(installer, artifact)
            except (OSError, ValueError, UpdateValidationError):
                continue
            verified.append((artifact.version, installer))
        verified.sort(reverse=True)
        return [(str(version), installer) for version, installer in verified]

    def verify_cached_installer(self, path: str | Path) -> bool:
        """Re-verify a selected cache entry immediately before launching it."""

        candidate = Path(path).resolve()
        return any(
            candidate == installer
            for _version, installer in self.verified_cached_installers()
        )

    def _retain_two_versions(self) -> None:
        from packaging.version import InvalidVersion, Version

        versions: list[tuple[Version, Path]] = []
        if not self.cache_root.is_dir():
            return
        for directory in self.cache_root.iterdir():
            if not directory.is_dir():
                continue
            try:
                versions.append((Version(directory.name), directory))
            except InvalidVersion:
                continue
        versions.sort(reverse=True)
        for _, obsolete in versions[2:]:
            shutil.rmtree(obsolete)
