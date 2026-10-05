from uuid import uuid4

from django.conf import settings
from django.db import models


def new_order_id():
	return str(uuid4())


class PaymentOrder(models.Model):
	class Status(models.TextChoices):
		PENDING = 'PENDING', '결제 대기'
		PAID = 'PAID', '결제 완료'
		EXPIRED = 'EXPIRED', '만료'
		REFUNDED = 'REFUNDED', '환불'

	id = models.CharField(primary_key=True, max_length=36, default=new_order_id, editable=False)
	user = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.PROTECT,
		related_name='payment_orders',
	)
	product = models.ForeignKey(
		'coins.CoinProduct',
		on_delete=models.PROTECT,
		related_name='payment_orders',
	)
	coin_amount = models.IntegerField()
	price_krw = models.IntegerField()
	status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
	paddle_transaction_id = models.CharField(max_length=64, null=True, blank=True)
	paid_at = models.DateTimeField(null=True, blank=True)
	refunded_at = models.DateTimeField(null=True, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		db_table = 'payment_orders'


class PaymentRefund(models.Model):
	class Status(models.TextChoices):
		PENDING = 'PENDING', '대기'
		APPROVED = 'APPROVED', '승인'
		REJECTED = 'REJECTED', '거절'

	order = models.ForeignKey(PaymentOrder, on_delete=models.PROTECT, related_name='refunds')
	paddle_adjustment_id = models.CharField(max_length=64)
	status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
	refund_amount = models.IntegerField(null=True, blank=True)
	coins_revoked = models.IntegerField(default=0)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		db_table = 'payment_refunds'


class PaddleWebhookEvent(models.Model):
	class Result(models.TextChoices):
		PROCESSED = 'PROCESSED', '처리'
		IGNORED = 'IGNORED', '무시'
		DATA_ERROR = 'DATA_ERROR', '데이터 오류'

	event_id = models.CharField(max_length=64, unique=True)
	event_type = models.CharField(max_length=64)
	occurred_at = models.DateTimeField()
	order = models.ForeignKey(
		PaymentOrder,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='webhook_events',
	)
	result = models.CharField(max_length=20, choices=Result.choices, default=Result.PROCESSED)
	note = models.CharField(max_length=255, null=True, blank=True)
	payload = models.TextField()
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		db_table = 'paddle_webhook_events'
