from django.urls import path

from loyalty.views import LoyaltySummaryView, VaultMoveView

urlpatterns = [
    path("summary/", LoyaltySummaryView.as_view(), name="loyalty-summary"),
    path("vault/", VaultMoveView.as_view(), name="loyalty-vault"),
]
