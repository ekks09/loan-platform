"""Contract tests for the document-storage abstraction.

Both adapters run the same behavioural contract with their transport mocked.
The Supabase SDK client and the S3 HTTP layer are faked; validation and path
policy run for real. An optional MinIO integration test exercises the real S3
adapter end to end and skips cleanly when no endpoint is configured.
"""
from __future__ import annotations

import io
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase, override_settings

from core.errors import InvalidFileUpload, StorageUnavailable
from core.storage import (
    DocumentStore,
    SignedUrl,
    StoredDocument,
    document_storage_configured,
    get_document_store,
    validate_document_file,
    validate_object_path,
)
from core.storage.base import InvalidObjectPath
from core.storage.s3_store import S3DocumentStore, _signing_key
from core.storage.supabase_store import SupabaseDocumentStore


def _jpeg(size: int = 1024, name: str = "doc.jpg"):
    class FakeUpload:
        def __init__(self, data, content_type):
            self._data = data
            self._buf = io.BytesIO(data)
            self.content_type = content_type
            self.size = len(data)
            self.name = name

        def read(self, n=-1):
            return self._buf.read(n)

        def seek(self, pos):
            return self._buf.seek(pos)

    return FakeUpload(b"\xff\xd8\xff" + b"0" * (size - 3), "image/jpeg")


def _png(size: int = 1024):
    class FakeUpload:
        def __init__(self):
            self._buf = io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * (size - 8))
            self.content_type = "image/png"
            self.size = size

        def read(self, n=-1):
            return self._buf.read(n)

        def seek(self, pos):
            return self._buf.seek(pos)

    return FakeUpload()


class PathPolicyTests(SimpleTestCase):
    def test_platform_paths_are_accepted(self):
        for path in (
            "users/12/id_front/9f1c2d3e4a5b6c7d8e9f0a1b2c3d4e5f.jpg",
            "users/stagingid/selfie/ab12cd34.png",
            "users/9/id_back/doc-file_1.webp",
        ):
            self.assertEqual(validate_object_path(path), path)

    def test_traversal_and_foreign_shapes_are_rejected(self):
        for path in (
            "",
            None,
            "/users/1/id_front/abcdefghij.jpg",
            "users/1/../../etc/passwd",
            "users/1//id_front/abcdefghij.jpg",
            "users/1/id_front/../../../etc/passwd",
            "../users/1/id_front/abcdefghij.jpg",
            "users/1/id_front/abcdef\x00gh.jpg",
            "users/1/other_folder/abcdefghij.jpg",
            "users/1/id_front/abcdefghij.exe",
            "users/1/id_front/abcdefghij.jpg/extra",
            "users/1/id_front/",
            "public/1/id_front/abcdefghij.jpg",
            "users//id_front/abcdefghij.jpg",
        ):
            with self.assertRaises(InvalidObjectPath, msg=repr(path)):
                validate_object_path(path)  # type: ignore[arg-type]


class SharedValidationTests(SimpleTestCase):
    def test_rejects_non_image_bytes(self):
        class NotImage:
            content_type = "image/png"
            size = 20

            def read(self, n=-1):
                return b"not an image at all"

            def seek(self, pos):
                return 0

        with self.assertRaises(InvalidFileUpload):
            validate_document_file(NotImage(), max_bytes=5 * 1024 * 1024)

    def test_rejects_wrong_content_type_and_oversize(self):
        upload = _jpeg()
        upload.content_type = "application/pdf"
        with self.assertRaises(InvalidFileUpload):
            validate_document_file(upload, max_bytes=5 * 1024 * 1024)
        big = _jpeg(size=6 * 1024 * 1024)
        with self.assertRaises(InvalidFileUpload):
            validate_document_file(big, max_bytes=5 * 1024 * 1024)

    def test_accepts_a_real_jpeg_header(self):
        validate_document_file(_jpeg(), max_bytes=5 * 1024 * 1024)
        validate_document_file(_png(), max_bytes=5 * 1024 * 1024)


