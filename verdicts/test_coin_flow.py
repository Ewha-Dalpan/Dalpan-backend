from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from cases.models import Case
from coins.models import CoinLedger, CoinTxType, CoinWallet
from coins.services import InsufficientCoins
from verdicts.models import Verdict, VerdictRequest
from verdicts.services import (
    AlreadyRequested,
    cancel_request,
    complete_request,
    expire_abandoned,
    fail_request,
    fail_stale_requests,
    mark_running,
    start_request,
    start_verdict,
)

WAITING = "AWAITING_CONFIRMATION"


class CoinFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="coinflow_user", nickname="코인테스트")
        CoinWallet.objects.create(user=self.user, balance=10)
        self.case = Case.objects.create(user=self.user)

    # ---------- 헬퍼 ----------
    def balance(self):
        return CoinWallet.objects.get(user=self.user).balance

    def ledgers(self, tx_type):
        return CoinLedger.objects.filter(user=self.user, tx_type=tx_type).count()

    def to_waiting(self, req, age=timedelta(0)):
        """AI 상황 분석이 끝나 사용자 확인을 기다리는 상태를 만든다 (complete_analysis가 하는 일)."""
        VerdictRequest.objects.filter(pk=req.pk).update(
            status=WAITING,
            progress_step="WAITING_CONFIRMATION",
            started_at=timezone.now() - age,
        )

    def to_judging(self, req):
        """접수 → 확인 대기 → 판결 시작 → AI 작업 중(RUNNING)까지 진행한다."""
        self.to_waiting(req)
        self.assertTrue(start_verdict(req.pk))
        self.assertTrue(mark_running(req.pk))

    # ---------- 접수 ----------
    def test_request_spends_one_coin(self):
        req = start_request(user=self.user, case=self.case)
        self.assertEqual(self.balance(), 9)
        self.assertEqual(self.ledgers(CoinTxType.VERDICT_SPEND), 1)
        req.refresh_from_db()
        self.assertEqual((req.status, req.stage), ("PENDING", "ANALYSIS"))

    def test_duplicate_request_blocked(self):
        start_request(user=self.user, case=self.case)
        with self.assertRaises(AlreadyRequested):
            start_request(user=self.user, case=self.case)
        self.assertEqual(self.balance(), 9)  # 이중 차감 없음
        self.assertEqual(VerdictRequest.objects.filter(case=self.case).count(), 1)

    def test_insufficient_coins_creates_nothing(self):
        CoinWallet.objects.filter(user=self.user).update(balance=0)
        with self.assertRaises(InsufficientCoins):
            start_request(user=self.user, case=self.case)
        self.assertEqual(VerdictRequest.objects.filter(case=self.case).count(), 0)
        self.assertEqual(self.balance(), 0)

    # ---------- 상황확인 대기 / 판결 시작 ----------
    def test_waiting_for_confirmation_is_never_timed_out(self):
        req = start_request(user=self.user, case=self.case)
        self.to_waiting(req, age=timedelta(hours=5))
        self.assertEqual(fail_stale_requests(), 0)
        req.refresh_from_db()
        self.assertEqual(req.status, WAITING)
        self.assertEqual(self.balance(), 9)

    def test_start_verdict_resets_clock_and_blocks_double_click(self):
        req = start_request(user=self.user, case=self.case)
        self.to_waiting(req, age=timedelta(hours=3))  # 확인 화면에 3시간 머무름
        self.assertTrue(start_verdict(req.pk))
        self.assertFalse(start_verdict(req.pk))       # 중복 클릭
        req.refresh_from_db()
        self.assertEqual((req.status, req.stage), ("PENDING", "JUDGMENT"))
        self.assertGreater(req.started_at, timezone.now() - timedelta(minutes=1))
        self.assertEqual(fail_stale_requests(), 0)    # 3시간 전 시각 때문에 바로 실패하면 안 된다
        self.assertEqual(self.balance(), 9)

    # ---------- 판결 성공 / 실패 ----------
    def test_success_keeps_coin_spent(self):
        req = start_request(user=self.user, case=self.case)
        self.to_judging(req)
        self.assertTrue(complete_request(req.pk, fault_ratio=50, judgment_text="판결", reasons=["근거"]))
        req.refresh_from_db()
        self.assertEqual(req.status, "DONE")
        self.assertEqual(Verdict.objects.filter(request=req).count(), 1)
        self.assertEqual(self.balance(), 9)
        self.assertFalse(fail_request(req.pk, "x"))   # 끝난 요청은 실패·반환 처리 불가
        self.assertEqual(self.balance(), 9)

    def test_failure_refunds_exactly_once(self):
        req = start_request(user=self.user, case=self.case)
        self.to_judging(req)
        self.assertTrue(fail_request(req.pk, "AI 오류"))
        self.assertFalse(fail_request(req.pk, "중복 실패"))
        self.assertEqual(self.balance(), 10)
        self.assertEqual(self.ledgers(CoinTxType.VERDICT_RESTORE), 1)

    def test_analysis_failure_refunds(self):
        req = start_request(user=self.user, case=self.case)
        self.assertTrue(mark_running(req.pk))
        self.assertTrue(fail_request(req.pk, "상황 분석 오류"))
        self.assertEqual(self.balance(), 10)

    def test_timeout_refunds_exactly_once(self):
        req = start_request(user=self.user, case=self.case)
        mark_running(req.pk)
        VerdictRequest.objects.filter(pk=req.pk).update(started_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(fail_stale_requests(), 1)
        self.assertEqual(fail_stale_requests(), 0)
        self.assertEqual(self.balance(), 10)
        self.assertEqual(self.ledgers(CoinTxType.VERDICT_RESTORE), 1)

    def test_late_result_after_failure_is_discarded(self):
        req = start_request(user=self.user, case=self.case)
        self.to_judging(req)
        self.assertTrue(fail_request(req.pk, "시간 초과"))
        self.assertFalse(complete_request(req.pk, fault_ratio=50, judgment_text="판결", reasons=["근거"]))
        self.assertEqual(Verdict.objects.filter(request=req).count(), 0)  # 판결도 받고 코인도 돌려받는 구멍 없음
        self.assertEqual(self.balance(), 10)

    # ---------- 취소 / 방치 만료 ----------
    def test_cancel_only_while_waiting_and_no_refund(self):
        req = start_request(user=self.user, case=self.case)
        self.assertTrue(mark_running(req.pk))
        self.assertFalse(cancel_request(req.pk))      # AI 작업 중에는 취소 불가
        self.to_waiting(req)
        self.assertTrue(cancel_request(req.pk))
        req.refresh_from_db()
        self.assertEqual(req.status, "CANCELED")
        self.assertEqual(self.balance(), 9)           # 취소는 미반환
        self.assertEqual(self.ledgers(CoinTxType.VERDICT_RESTORE), 0)

    def test_abandoned_requests_expire_without_refund(self):
        req = start_request(user=self.user, case=self.case)
        self.to_waiting(req, age=timedelta(days=1))
        self.assertEqual(expire_abandoned(), [])      # 1일은 아직 만료 아님
        self.to_waiting(req, age=timedelta(days=400))
        self.assertEqual(expire_abandoned(), [self.case.pk])
        req.refresh_from_db()
        self.assertEqual(req.status, "CANCELED")
        self.assertEqual(self.balance(), 9)