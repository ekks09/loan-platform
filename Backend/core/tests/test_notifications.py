"""Notification provider, retry, fallback and OTP-reliability tests.

All provider HTTP is mocked; nothing here sends real messages. OTP content
must never appear in logs except via the explicit console provider, which
production configuration refuses.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from core.models import InAppNotification
from core.notifications import (
    DeliveryStatus,
    NotificationResult,
    get_sms_provider,
    record_in_app_fallback,
    send_sms,
    send_with_retry,
    sms_available,
)
from core.notifications.sms import AfricasTalkingSMSProvider, NullSMSProvider
from core.tests.helpers import auth_client, make_user, make_verified_user
from users.models import SmsAuditLog, User
from users.otp import issue_password_reset


def _result(status, detail="", provider="test"):
    return NotificationResult(status=status, provider=provider, detail=detail)


class ProviderSelectionTests(TestCase):
    def test_none_backend_yields_null_provider(self):
        with override_settings(SMS_BACKEND="none"):
            provider = get_sms_provider()
            self.assertIsInstance(provider, NullSMSProvider)
            self.assertFalse(provider.available())
            self.assertFalse(sms_available())

    def test_africastalking_requires_credentials(self):
        with override_settings(
            SMS_BACKEND="africastalking", AFRICASTALKING_USERNAME="", AFRICASTALKING_API_KEY=""
        ):
            provider = get_sms_provider()
            self.assertIsInstance(provider, AfricasTalkingSMSProvider)
            self.assertFalse(provider.available())


class RetryPolicyTests(TestCase):
    @override_settings(NOTIFICATION_MAX_RETRIES=2, NOTIFICATION_RETRY_BACKOFF_SECONDS=0)
    def test_transient_failure_retries_until_success(self):
        provider = MagicMock()
        provider.name = "test"
        provider.send.side_effect = [
            _result(DeliveryStatus.FAILED, detail="timeout"),
            _result(DeliveryStatus.SENT),
        ]
        result = send_with_retry(provider, "254700000000", "hello", purpose="general")
        self.assertTrue(result.sent)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(provider.send.call_count, 2)

    @override_settings(NOTIFICATION_MAX_RETRIES=1, NOTIFICATION_RETRY_BACKOFF_SECONDS=0)
    def test_retry_exhaustion_stays_failed_and_bounded(self):
        provider = MagicMock()
        provider.name = "test"
        provider.send.side_effect = [
            _result(DeliveryStatus.FAILED, detail="provider_5xx"),
            _result(DeliveryStatus.FAILED, detail="provider_5xx"),
            _result(DeliveryStatus.SENT),
        ]
        result = send_with_retry(provider, "254700000000", "hello", purpose="general")
        self.assertFalse(result.sent)
        # 1 initial + 1 retry, never the third side_effect.
        self.assertEqual(provider.send.call_count, 2)

    @override_settings(NOTIFICATION_MAX_RETRIES=3, NOTIFICATION_RETRY_BACKOFF_SECONDS=0)
    def test_permanent_rejection_never_retries(self):
        provider = MagicMock()
        provider.name = "test"
        provider.send.side_effect = [_result(DeliveryStatus.FAILED, detail="rejected")]
        result = send_with_retry(provider, "254700000000", "hello", purpose="general")
        self.assertFalse(result.sent)
        self.assertEqual(provider.send.call_count, 1)

    @override_settings(NOTIFICATION_MAX_RETRIES=1, NOTIFICATION_RETRY_BACKOFF_SECONDS=0)
    def test_a_crashing_provider_yields_failure_not_an_exception(self):
        provider = MagicMock()
        provider.name = "test"
        provider.send.side_effect = RuntimeError("boom")
        result = send_with_retry(provider, "254700000000", "hello", purpose="general")
        self.assertFalse(result.sent)


class AfricasTalkingMappingTests(TestCase):
    def _provider(self):
        return AfricasTalkingSMSProvider()

    def _response(self, status):
        response = MagicMock()
        response.status_code = status
        response.raise_for_status = MagicMock()
        return response

    @override_settings(AFRICASTALKING_USERNAME="u", AFRICASTALKING_API_KEY="k", SMS_BACKEND="africastalking")
    def test_2xx_means_accepted_not_delivered(self):
        with patch("core.notifications.sms.requests.post") as post:
            post.return_value = self._response(200)
            result = self._provider().send("254700000000", "hello")
        self.assertIs(result.status, DeliveryStatus.SENT)
        self.assertEqual(result.provider, "africastalking")

    @override_settings(AFRICASTALKING_USERNAME="u", AFRICASTALKING_API_KEY="k", SMS_BACKEND="africastalking")
    def test_errors_are_classified(self):
        for http_status, detail in ((500, "provider_5xx"), (429, "rate_limited"), (400, "rejected")):
            with patch("core.notifications.sms.requests.post") as post:
                post.return_value = self._response(http_status)
                result = self._provider().send("254700000000", "hello")
            self.assertIs(result.status, DeliveryStatus.FAILED)
            self.assertEqual(result.detail, detail)

    @override_settings(AFRICASTALKING_USERNAME="u", AFRICASTALKING_API_KEY="k", SMS_BACKEND="africastalking")
    def test_timeout_is_transient_failure(self):
        import requests as _requests

        with patch("core.notifications.sms.requests.post") as post:
            post.side_effect = _requests.Timeout("slow")
            result = self._provider().send("254700000000", "hello")
        self.assertIs(result.status, DeliveryStatus.FAILED)
        self.assertEqual(result.detail, "timeout")

    @override_settings(AFRICASTALKING_USERNAME="u", AFRICASTALKING_API_KEY="k", SMS_BACKEND="africastalking")
    def test_message_content_is_never_logged(self):
        secret_message = "Your reset code is 987654"
        with patch("core.notifications.sms.requests.post") as post:
            post.return_value = self._response(500)
            with self.assertNoLogs("core.notifications", level="DEBUG"):
                self._provider().send("254700000000", secret_message)


class FallbackPolicyTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def test_fallback_records_allowed_transactional_purposes(self):
        for purpose in ("verification", "disbursed", "disbursement_failed"):
            self.assertTrue(
                record_in_app_fallback(user=self.user, purpose=purpose, body="hello")
            )
        self.assertEqual(InAppNotification.objects.count(), 3)

    def test_otp_never_falls_back(self):
        self.assertFalse(
            record_in_app_fallback(user=self.user, purpose="password_reset", body="code 112233")
        )
        self.assertEqual(InAppNotification.objects.count(), 0)

    def test_unknown_purposes_do_not_fall_back(self):
        self.assertFalse(record_in_app_fallback(user=self.user, purpose="general", body="x"))
        self.assertEqual(InAppNotification.objects.count(), 0)

    def test_send_sms_with_no_provider_falls_back_only_with_a_user(self):
        with override_settings(SMS_BACKEND="none"):
            self.assertTrue(
                send_sms(
                    self.user.phone,
                    "Transfer done",
                    purpose="disbursed",
                    user=self.user,
                )
                is False
            )
            self.assertEqual(InAppNotification.objects.count(), 1)
            # No user attached: nothing is recorded.
            self.assertFalse(
                send_sms("254700000000", "Transfer done", purpose="disbursed")
            )
            self.assertEqual(InAppNotification.objects.count(), 1)

    def test_send_sms_sensitive_purpose_records_nothing(self):
        with override_settings(SMS_BACKEND="none"):
            self.assertFalse(
                send_sms(
                    self.user.phone,
                    "Your reset code is 112233",
                    purpose="password_reset",
                    user=self.user,
                )
            )
            self.assertEqual(InAppNotification.objects.count(), 0)

    @override_settings(NOTIFICATION_MAX_RETRIES=0)
    def test_send_sms_never_raises_from_a_crashing_provider(self):
        # Even a provider that raises inside send() must not propagate into a
        # financial or identity code path.
        provider = MagicMock()
        provider.name = "crashy"
        provider.available.return_value = True
        provider.send.side_effect = RuntimeError("boom")
        with patch("core.notifications.service.get_sms_provider", return_value=provider):
            self.assertFalse(send_sms("254700000000", "x", purpose="general"))


class NotificationApiTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.other = make_user(phone="0722222222", national_id="87654321")

    def _seed(self):
        record_in_app_fallback(user=self.user, purpose="disbursed", body="Yours")
        record_in_app_fallback(user=self.other, purpose="disbursed", body="Theirs")

    def test_list_and_mark_read_are_owner_scoped(self):
        self._seed()
        client = auth_client(self.user)
        response = client.get("/api/users/notifications/")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(len(body["results"]), 1)
        self.assertEqual(body["results"][0]["body"], "Yours")
        self.assertEqual(body["unread"], 1)

        other_client = auth_client(self.other)
        listing = other_client.get("/api/users/notifications/").json()
        self.assertEqual(len(listing["results"]), 1)
        self.assertEqual(listing["results"][0]["body"], "Theirs")

        read = client.post("/api/users/notifications/read-all/", {}, format="json")
        self.assertEqual(read.json()["marked_read"], 1)
        self.assertEqual(client.get("/api/users/notifications/").json()["unread"], 0)
        # The other user's notification is untouched.
        self.assertEqual(other_client.get("/api/users/notifications/").json()["unread"], 1)

    def test_list_requires_authentication(self):
        self.assertEqual(self.client.get("/api/users/notifications/").status_code, 401)


class OtpCooldownTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.factory = RequestFactory()

    def _request(self):
        request = self.factory.post("/api/users/password-reset/request/")
        request.META["REMOTE_ADDR"] = "10.0.0.9"
        return request

    def _issue(self):
        return issue_password_reset(self.user, self.user.phone, self._request())

    def test_first_issue_sends_and_second_is_cooled_down(self):
        first = self._issue()
        self.assertTrue(first.sent)
        second = self._issue()
        self.assertFalse(second.sent)
        self.assertEqual(second.reason, "cooldown")
        self.assertGreater(second.retry_after, 0)
        self.assertLessEqual(second.retry_after, 60)

    def test_cooldown_expires_and_allows_resend(self):
        first = self._issue()
        self.assertTrue(first.sent)
        # Move the last send into the past beyond the cooldown.
        last = SmsAuditLog.objects.filter(status=SmsAuditLog.Status.SENT).first()
        SmsAuditLog.objects.filter(pk=last.pk).update(
            created_at=timezone.now() - timedelta(seconds=120)
        )
        third = self._issue()
        self.assertTrue(third.sent)

    def test_windowed_limits_still_apply_after_cooldowns(self):
        # 3 allowed sends (window limit), each separated past the cooldown.
        for _ in range(3):
            last = SmsAuditLog.objects.order_by("-created_at").first()
            if last:
                SmsAuditLog.objects.filter(pk=last.pk).update(
                    created_at=timezone.now() - timedelta(seconds=120)
                )
            result = self._issue()
        self.assertTrue(result.sent)
        fourth = self._issue()
        self.assertFalse(fourth.sent)
        self.assertEqual(fourth.reason, "phone_limit")

    def test_unknown_number_uniform_response_but_ok_request(self):
        result = issue_password_reset(None, "254799999999", self._request())
        # Unknown numbers skip without sending; the cooldown bookkeeping does
        # not reveal account existence.
        self.assertFalse(result.sent)
        self.assertEqual(result.reason, "unknown_number")
