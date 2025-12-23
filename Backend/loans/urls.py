from django.urls import path
from .views import ApplyLoanView, CurrentLoanView

urlpatterns = [
    path("apply/", ApplyLoanView.as_view(), name="apply-loan"),
    path("current/", CurrentLoanView.as_view(), name="current-loan"),
]
