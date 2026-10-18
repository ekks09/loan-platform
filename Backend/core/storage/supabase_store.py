"""Supabase Storage adapter for private identity documents.

Ports the proven behaviour of the original ``users.storage.SupabaseStorage``
class onto the :class:`DocumentStore` contract: paths only, magic-byte
validation, short-lived signed URLs, service-role key server-side.
"""
from __future__ import annotations

import logging

from django.conf import settings

from core.errors import StorageUnavailable
from core.storage.base import (
    ALLOWED_TYPES,
    DocumentStoreBase,
    SignedUrl,
    StoredDocument,
    build_object_path,
)

logger = logging.getLogger(__name__)

try:  # pragma: no cover - import guard exercised via configuration checks
    from supabase import create_client

    SUPABASE_AVAILABLE = True
except ImportError:  # pragma: no cover
    create_client = None  # type: ignore[assignment]
    SUPABASE_AVAILABLE = False


class SupabaseDocumentStore(DocumentStoreBase):
    """Supabase Storage implementation of :class:`DocumentStore`."""

    name = "supabase"

    _client = None

    @classmethod
    def configured(cls) -> bool:
        return bool(
            SUPABASE_AVAILABLE and settings.SUPABASE_URL and settings.SUPABASE_SERVICE_KEY
        )

    @classmethod
    def _get_client(cls):
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
    def _bucket(cls):
        return cls._get_client().storage.from_(settings.SUPABASE_STORAGE_BUCKET)

    def upload(self, file, *, folder: str, user_identifier: str | int) -> StoredDocument:
        self.validate_file(file)
        path = build_object_path(
            folder=folder,
            user_identifier=user_identifier,
            extension=ALLOWED_TYPES[file.content_type],
        )
        content = file.read()
        try:
            self._bucket().upload(
                path=path,
                file=content,
                file_options={
                    "content-type": file.content_type,
                    "upsert": "false",
                },
            )
        except Exception as exc:  # provider errors are translated, not leaked
            # The path was generated before the call, so the caller can
            # attempt a compensating delete of the exact same key.
            logger.exception("Storage upload failed for %s", folder)
            raise StorageUnavailable(
                "Document upload failed. Please try again.",
                details={"path": path},
            ) from exc
        return StoredDocument(
            path=path, content_type=file.content_type, size=len(content)
        )

    def signed_url(self, path: str, *, expires_in: int | None = None) -> SignedUrl | None:
        self._check(path)
        ttl = expires_in or settings.DOCUMENT_SIGNED_URL_TTL
        try:
            result = self._bucket().create_signed_url(path, ttl)
        except Exception:
            logger.exception("Failed to sign a document path")
            return None
        url = None
        if isinstance(result, dict):
            url = (
                result.get("signedURL")
                or result.get("signedUrl")
                or result.get("signed_url")
            )
        else:
            url = getattr(result, "signed_url", None)
        if not url:
            logger.warning("Storage returned no signed URL for a document path")
            return None
        return SignedUrl(url=str(url), expires_in=ttl)

    def delete(self, path: str) -> bool:
        self._check(path)
        try:
            self._bucket().remove([path])
            return True
        except Exception:
            # Whether the object is gone is unknown; the retention job retries.
            logger.warning("Document delete failed for a stored path", exc_info=True)
            return False
