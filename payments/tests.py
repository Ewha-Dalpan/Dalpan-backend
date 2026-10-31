import hashlib
import hmac
import json
import time
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from coins.models import CoinLedger, CoinTxType, CoinWallet

from .models import CoinProduct, OrderStatus, PaddleWebhookEvent, PaymentOrder, PaymentRefund, WebhookResult
from .services import _is_full_refund

SECRET = "pdl_ntfset_test_secret"


@override_settings(PADDLE_WEBHOOK_SECRET=SECRET)
class WebhookTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="pay_user", nickname="결제테스트")
        self.product = CoinProduct.objects.create(
            name="5톨", coin_amount=5, price_krw=4000, paddle_price_id="pri_test_5")
        self.big = CoinProduct.objects.create(
            name="50톨", coin_amount=50, price_krw=40000, paddle_price_id="pri_test_50")
        self.order = PaymentOrder.objects.create(
            user=self.user, product=self.product, coin_amount=5, price_krw=4000)
        self.url = reverse("paddle-webhook")

    # ---------- 헬퍼 ----------
    def send(self, payload, *, ts=None, secret=SECRET):
        body = json.dumps(payload)
        ts = str(int(time.time()) if ts is None else ts)
        sig = hmac.new(secret.encode(), f"{ts}:{body}".encode(), hashlib.sha256).hexdigest()
        return self.client.post(
            self.url, data=body, content_type="application/json",
            HTTP_PADDLE_SIGNATURE=f"ts={ts};h1={sig}",
        )

    def completed(self, event_id="evt_1", txn="txn_1", price="pri_test_5", order=None):
        order = order or self.order
        return {
            "event_id": event_id,
            "event_type": "transaction.completed",
            "occurred_at": timezone.now().isoformat(),
            "data": {
                "id": txn, "status": "completed",
                "custom_data": {"order_id": str(order.pk)},
                "items": [{"quantity": 1, "price": {"id": price}}],
            },
        }

    def adjustment(self, event_id, adj="adj_1", txn="txn_1", status="approved", type_="full",
                   event_type="adjustment.updated", action="refund", total="4000"):
        # total: 환불 금액(세금 포함). 기본값은 결제 금액과 같은 4000 = 전액 환불
        return {
            "event_id": event_id,
            "event_type": event_type,
            "occurred_at": timezone.now().isoformat(),
            "data": {"id": adj, "action": action, "type": type_, "status": status,
                     "transaction_id": txn, "totals": {"total": total}},
        }

    def balance(self):
        return CoinWallet.objects.filter(user=self.user).values_list("balance", flat=True).first() or 0

    def ledger_count(self, tx_type):
        return CoinLedger.objects.filter(user=self.user, tx_type=tx_type).count()

    # ---------- 서명 ----------
    def test_invalid_signature_rejected(self):
        res = self.send(self.completed(), secret="wrong")
        self.assertEqual(res.status_code, 401)
        self.assertEqual(self.balance(), 0)
        self.assertEqual(PaddleWebhookEvent.objects.count(), 0)

    def test_old_timestamp_rejected(self):
        res = self.send(self.completed(), ts=int(time.time()) - 3600)
        self.assertEqual(res.status_code, 401)

    # ---------- 결제 완료 ----------
    def test_payment_credits_coins_once(self):
        self.assertEqual(self.send(self.completed()).status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.PAID)
        self.assertEqual(self.order.paddle_transaction_id, "txn_1")
        self.assertEqual(self.balance(), 5)
        # 같은 이벤트 재전송 (Paddle 재시도)
        self.assertEqual(self.send(self.completed()).status_code, 200)
        self.assertEqual(self.balance(), 5)
        self.assertEqual(self.ledger_count(CoinTxType.CHARGE), 1)
        self.assertEqual(PaddleWebhookEvent.objects.count(), 1)

    def test_price_mismatch_is_blocked(self):
        # 5톨 가격 ID로 결제하면서 50톨 주문번호를 넣는 조작
        order50 = PaymentOrder.objects.create(
            user=self.user, product=self.big, coin_amount=50, price_krw=40000)
        res = self.send(self.completed(price="pri_test_5", order=order50))
        self.assertEqual(res.status_code, 200)
        order50.refresh_from_db()
        self.assertEqual(order50.status, OrderStatus.PENDING)
        self.assertEqual(self.balance(), 0)
        self.assertEqual(PaddleWebhookEvent.objects.get().result, WebhookResult.DATA_ERROR)

    def test_expired_order_is_still_credited(self):
        PaymentOrder.objects.filter(pk=self.order.pk).update(status=OrderStatus.EXPIRED)
        self.send(self.completed())
        self.assertEqual(self.balance(), 5)

    def test_second_payment_for_same_order_not_credited(self):
        self.send(self.completed(event_id="evt_1", txn="txn_1"))
        self.send(self.completed(event_id="evt_2", txn="txn_2"))
        self.assertEqual(self.balance(), 5)
        self.assertEqual(PaddleWebhookEvent.objects.get(event_id="evt_2").result, WebhookResult.DATA_ERROR)

    def test_unknown_order_recorded_not_retried(self):
        payload = self.completed()
        payload["data"]["custom_data"] = {"order_id": "00000000-0000-0000-0000-000000000000"}
        self.assertEqual(self.send(payload).status_code, 200)
        self.assertEqual(PaddleWebhookEvent.objects.get().result, WebhookResult.DATA_ERROR)

    # ---------- 환불 ----------
    def test_full_refund_revokes_coins_once(self):
        self.send(self.completed())
        self.assertEqual(self.send(self.adjustment("evt_r1")).status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.REFUNDED)
        self.assertEqual(self.balance(), 0)
        refund = PaymentRefund.objects.get()
        self.assertEqual((refund.status, refund.coins_revoked), ("APPROVED", 5))
        # 같은 환불에 대한 다른 이벤트가 또 와도 한 번만 회수
        self.send(self.adjustment("evt_r2", event_type="adjustment.created"))
        self.assertEqual(self.ledger_count(CoinTxType.PAYMENT_REVOKE), 1)

    def test_dashboard_full_refund_with_partial_type_revokes(self):
        # 실제 샌드박스에서 겪은 버그: 대시보드 전액 환불은 type이 'partial'로 와도 금액이 결제 금액과 같다
        self.send(self.completed())
        self.send(self.adjustment("evt_r1", type_="partial", total="4000"))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.REFUNDED)
        self.assertEqual(self.balance(), 0)
        self.assertEqual(PaymentRefund.objects.get().coins_revoked, 5)
        self.assertEqual(PaddleWebhookEvent.objects.get(event_id="evt_r1").result, WebhookResult.PROCESSED)

    def test_refund_after_spending_revokes_only_remaining(self):
        self.send(self.completed())
        CoinWallet.objects.filter(user=self.user).update(balance=2)  # 3톨은 이미 사용
        self.send(self.adjustment("evt_r1"))
        self.assertEqual(self.balance(), 0)
        self.assertEqual(PaymentRefund.objects.get().coins_revoked, 2)
        self.assertEqual(PaddleWebhookEvent.objects.get(event_id="evt_r1").result, WebhookResult.DATA_ERROR)

    def test_partial_refund_is_left_for_admin(self):
        # 진짜 부분 환불: 결제 금액(4000)보다 적은 금액
        self.send(self.completed())
        self.send(self.adjustment("evt_r1", type_="partial", total="2000"))
        self.assertEqual(self.balance(), 5)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.PAID)
        self.assertEqual(PaddleWebhookEvent.objects.get(event_id="evt_r1").result, WebhookResult.DATA_ERROR)

    def test_pending_refund_waits_for_approval(self):
        self.send(self.completed())
        self.send(self.adjustment("evt_r1", status="pending_approval", event_type="adjustment.created"))
        self.assertEqual(self.balance(), 5)
        self.send(self.adjustment("evt_r2", status="approved"))
        self.assertEqual(self.balance(), 0)

    def test_refund_before_payment_event_is_retried(self):
        adj = self.adjustment("evt_r1")
        self.assertEqual(self.send(adj).status_code, 500)  # 결제 처리 전이라 재전송을 요청
        self.assertFalse(PaddleWebhookEvent.objects.filter(event_id="evt_r1").exists())
        self.send(self.completed())
        self.assertEqual(self.send(adj).status_code, 200)  # 재전송되면 정상 처리
        self.assertEqual(self.balance(), 0)

    # ---------- 차지백 ----------
    def test_chargeback_after_refund_not_revoked_twice(self):
        # 5톨 결제 → 전액 환불(5 회수) → 다른 결제로 3톨 보유 → 같은 결제에 차지백
        self.send(self.completed())
        self.send(self.adjustment("evt_r1"))
        CoinWallet.objects.filter(user=self.user).update(balance=3)
        self.send(self.adjustment("evt_c1", adj="adj_2", action="chargeback"))
        self.assertEqual(self.balance(), 3)
        self.assertEqual(self.ledger_count(CoinTxType.PAYMENT_REVOKE), 1)
        self.assertEqual(PaddleWebhookEvent.objects.get(event_id="evt_c1").result, WebhookResult.DATA_ERROR)

    def test_chargeback_reverse_is_flagged(self):
        self.send(self.completed())
        self.send(self.adjustment("evt_c1", action="chargeback"))
        self.send(self.adjustment("evt_c2", adj="adj_2", action="chargeback_reverse"))
        event = PaddleWebhookEvent.objects.get(event_id="evt_c2")
        self.assertEqual((event.result, event.order_id), (WebhookResult.DATA_ERROR, self.order.pk))


class ApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="pay_api", nickname="결제API")
        self.other_user = User.objects.create_user(username="pay_api2", nickname="결제API2")
        self.product = CoinProduct.objects.create(
            name="5톨", coin_amount=5, price_krw=4000, paddle_price_id="pri_api_5")
        self.hidden = CoinProduct.objects.create(
            name="숨김", coin_amount=1, price_krw=1000, paddle_price_id="pri_api_1", is_active=False)
        self.api = APIClient()

    def test_products_are_public_and_hide_inactive(self):
        res = self.api.get(reverse("payment-products"))
        self.assertEqual(res.status_code, 200)
        self.assertIn("5톨", [p["name"] for p in res.json()])
        self.assertNotIn("숨김", [p["name"] for p in res.json()])

    def test_order_requires_login(self):
        res = self.api.post(reverse("payment-orders"), {"product_id": self.product.pk}, format="json")
        self.assertEqual(res.status_code, 401)

    @override_settings(PAYAPP_USER_ID="seller", PAYAPP_LINK_KEY="key", PAYAPP_LINK_VALUE="value",
                       PAYAPP_REQUEST_KEY="request-key",
                       PAYAPP_FEEDBACK_URL="https://api.example.com/api/payments/payapp/feedback/",
                       PAYAPP_RETURN_URL="https://example.com/result")
    @patch("payments.payapp_views.create_payment", return_value=("123", "https://payapp.kr/pay/123"))
    def test_order_uses_server_side_price(self, mocked):
        self.api.force_authenticate(self.user)
        res = self.api.post(
            reverse("payment-orders"), {"product_id": self.product.pk, "price_krw": 1, "recvphone": "01012345678"}, format="json")
        self.assertEqual(res.status_code, 201)
        order = PaymentOrder.objects.get(pk=res.json()["order_id"])
        self.assertEqual((order.price_krw, order.coin_amount, order.status), (4000, 5, "PENDING"))
        self.assertEqual(res.json()["pay_url"], "https://payapp.kr/pay/123")

    def test_inactive_product_rejected(self):
        self.api.force_authenticate(self.user)
        res = self.api.post(reverse("payment-orders"), {"product_id": self.hidden.pk, "recvphone": "01012345678"}, format="json")
        self.assertEqual(res.status_code, 400)

    def test_order_detail_only_for_owner(self):
        order = PaymentOrder.objects.create(
            user=self.user, product=self.product, coin_amount=5, price_krw=4000)
        url = reverse("payment-order-detail", kwargs={"order_id": order.pk})
        self.api.force_authenticate(self.user)
        self.assertEqual(self.api.get(url).status_code, 200)
        self.api.force_authenticate(self.other_user)
        self.assertEqual(self.api.get(url).status_code, 404)

    def test_expire_pending_orders(self):
        order = PaymentOrder.objects.create(
            user=self.user, product=self.product, coin_amount=5, price_krw=4000)
        PaymentOrder.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(hours=48))
        call_command("expire_pending_orders")
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.EXPIRED)


