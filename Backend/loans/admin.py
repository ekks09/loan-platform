from __future__ import annotations

from django.contrib import admin, messages
from django.urls import reverse
from django.utils.html import format_html

from loans.models import Loan, LoanEvent
from loans.services import approve, reject


@admin.register(Loan)
class LoanAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "user_kyc_status",
        "amount",
        "service_fee",
        "status",
        "offer_accepted_at",
        "created_at",
    )
    list_filter = ("status", "currency", "created_at")
    search_fields = ("user__phone", "paystack_reference", "id")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    actions = ("approve_loans", "reject_loans")
    readonly_fields = (
        "service_fee",
        "currency",
        "paystack_reference",
        "paystack_authorization_url",
        "paystack_access_code",
        "approved_at",
        "approved_by",
        "offer_accepted_at",
        "offer_terms_version",
        "disbursed_at",
        "created_at",
        "updated_at",
        "last_event",
    )
    fieldsets = (
        ("Application", {"fields": ("user", "amount", "currency", "mpesa_phone", "status")}),
        ("Pricing", {"fields": ("service_fee",)}),
        ("Offer", {"fields": ("approved_at", "approved_by", "offer_expires_at", "offer_accepted_at", "offer_terms_version")}),
        ("Provider", {"fields": ("paystack_reference", "paystack_authorization_url", "paystack_access_code")}),
        ("Outcome", {"fields": ("rejection_reason", "disbursed_at", "last_event", "created_at", "updated_at")}),
    )

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            readonly += ["user", "amount", "mpesa_phone"]
        return readonly

    @admin.display(description="KYC", ordering="user__verification_status")
    def user_kyc_status(self, obj):
        status = obj.user.verification_status
        colors = {
            "verified": "#10B981",
            "pending": "#F59E0B",
            "rejected": "#EF4444",
        }
        color = colors.get(status, "#6B7280")
        url = reverse("v1-users:verification-dashboard") + f"?q={obj.user.phone}"
        return format_html(
            '<a href="{}" style="color: {}; font-weight: 600;">{}</a>',
            url,
            color,
            status.capitalize(),
        )

    @admin.action(description="Approve selected applications (issue offer)")
    def approve_loans(self, request, queryset):
        approved = 0
        for loan in queryset:
            try:
                approve(loan, actor=request.user)
                approved += 1
            except Exception as exc:  # surfaced to the operator, never silent
                self.message_user(
                    request, f"Loan {loan.pk}: {exc}", level=messages.ERROR
                )
        self.message_user(request, f"Approved {approved} loan(s).", level=messages.SUCCESS)

    @admin.action(description="Reject selected applications")
    def reject_loans(self, request, queryset):
        rejected = 0
        for loan in queryset:
            try:
                reject(loan, actor=request.user, reason="Rejected from admin console")
                rejected += 1
            except Exception as exc:
                self.message_user(
                    request, f"Loan {loan.pk}: {exc}", level=messages.ERROR
                )
        self.message_user(request, f"Rejected {rejected} loan(s).", level=messages.WARNING)


@admin.register(LoanEvent)
class LoanEventAdmin(admin.ModelAdmin):
    list_display = ("loan", "from_status", "to_status", "actor", "created_at")
    list_filter = ("to_status",)
    search_fields = ("loan__id", "loan__user__phone")
    date_hierarchy = "created_at"
    readonly_fields = [f.name for f in LoanEvent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
