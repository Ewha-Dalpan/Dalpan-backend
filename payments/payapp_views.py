import logging
from uuid import UUID

from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import OrderStatus, PaymentOrder
from .payapp_services import (
    InvalidFeedback, PayAppError, PayAppRejected, create_payment,
    process_feedback, validate_settings,
)
from .serializers import OrderCreateSerializer

logger = logging.getLogger(__name__)


def order_response(order, response_status=status.HTTP_201_CREATED):
    return Response({
        "order_id": str(order.pk), "provider": "payapp",
        "pay_url": order.payapp_pay_url, "status": order.status,
        "coin_amount": order.coin_amount, "price_krw": order.price_krw,
    }, status=response_status)


class OrderCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = OrderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        product = serializer.validated_data["product"]
        key = request.headers.get("Idempotency-Key")
        if key:
            try:
                key = UUID(key)
            except (ValueError, TypeError, AttributeError):
                return Response({"detail": "Idempotency-Key는 UUID여야 합니다."}, status=400)
        try:
            validate_settings()
        except ImproperlyConfigured:
            return Response({"detail": "결제 서비스 설정이 필요합니다."}, status=503)

        # Commit the local order before making a remote request so feedback can find it.
        with transaction.atomic():
            try:
                with transaction.atomic():
                    order = PaymentOrder.objects.create(
                        user=request.user, product=product, provider="payapp",
                        coin_amount=product.coin_amount, price_krw=product.price_krw,
                        checkout_key=key, payapp_request_status="REQUESTING",
                    )
                created = True
            except IntegrityError:
                if key is None:
                    raise
                order = PaymentOrder.objects.filter(checkout_key=key).first()
                if order is None:
                    raise
                created = False
        if not created:
            if order.user_id != request.user.pk or order.product_id != product.pk:
                return Response({"detail": "이미 사용된 결제 요청 키입니다."}, status=409)
            if order.payapp_pay_url and order.status == OrderStatus.PENDING:
                return order_response(order, status.HTTP_200_OK)
            return Response({
                "detail": "기존 주문 상태를 확인해 주세요. 결제 요청을 반복하지 않습니다.",
                "order_id": str(order.pk), "status": order.status,
            }, status=409)

        try:
            mul_no, pay_url = create_payment(order, serializer.validated_data["recvphone"])
        except PayAppError as exc:
            outcome = "FAILED" if isinstance(exc, PayAppRejected) else "UNKNOWN"
            PaymentOrder.objects.filter(pk=order.pk).update(payapp_request_status=outcome)
            logger.warning("PayApp checkout %s: order_id=%s", outcome, order.pk)
            return Response({
                "detail": ("결제 요청이 거절되었습니다." if outcome == "FAILED" else
                           "결제 요청 확인이 지연됩니다. 주문 상태를 확인해 주세요."),
                "order_id": str(order.pk),
            }, status=502)

        with transaction.atomic():
            order = PaymentOrder.objects.select_for_update().get(pk=order.pk)
            if order.payapp_mul_no and order.payapp_mul_no != mul_no:
                logger.error("PayApp transaction mismatch: order_id=%s", order.pk)
                return Response({"detail": "결제 정보 확인이 필요합니다.",
                                 "order_id": str(order.pk)}, status=502)
            order.payapp_mul_no = mul_no
            order.payapp_pay_url = pay_url
            order.payapp_request_status = "READY"
            order.save(update_fields=["payapp_mul_no", "payapp_pay_url",
                                      "payapp_request_status", "updated_at"])
        return order_response(order)


@csrf_exempt
@require_POST
def payapp_feedback(request):
    # PayApp sends form data. Reject ambiguous repeated fields.
    if not request.POST or any(len(values) != 1 for _, values in request.POST.lists()):
        return HttpResponse("FAIL", status=400, content_type="text/plain")
    try:
        process_feedback(request.POST.dict())
    except InvalidFeedback:
        return HttpResponse("FAIL", status=400, content_type="text/plain")
    except ImproperlyConfigured:
        return HttpResponse("FAIL", status=503, content_type="text/plain")
    except Exception:
        logger.exception("PayApp feedback processing failed")
        return HttpResponse("FAIL", status=500, content_type="text/plain")
    return HttpResponse("SUCCESS", content_type="text/plain")
