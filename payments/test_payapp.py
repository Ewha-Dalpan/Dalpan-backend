from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import requests
from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from accounts.services import register_kakao_user
from coins.models import CoinLedger, CoinTxType, CoinWallet
from coins.services import change_coins
from .models import CoinProduct, OrderStatus, PayAppFeedback, PayAppRefund, PaymentOrder
from .payapp_services import PayAppError, create_payment, make_hash, return_url

CONFIG = dict(
    PAYAPP_USER_ID="seller", PAYAPP_LINK_KEY="key", PAYAPP_LINK_VALUE="value",
    PAYAPP_REQUEST_KEY="request-secret",
    PAYAPP_FEEDBACK_URL="https://api.example.com/api/payments/payapp/feedback/",
    PAYAPP_RETURN_URL="https://example.com/payments/result",
)


@override_settings(**CONFIG)
class PayAppTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="payapp-user", nickname="결제")
        self.product = CoinProduct.objects.create(
            name="50톨 + 5톨 보너스", coin_amount=55, bonus_coin_amount=5, price_krw=49500)
        self.order = PaymentOrder.objects.create(
            user=self.user, product=self.product, provider="payapp",
            coin_amount=55, price_krw=49500, payapp_mul_no="12345")
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def payload(self, **changes):
        date = timezone.localtime().strftime("%Y%m%d%H%M%S")
        data = dict(userid="seller", linkkey="key", linkval="value",
                    responsedate=date, hash=make_hash(date),
                    var1=str(self.order.pk), var2=str(self.order.payapp_token),
                    mul_no="12345", price="49500", pay_state="4")
        data.update(changes)
        return data

    def send(self, **changes):
        return self.client.post(reverse("payapp-feedback"), self.payload(**changes))

    def balance(self):
        return CoinWallet.objects.filter(user=self.user).values_list(
            "balance", flat=True).first() or 0

    def test_bonus_credited_once_even_with_different_response_time(self):
        for _ in range(2):
            response = self.send()
            self.assertEqual((response.status_code, response.content), (200, b"SUCCESS"))
        self.assertEqual(self.balance(), 55)
        self.assertEqual(CoinLedger.objects.filter(tx_type=CoinTxType.CHARGE).count(), 1)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.PAID)

    def test_invalid_credentials_amount_and_binding_do_not_credit(self):
        for changes in [
            {"userid": "wrong"}, {"linkkey": "wrong"}, {"linkval": "wrong"},
            {"hash": "wrong"}, {"price": "1000"}, {"price": "-1"},
            {"mul_no": "999"}, {"var1": "not-a-uuid"}, {"var1": str(uuid4())},
            {"var2": str(uuid4())}, {"responsedate": "20261301123456"},
        ]:
            with self.subTest(changes=changes):
                self.assertEqual(self.send(**changes).status_code, 400)
        self.assertEqual(self.balance(), 0)

    def test_authenticated_callback_recovers_missing_api_response(self):
        self.order.payapp_mul_no = None
        self.order.save(update_fields=["payapp_mul_no"])
        self.assertEqual(self.send().status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payapp_mul_no, "12345")
        self.assertEqual(self.balance(), 55)

    def test_same_transaction_cannot_be_bound_to_another_order(self):
        other = PaymentOrder.objects.create(
            user=self.user, product=self.product, provider="payapp",
            coin_amount=55, price_krw=49500)
        response = self.send(var1=str(other.pk), var2=str(other.payapp_token))
        self.assertEqual(response.status_code, 400)

    def test_paddle_order_cannot_receive_payapp_payment(self):
        self.order.provider = "paddle"
        self.order.save(update_fields=["provider"])
        self.assertEqual(self.send().status_code, 400)

    def test_waiting_virtual_account_does_not_credit(self):
        self.assertEqual(self.send(pay_state="10").status_code, 200)
        self.assertEqual(self.balance(), 0)

    def test_expired_order_still_credits_when_paid(self):
        self.order.status = OrderStatus.EXPIRED
        self.order.save(update_fields=["status"])
        self.send()
        self.assertEqual(self.balance(), 55)

    def test_full_refund_revokes_bonus_once_and_preserves_signup_coin(self):
        change_coins(user=self.user, tx_type=CoinTxType.SIGNUP_BONUS,
                     amount=1, idempotency_key="signup-test")
        self.send()
        self.assertEqual(self.balance(), 56)
        self.send(pay_state="9")
        self.send(pay_state="64")
        self.send()
        self.assertEqual(self.balance(), 1)
        self.assertEqual(PayAppRefund.objects.get().coins_revoked, 55)
        self.assertEqual(CoinLedger.objects.filter(tx_type=CoinTxType.PAYMENT_REVOKE).count(), 1)

    def test_refund_before_payment_prevents_late_credit(self):
        self.send(pay_state="9")
        self.send()
        self.assertEqual(self.balance(), 0)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.REFUNDED)

    def test_refund_after_spending_flags_shortfall(self):
        self.send()
        change_coins(user=self.user, tx_type=CoinTxType.VERDICT_SPEND,
                     amount=-53, idempotency_key="spend-test")
        self.send(pay_state="9")
        self.assertEqual(self.balance(), 0)
        self.assertEqual(PayAppRefund.objects.get().coins_revoked, 2)
        self.assertEqual(PayAppFeedback.objects.latest("id").result, "DATA_ERROR")

    def test_partial_cancel_requires_review_and_matches_original(self):
        self.send()
        response = self.send(pay_state="70", mul_no="67890",
                             orig_mul_no="12345", orig_price="49500", price="9900")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.balance(), 55)
        self.assertEqual(PayAppFeedback.objects.latest("id").result, "DATA_ERROR")
        self.assertEqual(self.send(pay_state="70", orig_mul_no="999",
                                  orig_price="49500", price="9900").status_code, 400)

    def test_request_cancel_and_stale_pending_do_not_undo_payment(self):
        self.send()
        self.send(pay_state="10")
        self.send(pay_state="8")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.PAID)
        self.assertEqual(self.balance(), 55)

    def test_request_cancel_is_terminal(self):
        self.send(pay_state="32")
        self.send()
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.CANCELLED)
        self.assertEqual(self.balance(), 0)

    @patch("payments.payapp_services.change_coins", side_effect=RuntimeError("DB failure"))
    def test_failure_rolls_back_order_and_event(self, mocked):
        self.assertEqual(self.send().status_code, 500)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.PENDING)
        self.assertFalse(PayAppFeedback.objects.exists())

    def test_logs_do_not_contain_secret_or_phone(self):
        self.send(recvphone="01012345678", card_num="1111")
        payload = PayAppFeedback.objects.get().payload
        for key in ("linkkey", "linkval", "hash", "var2", "recvphone", "card_num"):
            self.assertNotIn(key, payload)

    @patch("payments.payapp_views.create_payment", return_value=("98765", "https://payapp.kr/pay/98765"))
    def test_checkout_snapshots_price_and_reuses_idempotency_key(self, mocked):
        key = str(uuid4())
        data = dict(product_id=self.product.pk, recvphone="010-1234-5678", price_krw=1)
        first = self.api.post(reverse("payment-orders"), data, HTTP_IDEMPOTENCY_KEY=key)
        second = self.api.post(reverse("payment-orders"), data, HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual((first.status_code, second.status_code), (201, 200))
        self.assertEqual(first.json()["order_id"], second.json()["order_id"])
        order = PaymentOrder.objects.get(pk=first.json()["order_id"])
        self.assertEqual((order.coin_amount, order.price_krw, order.provider), (55, 49500, "payapp"))
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(mocked.call_args.args[1], "01012345678")

    @patch("payments.payapp_views.create_payment", side_effect=PayAppError())
    def test_timeout_keeps_order_and_does_not_repeat_same_request(self, mocked):
        key = str(uuid4())
        data = dict(product_id=self.product.pk, recvphone="01012345678")
        first = self.api.post(reverse("payment-orders"), data, HTTP_IDEMPOTENCY_KEY=key)
        second = self.api.post(reverse("payment-orders"), data, HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual((first.status_code, second.status_code), (502, 409))
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(PaymentOrder.objects.get(pk=first.json()["order_id"]).payapp_request_status, "UNKNOWN")

    @override_settings(PAYAPP_REQUEST_KEY="")
    @patch("payments.payapp_views.create_payment")
    def test_missing_config_fails_before_creating_order(self, mocked):
        before = PaymentOrder.objects.count()
        result = self.api.post(reverse("payment-orders"),
                               dict(product_id=self.product.pk, recvphone="01012345678"))
        self.assertEqual(result.status_code, 503)
        self.assertEqual(PaymentOrder.objects.count(), before)
        mocked.assert_not_called()

    @patch("payments.payapp_views.create_payment")
    def test_under_minimum_and_missing_phone_rejected(self, mocked):
        self.product.price_krw = 990
        self.product.save(update_fields=["price_krw"])
        result = self.api.post(reverse("payment-orders"),
                               dict(product_id=self.product.pk, recvphone="01012345678"))
        self.assertEqual(result.status_code, 400)
        self.assertEqual(self.api.post(reverse("payment-orders"),
                                      dict(product_id=self.product.pk)).status_code, 400)
        mocked.assert_not_called()

    def test_catalog_seed_and_bonus_metadata(self):
        result = self.client.get(reverse("payment-products")).json()
        seeded = {item["code"]: item for item in result if item["code"]}
        self.assertEqual([seeded[f"tol-{n}"]["price_krw"] for n in (1,5,10,20,50)],
                         [1000,4950,9900,19800,49500])
        self.assertEqual(seeded["tol-50"]["coin_amount"], 55)
        self.assertEqual(seeded["tol-50"]["base_coin_amount"], 50)
        self.assertEqual(seeded["tol-50"]["bonus_coin_amount"], 5)

    def test_signup_still_grants_one_coin_once(self):
        user, created = register_kakao_user("payapp-signup-regression")
        self.assertTrue(created)
        user.wallet.refresh_from_db()
        self.assertEqual(user.wallet.balance, 1)
        again, created = register_kakao_user("payapp-signup-regression")
        self.assertFalse(created)
        self.assertEqual(again.wallet.balance, 1)

    @patch("payments.payapp_services.requests.post")
    def test_rest_request_uses_form_and_parses_query_response(self, mocked):
        mocked.return_value = Mock(status_code=200, content=urlencode({
            "state": "1", "mul_no": "12345", "payurl": "https://payapp.kr/pay/12345",
        }).encode())
        self.assertEqual(create_payment(self.order, "01012345678")[0], "12345")
        sent = mocked.call_args.kwargs["data"]
        self.assertEqual((sent["price"], sent["var2"]), ("49500", str(self.order.payapp_token)))
        self.assertNotIn("requestkey", sent)
        self.assertNotIn("json", mocked.call_args.kwargs)

    @patch("payments.payapp_services.requests.post", side_effect=requests.Timeout)
    def test_remote_timeout_not_retried(self, mocked):
        with self.assertRaises(PayAppError):
            create_payment(self.order, "01012345678")
        self.assertEqual(mocked.call_count, 1)

    @patch("payments.payapp_services.requests.post")
    def test_untrusted_checkout_urls_rejected(self, mocked):
        for url in ("https://evil.example/pay", "http://payapp.kr/pay",
                    "https://payapp.kr.evil.example/pay", "https://user@payapp.kr/pay",
                    "https://payapp.kr:invalid/pay", "https://[invalid/pay"):
            mocked.return_value = Mock(status_code=200, content=urlencode(
                dict(state="1", mul_no="12345", payurl=url)).encode())
            with self.subTest(url=url), self.assertRaises(PayAppError):
                create_payment(self.order, "01012345678")

    @override_settings(PAYAPP_RETURN_URL="https://example.com/result?tab=coins&order_id=old")
    def test_return_url_preserves_query_without_duplicate_order_id(self):
        query = parse_qs(urlsplit(return_url(self.order)).query)
        self.assertEqual(query, {"tab": ["coins"], "order_id": [str(self.order.pk)]})

    @override_settings(CORS_ALLOWED_ORIGINS=["https://example.com"])
    def test_browser_allows_idempotency_header(self):
        response = self.client.options(
            reverse("payment-orders"),
            HTTP_ORIGIN="https://example.com",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS="authorization,content-type,idempotency-key",
        )
        self.assertIn("idempotency-key", response["Access-Control-Allow-Headers"])

    @patch("payments.payapp_views.create_payment", return_value=("98765", "https://payapp.kr/pay/98765"))
    def test_idempotency_key_cannot_disclose_another_users_order(self, mocked):
        key = str(uuid4())
        data = dict(product_id=self.product.pk, recvphone="01012345678")
        self.api.post(reverse("payment-orders"), data, HTTP_IDEMPOTENCY_KEY=key)
        other = User.objects.create_user(username="other-payapp", nickname="다른 회원")
        self.api.force_authenticate(other)
        response = self.api.post(reverse("payment-orders"), data, HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("order_id", response.json())
        self.assertEqual(mocked.call_count, 1)
