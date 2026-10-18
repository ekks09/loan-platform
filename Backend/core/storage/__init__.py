"""Document storage abstraction.

Public surface: the :class:`DocumentStore` contract, its normalized value
objects and the :func:`get_document_store` selector. Adapters hold every
provider-specific detail (Supabase Storage, S3-compatible object stores).
"""
from core.storage.base import (
    DocumentStore,
    SignedUrl,
    StoredDocument,
    validate_document_file,
    validate_object_path,
)
from core.storage.registry import document_storage_configured, get_document_store

__all__ = [
    "DocumentStore",
    "SignedUrl",
    "StoredDocument",
    "document_storage_configured",
    "get_document_store",
    "validate_document_file",
    "validate_object_path",
]
