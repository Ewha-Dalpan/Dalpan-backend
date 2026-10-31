"""PayApp server-side checkout and authenticated feedback processing."""
import hashlib
import hmac
import re
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID
from zoneinfo import ZoneInfo

import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from coins.models import CoinLedger, CoinTxType
from coins.services import change_coins
from .models import OrderStatus, PayAppFeedback, PayAppRefund, PaymentOrder, WebhookResult
from .services import _revoke_coins


class PayAppError(Exception):
    """The remote outcome may be unknown; do not automatically repeat payrequest."""


class PayAppRejected(PayAppError):
    pass


class InvalidFeedback(Exception):
    pass


def validate_settings():
    for name in ("PAYAPP_USER_ID", "PAYAPP_LINK_KEY", "PAYAPP_LINK_VALUE",
                 "PAYAPP_REQUEST_KEY", "PAYAPP_FEEDBACK_URL", "PAYAPP_RETURN_URL"):
        if not getattr(settings, name, ""):
            raise ImproperlyConfigured("PayApp configuration is incomplete.")
    for name in ("PAYAPP_FEEDBACK_URL", "PAYAPP_RETURN_URL"):
        try:
            url = urlsplit(getattr(settings, name))
        except ValueError as exc:
            raise ImproperlyConfigured("Invalid PayApp callback URL.") from exc
        if url.scheme != "https" or not url.hostname or url.fragment:
            raise ImproperlyConfigured("PayApp requires public HTTPS callback and return URLs.")
    if not settings.PAYAPP_FEEDBACK_URL.endswith("/"):
        raise ImproperlyConfigured("PayApp feedback URL must end with /.")


def make_hash(date_text):
    value = settings.PAYAPP_USER_ID + date_text + settings.PAYAPP_REQUEST_KEY
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def return_url(order):
    url = urlsplit(settings.PAYAPP_RETURN_URL)
    query = [(key, value) for key, value in parse_qsl(url.query)
             if key != "order_id"]
    query.append(("order_id", str(order.pk)))
    return urlunsplit(url._replace(query=urlencode(query)))


def create_payment(order, recvphone):
    validate_settings()
    requestdate = timezone.now().astimezone(
        ZoneInfo("Asia/Seoul")).strftime("%Y%m%d%H%M%S")
    data = {
        "cmd": "payrequest", "userid": settings.PAYAPP_USER_ID,
        "requestdate": requestdate, "hash": make_hash(requestdate),
        "goodname": order.product.name, "price": str(order.price_krw),
        "recvphone": recvphone, "feedbackurl": settings.PAYAPP_FEEDBACK_URL,
        "returnurl": return_url(order), "var1": str(order.pk),
        "var2": str(order.payapp_token), "smsuse": "n", "checkretry": "y",
    }
    try:
        response = requests.post(
            settings.PAYAPP_API_URL, data=data, timeout=(5, 20),
            allow_redirects=False,
        )
        response.raise_for_status()
        if response.status_code != 200:
            raise PayAppError("Unexpected PayApp HTTP response.")
        pairs = parse_qsl(response.content.decode("utf-8"), keep_blank_values=True)
        if len({key for key, _ in pairs}) != len(pairs):
            raise PayAppError("Ambiguous PayApp response.")
        result = dict(pairs)
    except (requests.RequestException, UnicodeError, ValueError) as exc:
        raise PayAppError("PayApp request outcome is unknown.") from exc
    if result.get("state") == "0":
        raise PayAppRejected("PayApp rejected the request.")
    if result.get("state") != "1":
        raise PayAppError("Invalid PayApp response.")
    mul_no, pay_url = result.get("mul_no", ""), result.get("payurl", "")
    try:
        url = urlsplit(pay_url)
        port = url.port
    except ValueError as exc:
        raise PayAppError("Invalid PayApp checkout URL.") from exc
    host = url.hostname or ""
    if (not re.fullmatch(r"[0-9]{1,64}", mul_no)
            or url.scheme != "https" or url.username or url.password
            or port not in (None, 443)
            or not (host == "payapp.kr" or host.endswith(".payapp.kr"))
            or len(pay_url) > 1000):
        raise PayAppError("Invalid PayApp checkout URL or transaction number.")
    return mul_no, pay_url


def verify_feedback(data):
    validate_settings()
    expected = {
        "userid": settings.PAYAPP_USER_ID,
        "linkkey": settings.PAYAPP_LINK_KEY,
        "linkval": settings.PAYAPP_LINK_VALUE,
    }
    for key, value in expected.items():
        if not hmac.compare_digest(data.get(key, "").encode(), value.encode()):
            raise InvalidFeedback()
    date_text = data.get("responsedate", "")
    if not re.fullmatch(r"[0-9]{14}", date_text):
        raise InvalidFeedback()
    try:
        datetime.strptime(date_text, "%Y%m%d%H%M%S")
    except ValueError as exc:
        raise InvalidFeedback() from exc
    if not hmac.compare_digest(
            data.get("hash", "").lower().encode(), make_hash(date_text).encode()):
        raise InvalidFeedback()
    # Do not apply Paddle's 300-second TTL: delayed PayApp retries are legitimate.
    # Order state + ledger idempotency prevent replay from crediting twice.


