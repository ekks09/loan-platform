from django.urls import path
from .views import ApplyLoanView, CurrentLoanView, ActiveLoanView

urlpatterns = [
    path("apply/", ApplyLoanView.as_view(), name="loan-apply"),
    path("current/", CurrentLoanView.as_view(), name="loan-current"),
    path("active/", ActiveLoanView.as_view(), name="loan-active"),
]
