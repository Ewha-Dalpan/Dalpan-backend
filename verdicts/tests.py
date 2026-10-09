import copy
import json
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from cases.models import Case, CaseSituation, CaseIssue
from cases.services import request_judgment
from .ai_client import AIClientError, judge_case
from .models import Verdict, VerdictRequest, VerdictShare
from .search_client import SearchUnavailable, search_sources
from .services import complete_analysis, complete_request
from .tasks import process_request


def analysis_data():
    return {
        "analyzable": True, "summary": "원래 AI 분석", "conflict_core": "연락 방식",
        "messages": [
            {"image_order": 0, "speaker_side": "RIGHT", "text": "이름 비밀사용자 010-1234-5678"},
            {"image_order": 0, "speaker_side": "LEFT", "text": "연락을 못 했어"},
        ],
        "uncertainties": ["시간 간격은 확인되지 않음"],
    }


def factor_plan():
    return {"factors": [
        {"key": "gap", "side": "OTHER", "name": "연락 공백", "summary": "약속 확인 연락에 응답하지 않음",
         "evidence": [1], "scholar_query": "communication responsiveness relationships",
         "web_query": "관계 응답성 언론 기사"},
        {"key": "tone", "side": "SELF", "name": "표현 방식", "summary": "불만을 강하게 표현함",
         "evidence": [0], "scholar_query": "conflict communication tone",
         "web_query": "약속 직전 연락 공백 사건"},
    ]}


def final_data():
    return {
        "judgeable": True, "title": "약속 직전 연락 공백 사건",
        "public_title": "연락 방식 갈등", "public_summary": "약속을 확인하는 과정에서 양측의 연락 방식과 표현에 갈등이 발생했다.",
        "fault_ratio": 10, "one_line": "상대 쪽으로 저울이 기울었어요.",
        "case_summary": "수정된 상황 분석을 바탕으로 한 요약",
        "judgment_text": "대인관계상 참고 의견으로 상대의 연락 공백이 더 큰 책임입니다.",
        "recommended_reply": "다음에는 늦어질 것 같으면 미리 알려줬으면 좋겠어.",
        "factors": [
            {**{k: v for k, v in f.items() if k not in ("scholar_query", "web_query")},
             "source_id": None, "reference_summary": ""}
            for f in factor_plan()["factors"]
        ],
    }


def source(kind="SCHOLAR"):
    return {
        "kind": kind, "title": "관계에서의 응답성", "url": "https://example.org/research",
        "publisher": "연구기관", "authors": ["Researcher"], "published_date": "2024",
        "description": "응답성과 관계 불확실성에 관한 연구",
    }


