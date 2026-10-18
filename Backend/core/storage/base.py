"""Provider-neutral document-storage contract.

Identity documents are private. Every adapter in this package must:

* return **paths only** to callers — never public URLs;
* verify file type by magic bytes, not the client-supplied content type;
* issue retrieval through short-lived signed URLs created on demand;
* keep credentials on the server and never log signed URLs or payloads
  (only relative paths, which are meaningless without a signature).

Object paths are generated server-side as
``users/<owner>/<folder>/<random-token>.<ext>`` (uuid4 hex in production).
:func:`validate_object_path` is applied to every read/delete so a
compromised caller cannot traverse, overwrite or delete arbitrary objects.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from core.errors import InvalidFileUpload, StorageUnavailable

ALLOWED_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
MAGIC_PREFIXES = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/webp": (b"RIFF",),
}

# Server-side path policy. Folders are the fixed document slots the product
# uses; owners are user pks or registration staging ids; object names are
# platform-generated tokens (uuid4 hex in production). Anything outside this
# shape — including every form of traversal — is rejected before any remote
# call, so reads and deletes can never touch an attacker-chosen key.
_ALLOWED_FOLDERS = {"id_front", "id_back", "selfie"}
_PATH_RE = re.compile(
    r"^users/(?P<owner>[A-Za-z0-9]{1,40})/(?P<folder>[a-z_]{3,20})/"
    r"(?P<object>[A-Za-z0-9_-]{4,64})\.(?P<ext>jpg|png|webp)$"
)


class InvalidObjectPath(StorageUnavailable):
    code = "invalid_object_path"
    http_status = 400
    default_message = "The document path is not valid."


def build_object_path(*, folder: str, user_identifier: str | int, extension: str) -> str:
    """Generate the private object path for a new document."""
    folder = str(folder).strip().lower()
    if folder not in _ALLOWED_FOLDERS:
        raise InvalidObjectPath(
            f"Unknown document folder {folder!r}.",
            details={"folder": folder},
        )
    owner = re.sub(r"[^A-Za-z0-9]", "", str(user_identifier))[:40]
    if not owner:
        raise InvalidObjectPath("A document owner is required.")
    return f"users/{owner}/{folder}/{uuid.uuid4().hex}.{extension}"


def validate_object_path(path: str) -> str:
    """Reject anything that is not a platform-generated document path."""
    if not path or not isinstance(path, str):
        raise InvalidObjectPath("A document path is required.")
    if "\x00" in path or ".." in path or path.startswith("/") or "//" in path:
        raise InvalidObjectPath("The document path is not permitted.")
    match = _PATH_RE.match(path)
    if not match or match.group("folder") not in _ALLOWED_FOLDERS:
        raise InvalidObjectPath("The document path is not recognised.")
    return path


def validate_document_file(file, *, max_bytes: int) -> None:
    """Validate an uploaded document by size and content, not by name."""
    if not file:
        raise InvalidFileUpload("No file provided.")
    content_type = getattr(file, "content_type", None)
    if content_type not in ALLOWED_TYPES:
        raise InvalidFileUpload("Use a JPG, PNG or WebP image.")
    size = getattr(file, "size", 0) or 0
    if size <= 0:
        raise InvalidFileUpload("The uploaded file is empty.")
    if size > max_bytes:
        raise InvalidFileUpload(
            f"File too large ({size / (1024 * 1024):.1f} MB). Maximum is 5 MB."
        )
    header = file.read(16)
    file.seek(0)
    prefixes = MAGIC_PREFIXES[content_type]
    if not any(header.startswith(prefix) for prefix in prefixes):
        raise InvalidFileUpload("The file content does not match an image format.")
    if content_type == "image/webp" and b"WEBP" not in header:
        raise InvalidFileUpload("The file content does not match a WebP image.")


@dataclass(frozen=True)
class StoredDocument:
    """Normalized upload result. Only the private path crosses the boundary."""

    path: str
    content_type: str = ""
    size: int = 0


@dataclass(frozen=True)
class SignedUrl:
    """Normalized signed-URL result: the URL and how long it is valid.

    Consumers must never log or forward the URL; it grants time-boxed access
    to a private document.
    """

    url: str
    expires_in: int


@runtime_checkable
class DocumentStore(Protocol):
    """Contract for private document storage adapters."""

    name: str

    def configured(self) -> bool:
        """True when the adapter has every setting it needs (never logs secrets)."""

    def validate_file(self, file) -> None:
        """Raise :class:`InvalidFileUpload` for unacceptable uploads."""

    def upload(self, file, *, folder: str, user_identifier: str | int) -> StoredDocument:
        """Validate and store a document; return its private path.

        On an ambiguous failure (timeout after the remote side may have
        stored the object) raise :class:`StorageUnavailable` whose
        ``details`` carry ``path`` so the caller can attempt a compensating
        delete of the same server-generated path.
        """

    def signed_url(self, path: str, *, expires_in: int | None = None) -> SignedUrl | None:
        """Return a short-lived signed URL for a platform-generated path.

        ``None`` means the provider could not sign right now; callers treat it
        as an unavailable document, never as permission to make it public.
        """

    def delete(self, path: str) -> bool:
        """Delete a platform-generated path. ``True`` also when already gone."""


class DocumentStoreBase:
    """Shared, provider-independent behaviour for adapters."""

    name = "abstract"

    def validate_file(self, file) -> None:
        from django.conf import settings

        validate_document_file(file, max_bytes=settings.DOCUMENT_MAX_BYTES)

    @staticmethod
    def _check(path: str) -> str:
        return validate_object_path(path)
