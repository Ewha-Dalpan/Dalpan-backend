from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class CoinProduct(models.Model):
	name = models.CharField(max_length=50)
	coin_amount = models.IntegerField()
	price_krw = models.IntegerField()
	paddle_price_id = models.CharField(max_length=64)
	is_active = models.BooleanField(default=True)
	sort_order = models.SmallIntegerField(default=0)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		db_table = 'coin_products'


class CoinWallet(models.Model):
	user = models.OneToOneField(
		settings.AUTH_USER_MODEL,
		on_delete=models.CASCADE,
		related_name='coin_wallet',
	)
	balance = models.IntegerField(default=0, validators=[MinValueValidator(0)])
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		db_table = 'coin_wallets'


class CoinLedger(models.Model):
	class TransactionType(models.TextChoices):
		CHARGE = 'CHARGE', '충전'
		VERDICT_SPEND = 'VERDICT_SPEND', '판결 사용'
		JURY_REWARD = 'JURY_REWARD', '배심 보상'
		SIGNUP_BONUS = 'SIGNUP_BONUS', '가입 보너스'
		VERDICT_RESTORE = 'VERDICT_RESTORE', '판결 코인 반환'
		PAYMENT_REVOKE = 'PAYMENT_REVOKE', '결제 코인 회수'
		ADMIN_GRANT = 'ADMIN_GRANT', '관리자 지급'
		ADMIN_DEDUCT = 'ADMIN_DEDUCT', '관리자 차감'

	user = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.PROTECT,
		related_name='coin_ledgers',
	)
	tx_type = models.CharField(max_length=20, choices=TransactionType.choices)
	amount = models.IntegerField()
	balance_after = models.IntegerField()
	case = models.ForeignKey(
		'cases.Case',
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='coin_ledgers',
	)
	payment_order = models.ForeignKey(
		'payments.PaymentOrder',
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='coin_ledgers',
	)
	idempotency_key = models.CharField(max_length=150, unique=True)
	memo = models.CharField(max_length=255, null=True, blank=True)
	created_by = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		db_column='created_by',
		related_name='admin_coin_transactions',
	)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		db_table = 'coin_ledgers'
