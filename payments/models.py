import uuid

from django.conf import settings
from django.db import models


class CoinProduct(models.Model):
    name = models.CharField(max_length=50)
    coin_amount = models.PositiveIntegerField()
    price_krw = models.PositiveIntegerField()
    paddle_price_id = models.CharField(max_length=64, unique=True)  
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "coin_products"
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.name


class OrderStatus(models.TextChoices):
    PENDING = "PENDING", "결제 대기"
    PAID = "PAID", "결제 완료"
    EXPIRED = "EXPIRED", "만료"
    REFUNDED = "REFUNDED", "환불"


class PaymentOrder(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # 주문번호
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payment_orders")
    product = models.ForeignKey(CoinProduct, on_delete=models.PROTECT, related_name="orders")
    coin_amount = models.PositiveIntegerField()  # 주문 시점 값 스냅샷
    price_krw = models.PositiveIntegerField()    # 주문 시점 값 스냅샷
    status = models.CharField(max_length=10, choices=OrderStatus.choices, default=OrderStatus.PENDING)
    paddle_transaction_id = models.CharField(max_length=64, null=True, blank=True, unique=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "payment_orders"
        indexes = [
            models.Index(fields=["user", "created_at"], name="ix_order_user"),
            models.Index(fields=["status", "created_at"], name="ix_order_status"),
        ]


class RefundStatus(models.TextChoices):
    PENDING = "PENDING", "대기"
    APPROVED = "APPROVED", "승인"
    REJECTED = "REJECTED", "거절"


class PaymentRefund(models.Model):
    order = models.ForeignKey(PaymentOrder, on_delete=models.PROTECT, related_name="refunds")
    paddle_adjustment_id = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=10, choices=RefundStatus.choices, default=RefundStatus.PENDING)
    refund_amount = models.PositiveIntegerField(null=True, blank=True)
    coins_revoked = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "payment_refunds"


class WebhookResult(models.TextChoices):
    PROCESSED = "PROCESSED", "처리 완료"
    IGNORED = "IGNORED", "무시"
    DATA_ERROR = "DATA_ERROR", "데이터 오류"


class PaddleWebhookEvent(models.Model):
    event_id = models.CharField(max_length=64, unique=True)  # evt_중복 처리 방지
    event_type = models.CharField(max_length=64)
    occurred_at = models.DateTimeField()
    order = models.ForeignKey(
        PaymentOrder, null=True, blank=True, on_delete=models.SET_NULL, related_name="webhook_events"
    )
    result = models.CharField(max_length=20, choices=WebhookResult.choices, default=WebhookResult.PROCESSED)
    note = models.CharField(max_length=255, null=True, blank=True)
    payload = models.TextField()  # 원본 JSON
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "paddle_webhook_events"