def _record(order, data, result, note=""):
    fields = ("mul_no", "pay_state", "price", "var1", "responsedate",
              "orig_mul_no", "orig_price", "canceldate")
    PayAppFeedback.objects.create(
        order=order, mul_no=data.get("mul_no", "")[:64],
        pay_state=data.get("pay_state", "")[:10], result=result,
        note=note[:255], payload={key: data[key] for key in fields if key in data},
    )


def _amount(value):
    if not re.fullmatch(r"[0-9]{1,12}", value):
        raise InvalidFeedback()
    return int(value)


@transaction.atomic
def process_feedback(data):
    verify_feedback(data)
    try:
        order_id = UUID(data.get("var1", ""))
    except (ValueError, TypeError, AttributeError) as exc:
        raise InvalidFeedback() from exc
    order = PaymentOrder.objects.select_for_update().filter(
        pk=order_id, provider="payapp").first()
    if order is None or not hmac.compare_digest(
            data.get("var2", "").encode(), str(order.payapp_token).encode()):
        raise InvalidFeedback()
    state = data.get("pay_state", "")
    mul_no = data.get("mul_no", "")
    if not re.fullmatch(r"[0-9]{1,64}", mul_no):
        raise InvalidFeedback()
    partial = state in {"70", "71"}
    remote_id = (data.get("orig_mul_no") or mul_no) if partial else mul_no
    if not re.fullmatch(r"[0-9]{1,64}", remote_id):
        raise InvalidFeedback()
    price = _amount(data.get("orig_price", "")) if partial else _amount(data.get("price", ""))
    if price != order.price_krw:
        raise InvalidFeedback()
    if partial and not 0 < _amount(data.get("price", "")) <= price:
        raise InvalidFeedback()
    if order.payapp_mul_no and order.payapp_mul_no != remote_id:
        raise InvalidFeedback()
    if PaymentOrder.objects.filter(payapp_mul_no=remote_id).exclude(pk=order.pk).exists():
        raise InvalidFeedback()
    # Authenticated feedback can recover a transaction whose API response was lost.
    if not order.payapp_mul_no:
        order.payapp_mul_no = remote_id
        order.save(update_fields=["payapp_mul_no", "updated_at"])

    if partial:
        _record(order, data, WebhookResult.DATA_ERROR,
                "부분취소: 금액 및 보너스를 포함한 코인 회수 수동 확인 필요")
        return

    if state == "4":
        if order.status in (OrderStatus.PAID, OrderStatus.REFUNDED):
            _record(order, data, WebhookResult.IGNORED, "이미 지급 또는 취소된 주문")
            return
        if order.status == OrderStatus.CANCELLED:
            _record(order, data, WebhookResult.DATA_ERROR, "요청 취소 후 결제 완료: 판매자 확인 필요")
            return
        change_coins(
            user=order.user, tx_type=CoinTxType.CHARGE, amount=order.coin_amount,
            idempotency_key=f"payment-charge:{order.pk}", payment_order=order,
        )
        order.status, order.paid_at = OrderStatus.PAID, timezone.now()
        order.save(update_fields=["status", "paid_at", "updated_at"])
        _record(order, data, WebhookResult.PROCESSED)
        return

    if state in {"9", "64"}:
        if order.status == OrderStatus.REFUNDED:
            _record(order, data, WebhookResult.IGNORED, "이미 전액 취소된 주문")
            return
        charged = CoinLedger.objects.filter(
            payment_order=order, tx_type=CoinTxType.CHARGE).exists()
        revoked = _revoke_coins(order, f"payapp:{remote_id}") if charged else 0
        PayAppRefund.objects.get_or_create(
            order=order, defaults={"refund_amount": order.price_krw,
                                   "coins_revoked": revoked})
        order.status, order.refunded_at = OrderStatus.REFUNDED, timezone.now()
        order.save(update_fields=["status", "refunded_at", "updated_at"])
        shortfall = charged and revoked < order.coin_amount
        _record(order, data,
                WebhookResult.DATA_ERROR if shortfall else WebhookResult.PROCESSED,
                f"사용한 코인이 있어 {revoked}/{order.coin_amount}만 회수" if shortfall else "")
        return

    if state in {"8", "16", "31", "32"}:
        # The official table and older sample enumerate different request-cancel codes.
        if order.status in (OrderStatus.PENDING, OrderStatus.EXPIRED):
            order.status = OrderStatus.CANCELLED
            order.save(update_fields=["status", "updated_at"])
            _record(order, data, WebhookResult.PROCESSED, "결제 요청 취소")
        else:
            _record(order, data, WebhookResult.DATA_ERROR, "확정 주문의 요청취소 통보 확인 필요")
        return
    _record(order, data, WebhookResult.IGNORED, f"코인 변경 없는 상태: {state}")
