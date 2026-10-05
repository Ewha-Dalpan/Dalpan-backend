from django.urls import path

from .views import BalanceView, LedgerListView

urlpatterns = [
    path("balance/", BalanceView.as_view()),
    path("ledger/", LedgerListView.as_view()),
]