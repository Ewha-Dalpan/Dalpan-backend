from django.conf import settings
from django.db import models
from django.db.models import Q


class CoinWallet(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="wallet")
    balance = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "coin_wallets"
        constraints = [
            models.CheckConstraint(condition=Q(balance__gte=0), name="ck_wallet_balance_gte0"),
        ]


class CoinTxType(models.TextChoices):
    CHARGE = "CHARGE", "충전"
    VERDICT_SPEND = "VERDICT_SPEND", "판결 사용"
    JURY_REWARD = "JURY_REWARD", "배심 적립"
    SIGNUP_BONUS = "SIGNUP_BONUS", "가입 보너스"
    VERDICT_RESTORE = "VERDICT_RESTORE", "판결 실패 반환"
    PAYMENT_REVOKE = "PAYMENT_REVOKE", "결제 취소 회수"
    ADMIN_GRANT = "ADMIN_GRANT", "관리자 지급"
    ADMIN_DEDUCT = "ADMIN_DEDUCT", "관리자 차감"


class CoinLedger(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="coin_ledgers")
    tx_type = models.CharField(max_length=20, choices=CoinTxType.choices)
    amount = models.IntegerField()  # 적립 +, 사용 -
    balance_after = models.PositiveIntegerField()
    case = models.ForeignKey(
        "cases.Case", null=True, blank=True, on_delete=models.SET_NULL, related_name="coin_ledgers"
    )
    payment_order = models.ForeignKey(
        "payments.PaymentOrder", null=True, blank=True, on_delete=models.PROTECT, related_name="coin_ledgers"
    )
    idempotency_key = models.CharField(max_length=150, unique=True)
    memo = models.CharField(max_length=255, null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="admin_coin_ledgers"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "coin_ledgers"
        indexes = [
            models.Index(fields=["user", "created_at"], name="ix_ledger_user_time"),
            models.Index(fields=["user", "tx_type", "created_at"], name="ix_ledger_user_type_time"),
        ]
        constraints = [
            models.CheckConstraint(condition=~Q(amount=0), name="ck_ledger_amount_nonzero"),
        ]