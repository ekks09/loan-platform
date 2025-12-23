from django.urls import path
from .views import InitPaymentView, VerifyPaymentView

urlpatterns = [
    path("init/", InitPaymentView.as_view(), name="payment-init"),
    path("verify/", VerifyPaymentView.as_view(), name="payment-verify"),
]