@override_settings(
    SUPABASE_URL="https://supabase.test",
    SUPABASE_SERVICE_KEY="service-role-test-key",
)
class SupabaseAdapterContractTests(TestCase):
    def setUp(self):
        self.store = SupabaseDocumentStore()
        self.bucket = MagicMock(name="bucket")
        client = MagicMock(name="client")
        client.storage.from_.return_value = self.bucket
        # The adapter caches its SDK client as a class attribute; swap it for
        # the duration of the test instead of depending on that cache.
        self._old_client = SupabaseDocumentStore._client
        SupabaseDocumentStore._client = client
        self.addCleanup(setattr, SupabaseDocumentStore, "_client", self._old_client)

    def test_contract_shape(self):
        self.assertIsInstance(self.store, DocumentStore)
        self.assertEqual(self.store.name, "supabase")

    def test_upload_returns_a_private_platform_path(self):
        stored = self.store.upload(_jpeg(), folder="id_front", user_identifier=42)
        self.assertIsInstance(stored, StoredDocument)
        self.assertTrue(stored.path.startswith("users/42/id_front/"))
        self.assertTrue(stored.path.endswith(".jpg"))
        validate_object_path(stored.path)
        args, kwargs = self.bucket.upload.call_args
        self.assertEqual(kwargs["file_options"]["upsert"], "false")

    def test_upload_failure_exposes_the_ambiguous_path_for_cleanup(self):
        self.bucket.upload.side_effect = RuntimeError("boom")
        with self.assertRaises(StorageUnavailable) as ctx:
            self.store.upload(_jpeg(), folder="selfie", user_identifier=7)
        path = ctx.exception.details.get("path")
        self.assertTrue(path.startswith("users/7/selfie/"))

    def test_signed_url_returns_url_and_ttl(self):
        self.bucket.create_signed_url.return_value = {
            "signedURL": "https://signed.test/doc"
        }
        signed = self.store.signed_url(
            "users/42/id_front/9f1c2d3e4a5b6c7d8e9f0a1b2c3d4e5f.jpg"
        )
        self.assertIsInstance(signed, SignedUrl)
        self.assertEqual(signed.url, "https://signed.test/doc")
        self.assertEqual(signed.expires_in, self._ttl())

    def _ttl(self):
        from django.conf import settings

        return settings.DOCUMENT_SIGNED_URL_TTL

    def test_signed_url_failure_returns_none_not_public_access(self):
        self.bucket.create_signed_url.side_effect = RuntimeError("boom")
        self.assertIsNone(
            self.store.signed_url("users/42/id_front/9f1c2d3e4a5b6c7d8e9f0a1b2c3d4e5f.jpg")
        )

    def test_traversal_never_reaches_the_remote(self):
        with self.assertRaises(InvalidObjectPath):
            self.store.delete("users/1/../../all")
        self.bucket.remove.assert_not_called()
        with self.assertRaises(InvalidObjectPath):
            self.store.signed_url("users/1/../id_back/x.jpg")
        self.bucket.create_signed_url.assert_not_called()

    @override_settings(SUPABASE_URL="", SUPABASE_SERVICE_KEY="")
    def test_unconfigured_store_reports_not_configured(self):
        fresh = SupabaseDocumentStore()
        self.assertFalse(fresh.configured())


class S3SigningTests(SimpleTestCase):
    def test_signing_key_matches_the_aws_known_answer(self):
        # Published AWS SigV4 test vector (service=iam, region us-east-1).
        key = _signing_key(
            "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
            "20150830",
            "us-east-1",
            service="iam",
        )
        self.assertEqual(
            key.hex(),
            "c4afb1cc5771d871763a393e44b703571b55cc28424d1a5e86da6ed3c154a4b9",
        )


