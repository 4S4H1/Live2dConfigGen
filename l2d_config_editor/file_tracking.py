"""Cheap file identity checks, with content reads only after an observed change."""
from __future__ import annotations

import hashlib
from pathlib import Path


class ExternalDocumentChangeError(ValueError):
    """Saving a stale in-memory document would overwrite an external edit."""


def file_stamp(path: str | Path) -> tuple[int, int, int, int] | None:
    try:
        stat = Path(path).stat()
        return stat.st_mtime_ns, stat.st_size, stat.st_ino, stat.st_ctime_ns
    except OSError:
        return None


def content_digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def disk_changed(document, path: str | Path | None = None) -> bool:
    target = Path(path or document.path) if (path or document.path) else None
    if target is None or document.disk_digest is None:
        return False
    if document.path and target.resolve() != Path(document.path).resolve():
        return False  # A separate copy has no shared disk baseline.
    stamp = file_stamp(target)
    if stamp == document.disk_stamp and stamp is not None:
        return False
    if stamp is not None and document.disk_observed is not None and document.disk_observed[0] == stamp:
        return document.disk_observed[1] != document.disk_digest
    try:
        digest = content_digest(target.read_bytes())
    except OSError:
        return True
    if file_stamp(target) != stamp:
        return True
    document.disk_observed = (stamp, digest)
    if digest == document.disk_digest:
        document.disk_stamp = stamp
        return False
    return True


def check_save_baseline(document, target: Path) -> None:
    if disk_changed(document, target):
        raise ExternalDocumentChangeError(
            "磁盘上的 JSON 已被 SVN 或其他程序修改，已暂停覆盖保存。"
            "请读取磁盘版本，或将本地修改另存为副本。"
        )
