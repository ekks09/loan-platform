from django.urls import path
from .views import ConfirmPaymentView

urlpatterns = [
    path("init/", InitPaymentView.as_view(), name="payment-init"),
    path("verify/", VerifyPaymentView.as_view(), name="payment-verify"),
    path("confirm/", ConfirmPaymentView.as_view(), name="confirm-payment"),
]
