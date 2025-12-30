from django.urls import path
from .views import RegisterView, LoginView, MeView, VerificationStatusView
from . import admin_views

urlpatterns = [
    path("register/", RegisterView.as_view(), name="register"),
    path("login/", LoginView.as_view(), name="login"),
    path("me/", MeView.as_view(), name="me"),
    path("verification-status/", VerificationStatusView.as_view(), name="verification-status"),
    
    # Admin verification endpoints
    path("admin/verification/", admin_views.verification_dashboard, name="verification-dashboard"),
    path("admin/verification/api/", admin_views.verification_api, name="verification-api"),
    path("admin/verify/<int:user_id>/", admin_views.verify_user, name="verify-user"),
]
