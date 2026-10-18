"""Backward-compatible facade over the configured :class:`DocumentStore`.

Original import location for document storage. The implementation moved to
``core.storage`` (Supabase + S3-compatible adapters behind a provider-neutral
contract selected by ``DOCUMENT_STORE``). This module keeps the legacy
classmethod API working so older callers and extensions do not break.

New code should use ``core.storage.get_document_store()`` directly.
"""
from __future__ import annotations

from django.conf import settings

from core.storage import get_document_store
from core.storage.base import ALLOWED_TYPES

# Kept for external callers that introspected the old module.
ALLOWED_TYPES = ALLOWED_TYPES
MAX_BYTES = 5 * 1024 * 1024


class SupabaseStorage:
    """Legacy name for the configured document store (delegating)."""

    BUCKET_NAME = settings.SUPABASE_STORAGE_BUCKET
    MAX_FILE_SIZE = MAX_BYTES

    @classmethod
    def configured(cls) -> bool:
        return get_document_store().configured()

    @classmethod
    def validate_file(cls, file) -> None:
        get_document_store().validate_file(file)

    @classmethod
    def upload_file(cls, file, folder: str, user_identifier: str | int) -> dict:
        stored = get_document_store().upload(
            file, folder=folder, user_identifier=user_identifier
        )
        return {"path": stored.path}

    @classmethod
    def create_signed_url(cls, path: str, expires_in: int | None = None) -> str | None:
        signed = get_document_store().signed_url(path, expires_in=expires_in)
        return signed.url if signed else None

    @classmethod
    def delete_file(cls, path: str) -> bool:
        return get_document_store().delete(path)

    @classmethod
    def delete_user_files(cls, *paths: str) -> None:
        for path in paths:
            if path:
                cls.delete_file(path)
