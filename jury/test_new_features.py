import io
import tempfile
from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from accounts.models import User
from cases.models import Case, CaseImage
from cases.relations import RELATION_CHOICES
from cases.serializers import CaseCreateSerializer
from verdicts.models import Verdict, VerdictRequest
from jury.models import JuryVote, JuryComment, CommentStatus


def picture(color):
    stream = io.BytesIO()
    Image.new("RGB", (5, 5), color).save(stream, "PNG")
    return SimpleUploadedFile("photo.png", stream.getvalue(), content_type="image/png")


class NewFeatureTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.directory.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.owner = User.objects.create_user(username="owner", nickname="owner", login_type="KAKAO")
        self.juror = User.objects.create_user(username="juror", nickname="juror", login_type="KAKAO")
        self.case = Case.objects.create(user=self.owner, status="JUDGED", is_public=True, public_at=timezone.now())
        req = VerdictRequest.objects.create(case=self.case, user=self.owner, status="DONE")
        Verdict.objects.create(case=self.case, request=req, relation=RELATION_CHOICES[3], public_title="title", public_summary="summary", fault_ratio=50, judgment_text="text")
        self.image = CaseImage.objects.create(case=self.case, image_key=picture("red"), sort_order=0)
        self.url = f"/api/cases/{self.case.pk}/images/{self.image.pk}/public-image/"
        self.public_url = f"/api/jury/cases/{self.case.pk}/images/{self.image.pk}/"
        self.client = APIClient()

    def content(self, response):
        self.assertEqual(response.status_code, 200)
        body = b"".join(response.streaming_content)
        response.close()
        return body

    def test_public_override_original_and_reset(self):
        original = self.content(self.client.get(self.public_url))
        self.client.force_authenticate(self.owner)
        result = self.client.put(self.url, {"image": picture("blue")}, format="multipart")
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.data["has_public_image"])
        masked = self.content(self.client.get(self.url))
        self.assertNotEqual(original, masked)
        self.assertEqual(self.content(self.client.get(f"/api/cases/{self.case.pk}/images/{self.image.pk}/")), original)
        self.client.force_authenticate(None)
        self.assertEqual(self.content(self.client.get(self.public_url)), masked)
        self.client.force_authenticate(self.owner)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.delete(self.url).status_code, 204)
        self.assertEqual(self.content(self.client.get(self.public_url)), original)

    def test_owner_only_and_bad_file(self):
        self.assertIn(self.client.get(self.url).status_code, (401, 403))
        self.client.force_authenticate(self.juror)
        self.assertEqual(self.client.put(self.url, {"image": picture("blue")}, format="multipart").status_code, 404)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.put(self.url, {"image": SimpleUploadedFile("bad.png", b"bad")}, format="multipart").status_code, 400)
        with override_settings(CASE_IMAGE_MAX_BYTES=1):
            self.assertEqual(self.client.put(self.url, {"image": picture("blue")}, format="multipart").status_code, 400)

    def test_activities_deduplicate_filter_and_visibility(self):
        JuryVote.objects.create(case=self.case, user=self.juror, fault_ratio=60)
        comment = JuryComment.objects.create(case=self.case, user=self.juror, content="hello")
        url = "/api/jury/me/activities/"
        self.assertIn(self.client.get(url).status_code, (401, 403))
        self.client.force_authenticate(self.juror)
        for kind in ("all", "votes", "comments"):
            result = self.client.get(url, {"type": kind})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.data["count"], 1)
            row = result.data["results"][0]
            self.assertEqual(row["my_fault_ratio"], 60)
            self.assertEqual(row["my_comment_count"], 1)
        comment.status = CommentStatus.HIDDEN
        comment.save()
        self.assertEqual(self.client.get(url, {"type": "comments"}).data["count"], 0)
        self.assertEqual(self.client.get(url).data["count"], 1)
        self.assertEqual(self.client.get(url, {"type": "invalid"}).status_code, 400)
        self.case.is_public = False
        self.case.save()
        self.assertEqual(self.client.get(url).data["count"], 0)

    def test_relation_shared_and_filter(self):
        data = CaseCreateSerializer(data={"relation": RELATION_CHOICES[3], "images": [picture("red")]})
        self.assertTrue(data.is_valid(), data.errors)
        result = self.client.get("/api/jury/cases/", {"relation": RELATION_CHOICES[3]})
        self.assertEqual(result.data["count"], 1)

    def test_comment_only_activity_and_latest_order(self):
        from datetime import timedelta
        JuryComment.objects.create(case=self.case, user=self.juror, content="only comment")
        second = Case.objects.create(user=self.owner, status="JUDGED", is_public=True, public_at=timezone.now())
        req = VerdictRequest.objects.create(case=second, user=self.owner, status="DONE")
        Verdict.objects.create(case=second, request=req, public_title="second", public_summary="summary", fault_ratio=50, judgment_text="text")
        vote = JuryVote.objects.create(case=second, user=self.juror, fault_ratio=30)
        JuryVote.objects.filter(pk=vote.pk).update(created_at=timezone.now()-timedelta(days=1))
        self.client.force_authenticate(self.juror)
        rows = self.client.get("/api/jury/me/activities/").data["results"]
        self.assertEqual([row["case_id"] for row in rows], [self.case.pk, second.pk])
        self.assertIsNone(rows[0]["my_fault_ratio"])
        self.assertFalse(rows[0]["has_voted"])

    def test_publish_roundtrip(self):
        self.client.force_authenticate(self.owner)
        path = f"/api/verdicts/cases/{self.case.pk}/publish/"
        self.assertEqual(self.client.delete(path).status_code, 204)
        self.assertEqual(self.client.get(self.public_url).status_code, 404)
        self.assertEqual(self.client.post(path).status_code, 200)
        self.assertEqual(self.client.post(path).status_code, 200)

    def test_effective_public_images_total_limit(self):
        other = CaseImage.objects.create(case=self.case, image_key=picture("green"), sort_order=1)
        upload = picture("blue")
        limit = upload.size + other.image_key.size - 1
        self.client.force_authenticate(self.owner)
        with override_settings(CASE_IMAGES_MAX_TOTAL_BYTES=limit):
            result = self.client.put(self.url, {"image": upload}, format="multipart")
        self.assertEqual(result.status_code, 400)
        self.image.refresh_from_db()
        self.assertFalse(self.image.public_image)

    def test_failed_file_deletion_is_retried(self):
        from unittest.mock import patch
        from django.core.management import call_command
        from django.core.files.storage import default_storage
        from cases.models import PendingImageDeletion
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.put(self.url, {"image": picture("blue")}, format="multipart").status_code, 200)
        self.image.refresh_from_db()
        old_name = self.image.public_image.name
        with patch("cases.image_cleanup.default_storage.delete", side_effect=OSError("unavailable")):
            with self.captureOnCommitCallbacks(execute=True):
                result = self.client.put(self.url, {"image": picture("green")}, format="multipart")
        self.assertEqual(result.status_code, 200)
        self.assertTrue(PendingImageDeletion.objects.filter(file_name=old_name).exists())
        self.assertTrue(default_storage.exists(old_name))
        call_command("retry_image_deletions", verbosity=0)
        self.assertFalse(default_storage.exists(old_name))
        self.assertFalse(PendingImageDeletion.objects.exists())
