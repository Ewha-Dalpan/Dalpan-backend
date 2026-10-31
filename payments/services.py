import hashlib
import hmac
import logging
import time
from datetime import timedelta
from datetime import timezone as dt_timezone
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from coins.models import CoinLedger, CoinTxType, CoinWallet
from coins.services import change_coins

from .models import (
    OrderStatus,
    PaddleWebhookEvent,
    PaymentOrder,
    PaymentRefund,
    RefundStatus,
    WebhookResult,
)

logger = logging.getLogger(__name__)


class InvalidSignature(Exception):
    """서명이 없거나 틀림 → 401"""


class WebhookNotConfigured(Exception):
    """시크릿 미설정 → 500 (설정 후 Paddle이 재전송)"""


class TransientError(Exception):
    """잠시 후 다시 보내면 해결될 수 있는 상태 → 500으로 응답해 Paddle이 재전송하게 한다."""


# ------------------------------------------------------------------
# 서명 검증
# ------------------------------------------------------------------
def verify_signature(raw_body: bytes, header: str) -> None:
    """Paddle-Signature 헤더(ts=...;h1=...)를 원본 바이트로 검증한다."""
    secret = getattr(settings, "PADDLE_WEBHOOK_SECRET", "")
    if not secret:
        raise WebhookNotConfigured()

    timestamp, signatures = None, []
    for part in (header or "").split(";"):
        key, _, value = part.strip().partition("=")
        if key == "ts":
            timestamp = value
        elif key == "h1" and value:
            signatures.append(value)  # 시크릿 교체 중에는 h1이 여러 개일 수 있다

    if not timestamp or not (timestamp.isascii() and timestamp.isdigit()) or not signatures:
        raise InvalidSignature()

    tolerance = getattr(settings, "PADDLE_WEBHOOK_TOLERANCE_SECONDS", 300)
    if abs(time.time() - int(timestamp)) > tolerance:
        raise InvalidSignature()

    expected = hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("ascii") + b":" + raw_body,
        hashlib.sha256,
    ).hexdigest().encode("utf-8")

    if not any(hmac.compare_digest(expected, sig.encode("utf-8")) for sig in signatures):
        raise InvalidSignature()


# ------------------------------------------------------------------
# 이벤트 처리
# ------------------------------------------------------------------
def _parse_dt(value):
    if not value:
        return None
    try:
        parsed = parse_datetime(value)
    except ValueError:
        return None
    if parsed is not None and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, dt_timezone.utc)
    return parsed


def _to_uuid(value):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def process_event(payload: dict, raw_body: str):
    """
    서명이 검증된 이벤트를 처리한다. 이벤트 저장, 주문 상태 변경, 코인 지급은 한 트랜잭션이다.
    예외가 나면 전부 롤백되고(이벤트 기록도 사라짐) 호출한 쪽이 500을 응답해 Paddle이 재전송한다.
    """
    event_id = payload.get("event_id")
    event_type = payload.get("event_type") or ""
    if not event_id:
        return "invalid"

    event_time = _parse_dt(payload.get("occurred_at")) or timezone.now()
    data = payload.get("data") or {}

    with transaction.atomic():
        try:
            # 중복 감지는 이 저장 하나에만 한정한다 (다른 DB 오류를 '중복'으로 오인하지 않도록 savepoint로 분리)
            with transaction.atomic():
                event = PaddleWebhookEvent.objects.create(
                    event_id=event_id,
                    event_type=event_type[:64],
                    occurred_at=event_time,
                    payload=raw_body,
                )
        except IntegrityError:
            return "duplicate"

        handler = _HANDLERS.get(event_type)
        if handler is None:
            result, note, order = WebhookResult.IGNORED, "처리 대상이 아닌 이벤트", None
        else:
            result, note, order = handler(data, event_time)

        event.result = result
        event.note = (note or "")[:255]
        event.order = order
        event.save(update_fields=["result", "note", "order"])
    return result


