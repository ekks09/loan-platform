from __future__ import annotations

from django.contrib import admin, messages
from django.utils import timezone

from users.models import (
    ConsentRecord,
    DocumentAccessLog,
    PasswordResetCode,
    User,
)


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = (
        "phone",
        "verification_status",
        "is_active",
        "is_staff",
        "created_at",
    )
    list_filter = ("verification_status", "is_active", "is_staff")
    search_fields = ("phone", "national_id")
    ordering = ("-created_at",)
    date_hierarchy = "created_at"
    actions = ("approve_verification", "reject_verification")
    readonly_fields = (
        "id_front_path",
        "id_back_path",
        "selfie_path",
        "documents_uploaded_at",
        "verified_at",
        "verified_by",
        "created_at",
        "last_login",
    )
    fieldsets = (
        (None, {"fields": ("phone", "national_id", "password")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        (
            "Verification",
            {
                "fields": (
                    "verification_status",
                    "verification_notes",
                    "verified_at",
                    "verified_by",
                    "documents_uploaded_at",
                    "id_front_path",
                    "id_back_path",
                    "selfie_path",
                )
            },
        ),
        ("Audit", {"fields": ("created_at", "last_login", "deletion_requested_at")}),
    )

    @admin.action(description="Approve identity verification")
    def approve_verification(self, request, queryset):
        updated = 0
        for user in queryset:
            if not user.has_uploaded_documents:
                self.message_user(
                    request,
                    f"{user.phone}: no documents uploaded.",
                    level=messages.WARNING,
                )
                continue
            user.verification_status = User.VerificationStatus.VERIFIED
            user.verified_at = timezone.now()
            user.verified_by = request.user
            user.save(update_fields=["verification_status", "verified_at", "verified_by"])
            updated += 1
        self.message_user(request, f"Approved {updated} user(s).", level=messages.SUCCESS)

    @admin.action(description="Reject identity verification")
    def reject_verification(self, request, queryset):
        updated = queryset.update(
            verification_status=User.VerificationStatus.REJECTED,
            verified_at=None,
            verified_by=request.user,
        )
        self.message_user(request, f"Rejected {updated} user(s).", level=messages.WARNING)


@admin.register(ConsentRecord)
class ConsentRecordAdmin(admin.ModelAdmin):
    list_display = ("user", "purpose", "version", "granted_at", "withdrawn_at")
    list_filter = ("purpose", "version")
    search_fields = ("user__phone",)
    readonly_fields = [f.name for f in ConsentRecord._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(PasswordResetCode)
class PasswordResetCodeAdmin(admin.ModelAdmin):
    list_display = ("user", "created_at", "expires_at", "used_at", "attempts")
    list_filter = ("used_at",)
    search_fields = ("user__phone",)
    readonly_fields = ("user", "code_hash", "expires_at", "used_at", "attempts", "created_at")

    def has_add_permission(self, request):
        return False


@admin.register(DocumentAccessLog)
class DocumentAccessLogAdmin(admin.ModelAdmin):
    list_display = ("subject_user", "document", "staff_user", "purpose", "created_at")
    list_filter = ("document", "purpose")
    search_fields = ("subject_user__phone", "staff_user__phone")
    date_hierarchy = "created_at"
    readonly_fields = [f.name for f in DocumentAccessLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
