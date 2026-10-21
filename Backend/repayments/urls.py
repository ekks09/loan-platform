from django.urls import path

from repayments.views import (
    InitiateRepaymentView,
    RepaymentCallbackView,
    RepaymentScheduleView,
)

urlpatterns = [
    path("<int:loan_id>/schedule/", RepaymentScheduleView.as_view(), name="repayment-schedule"),
    path("<int:loan_id>/initiate/", InitiateRepaymentView.as_view(), name="repayment-initiate"),
    path("<int:loan_id>/callback/", RepaymentCallbackView.as_view(), name="repayment-callback"),
]
