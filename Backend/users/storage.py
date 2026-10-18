"""Private Supabase Storage adapter for identity documents.

Rules enforced here:
* only paths are returned to callers; no public URLs are ever generated;
* file type is verified by magic bytes, not just the client-supplied MIME type;
* retrieval happens through short-lived signed URLs created on demand;
* the service-role key never leaves the backend.
"""
from __future__ import annotations

import logging
import uuid

from django.conf import settings

from core.errors import InvalidFileUpload, StorageUnavailable

logger = logging.getLogger(__name__)

try:  # pragma: no cover - import guard exercised via configuration checks
    from supabase import Client, create_client

    SUPABASE_AVAILABLE = True
except ImportError:  # pragma: no cover
    Client = None  # type: ignore[assignment]
    create_client = None  # type: ignore[assignment]
    SUPABASE_AVAILABLE = False

ALLOWED_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
MAX_BYTES = 5 * 1024 * 1024
MAGIC_PREFIXES = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/webp": (b"RIFF",),
}


class SupabaseStorage:
    BUCKET_NAME = settings.SUPABASE_STORAGE_BUCKET
    MAX_FILE_SIZE = MAX_BYTES

    _client = None

    @classmethod
    def configured(cls) -> bool:
        return bool(
            SUPABASE_AVAILABLE and settings.SUPABASE_URL and settings.SUPABASE_SERVICE_KEY
        )

    @classmethod
    def get_client(cls):
        if not cls.configured():
            raise StorageUnavailable(
                "Document storage is not configured on this deployment."
            )
        if cls._client is None:
            cls._client = create_client(
                settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY
            )
        return cls._client

    @classmethod
    def validate_file(cls, file) -> None:
        if not file:
            raise InvalidFileUpload("No file provided.")
        content_type = getattr(file, "content_type", None)
        if content_type not in ALLOWED_TYPES:
            raise InvalidFileUpload("Use a JPG, PNG or WebP image.")
        size = getattr(file, "size", 0) or 0
        if size <= 0:
            raise InvalidFileUpload("The uploaded file is empty.")
        if size > cls.MAX_FILE_SIZE:
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

    @classmethod
    def upload_file(cls, file, folder: str, user_identifier: str | int) -> dict:
        """Upload a document and return its private storage path only."""
        cls.validate_file(file)
        client = cls.get_client()
        extension = ALLOWED_TYPES[file.content_type]
        path = f"users/{user_identifier}/{folder}/{uuid.uuid4().hex}.{extension}"
        content = file.read()
        try:
            client.storage.from_(cls.BUCKET_NAME).upload(
                path=path,
                file=content,
                file_options={
                    "content-type": file.content_type,
                    "upsert": "false",
                },
            )
        except Exception as exc:  # provider errors are translated, not leaked
            logger.exception("Supabase upload failed for %s", folder)
            raise StorageUnavailable("Document upload failed. Please try again.") from exc
        return {"path": path}

    @classmethod
    def create_signed_url(cls, path: str, expires_in: int | None = None) -> str | None:
        if not path:
            return None
        client = cls.get_client()
        ttl = expires_in or settings.DOCUMENT_SIGNED_URL_TTL
        try:
            result = client.storage.from_(cls.BUCKET_NAME).create_signed_url(path, ttl)
        except Exception:
            logger.exception("Failed to sign document path")
            return None
        if isinstance(result, dict):
            return (
                result.get("signedURL")
                or result.get("signedUrl")
                or result.get("signed_url")
            )
        return getattr(result, "signed_url", None)

    @classmethod
    def delete_file(cls, path: str) -> bool:
        if not path:
            return True
        try:
            client = cls.get_client()
            client.storage.from_(cls.BUCKET_NAME).remove([path])
            return True
        except Exception:
            logger.warning("Failed to delete document at path", exc_info=True)
            return False

    @classmethod
    def delete_user_files(cls, *paths: str) -> None:
        for path in paths:
            if path:
                cls.delete_file(path)