def _handle_transaction_completed(data, event_time):
    if data.get("status") != "completed":
        return WebhookResult.IGNORED, f"거래 상태 {data.get('status')}", None

    transaction_id = data.get("id")
    order_id = _to_uuid((data.get("custom_data") or {}).get("order_id"))
    if not transaction_id or order_id is None:
        return WebhookResult.DATA_ERROR, "거래 ID 또는 custom_data.order_id가 없거나 형식이 올바르지 않음", None

    order = PaymentOrder.objects.select_for_update().filter(pk=order_id, provider="paddle").first()
    if order is None:
        return WebhookResult.DATA_ERROR, f"주문 없음: {order_id}", None

    # 가장 중요한 교차 검증: 실제 결제된 가격이 이 주문의 상품과 같은가 (주문번호는 사용자가 조작할 수 있다)
    items = data.get("items") or []
    paid_price_id = (items[0].get("price") or {}).get("id") if len(items) == 1 else None
    if (
        len(items) != 1
        or items[0].get("quantity", 1) != 1
        or paid_price_id != order.product.paddle_price_id
    ):
        return WebhookResult.DATA_ERROR, "결제된 상품이 주문과 일치하지 않음 - 지급하지 않음", order

    if order.status == OrderStatus.PAID:
        if order.paddle_transaction_id == transaction_id:
            return WebhookResult.PROCESSED, "이미 지급된 결제", order
        return (
            WebhookResult.DATA_ERROR,
            f"이미 다른 결제({order.paddle_transaction_id})로 지급된 주문 - 중복 결제 확인 필요",
            order,
        )
    if order.status == OrderStatus.REFUNDED:
        return WebhookResult.DATA_ERROR, "이미 환불된 주문", order
    if PaymentOrder.objects.filter(paddle_transaction_id=transaction_id).exclude(pk=order.pk).exists():
        return WebhookResult.DATA_ERROR, "같은 결제가 다른 주문에 연결되어 있음", order

    was_expired = order.status == OrderStatus.EXPIRED  # 만료된 주문도 돈을 받았으니 지급한다
    order.status = OrderStatus.PAID
    order.paddle_transaction_id = transaction_id
    order.paid_at = _parse_dt(data.get("billed_at")) or event_time
    order.save(update_fields=["status", "paddle_transaction_id", "paid_at", "updated_at"])

    change_coins(
        user=order.user,
        tx_type=CoinTxType.CHARGE,
        amount=order.coin_amount,
        idempotency_key=f"payment-charge:{order.pk}",
        payment_order=order,
    )
    note = "만료된 주문이었지만 결제가 확인되어 지급함" if was_expired else ""
    return WebhookResult.PROCESSED, note, order


_REFUND_STATUS = {
    "pending_approval": RefundStatus.PENDING,
    "approved": RefundStatus.APPROVED,
    "rejected": RefundStatus.REJECTED,
    "reversed": RefundStatus.REJECTED,
}


def _revoke_coins(order, adjustment_id):
    """회수 가능한 만큼만(잔액 한도) 회수한다. 같은 환불에는 한 번만 반영되고 실제 회수량을 돌려준다."""
    key = f"payment-revoke:{adjustment_id}"
    existing = CoinLedger.objects.filter(idempotency_key=key).first()
    if existing:
        return -existing.amount

    CoinWallet.objects.get_or_create(user=order.user)
    wallet = CoinWallet.objects.select_for_update().get(user=order.user)
    amount = min(order.coin_amount, wallet.balance)
    if amount <= 0:
        return 0

    ledger, _ = change_coins(
        user=order.user,
        tx_type=CoinTxType.PAYMENT_REVOKE,
        amount=-amount,
        idempotency_key=key,
        payment_order=order,
        memo="결제 환불로 인한 코인 회수",
    )
    return -ledger.amount

