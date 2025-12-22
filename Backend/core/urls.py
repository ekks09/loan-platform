from django.contrib import admin
from django.urls import path, include

from payments.webhook import paystack_webhook

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/users/", include("users.urls")),
    path("api/loans/", include("loans.urls")),
    path("api/payments/", include("payments.views")),  # payments/views.py defines urlpatterns
    path("api/webhooks/paystack/", paystack_webhook, name="paystack-webhook"),
]