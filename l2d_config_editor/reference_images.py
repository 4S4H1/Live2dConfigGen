"""Bounded encoding and decoding helpers for embedded canvas reference images."""

from __future__ import annotations

import base64
import binascii
import math
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QSize, Qt
from PySide6.QtGui import QImage, QImageReader

MAX_REFERENCE_IMAGE_EDGE = 4096
MAX_REFERENCE_IMAGE_PIXELS = 16_000_000
MAX_REFERENCE_IMAGE_BYTES = 64 * 1024 * 1024
MAX_REFERENCE_IMAGE_COUNT = 32
MAX_DOCUMENT_REFERENCE_IMAGE_BYTES = 128 * 1024 * 1024
MAX_DOCUMENT_REFERENCE_IMAGE_PIXELS = 64_000_000
SUPPORTED_REFERENCE_IMAGE_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/bmp",
    "image/webp",
}
SUPPORTED_REFERENCE_IMAGE_FORMATS = {b"png", b"jpeg", b"jpg", b"bmp", b"webp"}


def _bounded_dimensions(width: int, height: int) -> tuple[int, int]:
    if width <= 0 or height <= 0:
        return 0, 0
    edge_scale = MAX_REFERENCE_IMAGE_EDGE / max(width, height)
    pixel_scale = math.sqrt(MAX_REFERENCE_IMAGE_PIXELS / max(1, width * height))
    scale = min(1.0, edge_scale, pixel_scale)
    return max(1, int(round(width * scale))), max(1, int(round(height * scale)))


def normalized_reference_image(image: QImage) -> QImage:
    if image.isNull() or image.width() <= 0 or image.height() <= 0:
        return QImage()
    width, height = _bounded_dimensions(image.width(), image.height())
    if width == image.width() and height == image.height():
        return image.copy()
    return image.scaled(
        width,
        height,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def encode_reference_image(image: QImage) -> tuple[str, tuple[float, float]] | None:
    normalized = normalized_reference_image(image)
    if normalized.isNull():
        return None
    buffer = QBuffer()
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not normalized.save(buffer, "PNG"):
        return None
    raw = bytes(buffer.data())
    if not raw or len(raw) > MAX_REFERENCE_IMAGE_BYTES:
        return None
    encoded = base64.b64encode(raw).decode("ascii")
    return encoded, (float(normalized.width()), float(normalized.height()))


def _decoded_reference_image_bytes(data_base64: str) -> bytes | None:
    try:
        encoded = str(data_base64 or "").strip().encode("ascii")
    except UnicodeEncodeError:
        return None
    if not encoded or len(encoded) > ((MAX_REFERENCE_IMAGE_BYTES + 2) // 3) * 4 + 4:
        return None
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return None
    if not raw or len(raw) > MAX_REFERENCE_IMAGE_BYTES:
        return None
    return raw


def decoded_reference_image_size(data_base64: str) -> int | None:
    raw = _decoded_reference_image_bytes(data_base64)
    return len(raw) if raw is not None else None


def trusted_reference_image_size(data_base64: str) -> int:
    """Return decoded byte size for already-validated canonical Base64."""

    encoded = str(data_base64 or "").strip()
    if not encoded or len(encoded) % 4:
        return 0
    padding = 2 if encoded.endswith("==") else (1 if encoded.endswith("=") else 0)
    return max(0, len(encoded) * 3 // 4 - padding)


def _decode_raw_reference_image(raw: bytes) -> QImage:
    buffer = QBuffer()
    buffer.setData(QByteArray(raw))
    if not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
        return QImage()
    reader = QImageReader(buffer)
    reader.setDecideFormatFromContent(True)
    image_format = bytes(reader.format()).lower()
    if image_format not in SUPPORTED_REFERENCE_IMAGE_FORMATS:
        return QImage()
    source_size = reader.size()
    if not source_size.isValid() or source_size.width() <= 0 or source_size.height() <= 0:
        return QImage()
    width, height = _bounded_dimensions(source_size.width(), source_size.height())
    reader.setScaledSize(QSize(width, height))
    return normalized_reference_image(reader.read())


def decode_reference_image(data_base64: str, mime_type: str = "image/png") -> QImage:
    if str(mime_type or "image/png").lower() not in SUPPORTED_REFERENCE_IMAGE_MIME_TYPES:
        return QImage()
    raw = _decoded_reference_image_bytes(data_base64)
    if raw is None:
        return QImage()
    return _decode_raw_reference_image(raw)


def canonicalize_reference_image(
    data_base64: str,
    mime_type: str = "image/png",
) -> tuple[str, tuple[float, float], int] | None:
    image = decode_reference_image(data_base64, mime_type)
    encoded = encode_reference_image(image)
    if encoded is None:
        return None
    canonical_base64, size = encoded
    byte_size = decoded_reference_image_size(canonical_base64)
    if byte_size is None:
        return None
    return canonical_base64, size, byte_size


def read_reference_image(path: str | Path) -> QImage:
    try:
        if Path(path).stat().st_size > MAX_REFERENCE_IMAGE_BYTES:
            return QImage()
    except OSError:
        return QImage()
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    image_format = bytes(reader.format()).lower()
    if image_format not in SUPPORTED_REFERENCE_IMAGE_FORMATS:
        return QImage()
    source_size = reader.size()
    if not source_size.isValid() or source_size.width() <= 0 or source_size.height() <= 0:
        return QImage()
    width, height = _bounded_dimensions(source_size.width(), source_size.height())
    reader.setScaledSize(QSize(width, height))
    return normalized_reference_image(reader.read())