def _is_full_refund(data, order):
    """환불 금액이 결제 금액과 같으면 전액 환불로 본다.
    대시보드 환불은 품목 단위라 type이 'partial'로 와도 실제로는 전액일 수 있다."""
    if data.get("type") == "full":
        return True
    total = str((data.get("totals") or {}).get("total", ""))
    return total.isascii() and total.isdigit() and int(total) == order.price_krw

def _handle_adjustment(data, event_time):
    action = data.get("action")
    if action == "chargeback_reverse":
        order = PaymentOrder.objects.filter(paddle_transaction_id=data.get("transaction_id")).first()
        return WebhookResult.DATA_ERROR, "차지백 취소(고객 결제 유지) - 회수한 코인 복구 여부 확인", order
    if action not in ("refund", "chargeback"):
        return WebhookResult.IGNORED, f"action={action}", None

    adjustment_id = data.get("id")
    transaction_id = data.get("transaction_id")
    new_status = _REFUND_STATUS.get(data.get("status"))
    if not adjustment_id or not transaction_id or new_status is None:
        return WebhookResult.DATA_ERROR, "환불 정보가 올바르지 않음", None

    order = PaymentOrder.objects.select_for_update().filter(paddle_transaction_id=transaction_id).first()
    if order is None:
        if timezone.now() - event_time > timedelta(hours=1):
            return WebhookResult.DATA_ERROR, f"결제 주문 없음: {transaction_id}", None
        raise TransientError("결제 완료 처리 전에 환불 이벤트가 먼저 도착함")

    refund = PaymentRefund.objects.filter(paddle_adjustment_id=adjustment_id).first()
    if (
        refund is not None
        and refund.status in (RefundStatus.APPROVED, RefundStatus.REJECTED)
        and new_status == RefundStatus.PENDING
    ):
        return WebhookResult.IGNORED, "이미 확정된 환불에 대한 이전 상태 이벤트", order
    if refund is None:
        refund = PaymentRefund(order=order, paddle_adjustment_id=adjustment_id)

    refund.status = new_status
    amount = str((data.get("totals") or {}).get("total", ""))
    if amount.isascii() and amount.isdigit():
        refund.refund_amount = int(amount)  # Paddle이 보낸 금액 그대로 (참고용)

    # 관리자가 손봐야 하는 건은 모두 DATA_ERROR('확인 필요')로 남겨 관리자 필터 하나로 찾게 한다
    result, note = WebhookResult.PROCESSED, ""
    if new_status == RefundStatus.APPROVED:
        already_revoked = (
            order.status == OrderStatus.REFUNDED
            and order.refunds.filter(status=RefundStatus.APPROVED)
            .exclude(paddle_adjustment_id=adjustment_id)
            .exists()
        )
        if not _is_full_refund(data, order):
            result, note = WebhookResult.DATA_ERROR, "부분 환불 - 코인 회수는 관리자가 직접 처리"
        elif already_revoked:
            # 환불 뒤 같은 결제에 차지백이 또 오는 경우: 코인을 두 번 회수하지 않는다
            result, note = WebhookResult.DATA_ERROR, "이미 다른 환불로 회수된 주문 - 추가 회수 안 함"
        else:
            revoked = _revoke_coins(order, adjustment_id)
            refund.coins_revoked = revoked
            if order.status == OrderStatus.PAID:
                order.status = OrderStatus.REFUNDED
                order.refunded_at = timezone.now()
                order.save(update_fields=["status", "refunded_at", "updated_at"])
            if revoked < order.coin_amount:
                result = WebhookResult.DATA_ERROR
                note = f"이미 사용한 코인이 있어 {revoked}/{order.coin_amount}만 회수"
    elif data.get("status") == "reversed" and refund.coins_revoked:
        result, note = WebhookResult.DATA_ERROR, "환불/차지백 취소(reversed) - 회수한 코인 복구 여부 확인"

    refund.save()
    return result, note, order

# payments/services.py 파일 맨 아래에 추가

_HANDLERS = {
    "transaction.completed": _handle_transaction_completed,
    "adjustment.created": _handle_adjustment,
    "adjustment.updated": _handle_adjustment,
}