@override_settings(
    S3_ENDPOINT_URL="http://minio.test:9000",
    # Keep the public endpoint pinned to the same value so the existing
    # presigning tests stay deterministic; the split path has its own test.
    S3_PUBLIC_ENDPOINT_URL="http://minio.test:9000",
    S3_ACCESS_KEY="test-access",
    S3_SECRET_KEY="test-secret",
    S3_BUCKET="docs",
    S3_REGION="us-east-1",
    DOCUMENT_STORE="s3",
)
class S3AdapterContractTests(TestCase):
    def setUp(self):
        self.store = S3DocumentStore()

    def _response(self, status=200):
        response = MagicMock(name="response")
        response.status_code = status
        return response

    def test_contract_shape(self):
        self.assertIsInstance(self.store, DocumentStore)
        self.assertEqual(self.store.name, "s3")
        self.assertTrue(self.store.configured())

    @override_settings(S3_SECRET_KEY="")
    def test_unconfigured_store_reports_not_configured(self):
        self.assertFalse(S3DocumentStore().configured())

    def test_upload_puts_sigv4_signed_bytes_to_the_object_url(self):
        with patch("core.storage.s3_store.requests.request") as http:
            http.return_value = self._response(200)
            stored = self.store.upload(_png(), folder="id_back", user_identifier=9)
        self.assertTrue(stored.path.startswith("users/9/id_back/"))
        method, url = http.call_args.args
        self.assertEqual(method, "PUT")
        self.assertEqual(
            url, f"http://minio.test:9000/docs/{stored.path}"
        )
        headers = http.call_args.kwargs["headers"]
        self.assertIn("Authorization", headers)
        self.assertIn("AWS4-HMAC-SHA256 Credential=test-access/", headers["Authorization"])
        self.assertEqual(headers["Content-Type"], "image/png")

    def test_upload_transport_failure_is_ambiguous_with_cleanup_path(self):
        import requests as _requests

        with patch("core.storage.s3_store.requests.request") as http:
            http.side_effect = _requests.Timeout("timeout")
            with self.assertRaises(StorageUnavailable) as ctx:
                self.store.upload(_png(), folder="id_front", user_identifier=9)
        self.assertTrue(ctx.exception.details["path"].startswith("users/9/id_front/"))

    def test_upload_rejection_is_a_storage_error_not_a_crash(self):
        with patch("core.storage.s3_store.requests.request") as http:
            http.return_value = self._response(403)
            with self.assertRaises(StorageUnavailable):
                self.store.upload(_png(), folder="id_front", user_identifier=9)

    def test_presigned_url_structure_and_determinism(self):
        path = "users/9/id_front/9f1c2d3e4a5b6c7d8e9f0a1b2c3d4e5f.png"
        frozen = datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)
        with patch("core.storage.s3_store.datetime") as dt:
            dt.now.return_value = frozen
            signed = self.store.signed_url(path, expires_in=120)
        self.assertIsInstance(signed, SignedUrl)
        self.assertEqual(signed.expires_in, 120)
        self.assertTrue(
            signed.url.startswith(f"http://minio.test:9000/docs/{path}?")
        )
        for marker in (
            "X-Amz-Algorithm=AWS4-HMAC-SHA256",
            "X-Amz-Credential=test-access%2F20261018%2Fus-east-1%2Fs3%2Faws4_request",
            "X-Amz-Date=20261018T120000Z",
            "X-Amz-Expires=120",
            "X-Amz-SignedHeaders=host",
            "X-Amz-Signature=",
        ):
            self.assertIn(marker, signed.url)
        # Deterministic: same inputs, same signature.
        with patch("core.storage.s3_store.datetime") as dt:
            dt.now.return_value = frozen
            self.assertEqual(self.store.signed_url(path, expires_in=120).url, signed.url)

    def test_presigned_url_uses_the_public_endpoint_when_configured(self):
        # Split-horizon signing: server-side ops keep S3_ENDPOINT_URL, but the
        # browser must receive a URL for the host it can actually reach (the
        # signature covers that host header).
        path = "users/9/id_front/9f1c2d3e4a5b6c7d8e9f0a1b2c3d4e5f.png"
        with override_settings(S3_PUBLIC_ENDPOINT_URL="http://localhost:9000"):
            signed = self.store.signed_url(path, expires_in=60)
            self.assertTrue(
                signed.url.startswith(f"http://localhost:9000/docs/{path}?"),
                signed.url,
            )
        # default when unset: fall back to the server endpoint
        with override_settings(S3_PUBLIC_ENDPOINT_URL="http://minio.test:9000"):
            self.assertTrue(
                self.store.signed_url(path, expires_in=60).url.startswith(
                    "http://minio.test:9000/"
                )
            )

    def test_delete_treats_missing_objects_as_done(self):
        with patch("core.storage.s3_store.requests.request") as http:
            http.return_value = self._response(204)
            self.assertTrue(
                self.store.delete("users/9/id_front/9f1c2d3e4a5b6c7d8e9f0a1b2c3d4e5f.png")
            )
            http.return_value = self._response(404)
            self.assertTrue(
                self.store.delete("users/9/id_front/9f1c2d3e4a5b6c7d8e9f0a1b2c3d4e5f.png")
            )
            http.return_value = self._response(500)
            self.assertFalse(
                self.store.delete("users/9/id_front/9f1c2d3e4a5b6c7d8e9f0a1b2c3d4e5f.png")
            )

    def test_traversal_never_reaches_the_remote(self):
        with patch("core.storage.s3_store.requests.request") as http:
            with self.assertRaises(InvalidObjectPath):
                self.store.delete("../../everything")
            with self.assertRaises(InvalidObjectPath):
                self.store.signed_url("users/1/../dsecrets.png")
        http.assert_not_called()


