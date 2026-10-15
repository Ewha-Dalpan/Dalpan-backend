from django.shortcuts import render
import json
import logging

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import CoinProduct, PaymentOrder
from .serializers import CoinProductSerializer, OrderCreateSerializer
from .services import (
    InvalidSignature,
    TransientError,
    WebhookNotConfigured,
    process_event,
    verify_signature,
)

logger = logging.getLogger(__name__)


class ProductListView(generics.ListAPIView):
    """코인 상품 목록. 로그인 없이 볼 수 있어야 한다 (Paddle 심사: 가격 공개)."""
    authentication_classes = []
    permission_classes = [AllowAny]
    serializer_class = CoinProductSerializer
    pagination_class = None
    queryset = CoinProduct.objects.filter(is_active=True).order_by("sort_order", "id")


class OrderCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = OrderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        product = serializer.validated_data["product"]

        order = PaymentOrder.objects.create(
            user=request.user,
            product=product,
            coin_amount=product.coin_amount,
            price_krw=product.price_krw,
        )
        return Response(
            {
                "order_id": str(order.pk),            # 프론트: Paddle 결제창 customData.order_id 로 넘길 것
                "paddle_price_id": product.paddle_price_id,
                "coin_amount": order.coin_amount,
                "price_krw": order.price_krw,
            },
            status=status.HTTP_201_CREATED,
        )


class OrderDetailView(APIView):
    """프론트가 몇 초마다 조회: PENDING → PAID"""
    permission_classes = [IsAuthenticated]

    def get(self, request, order_id):
        order = get_object_or_404(PaymentOrder, pk=order_id, user=request.user)
        return Response(
            {
                "order_id": str(order.pk),
                "status": order.status,
                "coin_amount": order.coin_amount,
                "paid_at": order.paid_at,
            }
        )


@csrf_exempt
@require_POST
def paddle_webhook(request):
    """Paddle만 호출하는 주소: 로그인·CSRF 검사에서 제외하고 서명으로만 인증한다."""
    raw_body = request.body  # 반드시 다른 파싱보다 먼저 (서명은 원본 바이트 기준)

    try:
        verify_signature(raw_body, request.headers.get("Paddle-Signature", ""))
    except InvalidSignature:
        return JsonResponse({"detail": "invalid signature"}, status=401)
    except WebhookNotConfigured:
        logger.error("PADDLE_WEBHOOK_SECRET이 설정되지 않았습니다.")
        return JsonResponse({"detail": "server misconfigured"}, status=500)

    try:
        payload = json.loads(raw_body)
        if not isinstance(payload, dict):
            raise ValueError
        raw_text = raw_body.decode("utf-8")
    except ValueError:
        return JsonResponse({"detail": "invalid body"}, status=400)

    try:
        process_event(payload, raw_text)
    except TransientError:
        return JsonResponse({"detail": "retry later"}, status=500)
    except Exception:
        logger.exception("Paddle webhook processing failed: event_id=%s", payload.get("event_id"))
        return JsonResponse({"detail": "processing failed"}, status=500)

    return JsonResponse({"success": True})