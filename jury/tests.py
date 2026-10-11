import io
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from cases.models import Case, CaseImage
from coins.models import CoinLedger, CoinWallet
from verdicts.models import Verdict, VerdictRequest

from .models import JuryComment, Report


def make_user(name):
    return User.objects.create_user(username=name, nickname=f"{name}달토끼", login_type="KAKAO")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class JuryTests(TestCase):
    def setUp(self):
        self.author = make_user("author")
        self.case = self.make_public_case(self.author, "연애", "약속 직전 연락 공백 사건")
        self.client = APIClient()

    def make_public_case(self, owner, relation, title):
        case = Case.objects.create(user=owner, status="JUDGED", is_public=True, public_at=timezone.now())
        req = VerdictRequest.objects.create(case=case, user=owner, status="DONE")
        Verdict.objects.create(
            request=req, case=case, relation=relation, public_title=title,
            public_summary="공개 요약", fault_ratio=30, judgment_text="판결",
        )
        return case

    def vote(self, user, ratio, case=None):
        self.client.force_authenticate(user)
        return self.client.post(f"/api/jury/cases/{(case or self.case).pk}/votes/", {"fault_ratio": ratio})

    def test_result_rounding_always_sums_100(self):
        for i, ratio in enumerate([80, 70, 70]):
            self.vote(make_user(f"j{i}"), ratio)
        res = self.vote(make_user("j3"), 70).data  # 평균 72.5
        self.assertEqual(res["result"], {"author": 73, "other": 27})  # 각각 반올림하면 73:28 = 101
        self.case.refresh_from_db()
        self.assertEqual((self.case.jury_count, self.case.fault_ratio_sum), (4, 290))

    def test_vote_rules(self):
        juror = make_user("juror")
        self.assertEqual(self.vote(juror, 35).status_code, 400)   # 10 단위 아님
        self.assertEqual(self.vote(juror, 110).status_code, 400)
        self.assertEqual(self.vote(self.author, 50).status_code, 403)  # 본인 사건
        self.assertEqual(self.vote(juror, 60).status_code, 201)
        self.assertEqual(self.vote(juror, 40).status_code, 409)   # 수정 불가
        self.case.refresh_from_db()
        self.assertEqual((self.case.jury_count, self.case.fault_ratio_sum), (1, 60))

    def test_private_case_not_votable(self):
        Case.objects.filter(pk=self.case.pk).update(is_public=False)
        self.assertEqual(self.vote(make_user("x"), 50).status_code, 404)

    def test_daily_coin_limit_5(self):
        juror = make_user("juror")
        cases = [self.make_public_case(self.author, "친구", f"사건{i}") for i in range(6)]
        rewarded = [self.vote(juror, 50, c).data["coin_rewarded"] for c in cases]
        self.assertEqual(rewarded, [True] * 5 + [False])  # 6번째도 참여는 성공, 코인만 미지급
        self.assertEqual(CoinWallet.objects.get(user=juror).balance, 5)
        self.case.refresh_from_db()

    def test_detail_and_comment_access(self):
        juror, outsider = make_user("juror"), make_user("outsider")
        detail = f"/api/jury/cases/{self.case.pk}/"
        comments = f"/api/jury/cases/{self.case.pk}/comments/"

        anon = APIClient().get(detail).data
        self.assertFalse(anon["can_vote"])
        self.assertIsNone(anon["result"])
        self.assertEqual(anon["title"], "약속 직전 연락 공백 사건")

        self.client.force_authenticate(outsider)
        before = self.client.get(detail).data
        self.assertTrue(before["can_vote"])
        self.assertIsNone(before["result"])
        self.assertEqual(self.client.get(comments).status_code, 403)
        self.assertEqual(self.client.post(comments, {"content": "의견"}).status_code, 403)

        self.vote(juror, 80)
        self.assertEqual(self.client.post(comments, {"content": "약속 전엔 연락했어야"}).status_code, 201)
        after = self.client.get(detail).data
        self.assertEqual(after["my_vote"], {"author": 80, "other": 20})
        self.assertEqual(after["result"], {"author": 80, "other": 20})
        self.assertEqual(after["comment_count"], 1)
        listed = self.client.get(comments).data["results"]
        self.assertTrue(listed[0]["is_mine"])

        # 작성자: 투표는 못 하지만 결과/댓글은 봄
        self.client.force_authenticate(self.author)
        mine = self.client.get(detail).data
        self.assertFalse(mine["can_vote"])
        self.assertEqual(mine["result"], {"author": 80, "other": 20})
        self.assertEqual(self.client.get(comments).status_code, 200)
        self.assertEqual(self.client.post(comments, {"content": "작성자 해명"}).status_code, 201)

        # 남의 댓글 삭제 불가, 본인 삭제는 소프트 삭제
        cid = JuryComment.objects.get(user=juror).pk
        self.assertEqual(self.client.delete(f"{comments}{cid}/").status_code, 404)
        self.client.force_authenticate(juror)
        self.assertEqual(self.client.delete(f"{comments}{cid}/").status_code, 204)
        self.assertEqual(JuryComment.objects.get(pk=cid).status, "DELETED")
        self.assertEqual(self.client.get(detail).data["comment_count"], 1)  # 작성자 댓글만 남음

    def test_list_sort_and_filter(self):
        friend = self.make_public_case(self.author, "친구", "생일 선물 요구 사건")
        for i in range(2):
            self.vote(make_user(f"p{i}"), 50, friend)
        hidden = self.make_public_case(self.author, "가족", "숨김")
        Case.objects.filter(pk=hidden.pk).update(is_hidden=True)

        c = APIClient()
        latest = [r["case_id"] for r in c.get("/api/jury/cases/").data["results"]]
        self.assertEqual(latest, [friend.pk, self.case.pk])
        popular = c.get("/api/jury/cases/?sort=popular").data["results"]
        self.assertEqual([r["case_id"] for r in popular], [friend.pk, self.case.pk])
        self.assertEqual(popular[0]["jury_count"], 2)
        only = c.get("/api/jury/cases/?relation=연애").data["results"]
        self.assertEqual([(r["case_id"], r["relation"]) for r in only], [(self.case.pk, "연애")])

    def test_original_images_follow_public_state(self):
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (2, 2)).save(buf, "PNG")
        image = CaseImage.objects.create(
            case=self.case, sort_order=0, image_key=SimpleUploadedFile("a.png", buf.getvalue(), "image/png"),
        )
        c = APIClient()
        url = c.get(f"/api/jury/cases/{self.case.pk}/").data["images"][0]
        self.assertTrue(url.endswith(f"/api/jury/cases/{self.case.pk}/images/{image.pk}/"))
        self.assertEqual(c.get("/api/jury/cases/").data["results"][0]["thumbnail"], url)
        self.assertEqual(c.get(url).status_code, 200)

        # 공유 취소하면 목록/상세/이미지 모두 404
        self.client.force_authenticate(self.author)
        self.assertEqual(self.client.delete(f"/api/verdicts/cases/{self.case.pk}/publish/").status_code, 204)
        self.assertEqual(c.get(url).status_code, 404)
        self.assertEqual(c.get(f"/api/jury/cases/{self.case.pk}/").status_code, 404)

    def test_reports(self):
        juror, other = make_user("juror"), make_user("other")
        case_report = f"/api/jury/cases/{self.case.pk}/reports/"

        self.client.force_authenticate(self.author)
        self.assertEqual(self.client.post(case_report, {"reason": "SPAM"}).status_code, 403)  # 본인 사건
        self.client.force_authenticate(other)
        self.assertEqual(self.client.post(case_report, {"reason": "OTHER"}).status_code, 400)  # 기타는 내용 필수
        self.assertEqual(self.client.post(case_report, {"reason": "PRIVACY"}).status_code, 201)
        self.assertEqual(self.client.post(case_report, {"reason": "SPAM"}).status_code, 409)  # 중복

        self.vote(juror, 50)
        comment = JuryComment.objects.create(case=self.case, user=juror, content="댓글")
        comment_report = f"/api/jury/cases/{self.case.pk}/comments/{comment.pk}/reports/"
        self.client.force_authenticate(other)
        self.assertEqual(self.client.post(comment_report, {"reason": "ABUSE"}).status_code, 403)  # 판결 미제출
        self.client.force_authenticate(juror)
        self.assertEqual(self.client.post(comment_report, {"reason": "ABUSE"}).status_code, 403)  # 본인 댓글
        self.client.force_authenticate(self.author)
        self.assertEqual(
            self.client.post(comment_report, {"reason": "OTHER", "detail": "사실과 다름"}).status_code, 201,
        )
        self.assertEqual(Report.objects.filter(status="PENDING").count(), 2)

    def test_withdrawn_author_images_hidden_then_deleted(self):
        from datetime import timedelta
        from django.core.management import call_command
        from PIL import Image
        from accounts.withdrawal import withdraw_user
        buf = io.BytesIO()
        Image.new("RGB", (2, 2)).save(buf, "PNG")
        image = CaseImage.objects.create(
            case=self.case, sort_order=0, image_key=SimpleUploadedFile("a.png", buf.getvalue(), "image/png"),
        )
        c = APIClient()
        url = c.get(f"/api/jury/cases/{self.case.pk}/").data["images"][0]
        with self.settings(KAKAO_ADMIN_KEY=""):
            withdraw_user(self.author.pk)
        detail = c.get(f"/api/jury/cases/{self.case.pk}/").data
        self.assertEqual(detail["images"], [])          # 즉시 비공개
        self.assertEqual(detail["title"], "약속 직전 연락 공백 사건")  # 텍스트는 유지
        self.assertEqual(c.get(url).status_code, 404)

        call_command("cleanup_withdrawn_images", "--apply", stdout=io.StringIO())
        self.assertTrue(CaseImage.objects.filter(pk=image.pk).exists())  # 30일 전엔 보관
        User.objects.filter(pk=self.author.pk).update(withdrawn_at=timezone.now() - timedelta(days=31))
        call_command("cleanup_withdrawn_images", "--apply", stdout=io.StringIO())
        self.assertFalse(CaseImage.objects.filter(pk=image.pk).exists())