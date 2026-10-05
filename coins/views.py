from django.shortcuts import render
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import CoinLedger, CoinWallet
from .serializers import CoinLedgerSerializer

KST = ZoneInfo("Asia/Seoul")


def _parse_date(value, name):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValidationError({name: "YYYY-MM-DD 형식이어야 합니다."})


class BalanceView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        balance = CoinWallet.objects.filter(user=request.user).values_list("balance", flat=True).first() or 0
        return Response({"balance": balance})


class LedgerPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class LedgerListView(ListAPIView):
    """내 코인 이용 내역. ?date_from=&date_to=(한국 날짜, 양끝 포함) &kind=all|earn|spend &page="""
    permission_classes = [IsAuthenticated]
    serializer_class = CoinLedgerSerializer
    pagination_class = LedgerPagination

    def get_queryset(self):
        params = self.request.query_params
        qs = CoinLedger.objects.filter(user=self.request.user)

        start = _parse_date(params.get("date_from"), "date_from")
        end = _parse_date(params.get("date_to"), "date_to")
        if start:
            qs = qs.filter(created_at__gte=datetime.combine(start, time.min, tzinfo=KST))
        if end:
            qs = qs.filter(created_at__lt=datetime.combine(end + timedelta(days=1), time.min, tzinfo=KST))

        kind = params.get("kind", "all")
        if kind == "earn":
            qs = qs.filter(amount__gt=0)
        elif kind == "spend":
            qs = qs.filter(amount__lt=0)
        elif kind != "all":
            raise ValidationError({"kind": "all, earn, spend 중 하나여야 합니다."})

        return qs.order_by("-created_at", "-id")