@override_settings(LINER_API_KEY="test-key")
class VerdictPipelineTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="verdict_owner", nickname="owner")
        self.other = User.objects.create_user(username="verdict_other", nickname="other")
        self.case = Case.objects.create(user=self.owner, status="CONFIRMING")
        self.situation = CaseSituation.objects.create(case=self.case, relation="연애", analysis_status="RUNNING")
        self.req = VerdictRequest.objects.create(case=self.case, user=self.owner, stage="ANALYSIS")
        complete_analysis(
            self.req.pk, summary="원래 AI 분석", conflict_core="연락 방식", ai_raw=analysis_data(),
        )
        self.situation.refresh_from_db()
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def start_judgment(self):
        with patch("cases.services.get_dispatcher", return_value=Mock()):
            return request_judgment(user=self.owner, case_id=self.case.pk)

    def finish(self):
        self.start_judgment()
        with patch("verdicts.ai_client._call_liner", side_effect=[
            copy.deepcopy(factor_plan()), copy.deepcopy(final_data()),
        ]), patch("verdicts.ai_client.search_sources", return_value=[]):
            process_request(self.req.pk)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, "DONE")
        return Verdict.objects.get(request=self.req)

    def test_summary_edit_used_and_core_extra_context_locked(self):
        response = self.client.patch(
            f"/api/cases/{self.case.pk}/situation/",
            {"summary": "사용자가 수정한 상황", "user_speaker_side": "LEFT"}, format="json",
        )
        self.assertEqual(response.status_code, 200)
        for field in ["conflict_core", "user_extra_context", "relation", "ai_raw"]:
            response = self.client.patch(
                f"/api/cases/{self.case.pk}/situation/", {field: "연애"}, format="json",
            )
            self.assertEqual(response.status_code, 400)
        self.start_judgment()
        with patch("verdicts.ai_client._call_liner", side_effect=[
            copy.deepcopy(factor_plan()), copy.deepcopy(final_data()),
        ]) as model, patch("verdicts.ai_client.search_sources", return_value=[]):
            with patch("verdicts.ai_client._image_parts", side_effect=AssertionError("No images in phase two")):
                process_request(self.req.pk)
        payload = json.loads(model.call_args_list[0].kwargs["content"])
        self.assertEqual(payload["confirmed_summary"], "사용자가 수정한 상황")
        self.assertEqual(payload["original_summary"], "원래 AI 분석")
        self.assertEqual(payload["conflict_core"], "연락 방식")
        self.assertEqual(payload["user_speaker_side"], "LEFT")
        self.assertNotIn("user_extra_context", payload)
        self.assertEqual(CaseIssue.objects.get(case=self.case).content, "연락 방식")
        self.assertEqual(json.loads(CaseSituation.objects.get(pk=self.situation.pk).ai_raw)["summary"], "원래 AI 분석")

    def test_final_payload_title_chips_reply_and_ratio(self):
        verdict = self.finish()
        self.case.refresh_from_db()
        self.assertEqual(self.case.title, verdict.title)
        response = self.client.get(f"/api/verdicts/cases/{self.case.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["case_id"], self.case.pk)
        self.assertEqual(response.data["relation"], "연애")
        self.assertEqual(response.data["other_fault_ratio"], 90)
        self.assertEqual(response.data["self_factors"][0]["name"], "표현 방식")
        self.assertEqual(response.data["other_factors"][0]["name"], "연락 공백")
        self.assertFalse(response.data["self_factors"][0]["can_expand"])
        self.assertIsNone(response.data["self_factors"][0]["source"])
        self.assertIn("미리", response.data["recommended_reply"])
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f"/api/verdicts/cases/{self.case.pk}/").status_code, 404)

    def test_source_saved_and_returned_with_expandable_chip(self):
        self.start_judgment()
        final = final_data()
        final["factors"][0].update(source_id="scholar_0", reference_summary="응답성 연구를 참고했습니다.")
        with patch("verdicts.ai_client._call_liner", side_effect=[
            factor_plan(),
            {"selections": [
                {"factor_key": "gap", "source_id": "scholar_0", "reference_summary": "응답성 연구를 참고했습니다."},
                {"factor_key": "tone", "source_id": None, "reference_summary": ""},
            ]},
            final,
        ]), patch("verdicts.ai_client.search_sources", side_effect=[[source()], [], []]):
            process_request(self.req.pk)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, "DONE")
        response = self.client.get(f"/api/verdicts/cases/{self.case.pk}/")
        factor = response.data["other_factors"][0]
        self.assertTrue(factor["can_expand"])
        self.assertEqual(factor["source"]["kind"], "SCHOLAR")
        self.assertEqual(factor["source"]["url"], "https://example.org/research")
        self.assertEqual(factor["source"]["authors"], ["Researcher"])
        verdict = Verdict.objects.get(request=self.req)
        self.assertEqual(verdict.input_snapshot["confirmed_summary"], self.situation.summary)

    def test_no_edit_after_final_request(self):
        self.start_judgment()
        response = self.client.patch(
            f"/api/cases/{self.case.pk}/situation/", {"summary": "뒤늦은 수정"}, format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_share_is_private_projection_reusable_and_revocable(self):
        self.finish()
        path = f"/api/verdicts/cases/{self.case.pk}/share/"
        first = self.client.post(path)
        second = self.client.post(path)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.data["token"], second.data["token"])
        anonymous = APIClient()
        shared_path = f"/api/verdicts/shared/{first.data['token']}/"
        response = anonymous.get(shared_path)
        self.assertEqual(response.status_code, 200)
        payload = json.dumps(response.data, ensure_ascii=False)
        self.assertNotIn("010-1234", payload)
        self.assertNotIn("비밀사용자", payload)
        for field in ["images", "messages", "input_snapshot", "judgment_text", "recommended_reply"]:
            self.assertNotIn(field, response.data)
        self.assertFalse(Case.objects.get(pk=self.case.pk).is_public)
        self.assertEqual(self.client.delete(path).status_code, 204)
        self.assertEqual(anonymous.get(shared_path).status_code, 404)
        new_share = self.client.post(path)
        self.assertNotEqual(new_share.data["token"], first.data["token"])

    def test_other_owner_cannot_share_publish_and_hidden_shared_case_unavailable(self):
        self.finish()
        share = self.client.post(f"/api/verdicts/cases/{self.case.pk}/share/")
        self.client.force_authenticate(self.other)
        for action in ["share", "publish"]:
            self.assertEqual(self.client.post(f"/api/verdicts/cases/{self.case.pk}/{action}/").status_code, 404)
        Case.objects.filter(pk=self.case.pk).update(is_hidden=True)
        self.assertEqual(APIClient().get(f"/api/verdicts/shared/{share.data['token']}/").status_code, 404)

    def test_publish_is_idempotent_and_can_be_cancelled(self):
        self.finish()
        publish_path = f"/api/verdicts/cases/{self.case.pk}/publish/"
        response = self.client.post(publish_path)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_public"])
        self.assertNotIn("jury_url", response.data)
        self.case.refresh_from_db()
        self.assertTrue(self.case.is_public)
        first_public_at = self.case.public_at
        self.assertIsNotNone(first_public_at)

        self.client.post(publish_path)
        self.case.refresh_from_db()
        self.assertEqual(self.case.public_at, first_public_at)
        self.assertEqual((self.case.jury_count, self.case.fault_ratio_sum), (0, 0))

        self.assertEqual(self.client.delete(publish_path).status_code, 204)
        self.case.refresh_from_db()
        self.assertFalse(self.case.is_public)
        self.assertIsNone(self.case.public_at)

    def test_jury_lookup_and_vote_routes_are_not_registered(self):
        self.finish()
        self.client.post(f"/api/verdicts/cases/{self.case.pk}/publish/")
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get("/api/jury/cases/").status_code, 404)
        self.assertEqual(self.client.get(f"/api/jury/cases/{self.case.pk}/").status_code, 404)
        self.assertEqual(
            self.client.post(f"/api/jury/cases/{self.case.pk}/votes/", {"fault_ratio": 20}).status_code,
            404,
        )

    def test_invalid_factor_does_not_partially_store(self):
        self.start_judgment()
        snapshot = {"relation": "연애", "messages": analysis_data()["messages"]}
        factors = [{
            "key": "gap", "side": "OTHER", "name": "연애", "summary": "연애",
            "evidence": [999], "source": None,
        }]
        self.assertFalse(complete_request(
            self.req.pk, fault_ratio=10, judgment_text="연애", title="연애", relation="연애",
            factors=factors, recommended_reply="연애", public_title="연락 방식",
            public_summary="연락 방식", input_snapshot=snapshot, one_line="연애", case_summary="연애",
        ))
        self.assertFalse(Verdict.objects.exists())


