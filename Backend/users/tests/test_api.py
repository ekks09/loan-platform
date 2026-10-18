from __future__ import annotations

from unittest.mock import patch

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from core.tests.helpers import (
    attach_documents,
    auth_client,
    make_staff,
    make_user,
    make_verified_user,
)
from users.models import ConsentRecord, PasswordResetCode, User


def png_file(name: str = "id.png") -> SimpleUploadedFile:
    # Minimal PNG magic header is enough for the application's validator.
    return SimpleUploadedFile(
        name, b"\x89PNG\r\n\x1a\n" + b"0" * 64, content_type="image/png"
    )


def jpeg_file(name: str = "photo.jpg") -> SimpleUploadedFile:
    return SimpleUploadedFile(
        name, b"\xff\xd8\xff\xe0" + b"0" * 64, content_type="image/jpeg"
    )


class RegistrationApiTests(APITestCase):
    def payload(self, **overrides):
        data = {
            "phone": "0712345678",
            "national_id": "12345678",
            "password": "StrongPass!234",
            "consent": True,
            "consent_version": settings.CONSENT_VERSION,
        }
        data.update(overrides)
        return data

    def test_json_registration_creates_account_and_records_consent(self):
        response = self.client.post("/api/users/register/", self.payload(), format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertFalse(body["has_documents"])

        user = User.objects.get(phone="254712345678")
        self.assertEqual(user.verification_status, User.VerificationStatus.PENDING)
        self.assertEqual(user.consents.count(), 2)
        self.assertTrue(
            user.consents.filter(
                purpose=ConsentRecord.Purpose.TERMS,
                version=settings.TERMS_VERSION,
            ).exists()
        )

    def test_consent_is_required(self):
        response = self.client.post(
            "/api/users/register/", self.payload(consent=False), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["error"]["code"], "validation_error")
        self.assertFalse(User.objects.exists())

    def test_stale_consent_version_is_rejected(self):
        response = self.client.post(
            "/api/users/register/",
            self.payload(consent_version="2019-01"),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("consent_version", response.json()["error"]["details"])

    def test_invalid_phone_is_rejected_without_creating_an_account(self):
        response = self.client.post(
            "/api/users/register/", self.payload(phone="0812"), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("phone", response.json()["error"]["details"])

    def test_weak_password_is_rejected(self):
        response = self.client.post(
            "/api/users/register/", self.payload(password="password"), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password", response.json()["error"]["details"])

    def test_duplicate_account_returns_conflict_without_leaking_details(self):
        make_user(phone="0712345678", national_id="11111111")
        response = self.client.post(
            "/api/users/register/", self.payload(phone="0712345678"), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # The serializer catches the duplicate phone before the IntegrityError
        # path, and the message is deliberately generic.
        self.assertIn("phone", response.json()["error"]["details"])

    def test_duplicate_national_id_returns_conflict(self):
        make_user(phone="0700000009", national_id="12345678")
        response = self.client.post(
            "/api/users/register/",
            self.payload(phone="0712345678", national_id="12345678"),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("national_id", response.json()["error"]["details"])

    def test_multipart_registration_without_storage_fails_closed(self):
        response = self.client.post(
            "/api/users/register/",
            {
                **self.payload(),
                "id_front": png_file(),
                "id_back": png_file("back.png"),
                "selfie": jpeg_file("selfie.jpg"),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.json()["error"]["code"], "storage_unavailable")
        self.assertFalse(User.objects.exists())

    @patch("users.storage.SupabaseStorage.configured", return_value=True)
    @patch("users.storage.SupabaseStorage.validate_file", return_value=None)
    @patch("users.storage.SupabaseStorage.upload_file")
    def test_multipart_registration_stores_private_paths(
        self, upload_file, _validate, _configured
    ):
        upload_file.side_effect = [
            {"path": "users/staging/id_front/a.jpg"},
            {"path": "users/staging/id_back/b.jpg"},
            {"path": "users/staging/selfie/c.jpg"},
        ]
        response = self.client.post(
            "/api/users/register/",
            {
                **self.payload(),
                "id_front": jpeg_file("front.jpg"),
                "id_back": jpeg_file("back.jpg"),
                "selfie": jpeg_file("selfie.jpg"),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        user = User.objects.get(phone="254712345678")
        self.assertTrue(user.has_uploaded_documents)
        self.assertEqual(user.verification_status, User.VerificationStatus.PENDING)
        self.assertNotIn("http://", user.id_front_path)
        self.assertNotIn("https://", user.id_front_path)

    @patch("users.storage.SupabaseStorage.configured", return_value=True)
    @patch("users.storage.SupabaseStorage.validate_file", return_value=None)
    @patch("users.storage.SupabaseStorage.upload_file")
    @patch("users.storage.SupabaseStorage.delete_user_files")
    def test_storage_failure_during_upload_rolls_back_account(
        self, delete_files, upload_file, _validate, _configured
    ):
        from core.errors import StorageUnavailable

        upload_file.side_effect = [
            {"path": "users/staging/id_front/a.jpg"},
            StorageUnavailable("provider down"),
        ]
        response = self.client.post(
            "/api/users/register/",
            {
                **self.payload(),
                "id_front": jpeg_file("front.jpg"),
                "id_back": jpeg_file("back.jpg"),
                "selfie": jpeg_file("selfie.jpg"),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertFalse(User.objects.exists())
        delete_files.assert_called_once()

    def test_partial_document_upload_is_rejected(self):
        response = self.client.post(
            "/api/users/register/",
            {**self.payload(), "id_front": png_file()},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["error"]["code"], "invalid_file_upload")
        self.assertFalse(User.objects.exists())


class LoginAndProfileApiTests(APITestCase):
    def setUp(self):
        self.user = make_user()

    def test_login_returns_short_lived_access_token(self):
        response = self.client.post(
            "/api/users/login/",
            {"phone": "0712345678", "password": "StrongPass!234"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["token_type"], "Bearer")
        self.assertEqual(body["expires_in"], settings.JWT_ACCESS_TTL_MINUTES * 60)
        self.assertFalse(body["is_verified"])
        self.assertIn("access", body)

    def test_login_is_generic_for_unknown_phone_and_wrong_password(self):
        unknown = self.client.post(
            "/api/users/login/",
            {"phone": "0700000000", "password": "StrongPass!234"},
            format="json",
        )
        wrong = self.client.post(
            "/api/users/login/",
            {"phone": "0712345678", "password": "WrongPass!234"},
            format="json",
        )
        self.assertEqual(unknown.status_code, 400)
        self.assertEqual(wrong.status_code, 400)
        self.assertEqual(
            unknown.json()["error"]["details"]["non_field_errors"],
            wrong.json()["error"]["details"]["non_field_errors"],
        )
        self.assertIn("Invalid phone number or password", unknown.content.decode())

    def test_inactive_account_cannot_log_in(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        response = self.client.post(
            "/api/users/login/",
            {"phone": "0712345678", "password": "StrongPass!234"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("disabled", response.content.decode())

    def test_me_requires_authentication_and_never_returns_full_id(self):
        anonymous = self.client.get("/api/users/me/")
        self.assertEqual(anonymous.status_code, 401)
        self.assertEqual(anonymous.json()["error"]["code"], "not_authenticated")

        client = auth_client(self.user)
        response = client.get("/api/users/me/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertNotIn("national_id", body)
        self.assertEqual(body["national_id_masked"], "****5678")
        self.assertFalse(body["consent_current"])

    def test_tampered_token_is_rejected(self):
        client = auth_client(self.user)
        token = client._credentials["HTTP_AUTHORIZATION"].split()[1]
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}tampered")
        response = client.get("/api/users/me/")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "authentication_failed")


class DocumentUploadApiTests(APITestCase):
    def setUp(self):
        self.user = make_verified_user()

    def test_upload_requires_authentication(self):
        response = self.client.post("/api/users/documents/")
        self.assertEqual(response.status_code, 401)

    def test_upload_requires_storage_configuration(self):
        client = auth_client(self.user)
        response = client.post(
            "/api/users/documents/",
            {
                "id_front": png_file(),
                "id_back": png_file("back.png"),
                "selfie": jpeg_file("selfie.jpg"),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "storage_unavailable")

    @patch("users.storage.SupabaseStorage.configured", return_value=True)
    def test_upload_rejects_missing_files(self, _configured):
        client = auth_client(self.user)
        response = client.post(
            "/api/users/documents/", {"id_front": png_file()}, format="multipart"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_file_upload")
        self.assertEqual(
            set(response.json()["error"]["details"]["missing"]), {"id_back", "selfie"}
        )

    @patch("users.storage.SupabaseStorage.configured", return_value=True)
    def test_upload_validation_rejects_content_that_is_not_an_image(self, _configured):
        bad = SimpleUploadedFile("id.png", b"not-an-image", content_type="image/png")
        client = auth_client(self.user)
        response = client.post(
            "/api/users/documents/",
            {"id_front": bad, "id_back": png_file("back.png"), "selfie": jpeg_file()},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("content", response.content.decode())

    @patch("users.storage.SupabaseStorage.configured", return_value=True)
    @patch("users.storage.SupabaseStorage.upload_file")
    def test_upload_resets_verification_and_replaces_paths(
        self, upload_file, _configured
    ):
        upload_file.side_effect = [
            {"path": "users/1/id_front/new.jpg"},
            {"path": "users/1/id_back/new.jpg"},
            {"path": "users/1/selfie/new.jpg"},
        ]
        client = auth_client(self.user)
        response = client.post(
            "/api/users/documents/",
            {
                "id_front": png_file(),
                "id_back": png_file("back.png"),
                "selfie": jpeg_file("selfie.jpg"),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.id_front_path, "users/1/id_front/new.jpg")
        self.assertEqual(self.user.verification_status, User.VerificationStatus.PENDING)
        self.assertIsNone(self.user.verified_at)


class ConsentApiTests(APITestCase):
    def test_existing_user_can_grant_current_consent(self):
        user = make_verified_user(consent=False)
        client = auth_client(user)
        self.assertFalse(client.get("/api/users/me/").json()["consent_current"])
        response = client.post(
            "/api/users/consent/",
            {"consent_version": settings.CONSENT_VERSION},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["consent_current"])

    def test_consent_endpoint_requires_authentication(self):
        self.assertEqual(
            self.client.post("/api/users/consent/", {}, format="json").status_code, 401
        )


class PasswordResetApiTests(APITestCase):
    def setUp(self):
        self.user = make_user()

    def test_request_is_generic_for_known_and_unknown_numbers(self):
        known = self.client.post(
            "/api/users/password-reset/request/",
            {"phone": "0712345678"},
            format="json",
        )
        unknown = self.client.post(
            "/api/users/password-reset/request/",
            {"phone": "0700000000"},
            format="json",
        )
        self.assertEqual(known.status_code, 200)
        self.assertEqual(unknown.status_code, 200)
        self.assertEqual(known.json(), unknown.json())
        self.assertEqual(PasswordResetCode.objects.filter(user=self.user).count(), 1)

    def test_confirm_with_wrong_code_fails_and_keeps_password(self):
        self.client.post(
            "/api/users/password-reset/request/",
            {"phone": "0712345678"},
            format="json",
        )
        response = self.client.post(
            "/api/users/password-reset/confirm/",
            {"phone": "0712345678", "code": "000000", "new_password": "NewStrongPass!9"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_reset_code")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("StrongPass!234"))

    def test_confirm_with_correct_code_changes_password_once(self):
        self.client.post(
            "/api/users/password-reset/request/",
            {"phone": "0712345678"},
            format="json",
        )
        row = self.user.password_reset_codes.get()
        # The plaintext code is only returned by issue(); re-issue to capture it.
        row.delete()
        code = PasswordResetCode.issue(self.user)

        response = self.client.post(
            "/api/users/password-reset/confirm/",
            {"phone": "0712345678", "code": code, "new_password": "NewStrongPass!9"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("NewStrongPass!9"))

        replay = self.client.post(
            "/api/users/password-reset/confirm/",
            {"phone": "0712345678", "code": code, "new_password": "AnotherPass!9"},
            format="json",
        )
        self.assertEqual(replay.status_code, 400)


class ThrottlingTests(APITestCase):
    def test_login_throttle_returns_rate_limited_envelope(self):
        from django.core.cache import cache
        from rest_framework.throttling import ScopedRateThrottle

        payload = {"phone": "0712345678", "password": "WrongPass!234"}
        cache.clear()
        with patch.dict(
            ScopedRateThrottle.THROTTLE_RATES, {"login": "2/min"}, clear=False
        ):
            self.client.post("/api/users/login/", payload, format="json")
            self.client.post("/api/users/login/", payload, format="json")
            response = self.client.post("/api/users/login/", payload, format="json")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"]["code"], "rate_limited")
        self.assertIn("retry_after_seconds", response.json()["error"]["details"])


class StaffVerificationQueueTests(APITestCase):
    def setUp(self):
        self.staff = make_staff()
        self.subject = make_user(phone="0711111111", national_id="44444444")
        attach_documents(self.subject, prefix=f"users/{self.subject.pk}")

    def test_non_staff_cannot_open_the_queue(self):
        self.client.force_login(make_user(phone="0722222222", national_id="55555555"))
        response = self.client.get("/api/users/admin/verification/")
        self.assertEqual(response.status_code, 302)

    @patch("users.storage.SupabaseStorage.create_signed_url", return_value="https://signed.test/doc")
    def test_staff_dashboard_renders_rows_and_audits_each_document(self, _signed):
        self.client.force_login(self.staff)
        response = self.client.get("/api/users/admin/verification/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("254711111111", content)
        self.assertEqual(
            self.subject.document_access_events.count(), 3
        )

    @patch("users.storage.SupabaseStorage.create_signed_url", return_value="https://signed.test/doc")
    def test_verification_api_never_returns_full_national_id(self, _signed):
        self.client.force_login(self.staff)
        response = self.client.get("/api/users/admin/verification/api/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("44444444", response.content.decode())

    @patch("users.storage.SupabaseStorage.create_signed_url", return_value="https://signed.test/doc")
    def test_staff_can_approve_verification(self, _signed):
        self.client.force_login(self.staff)
        response = self.client.post(
            f"/api/users/admin/verify/{self.subject.pk}/",
            {"action": "approve", "notes": "Checked against ID"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.subject.refresh_from_db()
        self.assertEqual(self.subject.verification_status, User.VerificationStatus.VERIFIED)
        self.assertEqual(self.subject.verified_by, self.staff)
        self.assertEqual(self.subject.verification_notes, "Checked against ID")

    def test_rejection_requires_a_reason(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            f"/api/users/admin/verify/{self.subject.pk}/",
            {"action": "reject", "notes": "Blurry photos"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.subject.refresh_from_db()
        self.assertEqual(self.subject.verification_status, User.VerificationStatus.REJECTED)
        self.assertEqual(self.subject.verification_notes, "Blurry photos")

    def test_user_without_documents_cannot_be_verified(self):
        self.subject.id_front_path = ""
        self.subject.save(update_fields=["id_front_path"])
        self.client.force_login(self.staff)
        response = self.client.post(
            f"/api/users/admin/verify/{self.subject.pk}/",
            {"action": "approve"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_invalid_action_is_rejected(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            f"/api/users/admin/verify/{self.subject.pk}/",
            {"action": "delete"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)


class ConsentVersionSeparationTests(APITestCase):
    @override_settings(
        CONSENT_VERSION="privacy-2027-01",
        TERMS_VERSION="terms-2027-02",
    )
    def test_registration_records_each_purpose_at_its_own_current_version(self):
        response = self.client.post(
            "/api/users/register/",
            {
                "phone": "0712345678",
                "national_id": "12345678",
                "password": "StrongPass!234",
                "consent": True,
                "consent_version": "privacy-2027-01",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        user = User.objects.get(phone="254712345678")
        self.assertEqual(
            user.consents.get(
                purpose=ConsentRecord.Purpose.IDENTITY_PROCESSING
            ).version,
            "privacy-2027-01",
        )
        self.assertEqual(
            user.consents.get(purpose=ConsentRecord.Purpose.TERMS).version,
            "terms-2027-02",
        )
        self.assertTrue(user.has_current_consent)
