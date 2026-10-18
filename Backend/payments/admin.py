from __future__ import annotations

from django.contrib import admin, messages
from django.utils import timezone

from payments.models import (
    LedgerEntry,
    Payment,
    PaymentEvent,
    ReconciliationRun,
    Refund,
    Transfer,
    TransferEvent,
    WebhookEvent,
)
from payments.services import process_webhook_event, refresh_transfer_from_provider


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "user",
        "amount_kes",
        "purpose",
        "status",
        "paid_at",
        "created_at",
    )
    list_filter = ("status", "purpose", "currency", "created_at")
    search_fields = ("reference", "user__phone", "provider_transaction_id")
    date_hierarchy = "created_at"
    readonly_fields = (
        "loan",
        "user",
        "purpose",
        "reference",
        "idempotency_key",
        "amount_kes",
        "currency",
        "status",
        "verified",
        "provider_transaction_id",
        "provider_reference",
        "channel",
        "gateway_response",
        "authorization_url",
        "access_code",
        "paid_at",
        "last_verified_at",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Transfer)
class TransferAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "loan",
        "amount_kes",
        "status",
        "attempts",
        "max_attempts",
        "next_retry_at",
        "confirmed_at",
    )
    list_filter = ("status", "currency")
    search_fields = ("reference", "loan__user__phone", "provider_transfer_code")
    actions = ("refresh_from_provider",)
    readonly_fields = [f.name for f in Transfer._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Refresh selected transfers from Paystack")
    def refresh_from_provider(self, request, queryset):
        for transfer in queryset:
            refresh_transfer_from_provider(transfer, actor=request.user)
        self.message_user(
            request,
            f"Refreshed {queryset.count()} transfer(s).",
            level=messages.SUCCESS,
        )


@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "payment",
        "amount_kes",
        "status",
        "requested_by",
        "created_at",
        "processed_at",
    )
    list_filter = ("status", "currency")
    search_fields = ("reference", "payment__reference", "provider_refund_id")
    readonly_fields = [f.name for f in Refund._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = (
        "event_type",
        "reference",
        "status",
        "attempts",
        "received_at",
        "processed_at",
    )
    list_filter = ("status", "event_type")
    search_fields = ("event_id", "reference")
    date_hierarchy = "received_at"
    actions = ("replay_events",)
    readonly_fields = [f.name for f in WebhookEvent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Replay selected failed webhook events")
    def replay_events(self, request, queryset):
        replayed = 0
        for event in queryset.filter(status=WebhookEvent.Status.FAILED):
            try:
                process_webhook_event(event)
                replayed += 1
            except Exception as exc:
                self.message_user(
                    request, f"Event {event.pk}: {exc}", level=messages.ERROR
                )
        self.message_user(
            request, f"Replayed {replayed} event(s).", level=messages.SUCCESS
        )


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "entry_group",
        "account",
        "direction",
        "amount_kes",
        "reference",
    )
    list_filter = ("account", "direction", "currency")
    search_fields = ("reference", "memo")
    date_hierarchy = "created_at"
    readonly_fields = [f.name for f in LedgerEntry._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ReconciliationRun)
class ReconciliationRunAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "dry_run", "started_at", "finished_at")
    list_filter = ("status", "dry_run")
    readonly_fields = [f.name for f in ReconciliationRun._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


for model in (PaymentEvent, TransferEvent):
    admin.site.register(
        model,
        type(
            f"{model.__name__}Admin",
            (admin.ModelAdmin,),
            {
                "list_display": ("__str__", "created_at"),
                "readonly_fields": [f.name for f in model._meta.fields],
                "has_add_permission": lambda self, request: False,
                "has_change_permission": lambda self, request, obj=None: False,
                "has_delete_permission": lambda self, request, obj=None: False,
            },
        ),
    )
