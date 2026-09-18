"""Asynchronous LAN update client built on ``QNetworkAccessManager``."""

from __future__ import annotations

import os
import logging
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

from .update_discovery import UpdateHostDiscovery
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

logger = logging.getLogger(__name__)


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
        discovery: UpdateHostDiscovery | None = None,
        check_timeout_ms: int = 6000,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.public_key = load_public_key(public_key_pem)
        self.current_version = current_version
        self.cache_root = (cache_root or default_update_cache()).resolve()
        self.network = QNetworkAccessManager(self)
        self.discovery = discovery or UpdateHostDiscovery(parent=self)
        if discovery is not None and discovery.parent() is None:
            discovery.setParent(self)
        self.discovery.finished.connect(self._discovery_finished)
        self.check_timeout_ms = max(100, min(int(check_timeout_ms), 30_000))
        self._check_timeout = QTimer(self)
        self._check_timeout.setSingleShot(True)
        self._check_timeout.timeout.connect(self._check_timed_out)
        self._candidate_timer = QTimer(self)
        self._candidate_timer.setSingleShot(True)
        self._candidate_timer.timeout.connect(self._try_next_candidate)
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
        self._download_retry_timer = QTimer(self)
        self._download_retry_timer.setSingleShot(True)
        self._download_retry_timer.timeout.connect(self._retry_download)
        self._download_retry_cache_only: bool | None = None
        self._automatic_check = False
        self._candidate_urls: list[str] = []
        self._candidate_errors: list[str] = []
        self._fallback_url = ""
        self._fallback_attempted = False
        self._valid_no_update_found = False

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

        if self._download_is_active():
            self.checkFailed.emit(
                "更新正在下载；请等待下载完成，或取消下载后再重新检查。"
            )
            return
        self.cancel_check()
        self._reset_available_release()
        try:
            base = self.normalize_base_url(base_url)
        except UpdateValidationError as exc:
            self.checkFailed.emit(str(exc))
            return
        self._begin_check(base)

    def check_automatically(self, fallback_base_url: str = "") -> None:
        """Discover LAN Hosts, then use an optional manually configured fallback."""

        if self._download_is_active():
            self.checkFailed.emit(
                "更新正在下载；请等待下载完成，或取消下载后再重新检查。"
            )
            return
        self.cancel_check()
        self._reset_available_release()
        fallback = fallback_base_url.strip()
        if fallback:
            try:
                fallback = self.normalize_base_url(fallback).rstrip("/")
            except UpdateValidationError as exc:
                self.checkFailed.emit(f"手动更新主机地址无效：{exc}")
                return
        self._automatic_check = True
        self._fallback_url = fallback
        self._fallback_attempted = False
        self._candidate_urls = []
        self._candidate_errors = []
        self.discovery.start()

    def _reset_available_release(self) -> None:
        self._artifact = None
        self._artifact_url = ""
        self._raw_manifest = b""
        self._manifest_signature = b""

    def _download_is_active(self) -> bool:
        return (
            self._download_reply is not None
            or self._download_stream is not None
            or self._download_retry_timer.isActive()
        )

    def _discovery_finished(self, urls: list[str]) -> None:
        if not self._automatic_check:
            return
        candidates = [str(url).strip() for url in urls if str(url).strip()]
        self._candidate_urls = list(dict.fromkeys(candidates))
        if self._fallback_url in self._candidate_urls:
            self._fallback_attempted = True
        self._try_next_candidate()

    def _try_next_candidate(self) -> None:
        if not self._automatic_check:
            return
        if not self._candidate_urls:
            if self._valid_no_update_found:
                self._finish_successful_check()
                self.noUpdate.emit()
                return
            if self._fallback_url and not self._fallback_attempted:
                self._fallback_attempted = True
                self._candidate_urls.append(self._fallback_url)
                self._try_next_candidate()
                return
            errors = list(self._candidate_errors)
            self._automatic_check = False
            self._fallback_url = ""
            self._fallback_attempted = False
            if errors:
                self.checkFailed.emit(
                    "发现的更新主机均不可用；最后错误：" + errors[-1]
                )
            else:
                self.checkFailed.emit(
                    "局域网内未发现更新主机；可在“更新设置”中配置手动回退地址。"
                )
            return
        candidate = self._candidate_urls.pop(0)
        try:
            base = self.normalize_base_url(candidate)
        except UpdateValidationError as exc:
            self._candidate_failed(str(exc))
            return
        self._begin_check(base)

    def _begin_check(self, base: str) -> None:
        self._cancel_network_check()
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
        self._check_timeout.start(self.check_timeout_ms)

    def cancel_check(self) -> None:
        self.discovery.cancel()
        self._candidate_timer.stop()
        self._cancel_network_check()
        self._automatic_check = False
        self._candidate_urls = []
        self._candidate_errors = []
        self._fallback_url = ""
        self._fallback_attempted = False
        self._valid_no_update_found = False

    def _cancel_network_check(self) -> None:
        self._check_timeout.stop()
        replies, self._check_replies = self._check_replies, []
        for reply in replies:
            self._dispose_check_reply(reply, abort=True)
        self._check_parts.clear()
        self._check_buffers.clear()

    @staticmethod
    def _dispose_check_reply(
        reply: QNetworkReply,
        *,
        abort: bool,
    ) -> None:
        """Detach Python callbacks before an abort can synchronously emit signals."""

        for signal in (reply.readyRead, reply.finished):
            try:
                signal.disconnect()
            except (RuntimeError, TypeError):
                pass
        if abort:
            reply.abort()
        reply.deleteLater()

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
        self._dispose_check_reply(reply, abort=True)
        self._candidate_failed("更新主机返回的数据过大")

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
            self._dispose_check_reply(reply, abort=False)
            self._candidate_failed(f"检查更新失败：{message}")
            return
        data = bytes(self._check_buffers.pop(reply, bytearray()))
        self._dispose_check_reply(reply, abort=False)
        self._check_parts[kind] = data
        if {"manifest", "signature"} - self._check_parts.keys():
            return
        self._check_timeout.stop()
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
            self._candidate_has_no_update()
            return
        except UpdateValidationError as exc:
            self._candidate_failed(str(exc))
            return
        self._artifact = artifact
        self._artifact_url = artifact_url
        self._raw_manifest = raw
        self._manifest_signature = signature
        from packaging.version import Version

        if artifact.version == Version(self.current_version):
            self._candidate_has_no_update()
            return
        self._finish_successful_check()
        self.updateAvailable.emit(manifest, artifact_url)

    def _candidate_failed(self, message: str) -> None:
        self._cancel_network_check()
        if self._automatic_check:
            self._candidate_errors.append(message)
            self._candidate_timer.start(0)
            return
        self.checkFailed.emit(message)

    def _check_timed_out(self) -> None:
        self._candidate_failed("检查更新超时，更新主机未在限定时间内响应")

    def _candidate_has_no_update(self) -> None:
        if self._automatic_check:
            self._valid_no_update_found = True
            self._candidate_timer.start(0)
            return
        self._finish_successful_check()
        self.noUpdate.emit()

    def _finish_successful_check(self) -> None:
        self._candidate_timer.stop()
        self._automatic_check = False
        self._candidate_urls = []
        self._candidate_errors = []
        self._fallback_url = ""
        self._fallback_attempted = False
        self._valid_no_update_found = False

    def download_available(self, *, cache_only: bool = False) -> None:
        self._cache_only = bool(cache_only)
        try:
            self._begin_download()
        except OSError as exc:
            self._download_io_failed(exc)

    def _begin_download(self) -> None:
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
                self._download_retry_cache_only = cache_only
                self._download_retry_timer.start(0)
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
        try:
            self._initialize_download_stream()
            if self._download_stream is not None:
                self._download_stream.write(bytes(self._download_reply.readAll()))
                if (
                    self._artifact is not None
                    and self._download_stream.tell() > self._artifact.size
                ):
                    self._download_reply.abort()
        except OSError as exc:
            self._download_io_failed(exc)

    def _download_io_failed(self, error: OSError) -> None:
        # Detach the reply before aborting: finished can be emitted synchronously.
        # A buffered stream can fail again on close after a disk-full write.
        self.cancel_download(remove_partial=False)
        self._download_error(f"无法写入更新缓存：{error}")

    def _download_progress(self, received: int, total: int) -> None:
        base = self._download_offset
        self.downloadProgress.emit(base + received, base + total if total >= 0 else -1)

    def _download_finished(self) -> None:
        reply = self._download_reply
        if reply is None:
            return
        self._download_ready()
        # Stream initialization can cancel this reply and queue a clean retry
        # (for example after an invalid Range response with no readyRead).
        if self._download_reply is not reply:
            return
        if self._download_stream is not None:
            try:
                self._download_stream.flush()
                os.fsync(self._download_stream.fileno())
                self._download_stream.close()
                self._download_stream = None
            except OSError as exc:
                self._download_io_failed(exc)
                return
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
            try:
                self._download_part.unlink(missing_ok=True)
                self._download_part.with_suffix(
                    self._download_part.suffix + ".etag"
                ).unlink(missing_ok=True)
            except OSError:
                logger.warning("无效更新缓存暂时无法清理", exc_info=True)
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
        self._download_retry_timer.stop()
        self._download_retry_cache_only = None
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
            stream, self._download_stream = self._download_stream, None
            try:
                stream.close()
            except OSError:
                logger.warning("取消下载时无法刷新缓存", exc_info=True)
        if remove_partial and self._download_part is not None:
            self._download_part.unlink(missing_ok=True)
            self._download_part.with_suffix(
                self._download_part.suffix + ".etag"
            ).unlink(missing_ok=True)
        self._download_initialized = False

    def _retry_download(self) -> None:
        cache_only = self._download_retry_cache_only
        self._download_retry_cache_only = None
        if cache_only is None:
            return
        self.download_available(cache_only=cache_only)

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
            try:
                shutil.rmtree(obsolete)
            except OSError:
                # Windows may still hold an installer handle. The new verified
                # release remains usable; a later download retries pruning.
                logger.warning("旧更新缓存暂时无法清理：%s", obsolete, exc_info=True)
