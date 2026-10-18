from django.urls import path

from payments.views import (
    InitPaymentView,
    PaymentHistoryView,
    ReconciliationListView,
    ReconciliationView,
    RefundPaymentView,
    TransferListView,
    VerifyPaymentView,
)

urlpatterns = [
    path("init/", InitPaymentView.as_view(), name="payment-init"),
    path("verify/", VerifyPaymentView.as_view(), name="payment-verify"),
    path("history/", PaymentHistoryView.as_view(), name="payment-history"),
    path("ops/refund/", RefundPaymentView.as_view(), name="payment-refund"),
    path("ops/transfers/", TransferListView.as_view(), name="payment-transfers"),
    path("ops/reconcile/", ReconciliationView.as_view(), name="payment-reconcile"),
    path("ops/reconciliations/", ReconciliationListView.as_view(), name="payment-reconcile-list"),
]