class RegistryTests(TestCase):
    def test_default_is_supabase(self):
        self.assertIsInstance(get_document_store(), SupabaseDocumentStore)

    @override_settings(DOCUMENT_STORE="s3", S3_ENDPOINT_URL="http://x", S3_ACCESS_KEY="a", S3_SECRET_KEY="b", S3_BUCKET="c")
    def test_s3_is_selected_by_configuration(self):
        self.assertIsInstance(get_document_store(), S3DocumentStore)

    @override_settings(DOCUMENT_STORE="bogus")
    def test_unknown_store_fails_closed(self):
        with self.assertRaises(ImproperlyConfigured):
            get_document_store()

    @override_settings(DOCUMENT_STORE="s3", S3_ENDPOINT_URL="", S3_ACCESS_KEY="", S3_SECRET_KEY="", S3_BUCKET="")
    def test_configured_check_reflects_the_active_store(self):
        # Switching providers must not silently fall back to Supabase config.
        self.assertFalse(document_storage_configured())


@override_settings(
    DOCUMENT_STORE="s3",
)
class MinioIntegrationTests(TestCase):
    """End-to-end adapter exercise against a real S3-compatible endpoint.

    Runs only when S3_TEST_ENDPOINT (plus keys/bucket) is set — e.g. the
    docker-compose MinIO service. Skips silently otherwise. It performs a
    real upload, a real presigned GET and a real delete with throwaway keys.
    """

    def setUp(self):
        self.endpoint = os.environ.get("S3_TEST_ENDPOINT")
        if not self.endpoint:
            self.skipTest("No S3_TEST_ENDPOINT configured (MinIO not running)")

    def test_upload_sign_download_delete_roundtrip(self):
        import requests as _requests

        settings_overrides = override_settings(
            S3_ENDPOINT_URL=self.endpoint,
            S3_ACCESS_KEY=os.environ.get("S3_TEST_ACCESS_KEY", "minioadmin"),
            S3_SECRET_KEY=os.environ.get("S3_TEST_SECRET_KEY", "minioadmin"),
            S3_BUCKET=os.environ.get("S3_TEST_BUCKET", "documents"),
            S3_REGION=os.environ.get("S3_TEST_REGION", "us-east-1"),
        )
        settings_overrides.enable()
        self.addCleanup(settings_overrides.disable)
        store = S3DocumentStore()
        stored = store.upload(_jpeg(), folder="id_front", user_identifier="itest")
        signed = store.signed_url(stored.path)
        self.assertIsNotNone(signed)
        fetched = _requests.get(signed.url, timeout=10)
        self.assertEqual(fetched.status_code, 200)
        self.assertTrue(fetched.content.startswith(b"\xff\xd8\xff"))
        self.assertTrue(store.delete(stored.path))
