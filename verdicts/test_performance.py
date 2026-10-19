import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import Mock, patch

from django.db import close_old_connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone

from accounts.models import User
from cases.models import Case
from coins.models import CoinWallet, CoinLedger, CoinTxType
from .ai_client import _search_factors, _image_parts, AIClientError
from .models import VerdictRequest, SearchThrottle
from .performance import admit_search
from .search_client import SearchRateLimited, SearchUnavailable, search_sources
from .services import start_request, fail_request
from .tasks import _claim_request


@override_settings(LINER_SEARCH_CONCURRENCY=2, LINER_SEARCH_TIMEOUT_SECONDS=5)
class ParallelSearchTests(TestCase):
    def factors(self):
        return [{"key": str(i), "scholar_query": q, "web_query": q}
                for i, q in enumerate(["first", "second", "first"])]

    @patch("verdicts.ai_client.admit_search")
    def test_overlap_order_and_duplicate_query_reuse(self, admit):
        second_finished = threading.Event()
        def search(query, kind, **kwargs):
            if query == "first":
                if not second_finished.wait(3):
                    raise AssertionError("Searches did not overlap")
            else:
                second_finished.set()
            return [{"title": query}]
        with patch("verdicts.ai_client.search_sources", side_effect=search) as remote:
            candidates, completed = _search_factors(self.factors(), "SCHOLAR", lambda step: 5)
        self.assertEqual(remote.call_count, 2)
        self.assertEqual(admit.call_count, 2)
        self.assertEqual([c["title"] for c in candidates], ["first", "second", "first"])
        self.assertEqual([c["id"] for c in candidates], ["scholar_0", "scholar_1", "scholar_2"])
        self.assertEqual(completed, {"0", "1", "2"})

    @patch("verdicts.ai_client.defer_search")
    @patch("verdicts.ai_client.admit_search")
    def test_429_retry_keeps_success_and_is_bounded(self, admit, defer):
        with patch("verdicts.ai_client.search_sources", side_effect=[SearchRateLimited(2), []]) as remote:
            candidates, completed = _search_factors(self.factors()[:1], "SCHOLAR", lambda step: 5)
        self.assertEqual(completed, {"0"})
        self.assertEqual(remote.call_count, 2)
        defer.assert_called_once_with("SCHOLAR", 2)
        with patch("verdicts.ai_client.search_sources", side_effect=SearchRateLimited()) as remote:
            candidates, completed = _search_factors(self.factors()[:1], "SCHOLAR", lambda step: 5)
        self.assertEqual(remote.call_count, 3)
        self.assertEqual(completed, set())

    @patch("verdicts.ai_client.admit_search")
    def test_unavailable_is_not_a_successful_empty_search(self, admit):
        with patch("verdicts.ai_client.search_sources", side_effect=SearchUnavailable()):
            candidates, completed = _search_factors(self.factors(), "SCHOLAR", lambda step: 5)
        self.assertEqual((candidates, completed), ([], set()))

    @patch("verdicts.ai_client.admit_search")
    def test_expired_request_does_not_start_search(self, admit):
        with patch("verdicts.ai_client.search_sources") as remote:
            with self.assertRaises(AIClientError):
                _search_factors(self.factors(), "SCHOLAR", Mock(side_effect=AIClientError("expired")))
        remote.assert_not_called()

    def test_image_order_when_conversion_finishes_out_of_order(self):
        event = threading.Event()
        def prepare(image):
            if image == 0:
                self.assertTrue(event.wait(3))
            else:
                event.set()
            return bytes([image])
        with patch("verdicts.ai_client._prepare_image", side_effect=prepare):
            parts = _image_parts([0, 1])
        self.assertTrue(parts[1]["image_url"]["url"].endswith("AA=="))
        self.assertTrue(parts[3]["image_url"]["url"].endswith("AQ=="))


class SearchAdmissionTests(TestCase):
    def test_shared_admission_waits_until_previous_slot_expires(self):
        now = [timezone.now()]
        SearchThrottle.objects.create(kind="SCHOLAR", next_at=now[0] + timedelta(seconds=0.6))
        def sleep(seconds):
            now[0] += timedelta(seconds=seconds)
        with patch("verdicts.performance.timezone.now", side_effect=lambda: now[0]), patch(
            "verdicts.performance.time.sleep", side_effect=sleep,
        ) as sleeper:
            admit_search("SCHOLAR", 5)
        self.assertTrue(sleeper.called)
        self.assertEqual(SearchThrottle.objects.get(pk="SCHOLAR").next_at,
                         now[0] + timedelta(seconds=0.6))

    @override_settings(LINER_API_KEY="test")
    def test_http_429_exposes_retry_delay(self):
        response = Mock(status_code=429, headers={"Retry-After": "3"})
        with patch("verdicts.search_client.requests.post", return_value=response):
            with self.assertRaises(SearchRateLimited) as error:
                search_sources("topic", "SCHOLAR")
        self.assertEqual(error.exception.retry_after, 3)


@skipUnlessDBFeature("has_select_for_update")
class PostgreSQLWorkerTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="parallel_worker", nickname="worker")
        CoinWallet.objects.create(user=self.user, balance=10)
        self.case = Case.objects.create(user=self.user)
        self.req = start_request(user=self.user, case=self.case)

    def race(self, action):
        barrier = threading.Barrier(2)
        def run():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                return action()
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(lambda _: run(), range(2)))

    def test_only_one_worker_claims_and_charge_is_unchanged(self):
        result = self.race(lambda: _claim_request(self.req.pk) is not None)
        self.assertEqual(sorted(result), [False, True])
        self.assertEqual(CoinWallet.objects.get(user=self.user).balance, 9)
        self.assertEqual(CoinLedger.objects.filter(tx_type=CoinTxType.VERDICT_SPEND).count(), 1)

    def test_failure_refunds_only_once_across_workers(self):
        self.race(lambda: fail_request(self.req.pk, "failure"))
        self.assertEqual(CoinWallet.objects.get(user=self.user).balance, 10)
        self.assertEqual(CoinLedger.objects.filter(tx_type=CoinTxType.VERDICT_RESTORE).count(), 1)

    def test_shared_throttle_admits_one_worker_at_a_time(self):
        SearchThrottle.objects.create(kind="SCHOLAR", next_at=timezone.now())
        from time import monotonic
        def admit():
            admit_search("SCHOLAR", 5)
            return monotonic()
        admitted = sorted(self.race(admit))
        self.assertGreaterEqual(admitted[1] - admitted[0], 0.5)

    @skipUnlessDBFeature("has_select_for_update_skip_locked")
    def test_worker_skips_case_locked_by_another_connection(self):
        def claim():
            close_old_connections()
            try:
                return _claim_request(self.req.pk, skip_locked=True)
            finally:
                close_old_connections()
        with transaction.atomic():
            Case.objects.select_for_update().get(pk=self.case.pk)
            with ThreadPoolExecutor(max_workers=1) as pool:
                self.assertIsNone(pool.submit(claim).result(timeout=5))
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, "PENDING")
