from django.db import transaction

from .models import CoinLedger, CoinTxType, CoinWallet

# 거래 유형별 부호: 잘못된 부호로 호출하면 바로 오류
_SIGN = {
    CoinTxType.CHARGE: 1,
    CoinTxType.JURY_REWARD: 1,
    CoinTxType.SIGNUP_BONUS: 1,
    CoinTxType.VERDICT_RESTORE: 1,
    CoinTxType.ADMIN_GRANT: 1,
    CoinTxType.VERDICT_SPEND: -1,
    CoinTxType.PAYMENT_REVOKE: -1,
    CoinTxType.ADMIN_DEDUCT: -1,
}


class InsufficientCoins(Exception):
    """잔액 부족"""


@transaction.atomic
def change_coins(*, user, tx_type, amount, idempotency_key,
                 case=None, payment_order=None, memo=None, created_by=None):
    """
    코인 변경의 유일한 통로. 반환: (ledger, created)
    created=False면 같은 idempotency_key로 이미 처리된 요청(중복 호출).
    """
    if amount == 0 or (amount > 0) != (_SIGN[tx_type] > 0):
        raise ValueError(f"{tx_type}에 맞지 않는 amount: {amount}")

    CoinWallet.objects.get_or_create(user=user)
    # 반드시 잠금을 먼저 잡고, 그 다음에 멱등키를 확인한다 (동시 요청 중복 방지)
    wallet = CoinWallet.objects.select_for_update().get(user=user)

    existing = CoinLedger.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        if existing.user_id != user.pk or existing.tx_type != tx_type:
            raise ValueError("idempotency_key가 다른 거래와 충돌합니다.")
        return existing, False

    new_balance = wallet.balance + amount
    if new_balance < 0:
        raise InsufficientCoins()

    wallet.balance = new_balance
    wallet.save(update_fields=["balance", "updated_at"])
    ledger = CoinLedger.objects.create(
        user=user, tx_type=tx_type, amount=amount, balance_after=new_balance,
        case=case, payment_order=payment_order, idempotency_key=idempotency_key,
        memo=memo, created_by=created_by,
    )
    return ledger, True