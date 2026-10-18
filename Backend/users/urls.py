from django.urls import path

from users import admin_views
from users.views import (
    ConsentView,
    DocumentsUploadView,
    LoginView,
    MeView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    RegisterView,
    VerificationStatusView,
)

urlpatterns = [
    path("register/", RegisterView.as_view(), name="register"),
    path("login/", LoginView.as_view(), name="login"),
    path("me/", MeView.as_view(), name="me"),
    path("verification-status/", VerificationStatusView.as_view(), name="verification-status"),
    path("documents/", DocumentsUploadView.as_view(), name="documents"),
    path("consent/", ConsentView.as_view(), name="consent"),
    path("password-reset/request/", PasswordResetRequestView.as_view(), name="password-reset-request"),
    path("password-reset/confirm/", PasswordResetConfirmView.as_view(), name="password-reset-confirm"),
    path("admin/verification/", admin_views.verification_dashboard, name="verification-dashboard"),
    path("admin/verification/api/", admin_views.verification_api, name="verification-api"),
    path("admin/verify/<int:user_id>/", admin_views.verify_user, name="verify-user"),
]