@override_settings(LINER_API_KEY="test-key", LINER_SEARCH_TIMEOUT_SECONDS=5)
class SourcePipelineTests(TestCase):
    def setUp(self):
        self.situation = Mock(relation="연애", summary="뒤늦은 수정", user_speaker_side="RIGHT")

    def run_pipeline(self, responses, search_effect):
        with patch("verdicts.ai_client._call_liner", side_effect=copy.deepcopy(responses)), patch(
            "verdicts.ai_client.search_sources", side_effect=search_effect,
        ) as search:
            result = judge_case(situation=self.situation, conflict_core="연락 방식", analysis=analysis_data())
        return result, search

    def test_scholar_priority_web_only_for_unmatched_factor(self):
        final = final_data()
        final["factors"][0].update(source_id="scholar_0", reference_summary="응답성 연구 참고")
        final["factors"][1].update(source_id="web_0", reference_summary="대화 방식 기사 참고")
        result, search = self.run_pipeline([
            factor_plan(),
            {"selections": [
                {"factor_key": "gap", "source_id": "scholar_0", "reference_summary": "응답성 연구 참고"},
                {"factor_key": "tone", "source_id": None, "reference_summary": ""},
            ]},
            {"selections": [{"factor_key": "tone", "source_id": "web_0", "reference_summary": "대화 방식 기사 참고"}]},
            final,
        ], [[source()], [], [source("WEB")]])
        self.assertEqual([c.args[1] for c in search.call_args_list], ["SCHOLAR", "SCHOLAR", "WEB"])
        self.assertEqual(result["factors"][0]["source"]["url"], "https://example.org/research")
        self.assertEqual(result["factors"][1]["source"]["kind"], "WEB")

    def test_irrelevant_nonempty_scholar_results_fall_back_to_web(self):
        result, search = self.run_pipeline([
            factor_plan(),
            {"selections": [
                {"factor_key": "gap", "source_id": None, "reference_summary": ""},
                {"factor_key": "tone", "source_id": None, "reference_summary": ""},
            ]},
            final_data(),
        ], [[source()], [], [], []])
        self.assertEqual([c.args[1] for c in search.call_args_list], ["SCHOLAR", "SCHOLAR", "WEB", "WEB"])
        self.assertTrue(all(f["source"] is None for f in result["factors"]))

    def test_search_outage_is_not_treated_as_no_papers(self):
        result, search = self.run_pipeline(
            [factor_plan(), final_data()],
            [SearchUnavailable("outage"), SearchUnavailable("outage")],
        )
        self.assertEqual([c.args[1] for c in search.call_args_list], ["SCHOLAR", "SCHOLAR"])
        self.assertTrue(all(f["source"] is None for f in result["factors"]))

    def test_review_outage_does_not_trigger_web_fallback(self):
        result, search = self.run_pipeline(
            [factor_plan(), AIClientError("review failed"), final_data()],
            [[source()], [source()]],
        )
        self.assertEqual(search.call_count, 2)
        self.assertTrue(all(f["source"] is None for f in result["factors"]))

    def test_fabricated_source_id_rejected(self):
        final = final_data()
        final["factors"][0].update(source_id="invented", reference_summary="연락 방식")
        with self.assertRaises(AIClientError):
            self.run_pipeline([factor_plan(), final], [[], [], [], []])

    def test_cross_factor_source_rejected(self):
        with self.assertRaises(AIClientError):
            self.run_pipeline([
                factor_plan(),
                {"selections": [{"factor_key": "tone", "source_id": "scholar_0", "reference_summary": "연애"}]},
            ], [[source()], []])

    def test_invalid_message_index_rejected(self):
        plan = factor_plan()
        plan["factors"][0]["evidence"] = [999]
        with self.assertRaises(AIClientError):
            self.run_pipeline([plan], [])

    def test_search_http_shape_and_invalid_urls_filtered(self):
        response = Mock(ok=True)
        response.json.return_value = {"results": [
            {"title": "연애", "url": "https://example.org/paper", "description": "연애", "authors": ["A"], "journal": "Journal"},
            {"title": "연애", "url": "http://127.0.0.1/private", "description": "연애"},
        ]}
        with patch("verdicts.search_client.requests.post", return_value=response) as post:
            results = search_sources("communication", "SCHOLAR")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["publisher"], "Journal")
        self.assertTrue(post.call_args.args[0].endswith("/search/scholar"))
        self.assertEqual(post.call_args.kwargs["headers"]["x-api-key"], "test-key")
        self.assertNotIn("country_code", post.call_args.kwargs["json"])
