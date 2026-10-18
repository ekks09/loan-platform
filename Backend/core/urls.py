from django.contrib import admin
from django.urls import include, path

from core import views
from payments.views import PaystackReferenceStatusView
from payments.webhook import paystack_webhook

handler500 = "core.views.server_error"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", views.health, name="health"),
    path("health/ready/", views.readiness, name="readiness"),
    path("manifest.webmanifest", views.manifest, name="manifest"),
    path("sw.js", views.service_worker, name="service-worker"),
    path("offline/", views.offline, name="offline"),
    path("robots.txt", views.robots, name="robots"),
    # Versioned API. `/api/v1/...` is the public, documented surface. The
    # unversioned `/api/...` paths below stay as aliases because the bundled
    # frontend and the deployed Paystack webhook URL already use them;
    # removing them would be a breaking change for no benefit.
    path("api/v1/users/", include(("users.urls", "v1-users"), namespace="v1-users")),
    path("api/v1/loans/", include(("loans.urls", "v1-loans"), namespace="v1-loans")),
    path("api/v1/payments/", include(("payments.urls", "v1-payments"), namespace="v1-payments")),
    path("api/v1/loyalty/", include(("loyalty.urls", "v1-loyalty"), namespace="v1-loyalty")),
    path(
        "api/v1/paystack/verify/<str:reference>/",
        PaystackReferenceStatusView.as_view(),
        name="v1-paystack-verify-status",
    ),
    path("api/users/", include("users.urls")),
    path("api/loans/", include("loans.urls")),
    path("api/payments/", include("payments.urls")),
    path("api/loyalty/", include("loyalty.urls")),
    path(
        "api/paystack/verify/<str:reference>/",
        PaystackReferenceStatusView.as_view(),
        name="paystack-verify-status",
    ),
    path("api/webhooks/paystack/", paystack_webhook, name="paystack-webhook"),
    path("api/v1/webhooks/paystack/", paystack_webhook, name="v1-paystack-webhook"),
    path("", views.index, name="index"),
    path("register/", views.register, name="register"),
    path("login/", views.login_view, name="login_view"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("legal/privacy/", views.privacy, name="privacy"),
    path("legal/terms/", views.terms, name="terms"),
]