class IsFullRefundTests(SimpleTestCase):
    """대시보드 환불은 품목 단위라 type이 'partial'로 와도 전액일 수 있다.
    전액 여부는 결제 금액(세금 포함) 비교로 판단해야 한다."""

    def setUp(self):
        # 실제 주문 대신 price_krw만 가진 가짜 객체 (DB 불필요)
        self.order = SimpleNamespace(price_krw=4000)

    def test_type_full이면_전액(self):
        data = {"type": "full", "totals": {"total": "4000"}}
        self.assertTrue(_is_full_refund(data, self.order))

    def test_type_partial이어도_금액이_같으면_전액(self):
        data = {"type": "partial", "totals": {"total": "4000"}}
        self.assertTrue(_is_full_refund(data, self.order))

    def test_금액이_적으면_부분(self):
        data = {"type": "partial", "totals": {"total": "2000"}}
        self.assertFalse(_is_full_refund(data, self.order))

    def test_세금제외_금액은_전액이_아님(self):
        # subtotal(3636)을 잘못 꺼내 비교하면 안 된다는 기준
        data = {"type": "partial", "totals": {"total": "3636"}}
        self.assertFalse(_is_full_refund(data, self.order))

    def test_금액이_없거나_이상하면_부분(self):
        self.assertFalse(_is_full_refund({"type": "partial"}, self.order))
        self.assertFalse(_is_full_refund({"type": "partial", "totals": {"total": "abc"}}, self.order))