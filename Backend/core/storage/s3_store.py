"""S3-compatible object storage adapter (MinIO-friendly) for private documents.

A minimal, dependency-free implementation: SigV4 signing is done with the
standard library and HTTP goes through the already-pinned ``requests``
package. We deliberately do **not** pull in an SDK for three operations —
see docs/PRODUCTION_HARDENING_AUDIT.md (dependency-weight finding F6).

Scope and semantics:

* path-style addressing (``{endpoint}/{bucket}/{key}``), which MinIO and the
  mainstream S3-compatible services support;
* upload = SigV4-signed PUT, read = SigV4 **presigned** GET with the
  configured TTL, delete = SigV4-signed DELETE;
* a 404 on delete is success (idempotent); a provider error on delete returns
  ``False`` so the retention job can retry;
* on an ambiguous upload timeout the document path is attached to the raised
  error so the caller can attempt a compensating delete of that exact key;
* credentials and endpoints stay server-side; signed URLs are never logged.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

import requests
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

_SERVICE = "s3"
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_REQUEST_TIMEOUT = 15


def _signing_key(secret: str, date: str, region: str, service: str = _SERVICE) -> bytes:
    """Derive the SigV4 signing key (AWS-documented HMAC chain).

    ``service`` is a parameter only so the AWS published known-answer vector
    (which uses ``iam``) can verify the derivation in tests.
    """
    k_date = hmac.new(("AWS4" + secret).encode(), date.encode(), hashlib.sha256).digest()
    k_region = hmac.new(k_date, region.encode(), hashlib.sha256).digest()
    k_service = hmac.new(k_region, service.encode(), hashlib.sha256).digest()
    return hmac.new(k_service, b"aws4_request", hashlib.sha256).digest()


def _quote(value: str) -> str:
    return quote(value, safe="-_.~")


class S3DocumentStore(DocumentStoreBase):
    """S3-compatible implementation of :class:`DocumentStore`."""

    name = "s3"

    # ------------------------------------------------------------------
    def configured(self) -> bool:
        return bool(
            settings.S3_ENDPOINT_URL
            and settings.S3_ACCESS_KEY
            and settings.S3_SECRET_KEY
            and settings.S3_BUCKET
        )

    def _require_config(self) -> None:
        if not self.configured():
            raise StorageUnavailable(
                "Document storage is not configured on this deployment."
            )

    @property
    def _region(self) -> str:
        return settings.S3_REGION or "us-east-1"

    def _object_url(self, path: str, *, public: bool = False) -> str:
        endpoint = (
            settings.S3_PUBLIC_ENDPOINT_URL if public else settings.S3_ENDPOINT_URL
        ).rstrip("/")
        key = "/".join(_quote(part) for part in path.split("/"))
        return f"{endpoint}/{_quote(settings.S3_BUCKET)}/{key}"

    # ------------------------------------------------------------------
    def _signature(
        self,
        *,
        method: str,
        canonical_uri: str,
        canonical_query: str,
        headers: dict[str, str],
        signed_headers: str,
        payload_hash: str,
        amz_date: str,
        date: str,
    ) -> str:
        canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
        canonical_request = "\n".join(
            [method, canonical_uri, canonical_query, canonical_headers,
             signed_headers, payload_hash]
        )
        scope = f"{date}/{self._region}/{_SERVICE}/aws4_request"
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amz_date,
                scope,
                hashlib.sha256(canonical_request.encode()).hexdigest(),
            ]
        )
        return hmac.new(
            _signing_key(settings.S3_SECRET_KEY, date, self._region),
            string_to_sign.encode(),
            hashlib.sha256,
        ).hexdigest()

    def _signed_headers_request(
        self, method: str, path: str, *, body: bytes = b"", content_type: str = ""
    ) -> requests.Response:
        """Perform a SigV4 header-signed request (PUT/DELETE)."""
        self._require_config()
        url = self._object_url(path)
        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date = now.strftime("%Y%m%d")
        payload_hash = hashlib.sha256(body).hexdigest() if body else _EMPTY_SHA256
        host = urlsplit(url).netloc
        canonical_uri = urlsplit(url).path

        headers = {
            "host": host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        signed_headers = ";".join(sorted(headers))
        signature = self._signature(
            method=method,
            canonical_uri=canonical_uri,
            canonical_query="",
            headers=headers,
            signed_headers=signed_headers,
            payload_hash=payload_hash,
            amz_date=amz_date,
            date=date,
        )
        scope = f"{date}/{self._region}/{_SERVICE}/aws4_request"
        send_headers = {
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
            "Authorization": (
                "AWS4-HMAC-SHA256 "
                f"Credential={settings.S3_ACCESS_KEY}/{scope}, "
                f"SignedHeaders={signed_headers}, Signature={signature}"
            ),
        }
        if content_type:
            send_headers["Content-Type"] = content_type
        return requests.request(
            method, url, data=body or None, headers=send_headers, timeout=_REQUEST_TIMEOUT
        )

    # ------------------------------------------------------------------
    def upload(self, file, *, folder: str, user_identifier: str | int) -> StoredDocument:
        self.validate_file(file)
        path = build_object_path(
            folder=folder,
            user_identifier=user_identifier,
            extension=ALLOWED_TYPES[file.content_type],
        )
        content = file.read()
        try:
            response = self._signed_headers_request(
                "PUT", path, body=content, content_type=file.content_type
            )
        except requests.RequestException as exc:
            # Ambiguous: the object may exist server-side. Attach the exact
            # key so the caller can attempt a compensating delete.
            logger.warning("S3 upload transport failure for %s", folder, exc_info=True)
            raise StorageUnavailable(
                "Document upload failed. Please try again.",
                details={"path": path},
            ) from exc
        if response.status_code not in (200, 201, 204):
            logger.warning(
                "S3 upload rejected for %s (status %s)", folder, response.status_code
            )
            raise StorageUnavailable(
                "Document upload failed. Please try again.",
                details={"path": path},
            )
        return StoredDocument(
            path=path, content_type=file.content_type, size=len(content)
        )

    def signed_url(self, path: str, *, expires_in: int | None = None) -> SignedUrl | None:
        self._check(path)
        try:
            self._require_config()
        except StorageUnavailable:
            raise
        ttl = expires_in or settings.DOCUMENT_SIGNED_URL_TTL
        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date = now.strftime("%Y%m%d")
        scope = f"{date}/{self._region}/{_SERVICE}/aws4_request"
        # Presigned GETs are generated against the public endpoint so the
        # browser OS-level fetch hits the origin the signature covers.
        url = self._object_url(path, public=True)
        split = urlsplit(url)
        query = {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": f"{settings.S3_ACCESS_KEY}/{scope}",
            "X-Amz-Date": amz_date,
            "X-Amz-Expires": str(int(ttl)),
            "X-Amz-SignedHeaders": "host",
        }
        canonical_query = "&".join(
            f"{_quote(k)}={_quote(v)}" for k, v in sorted(query.items())
        )
        signature = self._signature(
            method="GET",
            canonical_uri=split.path,
            canonical_query=canonical_query,
            headers={"host": split.netloc},
            signed_headers="host",
            payload_hash="UNSIGNED-PAYLOAD",
            amz_date=amz_date,
            date=date,
        )
        return SignedUrl(
            url=f"{url}?{canonical_query}&X-Amz-Signature={signature}",
            expires_in=int(ttl),
        )

    def delete(self, path: str) -> bool:
        self._check(path)
        try:
            response = self._signed_headers_request("DELETE", path)
        except requests.RequestException:
            # Unknown whether the object is gone; the retention job retries.
            logger.warning("S3 delete transport failure for a stored path", exc_info=True)
            return False
        if response.status_code in (200, 202, 204, 404):
            return True
        logger.warning("S3 delete rejected (status %s)", response.status_code)
        return False
