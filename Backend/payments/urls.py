from django.urls import path
from .views import ConfirmPaymentView

urlpatterns = [
    path("confirm/", ConfirmPaymentView.as_view(), name="confirm-payment"),
]
