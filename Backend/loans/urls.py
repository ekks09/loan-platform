from django.urls import path

from loans.views import (
    AcceptOfferView,
    ActiveLoanView,
    ApplyLoanView,
    ApproveLoanView,
    CancelLoanView,
    CurrentLoanView,
    DisburseLoanView,
    LoanDetailView,
    LoanHistoryView,
    OpsLoanListView,
    QuoteView,
    RejectLoanView,
    RetryDisbursementView,
)
from repayments.views import (
    InitiateRepaymentView,
    RepaymentCallbackView,
    RepaymentScheduleView,
)

urlpatterns = [
    path("quote/", QuoteView.as_view(), name="loan-quote"),
    path("apply/", ApplyLoanView.as_view(), name="loan-apply"),
    path("active/", ActiveLoanView.as_view(), name="loan-active"),
    path("current/", CurrentLoanView.as_view(), name="loan-current"),
    path("history/", LoanHistoryView.as_view(), name="loan-history"),
    path("<int:loan_id>/", LoanDetailView.as_view(), name="loan-detail"),
    path("<int:loan_id>/accept-offer/", AcceptOfferView.as_view(), name="loan-accept-offer"),
    path("<int:loan_id>/cancel/", CancelLoanView.as_view(), name="loan-cancel"),
    path("<int:loan_id>/schedule/", RepaymentScheduleView.as_view(), name="loan-repayment-schedule"),
    path("<int:loan_id>/repay/", InitiateRepaymentView.as_view(), name="loan-repayment-initiate"),
    path("<int:loan_id>/repay/callback/", RepaymentCallbackView.as_view(), name="loan-repayment-callback"),
    path("ops/", OpsLoanListView.as_view(), name="loan-ops"),
    path("ops/<int:loan_id>/approve/", ApproveLoanView.as_view(), name="loan-approve"),
    path("ops/<int:loan_id>/reject/", RejectLoanView.as_view(), name="loan-reject"),
    path("ops/<int:loan_id>/disburse/", DisburseLoanView.as_view(), name="loan-disburse"),
    path("ops/<int:loan_id>/retry-disbursement/", RetryDisbursementView.as_view(), name="loan-retry-disbursement"),
]
