"""Core models: the in-app notification fallback channel.

When the SMS provider is unavailable or rejects a *transactional*
notification (never an OTP), the message is recorded here so the customer
still sees the event in their own account. This is a delivery record, not a
chat: writes come only from the notification service, reads are owner-scoped.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models


class InAppNotification(models.Model):
    class Status(models.TextChoices):
        UNREAD = "unread", "Unread"
        READ = "read", "Read"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="in_app_notifications",
    )
    purpose = models.CharField(max_length=32, db_index=True)
    body = models.TextField()
    # Recorded for audit: which primary channel the fallback replaced.
    fallback_for = models.CharField(max_length=16, default="sms")
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.UNREAD
    )
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status"], name="core_notif_user_status_idx"),
        ]

    def __str__(self) -> str:
        return f"InAppNotification({self.user_id}, {self.purpose}, {self.status})"
