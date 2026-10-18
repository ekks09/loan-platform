"""Document-store selection.

The active store is chosen by the validated ``DOCUMENT_STORE`` setting. Call
:func:`get_document_store` from views/commands; never construct an adapter
directly — that keeps provider credentials and endpoint knowledge inside the
adapter modules.
"""
from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from core.storage.base import DocumentStore


def get_document_store() -> DocumentStore:
    """Return the configured document store, failing closed on a bad name."""
    name = getattr(settings, "DOCUMENT_STORE", "supabase")
    if name == "supabase":
        from core.storage.supabase_store import SupabaseDocumentStore

        return SupabaseDocumentStore()
    if name == "s3":
        from core.storage.s3_store import S3DocumentStore

        return S3DocumentStore()
    # settings validates DOCUMENT_STORE at boot; this is defence in depth.
    raise ImproperlyConfigured(
        f"Unsupported DOCUMENT_STORE {name!r}. Supported: supabase, s3."
    )


def document_storage_configured() -> bool:
    """True when the selected store has complete configuration."""
    return get_document_store().configured()
